// scripts/start-all.js — one-command Legilimens dev launcher.
//
// Regenerates the certificate, then starts the Python backend and the Vite frontend
// together, streaming both logs into this one terminal. Ctrl+C stops everything.
//
//   npm run dev
//
// Uses the project's own venv (.venv) so the backend gets aioquic / cryptography / fastapi.

import { spawn, spawnSync } from 'child_process'
import path from 'path'
import fs from 'fs'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, '..')
const isWin = process.platform === 'win32'
const venvPy = isWin
  ? path.join(root, '.venv', 'Scripts', 'python.exe')
  : path.join(root, '.venv', 'bin', 'python')
const UI_PORT = '5180' // avoids the common 5173 clash with other Vite projects

const C = {
  reset: '\x1b[0m', cyan: '\x1b[36m', green: '\x1b[32m',
  blue: '\x1b[34m', magenta: '\x1b[35m', red: '\x1b[31m', yellow: '\x1b[33m',
}
const tag = (name, color, line) => console.log(`${color}[${name}]${C.reset} ${line}`)

// --- preflight: the venv is where the backend's Python deps live ---
if (!fs.existsSync(venvPy)) {
  tag('legilimens', C.red, `venv Python not found at ${venvPy}`)
  tag('legilimens', C.red, 'create it first:  python -m venv .venv   then   npm run install-py')
  process.exit(1)
}

// --- 1) certificate (WebTransport certs expire in <=14 days, so regenerate every start) ---
tag('legilimens', C.cyan, 'generating certificate...')
const cert = spawnSync(venvPy, [path.join(root, 'python', 'certs.py')], {
  cwd: root, stdio: ['ignore', 'ignore', 'inherit'],
})
if (cert.status !== 0) {
  tag('legilimens', C.red, 'certificate generation failed (see error above)')
  process.exit(1)
}
tag('legilimens', C.green, 'certificate ready.')

// --- 2) + 3) backend + frontend, each with a labelled log prefix ---
const children = []
function stream(name, color, proc) {
  const pipe = (s) => {
    let buf = ''
    s.on('data', (d) => {
      buf += d.toString()
      let i
      while ((i = buf.indexOf('\n')) >= 0) {
        tag(name, color, buf.slice(0, i).replace(/\r$/, ''))
        buf = buf.slice(i + 1)
      }
    })
  }
  if (proc.stdout) pipe(proc.stdout)
  if (proc.stderr) pipe(proc.stderr)
  proc.on('exit', (code) => tag(name, color, `exited (code ${code})`))
  children.push(proc)
}

tag('legilimens', C.cyan, 'starting backend (proxy :4433, target :4434, ws :4435, api :4436)...')
stream('backend', C.blue, spawn(venvPy, [path.join(root, 'python', 'backend.py')], { cwd: root }))

tag('legilimens', C.cyan, `starting UI on http://localhost:${UI_PORT} ...`)
stream('ui', C.magenta, spawn('npm', ['--prefix', 'client', 'run', 'dev', '--', '--port', UI_PORT], {
  cwd: root, shell: isWin,
}))

console.log('')
tag('legilimens', C.green, `open the dashboard at  http://localhost:${UI_PORT}   —   Ctrl+C stops everything`)
console.log('')

// --- clean shutdown: kill children on Ctrl+C ---
let shuttingDown = false
function shutdown() {
  if (shuttingDown) return
  shuttingDown = true
  tag('legilimens', C.yellow, 'shutting down...')
  for (const c of children) { try { c.kill() } catch { /* ignore */ } }
  setTimeout(() => process.exit(0), 300)
}
process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)
