/**
 * A plain WebTransport echo server — a "different app" to point Legilimens at,
 * so you can prove the proxy inspects/relays an upstream other than the bundled
 * vulnerable server. Unlike vulnerable-server.js it sends NO heartbeats and leaks
 * NO tokens; it simply echoes whatever you send. So when the proxy is repointed
 * here, the heartbeat spam stops and you see your own datagrams come back prefixed
 * with "ECHO:" — visible proof the target changed.
 *
 *   Run:  node server/echo-server.js        (defaults to port 4455)
 *         PORT=4456 node server/echo-server.js
 *
 * It reuses the same cert as the rest of the project, so the cert hash already
 * pre-filled in the UI's UPSTREAM TARGET bar works as-is.
 */
import { Http3Server, quicheLoaded } from '@fails-components/webtransport'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const certsDir = path.join(__dirname, 'certs')
const PORT = Number(process.env.PORT) || 4455

if (!fs.existsSync(path.join(certsDir, 'cert.pem'))) {
  console.error(JSON.stringify({ level: 'error', msg: 'No certificate. Run: npm run gen-cert' }))
  process.exit(1)
}

const certPem = fs.readFileSync(path.join(certsDir, 'cert.pem'), 'utf8')
const keyPem = fs.readFileSync(path.join(certsDir, 'key.pem'), 'utf8')

await quicheLoaded

const server = new Http3Server({
  port: PORT,
  host: '0.0.0.0',
  secret: 'echo-server',
  cert: certPem,
  privKey: keyPem,
})

for (const urlPath of ['/', '/stream']) {
  acceptSessions(urlPath).catch((err) =>
    console.error(JSON.stringify({ level: 'error', msg: 'session error', error: err?.message }))
  )
}

server.startServer()
console.log(JSON.stringify({ level: 'info', msg: 'Echo server started', port: PORT }))
server.ready
  .then(() => console.log(JSON.stringify({ level: 'info', msg: 'Echo server ready and bound', port: PORT })))
  .catch((err) => console.error(JSON.stringify({ level: 'error', msg: 'ready error', error: err?.message })))

async function acceptSessions(urlPath) {
  const reader = server.sessionStream(urlPath).getReader()
  while (true) {
    const { done, value: session } = await reader.read()
    if (done) break
    console.log(JSON.stringify({ level: 'info', msg: 'Session opened', path: urlPath }))
    echoDatagrams(session)
    echoStreams(session)
  }
}

async function echoDatagrams(session) {
  const reader = session.datagrams.readable.getReader()
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      const text = new TextDecoder().decode(value)
      const reply = `ECHO:${text}`
      try {
        const writer = session.datagrams.writable.getWriter()
        await writer.write(new TextEncoder().encode(reply))
        writer.releaseLock()
      } catch {
        // peer gone
      }
    }
  } catch {
    // session closed
  } finally {
    try { reader.releaseLock() } catch {}
  }
}

async function echoStreams(session) {
  const reader = session.incomingBidirectionalStreams.getReader()
  try {
    while (true) {
      const { done, value: stream } = await reader.read()
      if (done) break
      pipeEcho(stream)
    }
  } catch {
    // session closed
  } finally {
    try { reader.releaseLock() } catch {}
  }
}

async function pipeEcho(stream) {
  const reader = stream.readable.getReader()
  const writer = stream.writable.getWriter()
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      const text = new TextDecoder().decode(value)
      await writer.write(new TextEncoder().encode(`ECHO:${text}`))
    }
  } catch {
    // stream closed
  } finally {
    try { reader.releaseLock() } catch {}
    try { await writer.close() } catch {}
  }
}
