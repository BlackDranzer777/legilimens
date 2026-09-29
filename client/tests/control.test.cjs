const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { test } = require('node:test')
const ts = require('typescript')

function fixture(hash = '') {
  const storage = new Map()
  const timers = new Map()
  const sockets = []
  const requests = []
  const window = new EventTarget()
  window.location = { hash, pathname: '/', search: '', origin: 'http://127.0.0.1:4436' }
  window.history = { replaceState: (_state, _title, url) => { window.cleanedUrl = url } }
  const environment = {
    window, Event, Error, Headers, URLSearchParams, AbortSignal, console, crypto: require('node:crypto').webcrypto,
    sessionStorage: { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
    setTimeout: (fn) => { const id = Symbol(); timers.set(id, fn); return id },
    clearTimeout: (id) => timers.delete(id),
    fetch: async (url, init) => {
      requests.push({ url, init })
      return environment.response
    },
    response: { ok: true, status: 200, json: async () => ({ status: 'ok', service: 'legilimens', wsPort: 4545, proxyPort: 4543 }) },
    WebSocket: class {
      constructor(url) { this.url = url; this.readyState = 0; this.sent = []; sockets.push(this) }
      send(message) { this.sent.push(message) }
      close() { this.readyState = 3; this.onclose?.({ code: 1000 }) }
    },
  }
  function load(file, dependencies = {}) {
    const source = fs.readFileSync(path.join(__dirname, '..', 'src', file), 'utf8').replace('import.meta.env.DEV', 'true')
    const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
    const exports = {}
    vm.runInNewContext(output, { ...environment, exports, require: (name) => dependencies[name] ?? require(name) }, { filename: file })
    return exports
  }
  const control = load('control.ts')
  const store = load('store/useStore.ts', { '../control': control }).useStore
  return { control, store, environment, window, storage, timers, sockets, requests }
}

test('Rejected intercept settings preserve config and held messages and surface the error', async () => {
  const f = fixture()
  const before = f.store.getState().manualIntercept
  f.store.setState({ pendingIntercepts: [{ interceptId: 'held' }] })
  f.environment.response = { ok: false, status: 400, json: async () => ({ detail: 'Invalid timeout' }) }
  await f.store.getState().setManualIntercept({ enabled: false, timeoutMs: 999 })
  assert.equal(f.store.getState().manualIntercept, before)
  assert.equal(f.store.getState().pendingIntercepts.length, 1)
  assert.equal(f.store.getState().manualInterceptError, 'Invalid timeout')
  assert.deepEqual(JSON.parse(f.requests[0].init.body), { enabled: false, timeoutMs: 999 })
})

test('Sustained capture retains bounded full events and reports evictions', () => {
  const f = fixture()
  for (let i = 0; i < 1200; i++) {
    f.store.getState().addEvent({ id: String(i), type: 'datagram', direction: 'incoming', payload: 'x'.repeat(9000), rawSize: 9000, timestamp: i })
  }
  const state = f.store.getState()
  assert.ok(state.events.length <= 500)
  assert.ok(state.events.reduce((n, e) => n + JSON.stringify(e).length * 2, 0) <= 8 * 1024 * 1024)
  assert.equal(state.events.at(-1).payload.length, 9000)
  assert.equal(state.totalEvents, 1200)
  assert.equal(state.evictedEvents, 1200 - state.events.length)
  state.clearLog()
  assert.equal(f.store.getState().evictedEvents, 0)
})

test('Stream chunk count, bytes, stream count, and harvested tokens are bounded', () => {
  const f = fixture()
  const add = (streamId, payload) => f.store.getState().addEvent({ id: 'fixture', type: 'stream', streamId, direction: 'incoming', payload, timestamp: 1, rawSize: payload.length })
  add('first', 'opened')
  for (let i = 0; i < 1500; i++) add('first', 'x'.repeat(2000))
  const stream = f.store.getState().streams.first
  assert.ok(stream.chunks.length <= 128)
  assert.ok(JSON.stringify(stream.chunks).length * 2 <= 128 * 1024)
  assert.equal(stream.omittedChunks, 1500 - stream.chunks.length)
  for (let i = 0; i < 200; i++) {
    add(String(i), 'opened')
    f.store.getState().addHarvestedToken(String(i))
  }
  assert.equal(Object.keys(f.store.getState().streams).length, 64)
  assert.equal(f.store.getState().evictedStreams, 137)
  assert.equal(f.store.getState().harvestedTokens.length, 100)
})

test('Capture loss notifications and capacity closes remain visible', () => {
  const f = fixture()
  f.store.getState().connectWebSocket()
  f.sockets[0].onmessage({ data: JSON.stringify({ type: 'resource', message: 'Omitted capture' }) })
  assert.equal(f.store.getState().resourceWarning, 'Omitted capture')
  assert.equal(f.store.getState().events.length, 0)
  f.sockets[0].onclose({ code: 1013 })
  assert.match(f.store.getState().resourceWarning, /incomplete/)
})

test('Offline subscription disconnect preserves live evidence and records a gap until clear', () => {
  const f = fixture()
  const event = { id: 'retained', type: 'datagram', payload: 'untouched', direction: 'incoming', rawSize: 9, timestamp: 1 }
  f.store.getState().addEvent(event)
  f.store.getState().disconnectWebSocket()
  assert.equal(f.store.getState().events[0].payload, 'untouched')
  assert.match(f.store.getState().resourceWarning, /disconnected/)
  f.store.getState().clearLog()
  assert.equal(f.store.getState().resourceWarning, '')
  assert.equal(f.store.getState().events.length, 0)
  assert.ok(f.store.getState().observationStartedAt > 0)
})

const settle = () => new Promise((resolve) => setImmediate(resolve))
const snapshot = (sequence = 0, attacks = []) => ({ epoch: 'test-epoch', sequence, attacks,
  pendingIntercepts: [], manualIntercept: { enabled: false, directions: ['incoming'], types: ['datagram'], timeoutMs: 30000 },
  captureMode: 'paused', tamperEnabled: false })

test('Reconnect snapshot removes stale runs and held items; delayed evicted events cannot resurrect runs', async () => {
  const f = fixture()
  f.store.setState({ pendingIntercepts: [{ interceptId: 'stale' }], runningAttacks: { stale: { attackId: 'stale' } } })
  f.environment.response = { ok: true, status: 200, json: async () => snapshot() }
  f.store.getState().connectWebSocket()
  const ws = f.sockets[0]
  ws.onmessage({ data: JSON.stringify({ type: 'authenticated', epoch: 'test-epoch', sequence: 0 }) })
  await settle()
  assert.equal(f.requests[0].url, 'http://127.0.0.1:4436/state')
  assert.equal(f.store.getState().pendingIntercepts.length, 0)
  assert.equal(Object.keys(f.store.getState().runningAttacks).length, 0)
  for (let i = 1; i <= 30; i++) {
    ws.onmessage({ data: JSON.stringify({ epoch: 'test-epoch', sequence: i,
      event: { type: 'attack', attackId: String(i), attackType: 'fixture', status: 'complete' } }) })
  }
  assert.equal(f.store.getState().completedAttacks.length, 20)
  ws.onmessage({ data: JSON.stringify({ epoch: 'test-epoch', sequence: 1,
    event: { type: 'attack', attackId: '1', status: 'running' } }) })
  assert.equal(Object.keys(f.store.getState().runningAttacks).length, 0)
})

test('Sequence gap reloads state and records permanent capture loss warning', async () => {
  const f = fixture()
  f.environment.response = { ok: true, status: 200, json: async () => snapshot() }
  f.store.getState().connectWebSocket()
  const ws = f.sockets[0]
  ws.onmessage({ data: JSON.stringify({ type: 'authenticated', epoch: 'test-epoch', sequence: 0 }) })
  await settle()
  f.environment.response = { ok: true, status: 200, json: async () => snapshot(4, [{ attackId: 'finished', status: 'stopped' }]) }
  ws.onmessage({ data: JSON.stringify({ epoch: 'test-epoch', sequence: 4, event: { type: 'attack', attackId: 'finished', status: 'running' } }) })
  await settle()
  assert.equal(f.requests.length, 2)
  assert.equal(f.store.getState().completedAttacks[0].status, 'stopped')
  assert.match(f.store.getState().resourceWarning, /gap/)
})

test('Events after a snapshot cursor are replayed; stale responses cannot replace a new socket', async () => {
  const f = fixture()
  let resolve
  f.environment.response = { ok: true, status: 200, json: () => new Promise((r) => { resolve = r }) }
  f.store.getState().connectWebSocket()
  const ws = f.sockets[0]
  ws.onmessage({ data: JSON.stringify({ type: 'authenticated', epoch: 'test-epoch', sequence: 0 }) })
  await settle()
  ws.onmessage({ data: JSON.stringify({ epoch: 'test-epoch', sequence: 1, event: { type: 'attack', attackId: 'new', status: 'running' } }) })
  resolve(snapshot())
  await settle()
  assert.equal(f.store.getState().runningAttacks.new.status, 'running')
  ws.onmessage({ data: JSON.stringify({ epoch: 'test-epoch', sequence: 4, event: { type: 'resource' } }) })
  await settle()
  f.store.getState().disconnectWebSocket()
  resolve(snapshot(4))
  await settle()
  assert.equal(f.store.getState().wsConnected, false)
})

test('Recovery buffer overflow fails closed and reports missing capture', async () => {
  const f = fixture()
  let resolve
  f.environment.response = { ok: true, status: 200, json: () => new Promise((r) => { resolve = r }) }
  f.store.getState().connectWebSocket()
  const ws = f.sockets[0]
  ws.onmessage({ data: JSON.stringify({ type: 'authenticated', epoch: 'test-epoch', sequence: 0 }) })
  await settle()
  for (let i = 1; i <= 129; i++) ws.onmessage({ data: JSON.stringify({ epoch: 'test-epoch', sequence: i, event: { type: 'attack' } }) })
  assert.equal(ws.readyState, 3)
  assert.equal(f.store.getState().wsConnected, false)
  assert.match(f.store.getState().resourceWarning, /missed traffic|incomplete/)
  resolve(snapshot())
  await settle()
  assert.equal(f.store.getState().wsConnected, false)
})

test('Stop response reconciles state without WS and duplicate/late events do not resurrect a run', async () => {
  const f = fixture()
  const state = f.store.getState()
  state.handleAttackEvent({ attackId: 'run', attackType: 'fixture', status: 'running' })
  f.environment.response = { ok: true, status: 200, json: async () => ({ attackId: 'run', type: 'fixture', status: 'stopped' }) }
  await state.stopAttack('run')
  state.handleAttackEvent({ attackId: 'run', attackType: 'fixture', status: 'stopped' })
  state.handleAttackEvent({ attackId: 'run', attackType: 'fixture', status: 'running' })
  assert.equal(Object.keys(f.store.getState().runningAttacks).length, 0)
  assert.equal(f.store.getState().completedAttacks.length, 1)
  assert.equal(f.store.getState().completedAttacks[0].status, 'stopped')
})

test('Failed stop surfaces error without claiming the run stopped', async () => {
  const f = fixture()
  f.store.getState().handleAttackEvent({ attackId: 'run', attackType: 'fixture', status: 'running' })
  f.environment.response = { ok: false, status: 500, json: async () => ({ detail: 'Stop failed' }) }
  await assert.rejects(f.store.getState().stopAttack('run'), /Stop failed/)
  assert.equal(f.store.getState().runningAttacks.run.status, 'running')
  assert.equal(f.store.getState().completedAttacks.length, 0)
})

test('Late launch acknowledgement cannot overwrite progress or completion received over WS', async () => {
  for (const status of ['running', 'complete']) {
    const f = fixture()
    f.environment.response = { ok: true, status: 200, json: async () => {
      f.store.getState().handleAttackEvent({ attackId: 'run', attackType: 'fixture', status, progress: { current: 1, total: 2 } })
      return { attackId: 'run', status: 'started' }
    } }
    await f.store.getState().launchAttack('fixture', {})
    if (status === 'complete') {
      assert.equal(Object.keys(f.store.getState().runningAttacks).length, 0)
      assert.equal(f.store.getState().completedAttacks[0].status, 'complete')
    } else {
      assert.equal(f.store.getState().runningAttacks.run.status, 'running')
      assert.equal(f.store.getState().runningAttacks.run.progress.current, 1)
    }
  }
})

test('fragment token is removed and sent only in Authorization, not request URL', async () => {
  const f = fixture('#token=private-fixture-token')
  assert.equal(f.window.cleanedUrl, '/')
  assert.equal(f.control.getControlToken(), 'private-fixture-token')
  await f.control.apiFetch('/tamper', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
  const { url, init } = f.requests[0]
  assert.equal(url, 'http://127.0.0.1:4436/tamper')
  assert.equal(init.headers.get('authorization'), 'Bearer private-fixture-token')
  assert.equal(init.headers.get('content-type'), 'application/json')
  assert.equal(init.redirect, 'error')
  assert.equal(init.credentials, 'omit')
  await assert.rejects(f.control.apiFetch('//untrusted.example'), /Invalid API path/)
})

test('401 clears credentials and signals the access gate', async () => {
  const f = fixture('#token=expired')
  let expired = 0
  f.window.addEventListener(f.control.AUTH_REQUIRED, () => expired++)
  f.environment.response = { ok: false, status: 401, json: async () => ({ detail: 'Access denied' }) }
  await assert.rejects(f.control.apiFetch('/health'), /Access denied/)
  assert.equal(f.control.getControlToken(), '')
  assert.equal(f.storage.size, 0)
  assert.equal(expired, 1)
})

test('health config sets authenticated service ports and rejects unrelated services', async () => {
  const f = fixture()
  await f.control.authenticateControl()
  assert.equal(f.control.captureUrl(), 'ws://127.0.0.1:4545/')
  assert.equal(f.control.proxyUrl(), 'https://127.0.0.1:4543/')
  f.environment.response = { ok: true, status: 200, json: async () => ({ service: 'other' }) }
  await assert.rejects(f.control.authenticateControl(), /Unexpected backend/)
})

test('failed start does not report an active proxy', async () => {
  const f = fixture()
  f.environment.response = { ok: false, status: 500, json: async () => ({ detail: 'Backend failed' }) }
  await f.store.getState().startWebTransport()
  assert.equal(f.store.getState().isProxyActive, false)
  assert.equal(f.store.getState().connectionStatus, 'error')
})

test('capture connection waits for authentication acknowledgement', () => {
  const f = fixture('#token=fixture')
  f.store.getState().connectWebSocket()
  const ws = f.sockets[0]
  ws.onopen()
  assert.deepEqual(JSON.parse(ws.sent[0]), { type: 'authenticate', token: 'fixture' })
  assert.equal(f.store.getState().wsConnected, false)
  ws.onmessage({ data: JSON.stringify({ type: 'authenticated' }) })
  assert.equal(f.store.getState().wsConnected, true)
})

test('intentional disconnect cancels retry and stale close cannot replace a new connection', () => {
  const f = fixture()
  const state = f.store.getState()
  state.connectWebSocket()
  const first = f.sockets[0]
  first.onclose({ code: 1006 })
  assert.equal(f.timers.size, 1)
  state.disconnectWebSocket()
  assert.equal(f.timers.size, 0)
  state.connectWebSocket()
  first.onclose({ code: 1006 })
  assert.equal(f.timers.size, 0)
  f.sockets[1].onmessage({ data: '{"type":"authenticated"}' })
  assert.equal(f.store.getState().wsConnected, true)
  state.disconnectWebSocket()
  assert.equal(f.timers.size, 0)
  assert.equal(f.store.getState().wsConnected, false)
})

test('WebSocket authentication rejection clears credentials without retrying', () => {
  const f = fixture('#token=expired')
  let expired = 0
  f.window.addEventListener(f.control.AUTH_REQUIRED, () => expired++)
  f.store.getState().connectWebSocket()
  f.sockets[0].onclose({ code: 1008 })
  assert.equal(f.timers.size, 0)
  assert.equal(f.control.getControlToken(), '')
  assert.equal(expired, 1)
})

test('Repeater retains full captured text and binds the captured session', async () => {
  const f = fixture()
  const payload = ' \t' + 'x'.repeat(600) + '\u00e9'.repeat(80) + '\n'
  f.store.getState().sendToRepeater({ type: 'datagram', payload, payloadPreview: 'short preview',
    sessionId: 'selected-session', replayable: true, payloadEncoding: 'utf8', direction: 'incoming' })
  assert.equal(f.store.getState().repeater.payload, payload)
  assert.equal(f.store.getState().repeater.sessionId, 'selected-session')
  await f.store.getState().replaySend()
  assert.equal(f.requests[0].url, 'http://127.0.0.1:4436/replay')
  assert.deepEqual(JSON.parse(f.requests[0].init.body), {
    payload, direction: 'incoming', messageType: 'datagram', sessionId: 'selected-session',
  })
})

test('Repeater requires explicit identity and rejects non-replayable captures', async () => {
  const f = fixture()
  f.store.getState().setRepeater({ payload: 'test' })
  await f.store.getState().replaySend()
  assert.equal(f.requests.length, 0)
  for (const event of [
    { type: 'datagram', sessionId: 'a', replayable: true, payloadEncoding: 'base64' },
    { type: 'stream', sessionId: 'a', replayable: true },
    { type: 'datagram', sessionId: 'a', replayable: false },
    { type: 'datagram', replayable: true },
  ]) {
    f.store.getState().sendToRepeater({ payload: 'invalid', direction: 'incoming', ...event })
    assert.equal(f.store.getState().repeater.payload, 'test')
    assert.equal(f.store.getState().repeater.sessionId, '')
  }
})

test('Replay preserves empty and whitespace payloads and reports closed-session errors', async () => {
  const f = fixture()
  for (const payload of ['', ' \t\n']) {
    f.store.getState().setRepeater({ payload, sessionId: 'chosen' })
    await f.store.getState().replaySend()
    assert.equal(JSON.parse(f.requests.at(-1).init.body).payload, payload)
  }
  f.environment.response = { ok: false, status: 409, json: async () => ({ detail: 'Selected session is closed' }) }
  await f.store.getState().replaySend()
  assert.equal(f.store.getState().repeaterStatus, 'Selected session is closed')
  assert.equal(f.store.getState().repeater.sessionId, 'chosen')
})
