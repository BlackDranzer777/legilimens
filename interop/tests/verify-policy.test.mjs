import test from 'node:test'
import assert from 'node:assert/strict'
import { assess, isLocalOrigin } from '../videocall/verify-policy.mjs'

const cfg = { url: 'http://127.0.0.1:9000/', apiOrigin: 'http://127.0.0.1:9001',
  wsOrigin: 'ws://127.0.0.1:9002', wtOrigin: 'https://127.0.0.1:9003' }
function good() {
  return { appConfig: { oauthEnabled: 'false', apiBaseUrl: cfg.apiOrigin, wsUrl: cfg.wsOrigin,
    webTransportHost: cfg.wtOrigin }, mounted: true, crossOriginIsolated: true,
    requests: ['worker_decoder', 'neteq_worker'].map(n => ({ origin: 'http://127.0.0.1:9000',
      path: `/${n}_bg.wasm`, status: 200, mime: 'application/wasm' })),
    workers: { worker_decoder: { initialized: true, messages: [] },
      neteq_worker: { initialized: true, messages: [{ type: 'workerReady' }] } },
    failedRequests: [], blockedRequests: [], violations: [], consoleErrors: [], pageErrors: [] }
}
test('complete startup evidence passes', () => assert.equal(assess(good(), cfg).passed, true))
for (const [name, mutate] of [
  ['missing mount', r => { r.mounted = false }],
  ['missing isolation', r => { r.crossOriginIsolated = false }],
  ['missing worker WASM', r => { r.requests.pop() }],
  ['wrong MIME', r => { r.requests[0].mime = 'text/html' }],
  ['missing worker readiness', r => { r.workers.neteq_worker.messages = [] }],
  ['runtime exception', r => { r.pageErrors.push('TypeError: broken app') }],
  ['network-looking worker exception', r => { r.consoleErrors.push({ text: 'worker connection failed', url: cfg.url }) }],
  ['missing asset', r => { r.requests.push({ origin: cfg.url.slice(0, -1), path: '/missing.js', status: 404 }) }],
  ['unblocked failed analytics request', r => { r.failedRequests.push({ origin: 'https://matomo.videocall.rs', path: '/matomo.js', reason: 'net::ERR_FAILED' }) }],
  ['external response', r => { r.requests.push({ origin: 'https://example.invalid', path: '/x', status: 200 }) }],
  ['nonlocal transport config', r => { r.appConfig.webTransportHost = 'https://example.invalid' }],
]) {
  test(`${name} fails`, () => { const r = good(); mutate(r); assert.equal(assess(r, cfg).passed, false) })
}
test('intentional CSP-blocked analytics is recorded, not a dispatched request', () => {
  const r = good()
  r.violations.push({ blockedURI: 'https://matomo.videocall.rs/matomo.js', directive: 'script-src-elem' })
  assert.equal(assess(r, cfg).passed, true)
})
test('hostname substrings and credentials do not pass locality checks', () => {
  for (const value of ['https://127.0.0.1.example.com', 'https://example.com/?127.0.0.1', 'http://user@localhost']) {
    assert.equal(isLocalOrigin(value), false)
  }
})
