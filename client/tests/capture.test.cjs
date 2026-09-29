const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { test } = require('node:test')
const ts = require('typescript')

const source = fs.readFileSync(path.join(__dirname, '../src/capture.ts'), 'utf8')
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const api = {}
// No browser, store, fetch, WebSocket, or WebTransport globals: parsing is pure.
vm.runInNewContext(output, { exports: api, TextEncoder, TextDecoder })
const event = (overrides = {}) => ({ id: 'e1', type: 'datagram', direction: 'incoming', payload: 'hello',
  payloadEncoding: 'utf8', rawSize: 5, timestamp: 10, latency: 0, sessionId: 's1', target: '127.0.0.1:4434',
  flag: 'normal', replayable: true, ...overrides })
const state = (events = [event()]) => ({ events, observationStartedAt: 1, evictedEvents: 0, evictedStreams: 0,
  resourceWarning: '', streams: {}, controlToken: 'DO-NOT-EXPORT', privateKey: 'DO-NOT-EXPORT' })
const document = () => JSON.parse(api.exportCapture(state(), 20))
const parse = (value) => api.parseCapture(JSON.stringify(value))

test('Round-trip preserves full text, whitespace, empty payloads, Unicode, binary and stream chunks', () => {
  const binary = Buffer.from(Array.from({ length: 256 }, (_, i) => i))
  const events = ['  \n\t{"score":1}\r\n', '', '\u20ac\ud83d\ude80\u0000', 'x'.repeat(10000)].map((payload, i) => event({
    id: `text${i}`, payload, rawSize: Buffer.byteLength(payload),
  }))
  events.push(event({ id: 'binary', payload: binary.toString('base64'), payloadEncoding: 'base64', rawSize: 256 }))
  events.push(event({ id: 'stream', type: 'stream', streamId: 'stream1', payload: 'fragment', rawSize: 8 }))
  const result = api.parseCapture(api.exportCapture(state(events), 20))
  result.events.forEach((e, i) => {
    assert.equal(e.payload, events[i].payload)
    assert.equal(e.sessionId, events[i].sessionId)
    assert.equal(e.target, events[i].target)
    assert.equal(e.rawSize, events[i].rawSize)
    assert.equal(e.timestamp, events[i].timestamp)
  })
  assert.deepEqual(Buffer.from(result.events[4].payload, 'base64'), binary)
  assert.equal(result.events[5].streamId, 'stream1')
})

test('Export excludes private control state, config, unknown fields and harvested-token index', () => {
  const input = state([event({ controlToken: 'DO-NOT-EXPORT', payloadPreview: 'DO-NOT-EXPORT' })])
  input.harvestedTokens = ['DO-NOT-EXPORT']
  input.targetConfig = { privateKey: 'DO-NOT-EXPORT' }
  const text = api.exportCapture(input, 20)
  assert.equal(text.includes('DO-NOT-EXPORT'), false)
  assert.equal(text.includes('replayable'), false)
  assert.equal(text.includes('payloadPreview'), false)
})

test('Raw payload evidence is not silently redacted', () => {
  const payload = '{"password":"sensitive-evidence"}'
  assert.equal(api.parseCapture(api.exportCapture(state([event({ payload })]))).events[0].payload, payload)
})

test('Loss, target history and unknown metadata survive export; no completeness claim', () => {
  const input = state([event(), event({ id: 'e2', sessionId: 's2', target: 'other.local:443', payloadEncoding: undefined }),
    event({ id: 'e3', sessionId: undefined, target: undefined })])
  Object.assign(input, { evictedEvents: 40, evictedStreams: 3, resourceWarning: 'Capture gap',
    streams: { one: { omittedChunks: 5 }, two: { omittedChunks: 7 } } })
  const result = api.parseCapture(api.exportCapture(input))
  assert.equal(result.loss.completeness, 'not-confirmed')
  assert.equal(result.loss.evictedEvents, 40)
  assert.equal(result.loss.omittedStreamChunks, 12)
  assert.equal(result.loss.warning, 'Capture gap')
  assert.equal(result.events[1].target, 'other.local:443')
  assert.equal(result.events[1].payloadEncoding, null)
  assert.equal(result.events[2].target, null)
  assert.equal(result.events[2].sessionId, null)
})

