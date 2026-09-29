import { create } from 'zustand'
import { apiFetch, captureUrl, getControlToken, proxyUrl, requireAuthentication } from '../control'

export type WorkspaceView = 'traffic' | 'intercept' | 'repeater' | 'attacks' | 'streams' | 'settings'

export interface TrafficEvent {
  target?: string
  sessionId?: string
  payloadPreview?: string
  replayable?: boolean
  payloadEncoding?: 'utf8' | 'base64'
  id: string
  type: 'datagram' | 'stream' | 'connection' | 'attack'
  direction: 'incoming' | 'outgoing'
  payload: string
  rawSize: number
  timestamp: number
  latency: number
  streamId?: string
  flag?: 'suspicious' | 'normal' | 'tampered' | 'replay'
}

export interface RepeaterState {
  sessionId: string
  payload: string
  direction: 'incoming' | 'outgoing'
  messageType: 'datagram' | 'stream'
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
  omittedChunks?: number
}

export type AttackStatus = 'started' | 'running' | 'complete' | 'failed' | 'stopped'

export interface AttackState {
  attackId: string
  attackType: string
  status: AttackStatus
  progress?: { current: number; total: number; message: string }
  result?: Record<string, unknown>
  error?: string
  startedAt: number
  completedAt?: number
}

export interface InterceptItem {
  payloadEncoding?: 'utf8' | 'base64'
  interceptId: string
  timestamp: number
  direction: 'incoming' | 'outgoing'
  messageType: 'datagram' | 'stream'
  payload: string
  rawSize: number
  streamId?: string
}

export interface ManualInterceptConfig {
  enabled: boolean
  directions: string[]
  types: string[]
  timeoutMs: number
}

interface LegilimensStore {
  tamperEnabled: boolean
  setTamperEnabled: (enabled: boolean) => void
  activeView: WorkspaceView
  setActiveView: (view: WorkspaceView) => void
  isProxyActive: boolean
  connectionStatus: 'idle' | 'connecting' | 'active' | 'error'
  wsConnected: boolean
  events: TrafficEvent[]
  observationStartedAt: number
  streams: Record<string, StreamSession>
  totalEvents: number
  evictedEvents: number
  evictedStreams: number
  resourceWarning: string
  totalDatagrams: number
  suspiciousCount: number
  tamperedCount: number
  harvestedTokens: string[]
  runningAttacks: Record<string, AttackState>
  completedAttacks: AttackState[]
  manualIntercept: ManualInterceptConfig
  manualInterceptError: string
  pendingIntercepts: InterceptItem[]
  repeater: RepeaterState
  repeaterStatus: string

  addEvent: (event: TrafficEvent) => void
  setProxyActive: (active: boolean) => void
  setConnectionStatus: (status: 'idle' | 'connecting' | 'active' | 'error') => void
  clearLog: () => void
  addHarvestedToken: (token: string) => void
  connectWebSocket: () => void
  disconnectWebSocket: () => void
  startWebTransport: () => Promise<void>
  stopWebTransport: () => void
  disconnectProxy: () => void
  floodAttack: () => Promise<void>
  payloadInjection: () => Promise<void>
  unauthorizedStream: () => Promise<void>
  launchAttack: (type: string, params: Record<string, unknown>, target?: string) => Promise<string | null>
  stopAttack: (attackId: string) => Promise<void>
  handleAttackEvent: (event: AttackState & { progress?: AttackState['progress'] }) => void
  handleInterceptEvent: (event: Record<string, unknown>) => void
  fetchInterceptConfig: () => Promise<void>
  setManualIntercept: (partial: Partial<ManualInterceptConfig>) => Promise<void>
  fetchInterceptQueue: () => Promise<void>
  resolveIntercept: (interceptId: string, action: 'forward' | 'drop', payload?: string) => Promise<void>
  sendToRepeater: (event: TrafficEvent) => void
  setRepeater: (partial: Partial<RepeaterState>) => void
  replaySend: () => Promise<void>
}

