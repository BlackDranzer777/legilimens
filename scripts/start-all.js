import { spawn } from 'child_process'
import path from 'path'
import { fileURLToPath } from 'url'
import fs from 'fs'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const serverDir = path.join(__dirname, '../server')
const certsDir = path.join(serverDir, 'certs')

if (!fs.existsSync(path.join(certsDir, 'cert.pem'))) {
  console.error('[legilimens] No certificate found. Run: npm run gen-cert')
  process.exit(1)
}

const colors = { proxy: '\x1b[32m', server: '\x1b[33m', reset: '\x1b[0m' }

function spawnProcess(name, color, cmd, args, cwd) {
  const proc = spawn(cmd, args, { cwd, stdio: 'pipe' })

  proc.stdout.on('data', (data) => {
    data.toString().trim().split('\n').forEach(line => {
      console.log(`${color}[${name}]${colors.reset} ${line}`)
    })
  })

  proc.stderr.on('data', (data) => {
    data.toString().trim().split('\n').forEach(line => {
      console.error(`${color}[${name}]${colors.reset} ${line}`)
    })
  })

  proc.on('exit', (code) => {
    console.log(`${color}[${name}]${colors.reset} exited with code ${code}`)
  })

  return proc
}

console.log('\x1b[36m[legilimens]\x1b[0m Starting all servers...')
console.log('\x1b[36m[legilimens]\x1b[0m Proxy:    https://localhost:4433  (WebTransport)')
console.log('\x1b[36m[legilimens]\x1b[0m Target:   https://localhost:4434  (Vulnerable server)')
console.log('\x1b[36m[legilimens]\x1b[0m WS Log:   ws://localhost:4435     (UI events)')
console.log('\x1b[36m[legilimens]\x1b[0m HTTP API: http://localhost:4436   (Cert hash endpoint)')
console.log('')

const proxy = spawnProcess('PROXY ', colors.proxy, 'node', ['proxy.js'], serverDir)
// Small delay so proxy initializes first
setTimeout(() => {
  spawnProcess('TARGET', colors.server, 'node', ['vulnerable-server.js'], serverDir)
}, 500)

process.on('SIGINT', () => {
  console.log('\n\x1b[36m[legilimens]\x1b[0m Shutting down...')
  proxy.kill()
  process.exit(0)
})
