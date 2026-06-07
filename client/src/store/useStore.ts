import { create } from 'zustand'

export interface TrafficEvent {
  id: string
  type: 'datagram' | 'stream' | 'connection' | 'attack'
  direction: 'incoming' | 'outgoing'
  payload: string
  rawSize: number
  timestamp: number
  latency: number
  streamId?: string
  flag?: 'suspicious' | 'normal' | 'tampered'
}

export interface StreamChunk {
  direction: 'sent' | 'received'
  payload: string
  timestamp: number
}

export interface StreamSession {
  id: string
  status: 'open' | 'closed'
  sentChunks: number
  receivedChunks: number
  openedAt: number
  closedAt?: number
  chunks: StreamChunk[]
}

export interface LatencyPoint {
  time: number
  datagram: number
  stream: number
}

interface LegilimensStore {
  isProxyActive: boolean
  connectionStatus: 'idle' | 'connecting' | 'active' | 'error'
  wsConnected: boolean
  events: TrafficEvent[]
  streams: Record<string, StreamSession>
  totalEvents: number
  totalDatagrams: number
  avgLatency: number
  suspiciousCount: number
  tamperedCount: number
  latencyHistory: LatencyPoint[]
  harvestedTokens: string[]

  addEvent: (event: TrafficEvent) => void
  setProxyActive: (active: boolean) => void
  setConnectionStatus: (status: 'idle' | 'connecting' | 'active' | 'error') => void
  clearLog: () => void
  addHarvestedToken: (token: string) => void
  connectWebSocket: () => void
  disconnectWebSocket: () => void
  startWebTransport: () => Promise<void>
  stopWebTransport: () => void
  floodAttack: () => Promise<void>
  payloadInjection: () => Promise<void>
  unauthorizedStream: () => Promise<void>
}

const MAX_EVENTS = 500
const MAX_LATENCY_HISTORY = 120

let ws: WebSocket | null = null
let wt: WebTransport | null = null
let datagramWriter: WritableStreamDefaultWriter<Uint8Array> | null = null
let pingInterval: ReturnType<typeof setInterval> | null = null

function detectSuspicious(payload: string) {
  const lower = payload.toLowerCase()
  return ['session_token', 'password', 'secret', 'api_key'].some((k) => lower.includes(k))
}

function extractTokens(payload: string): string[] {
  const matches = [...payload.matchAll(/"session_token"\s*:\s*"([^"]+)"/g)]
  return matches.map((m) => m[1])
}

function base64ToUint8Array(b64: string): Uint8Array {
  const bin = atob(b64)
  const arr = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i)
  return arr
}

