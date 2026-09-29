import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { connectionVerdict, installTransportObserver } from '../videocall/connect-policy.mjs'

function evidence(overrides = {}) {
  return { kind: 'positive', errors: [], blocked: [], configMatches: true, isolated: true,
    authStatus: 200, admitted: true, startClicked: true, joined: true, ws: [],
    wt: [{ path: '/lobby', pinned: true, ready: 'ready' }], ...overrides }
}

test('positive requires native readiness AND the attributed server join', () => {
  assert.equal(connectionVerdict(evidence()), true)
  for (const change of [{ joined: false }, { wt: [] }, { startClicked: false }, { admitted: false },
    { configMatches: false }, { isolated: false }, { authStatus: 500 }, { errors: ['runtime failed'] },
    { error: 'timeout' }, { blocked: ['https://external.test/'] }, { ws: [{ ready: 'ready' }] }]) {
    assert.equal(connectionVerdict(evidence(change)), false, JSON.stringify(change))
  }
})

test('auth rejection needs an explicit HTTP denial, not inactivity or an exception', () => {
  const auth = evidence({ kind: 'auth', authStatus: 401, joined: false, wt: [] })
  assert.equal(connectionVerdict(auth), true)
  for (const change of [{ authStatus: undefined }, { authStatus: 500 }, { error: 'navigation failed' },
    { joined: true }, { wt: [{ ready: 'pending' }] }]) {
    assert.equal(connectionVerdict({ ...auth, ...change }), false)
  }
})

test('wrong pin requires an admitted user and a rejected pinned native attempt', () => {
  const pin = evidence({ kind: 'pin', joined: false, wt: [{ path: '/lobby', pinned: true, ready: 'rejected' }] })
  assert.equal(connectionVerdict(pin), true)
  for (const change of [{ wt: [] }, { admitted: false }, { authStatus: 401 }, { joined: true },
    { wt: [{ path: '/lobby', pinned: true, ready: 'pending' }] }, { error: 'timed out' }]) {
    assert.equal(connectionVerdict({ ...pin, ...change }), false)
  }
})

test('adapter pins only the exact endpoint and preserves native constructor semantics', async () => {
  const calls = []
  class Native {
    static marker = 42
    constructor(...args) { calls.push(args); this.ready = Promise.resolve() }
    addEventListener() {}
  }
  const context = vm.createContext({ WebTransport: Native, WebSocket: Native, URL, atob, Uint8Array,
    location: { href: 'http://127.0.0.1:8000/' } })
  vm.runInContext(`(${installTransportObserver.toString()})(${JSON.stringify({ origin: 'https://127.0.0.1:4433', pin: 'AA==' })})`, context)
  const url = 'https://127.0.0.1:4433/lobby?token=kept-in-memory'
  const original = { congestionControl: 'low-latency' }
  const wt = new context.WebTransport(url, original)
  assert.equal(wt instanceof Native, true)
  assert.equal(context.WebTransport.marker, 42)
  assert.equal(calls[0][0], url)
  assert.equal(calls[0][1].congestionControl, original.congestionControl)
  assert.equal(calls[0][1].serverCertificateHashes[0].value[0], 0)
  assert.equal(original.serverCertificateHashes, undefined)
  new context.WebTransport('https://127.0.0.1:4434/lobby', original)
  assert.equal(calls[1][1], original)
  await Promise.resolve()
  assert.equal(context.__transportEvidence.wt[0].ready, 'ready')
  assert.equal(JSON.stringify(context.__transportEvidence).includes('kept-in-memory'), false)
})
