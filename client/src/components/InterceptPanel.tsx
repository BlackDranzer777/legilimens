import { useEffect, useState } from 'react'
import { InterceptItem, useStore } from '../store/useStore'

function fmtTime(ts: number) {
  const d = new Date(ts)
  return (
    d.getHours().toString().padStart(2, '0') +
    ':' +
    d.getMinutes().toString().padStart(2, '0') +
    ':' +
    d.getSeconds().toString().padStart(2, '0')
  )
}

function chipStyle(active: boolean): React.CSSProperties {
  return {
    fontSize: 10,
    letterSpacing: '0.06em',
    padding: '3px 8px',
    border: `1px solid ${active ? 'var(--accent)' : 'var(--border-dim)'}`,
    background: active ? 'var(--accent)' : 'transparent',
    color: active ? '#090909' : 'var(--text-secondary)',
    cursor: 'pointer',
    fontFamily: 'inherit',
    fontWeight: active ? 700 : 400,
    textTransform: 'uppercase',
  }
}

// One held message: edit its payload, then forward (optionally edited) or drop it.
function InterceptItemCard({ item }: { item: InterceptItem }) {
  const resolve = useStore((s) => s.resolveIntercept)
  const [edited, setEdited] = useState(item.payload)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function decide(action: 'forward' | 'drop') {
    setBusy(true)
    setError('')
    try {
      await resolve(item.interceptId, action, action === 'forward' ? edited : undefined)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Intercept decision failed')
    } finally {
      setBusy(false)
    }
  }

  const label =
    item.messageType === 'stream' && item.streamId
      ? `STREAM #${item.streamId}`
      : item.messageType.toUpperCase()

  return (
    <div className="intercept-item">
      <div className="intercept-item__head">
        <span className={item.direction === 'incoming' ? 'dir-in' : 'dir-out'}>
          {item.direction === 'incoming' ? '→' : '←'}
        </span>
        <span style={{ color: 'var(--text-primary)', fontWeight: 600, fontSize: 10, letterSpacing: '0.06em' }}>
          {label}
        </span>
        <span style={{ color: 'var(--text-secondary)', fontSize: 10 }}>{fmtTime(item.timestamp)}</span>
        <span style={{ color: 'var(--text-secondary)', fontSize: 10 }}>{item.rawSize}B</span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          <button className="btn btn-primary" disabled={busy} onClick={() => decide('forward')}>
            ▶ FORWARD
          </button>
          <button className="btn btn-danger" disabled={busy} onClick={() => decide('drop')}>
            ✕ DROP
          </button>
        </span>
      </div>
      <textarea
        aria-label={item.payloadEncoding === 'base64' ? 'Binary payload in Base64' : 'Message payload'}
        className="intercept-item__payload"
        value={edited}
        onChange={(e) => setEdited(e.target.value)}
        spellCheck={false}
        rows={2}
      />
      {item.payloadEncoding === 'base64' && <span>Binary payload (Base64)</span>}
      {error && <span role="alert">{error}</span>}
    </div>
  )
}

// Control bar (enable + scope) plus the live queue of held messages.
// Backend holds each message until forwarded/dropped or it times out — and only while
// capture is running, so the hint nudges the user to press START first.
export default function InterceptPanel() {
  const cfg = useStore((s) => s.manualIntercept)
  const configError = useStore((s) => s.manualInterceptError)
  const pending = useStore((s) => s.pendingIntercepts)
  const setManual = useStore((s) => s.setManualIntercept)
  const fetchCfg = useStore((s) => s.fetchInterceptConfig)
  const fetchQueue = useStore((s) => s.fetchInterceptQueue)

  useEffect(() => {
    fetchCfg()
    fetchQueue()
  }, [fetchCfg, fetchQueue])

  // Toggle a value in a scope list, but never leave it empty (the API rejects empty lists).
  function toggle(list: string[], key: string): string[] {
    if (list.includes(key)) {
      const next = list.filter((x) => x !== key)
      return next.length ? next : list
    }
    return [...list, key]
  }

  const status = !cfg.enabled
    ? 'inactive'
    : pending.length > 0
      ? 'holding — forward or drop below'
      : 'armed — waiting for matching traffic'

  return (
    <div>
      <div className="target-bar">
        <span className="target-bar__label">/ INTERCEPT</span>

        <button
          className={cfg.enabled ? 'btn btn-danger' : 'btn btn-primary'}
          onClick={() => setManual({ enabled: !cfg.enabled })}
        >
          {cfg.enabled ? '■ INTERCEPT ON' : '▶ ENABLE INTERCEPT'}
        </button>

        <div style={{ display: 'flex', gap: 4 }}>
          {(['incoming', 'outgoing'] as const).map((d) => (
            <button
              key={d}
              style={chipStyle(cfg.directions.includes(d))}
              onClick={() => setManual({ directions: toggle(cfg.directions, d) })}
              title={`Hold ${d} messages`}
            >
              {d === 'incoming' ? '→ IN' : '← OUT'}
            </button>
          ))}
          {(['datagram', 'stream'] as const).map((t) => (
            <button
              key={t}
              style={chipStyle(cfg.types.includes(t))}
              onClick={() => setManual({ types: toggle(cfg.types, t) })}
              title={`Hold ${t} messages`}
            >
              {t.toUpperCase()}
            </button>
          ))}
        </div>

        <span style={{ color: 'var(--text-secondary)', fontSize: 11 }}>timeout</span>
        <input
          className="target-bar__input"
          type="number"
          aria-label="Intercept timeout in seconds"
          min={1}
          max={300}
          value={Math.round(cfg.timeoutMs / 1000)}
          onChange={(e) => {
            const secs = Math.max(1, Math.min(300, Number(e.target.value) || 1))
            setManual({ timeoutMs: secs * 1000 })
          }}
          style={{ width: 56 }}
        />
        <span style={{ color: 'var(--text-secondary)', fontSize: 11 }}>s</span>

        <span className={`target-bar__status ${cfg.enabled ? 'saved' : ''}`}>
          {pending.length} held · {status}
        </span>
      </div>

      {configError && <div className="action-error" role="alert">{configError}</div>}
      {pending.length > 0 && (
        <div className="intercept-queue">
          {pending.map((item) => (
            <InterceptItemCard key={item.interceptId} item={item} />
          ))}
        </div>
      )}
    </div>
  )
}