const MAX_EVENTS = 500
const MAX_CAPTURE_BYTES = 8 * 1024 * 1024
const MAX_STREAMS = 64
const MAX_STREAM_CHUNKS = 128
const MAX_STREAM_BYTES = 128 * 1024
// UTF-16 storage estimates bound retained strings, not total browser heap usage.
const byteSizes = new WeakMap<object, number>()
const storedBytes = (value: object) => {
  let bytes = byteSizes.get(value)
  if (bytes === undefined) {
    bytes = JSON.stringify(value).length * 2
    byteSizes.set(value, bytes)
  }
  return bytes
}
const boundedTokens = (tokens: string[]) => [...new Set(tokens.filter((t) => t.length <= 4096))].slice(-100)

let ws: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let wantsWebSocket = false
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
  tamperEnabled: false,
  setTamperEnabled: (tamperEnabled) => set({ tamperEnabled }),
  activeView: 'traffic',
  setActiveView: (activeView) => set({ activeView }),
  isProxyActive: false,
  connectionStatus: 'idle',
  wsConnected: false,
  events: [],
  observationStartedAt: Date.now(),
  streams: {},
  totalEvents: 0,
  evictedEvents: 0,
  evictedStreams: 0,
  resourceWarning: '',
  totalDatagrams: 0,
  suspiciousCount: 0,
  tamperedCount: 0,
  harvestedTokens: [],
  runningAttacks: {},
  completedAttacks: [],
  manualInterceptError: '',
  manualIntercept: {
    enabled: false,
    directions: ['incoming', 'outgoing'],
    types: ['datagram', 'stream'],
    timeoutMs: 30000,
  },
  pendingIntercepts: [],
  repeater: { payload: '', direction: 'incoming', messageType: 'datagram', sessionId: '' },
  repeaterStatus: '',

  addEvent: (raw) => {
    const event: TrafficEvent = {
      ...raw,
      flag: raw.flag ?? (detectSuspicious(raw.payload) ? 'suspicious' : 'normal'),
    }

    set((state) => {
      const events = [...state.events, event]
      let bytes = events.reduce((sum, item) => sum + storedBytes(item), 0)
      let evictedEvents = state.evictedEvents
      while (events.length > MAX_EVENTS || bytes > MAX_CAPTURE_BYTES) {
        bytes -= storedBytes(events.shift()!)
        evictedEvents++
      }

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
          let chunkBytes = storedBytes(s.chunks)
          while (s.chunks.length > MAX_STREAM_CHUNKS || chunkBytes > MAX_STREAM_BYTES) {
            s.chunks.shift()
            s.omittedChunks = (s.omittedChunks ?? 0) + 1
            byteSizes.delete(s.chunks)
            chunkBytes = storedBytes(s.chunks)
          }
          streams[sid] = s
        }
      }

      let evictedStreams = state.evictedStreams
      while (Object.keys(streams).length > MAX_STREAMS) {
        delete streams[Object.keys(streams)[0]]
        evictedStreams++
      }

      // Extract tokens from heartbeats
      const newTokens = extractTokens(event.payload)
      const harvestedTokens = newTokens.length
        ? boundedTokens([...state.harvestedTokens, ...newTokens])
        : state.harvestedTokens

      return {
        events,
        streams,
        evictedEvents,
        evictedStreams,
        totalEvents: state.totalEvents + 1,
        totalDatagrams: event.type === 'datagram' ? state.totalDatagrams + 1 : state.totalDatagrams,
        suspiciousCount: event.flag === 'suspicious' ? state.suspiciousCount + 1 : state.suspiciousCount,
        tamperedCount: event.flag === 'tampered' ? state.tamperedCount + 1 : state.tamperedCount,
        harvestedTokens,
      }
    })
  },

  setProxyActive: (active) => set({ isProxyActive: active }),
  setConnectionStatus: (status) => set({ connectionStatus: status }),

  clearLog: () =>
    set({
      events: [],
      observationStartedAt: Date.now(),
      streams: {},
      totalEvents: 0,
      evictedEvents: 0,
      evictedStreams: 0,
      resourceWarning: '',
      totalDatagrams: 0,
      suspiciousCount: 0,
      tamperedCount: 0,
      harvestedTokens: [],
    }),

  addHarvestedToken: (token) =>
    set((s) => ({ harvestedTokens: boundedTokens([...s.harvestedTokens, token]) })),

  connectWebSocket: () => {
    wantsWebSocket = true
    if (ws && ws.readyState < 2) return
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }

    const socket = new WebSocket(captureUrl())
    ws = socket
    let epoch: string | null = null
    let sequence = 0
    let recovering = false
    let buffered: string[] = []
    let bufferedBytes = 0
    const recover = async () => {
      if (recovering) return
      recovering = true
      set({ wsConnected: false })
      try {
        const response = await apiFetch('/state')
        const snapshot = await response.json()
        if (ws !== socket) return
        if (snapshot.epoch !== epoch || !Number.isSafeInteger(snapshot.sequence)
            || !Array.isArray(snapshot.attacks) || !Array.isArray(snapshot.pendingIntercepts)) {
          throw new Error('Invalid recovery snapshot')
        }
        const runningAttacks: Record<string, AttackState> = {}
        const completedAttacks: AttackState[] = []
        for (const attack of snapshot.attacks as AttackState[]) {
          if (['stopped', 'complete', 'failed'].includes(attack.status)) completedAttacks.push(attack)
          else runningAttacks[attack.attackId] = attack
        }
        sequence = snapshot.sequence
        set({ runningAttacks, completedAttacks: completedAttacks.slice(0, 20),
          pendingIntercepts: snapshot.pendingIntercepts, manualIntercept: snapshot.manualIntercept,
          tamperEnabled: !!snapshot.tamperEnabled, isProxyActive: snapshot.captureMode === 'capturing',
          connectionStatus: snapshot.captureMode === 'capturing' ? 'active' : 'idle', wsConnected: true })
        const queued = buffered
        buffered = []
        bufferedBytes = 0
        recovering = false
        for (const data of queued) {
          const packet = JSON.parse(data)
          if (packet.sequence <= sequence && ['datagram', 'stream', 'connection'].includes(packet.event?.type)) {
            set({ resourceWarning: 'State recovered; traffic during the capture gap may be missing.' })
          }
          socket.onmessage?.({ data } as MessageEvent)
        }
      } catch {
        if (ws === socket) {
          set({ resourceWarning: 'State recovery failed. Capture is incomplete; reconnecting.' })
          socket.close()
        }
      }
    }

    socket.onopen = () => {
      socket.send(JSON.stringify({ type: 'authenticate', token: getControlToken() }))
    }

    socket.onmessage = (msg) => {
      if (ws !== socket) return
      try {
        const packet = JSON.parse(msg.data)
        if (packet.type === 'authenticated') {
          if (typeof packet.epoch === 'string') {
            epoch = packet.epoch
            void recover()
          } else set({ wsConnected: true })
          return
        }
        if (typeof packet.sequence === 'number') {
          if (packet.epoch !== epoch) {
            set({ resourceWarning: 'Backend changed. Capture is incomplete; reconnecting.' })
            socket.close()
            return
          }
          if (recovering) {
            if (buffered.length >= 128 || bufferedBytes + msg.data.length * 2 > 4 * 1024 * 1024) {
              set({ resourceWarning: 'Recovery buffer exceeded. Capture is incomplete; reconnecting.' })
              socket.close()
              return
            }
            buffered.push(msg.data)
            bufferedBytes += msg.data.length * 2
            return
          }
          if (packet.sequence <= sequence) return
          if (packet.sequence !== sequence + 1) {
            set({ resourceWarning: 'Capture sequence gap detected. Recovering current state; missed traffic is unavailable.' })
            void recover()
            return
          }
          sequence = packet.sequence
        }
        const event = packet.event ?? packet
        if (event.type === 'resource') {
          set({ resourceWarning: String(event.message).slice(0, 300) })
          if (epoch) void recover()
          return
        }
        if (event.type === 'authenticated') {
          set({ wsConnected: true })
          return
        }
        // Attack progress/terminal events share this WS channel — route them separately.
        if (event.type === 'attack') {
          get().handleAttackEvent(event)
          return
        }
        // Manual-intercept events drive the Intercept panel, not the main traffic log.
        // (The proxy emits a normal traffic event after forward/drop, so nothing is lost.)
        if (event.type === 'intercept') {
          get().handleInterceptEvent(event)
          return
        }
        if (event.type && event.timestamp) get().addEvent(event as TrafficEvent)
      } catch {
        // ignore malformed messages
      }
    }

    socket.onclose = (event) => {
      if (ws !== socket) return
      set({ wsConnected: false })
      if (epoch && wantsWebSocket) {
        set({ resourceWarning: 'Capture gap detected; missed traffic is unavailable.' })
      }
      if (event.code === 1013) {
        set({ resourceWarning: 'Capture capacity exceeded or subscriber too slow. Capture is incomplete.' })
      }
      ws = null
      if (event.code === 1008) {
        wantsWebSocket = false
        requireAuthentication()
      } else if (wantsWebSocket) {
        reconnectTimer = setTimeout(() => get().connectWebSocket(), 2500)
      }
    }

    socket.onerror = () => socket.close()
  },

  disconnectWebSocket: () => {
    wantsWebSocket = false
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
    const socket = ws
    ws = null
    socket?.close()
    set({ wsConnected: false, resourceWarning: get().resourceWarning || 'Capture subscription disconnected; traffic outside the subscription is unavailable.' })
  },

  // START = tell the proxy to begin capturing (reliable HTTP toggle), then best-effort
  // open our own WebTransport session so the Attack Simulator can inject traffic.
  // Capture works even if that attack channel fails — so START never hangs.
  startWebTransport: async () => {
    set({ connectionStatus: 'connecting' })

    // 1) Reliable: tell the proxy to start relaying + logging.
    try {
      await apiFetch('/intercept', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'start' }),
      })
    } catch {
      set({ connectionStatus: 'error' })
      get().addEvent({
        id: crypto.randomUUID(),
        type: 'connection',
        direction: 'outgoing',
        payload: 'Failed to reach proxy API on :4436. Is the proxy running?',
        rawSize: 0,
        timestamp: Date.now(),
        latency: 0,
        flag: 'suspicious',
      })
      return
    }

    // Capture is now live regardless of the attack channel below.
    set({ isProxyActive: true, connectionStatus: 'active' })

    // 2) Best-effort: open our own WebTransport session for the Attack Simulator.
    //    Time-boxed so a flaky handshake can never freeze the UI in "connecting".
    if (!('WebTransport' in window)) return
    try {
      const res = await apiFetch('/cert-hash')
      const { hash } = await res.json()
      const hashBytes = base64ToUint8Array(hash)

      // Use 127.0.0.1, NOT localhost: Chromium resolves "localhost" to IPv6 ::1 first,
      // but the proxy's QUIC socket only listens on IPv4.
      const candidate = new WebTransport(proxyUrl(), {
        serverCertificateHashes: [{ algorithm: 'sha-256', value: hashBytes as BufferSource }],
      })
      await Promise.race([
        candidate.ready,
        new Promise((_, rej) => setTimeout(() => rej(new Error('attack-channel timeout')), 8000)),
      ])

      wt = candidate
      datagramWriter = wt.datagrams.writable.getWriter()

      // Low-rate keepalive only — just enough to keep the session under the proxy's 30s
      // idle timeout. (It was 500ms, which flooded the inspector's own log and the
      // intercept queue with self-traffic.) The target's own messages keep it alive too.
      pingInterval = setInterval(async () => {
        if (!datagramWriter) return
        try {
          await datagramWriter.write(
            new TextEncoder().encode(JSON.stringify({ action: 'ping', time: Date.now() }))
          )
        } catch {
          if (pingInterval) clearInterval(pingInterval)
        }
      }, 10000)

      readIncomingDatagrams()

      wt.closed.catch(() => {}).finally(() => {
        if (pingInterval) { clearInterval(pingInterval); pingInterval = null }
        try { datagramWriter?.releaseLock() } catch {}
        datagramWriter = null
        wt = null
      })
    } catch {
      // Attack channel unavailable — capture still works; the attack buttons just
      // won't be able to inject. Not a fatal error.
      try { wt?.close() } catch {}
      wt = null
    }
  },

  // STOP = PAUSE: hold the wire. The proxy stops relaying + logging, but the target
  // stays connected, so resuming (START) continues instantly with no reconnect.
  stopWebTransport: () => {
    apiFetch('/intercept', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'pause' }),
    }).catch(() => {})

    if (pingInterval) { clearInterval(pingInterval); pingInterval = null }
    try { datagramWriter?.releaseLock() } catch {}
    datagramWriter = null
    try { wt?.close() } catch {}
    wt = null
    set({ isProxyActive: false, connectionStatus: 'idle' })
  },

  // DISCONNECT = hard cut (Option A): sever every live session at the proxy. The target
  // loses its connection entirely and must redial on its own. Use before switching to a
  // different app via the UPSTREAM TARGET bar.
  disconnectProxy: () => {
    apiFetch('/intercept', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'disconnect' }),
    }).catch(() => {})

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

  // Launch a server-side QUIC attack via POST /attack. Returns the attackId (or null).
  // Progress + completion arrive asynchronously over the WebSocket as type:"attack".
  launchAttack: async (type, params, target = 'https://127.0.0.1:4434') => {
    try {
      const res = await apiFetch('/attack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, target, params }),
      })
      const data = await res.json()
      if (!res.ok || !data?.attackId) return null
      set((s) => s.runningAttacks[data.attackId] || s.completedAttacks.some((a) => a.attackId === data.attackId) ? s : ({
        runningAttacks: {
          ...s.runningAttacks,
          [data.attackId]: {
            attackId: data.attackId,
            attackType: type,
            status: 'started',
            startedAt: Date.now(),
          },
        },
      }))
      return data.attackId as string
    } catch {
      return null
    }
  },

  stopAttack: async (attackId) => {
    const res = await apiFetch(`/attack/${attackId}/stop`, { method: 'POST' })
    const data = await res.json()
    if (data.attackId !== attackId || !['stopped', 'complete', 'failed'].includes(data.status)) {
      throw new Error('Stop was not confirmed. Check the run status before retrying.')
    }
    get().handleAttackEvent({ ...data, attackType: data.type })
  },

  // Fold a WS attack event into runningAttacks; move terminal ones to completedAttacks.
  handleAttackEvent: (event) => {
    const { attackId, attackType, status, progress, result, error } = event
    if (!attackId || !status) return
    set((s) => {
      // HTTP acknowledgements and delayed WS events must not resurrect a terminal run.
      if (s.completedAttacks.some((a) => a.attackId === attackId)) return s
      const prev = s.runningAttacks[attackId]
      const merged: AttackState = {
        attackId,
        attackType: attackType ?? prev?.attackType ?? 'unknown',
        status,
        startedAt: prev?.startedAt ?? Date.now(),
        progress: progress ?? prev?.progress,
        result: result ?? prev?.result,
        error: error ?? prev?.error,
      }
      if (status === 'complete' || status === 'failed' || status === 'stopped') {
        const { [attackId]: _omit, ...rest } = s.runningAttacks
        merged.completedAt = Date.now()
        return {
          runningAttacks: rest,
          completedAttacks: [merged, ...s.completedAttacks].slice(0, 20),
        }
      }
      return { runningAttacks: { ...s.runningAttacks, [attackId]: merged } }
    })
  },

  // A held message arrives as type:"intercept" with status "pending"; the same interceptId
  // later arrives as forwarded/dropped/timeout. Add on pending, remove on any terminal status.
  handleInterceptEvent: (event) => {
    const interceptId = event.interceptId as string | undefined
    if (!interceptId) return
    const status = event.status as string | undefined

    if (status === 'pending') {
      set((s) => {
        if (s.pendingIntercepts.some((p) => p.interceptId === interceptId)) return s
        const item: InterceptItem = {
          interceptId,
          timestamp: (event.timestamp as number) ?? Date.now(),
          direction: (event.direction as InterceptItem['direction']) ?? 'incoming',
          messageType: (event.messageType as InterceptItem['messageType']) ?? 'datagram',
          payload: (event.payload as string) ?? '',
          payloadEncoding: event.payloadEncoding === 'base64' ? 'base64' : 'utf8',
          rawSize: (event.rawSize as number) ?? 0,
          streamId: event.streamId as string | undefined,
        }
        return { pendingIntercepts: [...s.pendingIntercepts, item] }
      })
    } else {
      set((s) => ({
        pendingIntercepts: s.pendingIntercepts.filter((p) => p.interceptId !== interceptId),
      }))
    }
  },

  fetchInterceptConfig: async () => {
    try {
      const res = await apiFetch('/intercept/manual')
      const cfg = await res.json()
      set({
        manualIntercept: {
          enabled: !!cfg.enabled,
          directions: Array.isArray(cfg.directions) ? cfg.directions : ['incoming', 'outgoing'],
          types: Array.isArray(cfg.types) ? cfg.types : ['datagram', 'stream'],
          timeoutMs: typeof cfg.timeoutMs === 'number' ? cfg.timeoutMs : 30000,
        },
      })
    } catch {
      // proxy not up yet — keep defaults
    }
  },

  setManualIntercept: async (partial) => {
    const next = { ...get().manualIntercept, ...partial }
    set({ manualInterceptError: '' })
    try {
      const res = await apiFetch('/intercept/manual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(partial),
      })
      const cfg = await res.json()
      set({
        manualIntercept: {
          enabled: !!cfg.enabled,
          directions: Array.isArray(cfg.directions) ? cfg.directions : next.directions,
          types: Array.isArray(cfg.types) ? cfg.types : next.types,
          timeoutMs: typeof cfg.timeoutMs === 'number' ? cfg.timeoutMs : next.timeoutMs,
        },
      })
      // Disabling intercept auto-forwards everything still held — clear the local queue.
      if (!cfg.enabled) set({ pendingIntercepts: [] })
    } catch (error) {
      set({ manualInterceptError: error instanceof Error ? error.message : 'Could not update intercept settings.' })
    }
  },

  fetchInterceptQueue: async () => {
    try {
      const res = await apiFetch('/intercept/queue')
      const data = await res.json()
      const items: InterceptItem[] = (data.items ?? []).map((it: Record<string, unknown>) => ({
        interceptId: it.id as string,
        timestamp: (it.timestamp as number) ?? Date.now(),
        direction: (it.direction as InterceptItem['direction']) ?? 'incoming',
        messageType: (it.messageType as InterceptItem['messageType']) ?? 'datagram',
        payload: (it.payload as string) ?? '',
        payloadEncoding: it.payloadEncoding === 'base64' ? 'base64' : 'utf8',
        rawSize: (it.rawSize as number) ?? 0,
        streamId: it.streamId as string | undefined,
      }))
      set({ pendingIntercepts: items })
    } catch {
      // proxy not up yet — keep whatever the WS has delivered
    }
  },

  resolveIntercept: async (interceptId, action, payload) => {
    const res = await apiFetch(`/intercept/${interceptId}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(action === 'forward' ? { action, payload } : { action }),
    })
    if (!res.ok) {
      const error = await res.json().catch(() => ({}))
      throw new Error(typeof error.detail === 'string' ? error.detail : 'Intercept decision failed')
    }
    set((s) => ({
      pendingIntercepts: s.pendingIntercepts.filter((p) => p.interceptId !== interceptId),
    }))
  },

  // Seed the Repeater from a captured event so it can be edited and resent.
  sendToRepeater: (event) => {
    if (!event.replayable || !event.sessionId || event.payloadEncoding === 'base64' || event.type !== 'datagram') {
      set({ repeaterStatus: 'This event cannot be replayed as a text datagram.' })
      return
    }
    set({
      activeView: 'repeater',
      repeater: { payload: event.payload, direction: event.direction, messageType: 'datagram', sessionId: event.sessionId },
      repeaterStatus: '',
    })
  },

  setRepeater: (partial) =>
    set((s) => ({ repeater: { ...s.repeater, ...partial }, repeaterStatus: '' })),

  replaySend: async () => {
    const { payload, direction, messageType, sessionId } = get().repeater
    if (!sessionId) {
      set({ repeaterStatus: 'Select a live session before sending.' })
      return
    }
    if (messageType !== 'datagram') {
      set({ repeaterStatus: 'Only text datagram replay is supported.' })
      return
    }
    set({ repeaterStatus: 'sending…' })
    try {
      const res = await apiFetch('/replay', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ payload, direction, messageType, sessionId }),
      })
      if (res.ok) {
        set({ repeaterStatus: `Queued to ${sessionId.slice(0, 8)}; delivery is not acknowledged.` })
      } else {
        const err = await res.json().catch(() => ({}))
        set({ repeaterStatus: err?.detail || `failed (${res.status})` })
      }
    } catch (error) {
      set({ repeaterStatus: error instanceof Error ? error.message : 'Replay failed.' })
    }
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
