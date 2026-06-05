/**
 * Deliberately vulnerable WebTransport server for security testing.
 * This server has 4 intentional security flaws. Do NOT expose to untrusted networks.
 */

import { Http3Server, quicheLoaded } from '@fails-components/webtransport'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const certsDir = path.join(__dirname, 'certs')

if (!fs.existsSync(path.join(certsDir, 'cert.pem'))) {
  console.error(JSON.stringify({ level: 'error', msg: 'No certificate. Run: npm run gen-cert' }))
  process.exit(1)
}

// Read as strings — the webtransport package expects PEM strings
const certPem = fs.readFileSync(path.join(certsDir, 'cert.pem'), 'utf8')
const keyPem = fs.readFileSync(path.join(certsDir, 'key.pem'), 'utf8')

// Wait for the quiche transport to load before creating any server
await quicheLoaded

// VULNERABILITY 1: No authentication — every connection is accepted without
// any token, API key, or credential check.
const server = new Http3Server({
  port: 4434,
  host: '0.0.0.0',
  secret: 'vulnerable-server-no-auth',
  cert: certPem,
  privKey: keyPem,
})

// Register session streams BEFORE startServer() so paths are pending-registered
// and get added to the transport as soon as it binds
for (const urlPath of ['/', '/stream']) {
  acceptSessions(urlPath).catch(console.error)
}

server.startServer()
console.log(JSON.stringify({ level: 'info', msg: 'Vulnerable server started', port: 4434 }))

server.ready
  .then(() => console.log(JSON.stringify({ level: 'info', msg: 'Vulnerable server ready and bound', port: 4434 })))
  .catch((err) => console.error(JSON.stringify({ level: 'error', msg: 'Server ready error', error: err?.message })))

async function acceptSessions(urlPath) {
  const sessionStream = server.sessionStream(urlPath)
  const reader = sessionStream.getReader()

  while (true) {
    const { done, value: session } = await reader.read()
    if (done) break
    handleSession(session)
  }
}

function handleSession(session) {
  // VULNERABILITY 1: No credentials checked — anonymous connections silently accepted.
  console.log(JSON.stringify({ level: 'warn', msg: 'Anonymous connection accepted', vuln: 'NO_AUTH' }))

  // VULNERABILITY 2: Sensitive data in datagrams — session token and user ID
  // broadcast in plaintext every 2 seconds to all connected clients.
  const heartbeatInterval = setInterval(async () => {
    const heartbeat = JSON.stringify({
      type: 'heartbeat',
      session_token: 'SECRET_TOKEN_abc123',
      user_id: 'admin',
      timestamp: Date.now(),
    })
    try {
      const writer = session.datagrams.writable.getWriter()
      await writer.write(new TextEncoder().encode(heartbeat))
      writer.releaseLock()
    } catch {
      clearInterval(heartbeatInterval)
    }
  }, 2000)

  // VULNERABILITY 3: No payload validation — all datagrams echoed back as-is
  receiveDatagrams(session)

  // VULNERABILITY 4: No rate limiting — all streams accepted unconditionally
  receiveStreams(session)

  session.closed.catch(() => {}).finally(() => clearInterval(heartbeatInterval))
}

async function receiveDatagrams(session) {
  const reader = session.datagrams.readable.getReader()

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      const payload = new TextDecoder().decode(value)

      // VULNERABILITY 3: No size check, no JSON validation, no injection filtering.
      // Malformed JSON, XSS strings, SQL injection — all accepted and echoed back.
      console.log(JSON.stringify({
        level: 'warn',
        msg: 'Echoing unvalidated datagram',
        vuln: 'NO_VALIDATION',
        preview: payload.slice(0, 80),
      }))

      const echo = JSON.stringify({ type: 'echo', original: payload, server: 'vulnerable-v1' })

      try {
        const writer = session.datagrams.writable.getWriter()
        await writer.write(new TextEncoder().encode(echo))
        writer.releaseLock()
      } catch {
        // VULNERABILITY 4: errors silently ignored — no circuit breaker.
      }
    }
  } catch {
    // Session closed
  } finally {
    try { reader.releaseLock() } catch {}
  }
}

async function receiveStreams(session) {
  const reader = session.incomingBidirectionalStreams.getReader()

  try {
    while (true) {
      const { done, value: stream } = await reader.read()
      if (done) break
      // VULNERABILITY 1 + 4: no auth check, no rate limit
      echoStream(stream)
    }
  } catch {
    // Session closed
  } finally {
    try { reader.releaseLock() } catch {}
  }
}

async function echoStream(stream) {
  const reader = stream.readable.getReader()
  const writer = stream.writable.getWriter()

  console.log(JSON.stringify({ level: 'warn', msg: 'Unauthenticated stream accepted', vuln: 'NO_AUTH' }))

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      const payload = new TextDecoder().decode(value)
      // VULNERABILITY 3: echo back without any sanitization
      const echo = JSON.stringify({ type: 'stream_echo', original: payload })
      await writer.write(new TextEncoder().encode(echo))
    }
  } catch {
    // Stream closed
  } finally {
    try { reader.releaseLock() } catch {}
    try { await writer.close() } catch {}
  }
}
