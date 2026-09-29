// scripts/start-all.js — one-command Legilimens dev launcher.
//
//   npm run dev
//
// Starts the Python backend (from source in .venv) and the Vite frontend together,
// streaming both logs into this one terminal. The backend owns its own certificate
// (no pre-generation here). The launcher waits for an AUTHENTICATED readiness signal
// tied to the exact instance it spawned before opening the UI, and on Ctrl+C asks the
// backend to stop gracefully before any forced cleanup. Ctrl+C stops everything.

import { spawn } from 'child_process'
import processLifecycle from '../desktop/process-lifecycle.cjs'
import path from 'path'
import fs from 'fs'
import http from 'http'
import { fileURLToPath } from 'url'
import { randomBytes } from 'crypto'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, '..')
const isWin = process.platform === 'win32'
const venvPy = isWin
  ? path.join(root, '.venv', 'Scripts', 'python.exe')
  : path.join(root, '.venv', 'bin', 'python')
const UI_PORT = '5180'
const API_PORT = 4436
const controlToken = randomBytes(32).toString('base64url')

const READY_TIMEOUT_MS = 30000
const SHUTDOWN_GRACE_MS = 8000
const MAX_LINE_BYTES = 64 * 1024   // bound an unterminated log line
const { forceKillTree, stopChild } = processLifecycle
const EXIT_RESTART = 75
const MAX_AUTO_RESTARTS = 3

const C = {
  reset: '\x1b[0m', cyan: '\x1b[36m', green: '\x1b[32m',
  blue: '\x1b[34m', magenta: '\x1b[35m', red: '\x1b[31m', yellow: '\x1b[33m',
}
const tag = (name, color, line) => console.log(`${color}[${name}]${C.reset} ${line}`)

if (!fs.existsSync(venvPy)) {
  tag('legilimens', C.red, `venv Python not found at ${venvPy}`)
  tag('legilimens', C.red, 'create it first:  python -m venv .venv   then   npm run install-py')
  process.exit(1)
}

// --- line-buffered stdio tagging with a bounded incomplete-line buffer ---
function streamLines(name, color, proc, onLine) {
  const pipe = (s) => {
    let buf = ''
    s.on('data', (d) => {
      buf += d.toString()
      let i
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).replace(/\r$/, '')
        buf = buf.slice(i + 1)
        tag(name, color, line)
        if (onLine) onLine(line)
      }
      if (buf.length > MAX_LINE_BYTES) {   // flush a runaway unterminated line
        tag(name, color, buf)
        if (onLine) onLine(buf)
        buf = ''
      }
    })
  }
  if (proc.stdout) pipe(proc.stdout)
  if (proc.stderr) pipe(proc.stderr)
}

// --- authenticated readiness check tied to the spawned instance ---
function checkHealth(expectedInstance) {
  return new Promise((resolve, reject) => {
    const req = http.get(`http://127.0.0.1:${API_PORT}/health`,
      { headers: { Authorization: `Bearer ${controlToken}` } }, (res) => {
        let body = ''
        res.on('data', (c) => { body += c; if (body.length > 4096) req.destroy(new Error('oversized health')) })
        res.on('end', () => {
          try {
            const h = JSON.parse(body)
            if (res.statusCode === 200 && h.service === 'legilimens' && h.instanceId === expectedInstance) resolve()
            else reject(new Error('unexpected or unauthenticated backend'))
          } catch (e) { reject(e) }
        })
      })
    req.setTimeout(2000, () => req.destroy(new Error('health timeout')))
    req.on('error', reject)
  })
}

function postShutdown() {
  return new Promise((resolve) => {
    const req = http.request(`http://127.0.0.1:${API_PORT}/shutdown`,
      { method: 'POST', headers: { Authorization: `Bearer ${controlToken}`, 'Content-Length': 0 } },
      (res) => { res.resume(); res.on('end', resolve) })
    req.setTimeout(2000, () => { req.destroy(); resolve() })
    req.on('error', resolve)
    req.end()
  })
}

// --- backend (source), owns its certificate; watches this launcher's liveness ---
let backend = null
let ui = null
let shuttingDown = false
let readyTimer = null
let autoRestarts = 0

