import { Http3Server, WebTransport as NodeWebTransport, quicheLoaded } from '@fails-components/webtransport'
import express from 'express'
import cors from 'cors'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'
import { createHash, X509Certificate } from 'crypto'
import { v4 as uuid } from 'uuid'
import { broadcast, logInfo, logError } from './logger.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const certsDir = path.join(__dirname, 'certs')

if (!fs.existsSync(path.join(certsDir, 'cert.pem'))) {
  console.error(JSON.stringify({ level: 'error', msg: 'No certificate found. Run: npm run gen-cert' }))
  process.exit(1)
}

// Read as strings — the webtransport package expects PEM strings
const certPem = fs.readFileSync(path.join(certsDir, 'cert.pem'), 'utf8')
const keyPem = fs.readFileSync(path.join(certsDir, 'key.pem'), 'utf8')

// WebTransport serverCertificateHashes uses SHA-256 over the ENTIRE DER-encoded
// certificate. This is NOT the SPKI hash (SHA-256 of SubjectPublicKeyInfo) — the
// SPKI hash is only for Chrome's --ignore-certificate-errors-spki-list launch flag.
// Using the SPKI hash here makes the QUIC handshake fail cert verification for every
// connection (browser client and the proxy→target hop alike).
const x509 = new X509Certificate(certPem)
const certHashBuf = createHash('sha256').update(x509.raw).digest() // Node Buffer, full-cert DER hash
const certHashBase64 = certHashBuf.toString('base64')

// --- HTTP API server (CORS-enabled, serves cert hash to browser) ---
const app = express()
app.use(cors())
app.use(express.json())

app.get('/health', (_req, res) => {
  res.json({ status: 'ok', proxy: 'active', certHash: certHashBase64 })
})

app.get('/cert-hash', (_req, res) => {
  res.json({ hash: certHashBase64 })
})

let tamperEnabled = false

app.post('/tamper', (req, res) => {
  tamperEnabled = req.body?.enabled ?? false
  res.json({ tamperEnabled })
  logInfo('Tamper mode changed', { tamperEnabled })
})

// --- Upstream target config (editable from the UI) ---
// Defaults to the bundled vulnerable server, which shares our self-signed cert,
// so its DER hash is the same value we serve at /cert-hash. Point it anywhere:
//  - certHash set   -> pin that SHA-256(DER cert) hash (self-signed targets)
//  - certHash empty -> normal CA verification (publicly-trusted targets)
let targetConfig = { host: '127.0.0.1', port: 4434, certHash: certHashBase64 }

app.get('/target', (_req, res) => {
  res.json(targetConfig)
})

app.post('/target', (req, res) => {
  const { host, port, certHash } = req.body ?? {}
  if (!host || typeof host !== 'string' || !host.trim()) {
    return res.status(400).json({ error: 'host is required' })
  }
  const p = Number(port)
  if (!Number.isInteger(p) || p < 1 || p > 65535) {
    return res.status(400).json({ error: 'port must be an integer 1–65535' })
  }
  let hash = ''
  if (typeof certHash === 'string' && certHash.trim()) {
    hash = certHash.trim()
    let buf
    try {
      buf = Buffer.from(hash, 'base64')
    } catch {
      return res.status(400).json({ error: 'cert hash is not valid base64' })
    }
    if (buf.length !== 32) {
      return res.status(400).json({ error: 'cert hash must be a base64 SHA-256 (32 bytes)' })
    }
  }
  targetConfig = { host: host.trim(), port: p, certHash: hash }
  logInfo('Upstream target changed', { host: targetConfig.host, port: targetConfig.port, pinned: !!hash })
  res.json(targetConfig)
})

app.listen(4436, () => {
  logInfo('HTTP API server started', { port: 4436 })
})

// --- Payload helpers ---
function tamperPayload(payload) {
  if (!tamperEnabled) return { tampered: false, payload }
  if (payload.includes('"token"')) {
    const modified = payload.replace(/"token"\s*:\s*"[^"]*"/g, '"token":"TAMPERED_BY_LEGILIMENS"')
    return { tampered: true, payload: modified }
  }
  return { tampered: false, payload }
}

function isSuspicious(payload) {
  const lower = payload.toLowerCase()
  return ['session_token', 'password', 'secret', 'api_key'].some((k) => lower.includes(k))
}

// --- Wait for quiche transport to load before starting servers ---
await quicheLoaded
logInfo('Quiche transport loaded')

// --- WebTransport Proxy Server ---
const proxyServer = new Http3Server({
  port: 4433,
  host: '0.0.0.0',
  secret: `legilimens-proxy-${Date.now()}`,
  cert: certPem,
  privKey: keyPem,
})

// Register session paths BEFORE startServer() so they are in _pendingPaths
// and get registered on the transport as soon as it initialises
const pathsToProxy = ['/', '/stream']
for (const p of pathsToProxy) {
  handlePath(p).catch((err) => logError(`Handler error ${p}`, err))
}

proxyServer.startServer()
logInfo('Proxy WebTransport server started', { port: 4433 })

// Optionally wait until the server socket is actually bound
proxyServer.ready
  .then(() => logInfo('Proxy server ready and bound', { port: 4433 }))
  .catch((err) => logError('Proxy server ready error', err))

