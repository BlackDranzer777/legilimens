import { WebSocketServer } from 'ws'

const wss = new WebSocketServer({ port: 4435 })
const clients = new Set()

wss.on('connection', (ws) => {
  clients.add(ws)
  console.log(JSON.stringify({ level: 'info', msg: 'UI client connected', clientCount: clients.size }))

  ws.on('close', () => {
    clients.delete(ws)
  })

  ws.on('error', () => {
    clients.delete(ws)
  })
})

wss.on('error', (err) => {
  console.error(JSON.stringify({ level: 'error', msg: 'WebSocket server error', error: err.message }))
})

export function broadcast(event) {
  const message = JSON.stringify(event)
  for (const client of clients) {
    if (client.readyState === 1) {
      client.send(message)
    }
  }
}

export function logInfo(msg, data = {}) {
  const entry = { level: 'info', msg, timestamp: Date.now(), ...data }
  console.log(JSON.stringify(entry))
}

export function logError(msg, err) {
  console.error(JSON.stringify({ level: 'error', msg, error: err?.message, timestamp: Date.now() }))
}

export { wss }