function startBackend() {
  if (shuttingDown) return
  tag('legilimens', C.cyan, 'starting backend (proxy :4433, target :4434, ws :4435, api :4436)...')
  const child = spawn(venvPy, [path.join(root, 'python', 'backend.py')], {
    cwd: root, windowsHide: true,
    env: { ...process.env, LEGILIMENS_CONTROL_TOKEN: controlToken, LEGILIMENS_PARENT_PID: String(process.pid) },
  })
  backend = child
  let readyInstance = null
  let ready = false

  child.on('error', (err) => {
    tag('legilimens', C.red, `failed to start backend: ${err.message}`)
    shutdown(1)
  })

  streamLines('backend', C.blue, child, (line) => {
    const m = line.match(/^READY instanceId=([0-9a-f]+)$/)
    if (m && !readyInstance) {
      readyInstance = m[1]
      checkHealth(readyInstance)
        .then(() => {
          if (backend !== child || child.exitCode !== null || child.signalCode !== null || shuttingDown) return
          ready = true
          clearTimeout(readyTimer)
          onBackendReady()
        })
        .catch((e) => { tag('legilimens', C.red, `readiness check failed: ${e.message}`); shutdown(1) })
    }
  })

  readyTimer = setTimeout(() => {
    if (!ready) { tag('legilimens', C.red, 'backend did not become ready in time'); shutdown(1) }
  }, READY_TIMEOUT_MS)

  child.on('exit', (code) => {
    tag('backend', C.blue, `exited (code ${code})`)
    clearTimeout(readyTimer)
    if (shuttingDown) return
    if (ready && code === EXIT_RESTART && autoRestarts < MAX_AUTO_RESTARTS) {
      autoRestarts += 1
      tag('legilimens', C.yellow, `certificate rotation: restarting backend (${autoRestarts}/${MAX_AUTO_RESTARTS})`)
      startBackend()
      return
    }
    if (!ready) { shutdown(code === 0 ? 1 : (code ?? 1)); return }   // early exit = startup failure
    tag('legilimens', code ? C.red : C.yellow, code ? 'backend crashed; stopping' : 'backend stopped; shutting down')
    shutdown(code ?? 0)
  })
}

function onBackendReady() {
  if (shuttingDown) return
  tag('legilimens', C.green, 'backend ready.')
  if (ui) return  // Keep Vite and the authenticated browser session across rotation.
  tag('legilimens', C.cyan, `starting UI on http://localhost:${UI_PORT} ...`)
  ui = spawn('npm', ['--prefix', 'client', 'run', 'dev', '--', '--port', UI_PORT], { cwd: root, shell: isWin })
  ui.on('error', (err) => { tag('ui', C.magenta, `failed to start: ${err.message}`); shutdown(1) })
  ui.on('exit', (code) => {
    tag('ui', C.magenta, `exited (code ${code})`)
    if (!shuttingDown) { tag('legilimens', C.yellow, 'UI stopped; shutting down'); shutdown(code || 1) }
  })
  streamLines('ui', C.magenta, ui)
  console.log('')
  tag('legilimens', C.green, `private dashboard link: http://127.0.0.1:${UI_PORT}/#token=${controlToken}`)
  tag('legilimens', C.yellow, 'Keep this link private. Access changes on restart. Ctrl+C stops everything.')
  console.log('')
}

// --- graceful shutdown: ask the backend to stop, then bounded forced cleanup ---
async function shutdown(code) {
  if (shuttingDown) return
  shuttingDown = true
  clearTimeout(readyTimer)
  tag('legilimens', C.yellow, 'shutting down...')

  try {
    if (ui) await forceKillTree(ui)
  } catch (err) {
    tag('legilimens', C.red, `UI cleanup failed: ${err.message}`)
    code = 1
  }
  try {
    await stopChild(backend, postShutdown, { grace: SHUTDOWN_GRACE_MS })
  } catch (err) {
    tag('legilimens', C.red, `backend cleanup failed: ${err.message}`)
    code = 1
  }
  process.exit(code)
}

process.on('SIGINT', () => shutdown(0))
process.on('SIGTERM', () => shutdown(0))
startBackend()
