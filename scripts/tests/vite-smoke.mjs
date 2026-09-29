import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from '../../client/node_modules/vite/dist/node/index.js'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../client')
process.chdir(root)
const server = await createServer({ root, server: { host: '127.0.0.1', port: 0, open: false } })
try {
  await server.listen()
  const { address, port } = server.httpServer.address()
  assert.equal(address, '127.0.0.1')
  const origin = `http://127.0.0.1:${port}`
  const page = await fetch(origin, { signal: AbortSignal.timeout(5000) })
  assert.equal(page.status, 200)
  assert.match(await page.text(), /src\/main.tsx/)
  const module = await fetch(`${origin}/src/main.tsx`, { signal: AbortSignal.timeout(10000) })
  assert.equal(module.status, 200)
  assert.match(await module.text(), /react/)
  await server.waitForRequestsIdle()
  console.log('Vite loopback HTML and TSX transformation smoke passed')
} finally {
  await server.close()
}
