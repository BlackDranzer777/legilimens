import { useStore } from '../store/useStore'

function chipStyle(active: boolean): React.CSSProperties {
  return {
    fontSize: 10,
    letterSpacing: '0.06em',
    padding: '4px 10px',
    border: `1px solid ${active ? 'var(--accent)' : 'var(--border-dim)'}`,
    background: active ? 'var(--accent)' : 'transparent',
    color: active ? '#090909' : 'var(--text-secondary)',
    cursor: 'pointer',
    fontFamily: 'inherit',
    fontWeight: active ? 700 : 400,
    textTransform: 'uppercase',
  }
}

// Burp-style Repeater: take a captured message, edit it, and resend it on demand into
// the live proxied session. The target's response comes back in the Traffic Log.
export default function Repeater() {
  const repeater = useStore((s) => s.repeater)
  const status = useStore((s) => s.repeaterStatus)
  const setRepeater = useStore((s) => s.setRepeater)
  const send = useStore((s) => s.replaySend)

  return (
    <div className="panel">
      <div className="panel-header">/ REPEATER</div>
      <div className="panel-body" style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 10, color: 'var(--text-secondary)', letterSpacing: '0.06em' }}>SEND</span>
          <button
            style={chipStyle(repeater.direction === 'incoming')}
            onClick={() => setRepeater({ direction: 'incoming' })}
            title="Inject toward the server, as if the client sent it"
          >
            → TO SERVER
          </button>
          <button
            style={chipStyle(repeater.direction === 'outgoing')}
            onClick={() => setRepeater({ direction: 'outgoing' })}
            title="Inject toward the client, as if the server sent it"
          >
            ← TO CLIENT
          </button>
        </div>

        <textarea
          value={repeater.payload}
          onChange={(e) => setRepeater({ payload: e.target.value })}
          spellCheck={false}
          placeholder="Expand a row in the Traffic Log and click ⟳ Send to Repeater — or paste a payload here."
          style={{
            flex: 1,
            minHeight: 80,
            resize: 'none',
            padding: 8,
            fontSize: 11,
            fontFamily: 'var(--font-mono)',
            background: 'var(--bg-primary)',
            border: '1px solid var(--border-dim)',
            color: 'var(--text-primary)',
            outline: 'none',
          }}
        />

        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <button className="btn btn-primary" onClick={send} disabled={!repeater.payload.trim()}>
            ▶ SEND
          </button>
          <span style={{ fontSize: 10, color: 'var(--text-secondary)', letterSpacing: '0.06em' }}>
            {status || 'datagram · needs an active session'}
          </span>
        </div>
      </div>
    </div>
  )
}
