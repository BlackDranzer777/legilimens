// Serialized by Playwright before the unmodified upstream WASM loads.
export function installTransportObserver({ origin, pin }) {
  const evidence = globalThis.__transportEvidence = { wt: [], ws: [] }
  const bytes = Uint8Array.from(atob(pin), (c) => c.charCodeAt(0))
  for (const [name, records] of [['WebTransport', evidence.wt], ['WebSocket', evidence.ws]]) {
    const Native = globalThis[name]
    globalThis[name] = new Proxy(Native, {
      construct(target, args, newTarget) {
        const url = new URL(args[0], globalThis.location.href)
        const rec = { origin: url.origin, path: url.pathname, ready: 'pending', pinned: false }
        // Preserve native constructor/prototype semantics, URL and token.
        if (name === 'WebTransport' && url.origin === origin) {
          args = [args[0], { ...args[1], serverCertificateHashes: [{ algorithm: 'sha-256', value: bytes }] }]
          rec.pinned = true
        }
        records.push(rec)
        const transport = Reflect.construct(target, args, newTarget)
        if (name === 'WebTransport') {
          transport.ready.then(() => { rec.ready = 'ready' }, () => { rec.ready = 'rejected' })
        } else {
          transport.addEventListener('open', () => { rec.ready = 'ready' })
          transport.addEventListener('error', () => { rec.ready = 'rejected' })
        }
        return transport
      },
    })
  }
}

export function connectionVerdict(c) {
  if (c.error || c.errors.length || !c.configMatches || !c.isolated || c.blocked.length
      || c.ws.some((r) => r.ready === 'ready')) return false
  const lobby = c.wt.filter((r) => r.path === '/lobby' && r.pinned)
  const ready = c.wt.some((r) => r.ready === 'ready')
  if (c.kind === 'auth') return [401, 403].includes(c.authStatus) && !c.joined && c.wt.length === 0
  if (c.authStatus !== 200 || !c.admitted || !c.startClicked) return false
  if (c.kind === 'positive') return c.joined && lobby.some((r) => r.ready === 'ready')
  if (c.kind === 'pin') return !c.joined && !ready && lobby.length > 0 && lobby.every((r) => r.ready === 'rejected')
  return false
}