export const useStore = create<LegilimensStore>((set, get) => ({
  isProxyActive: false,
  connectionStatus: 'idle',
  wsConnected: false,
  events: [],
  streams: {},
  totalEvents: 0,
  totalDatagrams: 0,
  avgLatency: 0,
  suspiciousCount: 0,
  tamperedCount: 0,
  latencyHistory: [],
  harvestedTokens: [],

  addEvent: (raw) => {
    const event: TrafficEvent = {
      ...raw,
      flag: raw.flag ?? (detectSuspicious(raw.payload) ? 'suspicious' : 'normal'),
    }

    set((state) => {
      const events = [...state.events, event]
      if (events.length > MAX_EVENTS) events.splice(0, events.length - MAX_EVENTS)

      // Update stream sessions
      let streams = { ...state.streams }
      if (event.streamId) {
        const sid = event.streamId
        if (event.payload.includes('opened') && !streams[sid]) {
          streams[sid] = {
            id: sid,
            status: 'open',
            sentChunks: 0,
            receivedChunks: 0,
            openedAt: event.timestamp,
            chunks: [],
          }
        } else if (streams[sid]) {
          const s = { ...streams[sid] }
          if (event.direction === 'incoming') s.sentChunks++
          else s.receivedChunks++
          s.chunks = [
            ...s.chunks,
            {
              direction: event.direction === 'incoming' ? 'sent' : 'received',
              payload: event.payload,
              timestamp: event.timestamp,
            },
          ]
          streams[sid] = s
        }
      }

      // Rolling latency history (one point per second)
      const now = Date.now()
      const history = [...state.latencyHistory]
      const last = history[history.length - 1]
      if (!last || now - last.time > 1000) {
        history.push({
          time: now,
          datagram: event.type === 'datagram' ? event.latency : 0,
          stream: event.type === 'stream' ? event.latency : 0,
        })
        if (history.length > MAX_LATENCY_HISTORY) history.shift()
      } else {
        if (event.type === 'datagram') last.datagram = event.latency
        if (event.type === 'stream') last.stream = event.latency
      }

      // Compute rolling avg latency
      const recent = events.slice(-50)
      const avgLatency = recent.length
        ? Math.round(recent.reduce((s, e) => s + e.latency, 0) / recent.length)
        : 0

      // Extract tokens from heartbeats
      const newTokens = extractTokens(event.payload)
      const harvestedTokens = newTokens.length
        ? [...new Set([...state.harvestedTokens, ...newTokens])]
        : state.harvestedTokens

      return {
        events,
        streams,
        latencyHistory: history,
        totalEvents: state.totalEvents + 1,
        totalDatagrams: event.type === 'datagram' ? state.totalDatagrams + 1 : state.totalDatagrams,
        suspiciousCount: event.flag === 'suspicious' ? state.suspiciousCount + 1 : state.suspiciousCount,
        tamperedCount: event.flag === 'tampered' ? state.tamperedCount + 1 : state.tamperedCount,
        avgLatency,
        harvestedTokens,
      }
    })
  },

  setProxyActive: (active) => set({ isProxyActive: active }),
  setConnectionStatus: (status) => set({ connectionStatus: status }),

  clearLog: () =>
    set({
      events: [],
      streams: {},
      totalEvents: 0,
      totalDatagrams: 0,
      avgLatency: 0,
      suspiciousCount: 0,
      tamperedCount: 0,
      latencyHistory: [],
      harvestedTokens: [],
    }),

  addHarvestedToken: (token) =>
    set((s) => ({ harvestedTokens: [...new Set([...s.harvestedTokens, token])] })),

  connectWebSocket: () => {
    if (ws && ws.readyState < 2) return

    ws = new WebSocket('ws://localhost:4435')

    ws.onopen = () => {
      set({ wsConnected: true })
    }

    ws.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data) as TrafficEvent
        if (event.type && event.timestamp) get().addEvent(event)
      } catch {
        // ignore malformed messages
      }
    }

    ws.onclose = () => {
      set({ wsConnected: false })
      ws = null
      setTimeout(() => get().connectWebSocket(), 2500)
    }

    ws.onerror = () => ws?.close()
  },

  disconnectWebSocket: () => {
    ws?.close()
    ws = null
    set({ wsConnected: false })
  },

  startWebTransport: async () => {
    if (!('WebTransport' in window)) {
      set({ connectionStatus: 'error' })
      alert(
        'WebTransport is not supported.\nPlease use Chrome/Edge launched with:\n--ignore-certificate-errors-spki-list=<hash>'
      )
      return
    }

    set({ connectionStatus: 'connecting' })

    let certHash: string
    try {
      const res = await fetch('http://localhost:4436/cert-hash')
      const data = await res.json()
      certHash = data.hash
    } catch {
      set({ connectionStatus: 'error' })
      get().addEvent({
        id: crypto.randomUUID(),
        type: 'connection',
        direction: 'outgoing',
        payload: 'Failed to fetch cert hash from proxy. Is the proxy running?',
        rawSize: 0,
        timestamp: Date.now(),
        latency: 0,
        flag: 'suspicious',
      })
      return
    }

    const hashBytes = base64ToUint8Array(certHash)

    try {
      // Use 127.0.0.1, NOT localhost: Chromium resolves "localhost" to IPv6 ::1 first,
      // but the proxy's QUIC socket only listens on IPv4 — so a localhost WebTransport
      // URL fails with ERR_CONNECTION_REFUSED. (TCP endpoints like the WS log and the
      // cert-hash fetch work over localhost because TCP falls back to IPv4; QUIC/UDP
      // does not.) Connecting to the IPv4 literal avoids the resolution entirely.
      wt = new WebTransport('https://127.0.0.1:4433/', {
        serverCertificateHashes: [{ algorithm: 'sha-256', value: hashBytes }],
      })
      await wt.ready

      set({ isProxyActive: true, connectionStatus: 'active' })

      datagramWriter = wt.datagrams.writable.getWriter()

      // Send ping datagrams every 500ms
      pingInterval = setInterval(async () => {
        if (!datagramWriter) return
        try {
          const msg = JSON.stringify({ action: 'ping', time: Date.now() })
          await datagramWriter.write(new TextEncoder().encode(msg))
        } catch {
          if (pingInterval) clearInterval(pingInterval)
        }
      }, 500)

      // Read incoming datagrams (proxy echoes them back — already logged server-side)
      readIncomingDatagrams()

      wt.closed.catch(() => {}).finally(() => {
        get().stopWebTransport()
      })
    } catch (err) {
      set({ connectionStatus: 'error', isProxyActive: false })
      get().addEvent({
        id: crypto.randomUUID(),
        type: 'connection',
        direction: 'outgoing',
        payload: `WebTransport connection failed: ${err instanceof Error ? err.message : String(err)}`,
        rawSize: 0,
        timestamp: Date.now(),
        latency: 0,
        flag: 'suspicious',
      })
    }
  },

  stopWebTransport: () => {
    if (pingInterval) { clearInterval(pingInterval); pingInterval = null }
    try { datagramWriter?.releaseLock() } catch {}
    datagramWriter = null
    try { wt?.close() } catch {}
    wt = null
    set({ isProxyActive: false, connectionStatus: 'idle' })
  },

  floodAttack: async () => {
    if (!wt || !datagramWriter) return
    for (let i = 0; i < 1000; i++) {
      try {
        const msg = new TextEncoder().encode(JSON.stringify({ action: 'flood', seq: i }))
        await datagramWriter.write(msg)
      } catch {
        break
      }
    }
  },

  payloadInjection: async () => {
    if (!wt || !datagramWriter) return
    const malicious = JSON.stringify({
      action: '__proto__',
      polluted: true,
      xss: '<script>alert("legilimens")</script>',
      sql: "' OR 1=1; DROP TABLE users; --",
    })
    await datagramWriter.write(new TextEncoder().encode(malicious))
  },

  unauthorizedStream: async () => {
    if (!wt) return
    const stream = await wt.createBidirectionalStream()
    const writer = stream.writable.getWriter()
    await writer.write(
      new TextEncoder().encode(JSON.stringify({ action: 'unauthorized', auth: null }))
    )
    writer.releaseLock()
  },
}))

async function readIncomingDatagrams() {
  if (!wt) return
  const reader = wt.datagrams.readable.getReader()
  try {
    while (true) {
      const { done } = await reader.read()
      if (done) break
      // Events are logged server-side and broadcast via WebSocket — no need to process here
    }
  } catch {
    // Connection closed
  } finally {
    try { reader.releaseLock() } catch {}
  }
}
