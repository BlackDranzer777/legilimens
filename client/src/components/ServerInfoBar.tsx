import { useEffect, useState } from 'react'

import { apiFetch } from '../control'
import { useStore } from '../store/useStore'

// Surfaces backend state the rest of the UI never showed:
//  • live capture mode + active MITM session count (GET /intercept, server truth)
//  • the proxy's cert hash (GET /cert-hash) with a copy button, so it can be pasted
//    into a real client's serverCertificateHashes
//  • whether the proxy control API is reachable at all
export default function ServerInfoBar() {
  const wsConnected = useStore((s) => s.wsConnected)
  const [certHash, setCertHash] = useState('')
  const [mode, setMode] = useState('—')
  const [sessions, setSessions] = useState<number | null>(null)
  const [online, setOnline] = useState(false)
  const [copied, setCopied] = useState(false)

  // Renewal replaces the backend's certificate without reloading the dashboard.
  useEffect(() => {
    let alive = true
    setCertHash('')
    setCopied(false)
    if (!wsConnected) return
    apiFetch('/cert-hash')
      .then((r) => r.json())
      .then((d) => { if (alive && typeof d?.hash === 'string') setCertHash(d.hash) })
      .catch(() => {/* proxy not up yet */})
    return () => { alive = false }
  }, [wsConnected])

  // Poll live server status every 2s (server-truth, not the UI's local guess).
  useEffect(() => {
    let alive = true
    const poll = async () => {
      try {
        const r = await apiFetch('/intercept')
        const d = await r.json()
        if (!alive) return
        setMode(typeof d?.captureMode === 'string' ? d.captureMode : '—')
        setSessions(typeof d?.activeSessions === 'number' ? d.activeSessions : null)
        setOnline(true)
      } catch {
        if (!alive) return
        setOnline(false)
        setSessions(null)
        setMode('—')
      }
    }
    poll()
    const id = setInterval(poll, 2000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  async function copyHash() {
    try {
      await navigator.clipboard.writeText(certHash)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {/* clipboard unavailable */}
  }

  return (
    <div className="target-bar">
      <span className="target-bar__label">/ SERVER</span>

      <span className={`status-dot ${online ? 'active' : 'error'}`} style={{ fontSize: 10 }}>
        <span className="dot" />
        {online ? 'ONLINE' : 'OFFLINE'}
      </span>

      <span style={{ color: 'var(--text-secondary)', fontSize: 11, whiteSpace: 'nowrap' }}>
        mode{' '}
        <span style={{ color: mode === 'capturing' ? 'var(--accent)' : 'var(--warning)' }}>{mode}</span>
      </span>

      <span style={{ color: 'var(--text-secondary)', fontSize: 11, whiteSpace: 'nowrap' }}>
        sessions <span style={{ color: 'var(--accent)' }}>{sessions ?? '—'}</span>
      </span>

      <span style={{ color: 'var(--text-secondary)', fontSize: 11, whiteSpace: 'nowrap' }}>cert</span>
      <input
        className="target-bar__input"
        value={certHash}
        aria-label="Proxy certificate hash"
        readOnly
        spellCheck={false}
        placeholder="(proxy offline)"
        onFocus={(e) => e.target.select()}
        style={{ flex: 1, minWidth: 160, color: 'var(--text-secondary)' }}
      />
      <button
        className="btn"
        onClick={copyHash}
        disabled={!certHash}
        style={{ borderColor: 'var(--border-dim)' }}
      >
        {copied ? 'COPIED' : 'COPY'}
      </button>
    </div>
  )
}
