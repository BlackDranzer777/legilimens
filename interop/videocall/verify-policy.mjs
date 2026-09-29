export function isLocalOrigin(value) {
  try {
    const u = new URL(value)
    return ['http:', 'https:', 'ws:', 'wss:'].includes(u.protocol)
      && ['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname)
      && !u.username && !u.password && u.pathname === '/' && !u.search && !u.hash
  } catch { return false }
}

export function isAnalytics(value) {
  try {
    const u = new URL(value)
    return u.origin === 'https://matomo.videocall.rs' && u.pathname === '/matomo.js'
  } catch { return false }
}

export function assess(r, cfg) {
  const expectedUnavailable = f => f.origin === cfg.apiOrigin
    && f.path === '/api/v1/meetings' && f.reason === 'net::ERR_CONNECTION_REFUSED'
  const configLocal = r.appConfig?.oauthEnabled === 'false'
    && r.appConfig.apiBaseUrl === cfg.apiOrigin
    && r.appConfig.wsUrl === cfg.wsOrigin && r.appConfig.webTransportHost === cfg.wtOrigin
    && [cfg.apiOrigin, cfg.wsOrigin, cfg.wtOrigin].every(isLocalOrigin)
  const externalResponses = r.requests.filter(q => ![new URL(cfg.url).origin, cfg.apiOrigin].includes(q.origin))
  const knownBlocked = f => isAnalytics(f.origin + f.path)
    && (r.blockedRequests.some(b => b.origin === f.origin && b.path === f.path)
      || r.violations.some(v => v.blockedURI === f.origin + f.path && v.directive === 'script-src-elem'))
  const badRequests = r.failedRequests.filter(f => !expectedUnavailable(f) && !knownBlocked(f))
  const badResponses = r.requests.filter(q => q.status >= 400)
  const badBlocks = r.blockedRequests.filter(q => !isAnalytics(q.origin + q.path))
  const badViolations = r.violations.filter(v => !isAnalytics(v.blockedURI))
  const fatalLoadErrors = r.consoleErrors.filter(e => {
    if (e.text.includes('ERR_CONNECTION_REFUSED') && e.url?.startsWith(cfg.apiOrigin + '/api/v1/meetings')) return false
    return !(e.text.includes('Content Security Policy') && e.text.includes('https://matomo.videocall.rs/matomo.js'))
  })
  const workersReady = ['worker_decoder', 'neteq_worker'].every(name => r.workers[name]?.initialized
    && !r.workers[name].messages.some(m => m.error)
    && r.requests.some(q => q.path === `/${name}_bg.wasm` && q.status === 200 && q.mime?.startsWith('application/wasm')))
    && r.workers.neteq_worker?.messages.some(m => m.type === 'workerReady')
  const passed = !!(configLocal && r.mounted && r.crossOriginIsolated && workersReady
    && !externalResponses.length && !badRequests.length && !badResponses.length
    && !badBlocks.length && !badViolations.length && !fatalLoadErrors.length && !r.pageErrors.length)
  return { configLocal, workersReady, externalResponses, badRequests, badResponses,
    badBlocks, badViolations, fatalLoadErrors, passed }
}
