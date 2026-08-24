// scripts/start-vulns.js — start every vulnerable practice app together.
//
// Auto-discovers each vuln_apps/<app>/server.py and launches it with a labelled log stream,
// so new apps (05, 06, ...) are picked up automatically. Ctrl+C stops them all.
//
//   npm run vulns             # start every vuln app
//   npm run vulns -- 03       # only apps whose folder name contains "03"
//
// These are DELIBERATELY INSECURE targets — for local testing through Legilimens only.

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
const vulnDir = path.join(root, 'vuln_apps')
const filter = process.argv[2] // optional folder-name filter

const C = { reset: '\x1b[0m', cyan: '\x1b[36m', green: '\x1b[32m', red: '\x1b[31m', yellow: '\x1b[33m' }
const APP_COLORS = ['\x1b[34m', '\x1b[35m', '\x1b[33m', '\x1b[36m', '\x1b[32m', '\x1b[31m']
const tag = (name, color, line) => console.log(`${color}[${name}]${C.reset} ${line}`)

// --- preflight ---
if (!fs.existsSync(venvPy)) {
  tag('vulns', C.red, `venv Python not found at ${venvPy}`)
  tag('vulns', C.red, 'create it first:  python -m venv .venv   then   npm run install-py')
  process.exit(1)
}
if (!fs.existsSync(vulnDir)) {
  tag('vulns', C.red, 'no vuln_apps/ directory found')
  process.exit(1)
}

// the servers load the shared cert on startup — make one if it's missing
if (!fs.existsSync(path.join(root, 'python', 'certs', 'cert.pem'))) {
  tag('vulns', C.cyan, 'no certificate found — generating one...')
  spawnSync(venvPy, [path.join(root, 'python', 'certs.py')], { cwd: root, stdio: ['ignore', 'ignore', 'inherit'] })
}

// --- discover apps ---
let apps = fs.readdirSync(vulnDir, { withFileTypes: true })
  .filter((d) => d.isDirectory() && fs.existsSync(path.join(vulnDir, d.name, 'server.py')))
  .map((d) => d.name)
  .sort()
if (filter) apps = apps.filter((a) => a.includes(filter))
if (apps.length === 0) {
  tag('vulns', C.red, filter ? `no vuln app matches "${filter}"` : 'no vuln apps found')
  process.exit(1)
}

// --- summary (port parsed from each server.py) ---
tag('vulns', C.green, `starting ${apps.length} app(s):`)
for (const a of apps) {
  const src = fs.readFileSync(path.join(vulnDir, a, 'server.py'), 'utf8')
  const m = src.match(/^PORT\s*=\s*(\d+)/m)
  console.log(`   ${a}${m ? '  ->  :' + m[1] : ''}`)
}
console.log('')

// --- launch each, streaming labelled output ---
const children = []
function stream(name, color, proc) {
  const pipe = (s) => {
    let buf = ''
    s.on('data', (d) => {
      buf += d.toString()
      let i
      while ((i = buf.indexOf('\n')) >= 0) { tag(name, color, buf.slice(0, i).replace(/\r$/, '')); buf = buf.slice(i + 1) }
    })
  }
  if (proc.stdout) pipe(proc.stdout)
  if (proc.stderr) pipe(proc.stderr)
  proc.on('exit', (code) => tag(name, color, `exited (code ${code})`))
  children.push(proc)
}
apps.forEach((a, i) => {
  stream(a, APP_COLORS[i % APP_COLORS.length], spawn(venvPy, [path.join(vulnDir, a, 'server.py')], { cwd: root }))
})

tag('vulns', C.green, 'up — point Legilimens at whichever port you want to test. Ctrl+C stops them all.')

// --- clean shutdown ---
let down = false
function shutdown() {
  if (down) return
  down = true
  tag('vulns', C.yellow, 'shutting down...')
  for (const c of children) { try { c.kill() } catch { /* ignore */ } }
  setTimeout(() => process.exit(0), 300)
}
process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)
