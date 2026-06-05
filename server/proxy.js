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

// Compute SPKI hash for serverCertificateHashes and the HTTP /cert-hash endpoint
const x509 = new X509Certificate(certPem)
const spkiDer = x509.publicKey.export({ type: 'spki', format: 'der' })
const certHashBuf = createHash('sha256').update(spkiDer).digest() // Node Buffer
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
    serverSession = new NodeWebTransport(`https://localhost:4434${urlPath}`, {
      serverCertificateHashes: [
        {
          algorithm: 'sha-256',
          // Pass the Buffer directly — Buffer extends Uint8Array (valid BufferSource)
          value: certHashBuf,
        },
      ],
    })
    await serverSession.ready
    logInfo('Connected to vulnerable server', { sessionId })
  } catch (err) {
    logError('Failed to connect to vulnerable server', err)
    broadcast({
      id: uuid(),
      type: 'connection',
      direction: 'outgoing',
      payload: `Failed to reach vulnerable server: ${err.message}`,
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