async function handlePath(urlPath) {
  const sessionStream = proxyServer.sessionStream(urlPath)
  const reader = sessionStream.getReader()

  while (true) {
    const { done, value: clientSession } = await reader.read()
    if (done) break

    const sessionId = uuid()
    logInfo('Incoming WebTransport session', { sessionId, path: urlPath })

    broadcast({
      id: uuid(),
      type: 'connection',
      direction: 'incoming',
      payload: `Session ${sessionId.slice(0, 8)} connected via ${urlPath}`,
      rawSize: 0,
      timestamp: Date.now(),
      latency: 0,
      flag: 'normal',
    })

    connectAndProxy(clientSession, sessionId, urlPath).catch((err) =>
      logError('Proxy session error', err)
    )
  }
}

async function connectAndProxy(clientSession, sessionId, urlPath) {
  let serverSession

  try {
    // Target is configurable from the UI (POST /target). Use 127.0.0.1 rather than
    // localhost by default so this never depends on IPv6/IPv4 resolution order.
    const target = targetConfig
    const targetUrl = `https://${target.host}:${target.port}${urlPath}`
    const options = {}
    if (target.certHash) {
      // Pin the target's self-signed cert. Buffer extends Uint8Array (valid BufferSource).
      options.serverCertificateHashes = [
        { algorithm: 'sha-256', value: Buffer.from(target.certHash, 'base64') },
      ]
    }
    // No certHash -> rely on normal CA verification (publicly-trusted target).
    serverSession = new NodeWebTransport(targetUrl, options)
    await serverSession.ready
    logInfo('Connected to upstream target', { sessionId, target: `${target.host}:${target.port}` })
  } catch (err) {
    logError('Failed to connect to upstream target', err)
    broadcast({
      id: uuid(),
      type: 'connection',
      direction: 'outgoing',
      payload: `Failed to reach upstream target ${targetConfig.host}:${targetConfig.port}: ${err.message}`,
      rawSize: 0,
      timestamp: Date.now(),
      latency: 0,
      flag: 'suspicious',
    })
    try { clientSession.close() } catch {}
    return
  }

  proxyDatagrams(clientSession.datagrams.readable, serverSession.datagrams.writable, 'incoming', sessionId)
  proxyDatagrams(serverSession.datagrams.readable, clientSession.datagrams.writable, 'outgoing', sessionId)
  proxyStreams(clientSession.incomingBidirectionalStreams, serverSession, sessionId)

  clientSession.closed.catch(() => {}).finally(() => { try { serverSession.close() } catch {} })
  serverSession.closed.catch(() => {}).finally(() => { try { clientSession.close() } catch {} })
}

async function proxyDatagrams(readable, writable, direction, sessionId) {
  const reader = readable.getReader()
  const writer = writable.getWriter()

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      const sendTime = Date.now()
      const raw = new TextDecoder().decode(value)
      const { tampered, payload } = tamperPayload(raw)
      const outBytes = new TextEncoder().encode(payload)

      await writer.write(outBytes)
      const latency = Date.now() - sendTime

      broadcast({
        id: uuid(),
        type: 'datagram',
        direction,
        payload: payload.length > 300 ? payload.slice(0, 300) + '…' : payload,
        rawSize: value.byteLength,
        timestamp: sendTime,
        latency,
        flag: tampered ? 'tampered' : isSuspicious(payload) ? 'suspicious' : 'normal',
      })
    }
  } catch {
    // Normal close
  } finally {
    try { reader.releaseLock() } catch {}
    try { writer.releaseLock() } catch {}
  }
}

async function proxyStreams(incomingStreams, serverSession, sessionId) {
  const reader = incomingStreams.getReader()

  try {
    while (true) {
      const { done, value: clientStream } = await reader.read()
      if (done) break

      const streamId = uuid().slice(0, 8)

      broadcast({
        id: uuid(),
        type: 'stream',
        direction: 'incoming',
        payload: `Stream ${streamId} opened`,
        rawSize: 0,
        timestamp: Date.now(),
        latency: 0,
        streamId,
        flag: 'normal',
      })

      let serverStream
      try {
        serverStream = await serverSession.createBidirectionalStream()
      } catch {
        continue
      }

      proxyStreamChunks(clientStream.readable, serverStream.writable, 'incoming', streamId)
      proxyStreamChunks(serverStream.readable, clientStream.writable, 'outgoing', streamId)
    }
  } catch {
    // Session closed
  } finally {
    try { reader.releaseLock() } catch {}
  }
}

async function proxyStreamChunks(readable, writable, direction, streamId) {
  const reader = readable.getReader()
  const writer = writable.getWriter()

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      const sendTime = Date.now()
      const raw = new TextDecoder().decode(value)
      const { tampered, payload } = tamperPayload(raw)
      const outBytes = new TextEncoder().encode(payload)

      await writer.write(outBytes)
      const latency = Date.now() - sendTime

      broadcast({
        id: uuid(),
        type: 'stream',
        direction,
        payload: payload.length > 300 ? payload.slice(0, 300) + '…' : payload,
        rawSize: value.byteLength,
        timestamp: sendTime,
        latency,
        streamId,
        flag: tampered ? 'tampered' : isSuspicious(payload) ? 'suspicious' : 'normal',
      })
    }
  } catch {
    // Stream closed
  } finally {
    try { reader.releaseLock() } catch {}
    try { writer.close() } catch {}
  }
}