test('Offline event projection disables replay and never changes original data', () => {
  const capture = parse(document())
  const before = JSON.stringify(capture)
  const events = api.offlineEvents(capture)
  assert.ok(events.every((e) => e.replayable === false))
  events[0].payload = 'edited'
  assert.equal(JSON.stringify(capture), before)
})

const invalidCases = [
  ['unsupported version', (d) => { d.version = 2 }],
  ['unsupported format', (d) => { d.format = 'other' }],
  ['unknown root fields', (d) => { d.authorization = 'secret' }],
  ['unknown event fields', (d) => { d.events[0].replayable = true }],
  ['missing fields', (d) => { delete d.loss }],
  ['completeness claim', (d) => { d.loss.completeness = 'complete' }],
  ['negative loss count', (d) => { d.loss.evictedEvents = -1 }],
  ['invalid timestamp', (d) => { d.events[0].timestamp = 9e15 }],
  ['fractional size', (d) => { d.events[0].rawSize = 0.5 }],
  ['bad direction', (d) => { d.events[0].direction = 'execute' }],
  ['payload object', (d) => { d.events[0].payload = { command: 'anything' } }],
  ['payload too long', (d) => { d.events[0].payload = 'x'.repeat(1024 * 1024 + 1) }],
  ['unpaired UTF-16 surrogate', (d) => { d.events[0].payload = '\ud800' }],
  ['bad target', (d) => { d.events[0].target = { host: 'remote' } }],
  ['too many events', (d) => { d.events = Array.from({ length: 501 }, (_, i) => ({ ...d.events[0], id: String(i) })) }],
  ['duplicate ids', (d) => { d.events.push({ ...d.events[0] }) }],
  ['empty ids', (d) => { d.events[0].id = '' }],
  ['prototype property', (d) => { Object.defineProperty(d, '__proto__', { value: { polluted: true }, enumerable: true }) }],
]
for (const [name, change] of invalidCases) test(`Rejects ${name}`, () => {
  const d = document()
  change(d)
  assert.throws(() => parse(d), /Invalid capture/)
  assert.equal({}.polluted, undefined)
})

for (const payload of ['=', '====', 'AA=A', 'AB==', 'AAB=', 'a===', 'AA', 'AA==\n', '!!!!']) {
  test(`Rejects malformed Base64 ${JSON.stringify(payload)}`, () => {
    const d = document()
    Object.assign(d.events[0], { payloadEncoding: 'base64', payload })
    assert.throws(() => parse(d), /Invalid capture/)
  })
}

test('Tampered payload length may differ from original rawSize', () => {
  assert.equal(api.parseCapture(api.exportCapture(state([event({ payload: 'longer payload', rawSize: 1, flag: 'tampered' })]))).events[0].rawSize, 1)
})

test('Malformed JSON and oversized text are rejected', () => {
  assert.throws(() => api.parseCapture('{'), /not valid JSON/)
  assert.throws(() => api.parseCapture(' '.repeat(api.MAX_CAPTURE_FILE_BYTES + 1)), /16 MiB/)
  assert.throws(() => api.parseCapture('\u20ac'.repeat(Math.floor(api.MAX_CAPTURE_FILE_BYTES / 3) + 1)), /16 MiB/)
})

test('File-size guard precedes reading; invalid UTF-8 and disguised oversized buffers rejected', async () => {
  let read = false
  await assert.rejects(api.readCaptureFile({ size: api.MAX_CAPTURE_FILE_BYTES + 1, arrayBuffer: () => { read = true } }), /16 MiB/)
  assert.equal(read, false)
  await assert.rejects(api.readCaptureFile({ size: 1, arrayBuffer: async () => new Uint8Array([255]).buffer }), /not UTF-8/)
  await assert.rejects(api.readCaptureFile({ size: 1, arrayBuffer: async () => new ArrayBuffer(api.MAX_CAPTURE_FILE_BYTES + 1) }), /16 MiB/)
  const bytes = new TextEncoder().encode(api.exportCapture(state([])))
  const result = await api.readCaptureFile({ size: bytes.length, arrayBuffer: async () => bytes.buffer })
  assert.equal(result.events.length, 0)
})

test('Base64 accepts all remainder/padding cases including empty', () => {
  for (let n = 0; n < 10; n++) {
    const payload = Buffer.alloc(n, 255).toString('base64')
    assert.equal(api.parseCapture(api.exportCapture(state([event({ payload, payloadEncoding: 'base64' })]))).events[0].payload, payload)
  }
})
