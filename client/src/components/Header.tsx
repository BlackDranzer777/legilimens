import { useStore } from '../store/useStore'

export default function Header() {
  const status = useStore((s) => s.connectionStatus)
  const wsConnected = useStore((s) => s.wsConnected)
  const isActive = useStore((s) => s.isProxyActive)
  const start = useStore((s) => s.startWebTransport)
  const stop = useStore((s) => s.stopWebTransport)
  const clear = useStore((s) => s.clearLog)

  const statusLabel =
    status === 'active'
      ? 'PROXY ACTIVE'
      : status === 'connecting'
      ? 'CONNECTING…'
      : status === 'error'
      ? 'CONNECTION ERROR'
      : 'PROXY INACTIVE'

  const statusClass =
    status === 'active' ? 'active' : status === 'connecting' ? 'connecting' : status === 'error' ? 'error' : ''

  return (
    <header className="site-header">
      <div className="site-header__left">
        <span className="site-header__title">LEGILIMENS</span>
        <span className="site-header__tagline">"Reading what others cannot see."</span>
      </div>

      <div className="site-header__right">
        <span className={`status-dot ${statusClass}`}>
          <span className={`dot ${status === 'connecting' ? 'pulse' : ''}`} />
          {statusLabel}
        </span>

        {wsConnected ? (
          <span style={{ fontSize: 10, color: 'var(--accent-dim)', letterSpacing: '0.06em' }}>
            WS ●
          </span>
        ) : (
          <span style={{ fontSize: 10, color: 'var(--text-secondary)', letterSpacing: '0.06em' }}>
            WS ○
          </span>
        )}

        <button
          className="btn btn-primary"
          onClick={start}
          disabled={isActive || status === 'connecting'}
        >
          ▶ START
        </button>

        <button className="btn btn-danger" onClick={stop} disabled={!isActive}>
          ■ STOP
        </button>

        <button className="btn" onClick={clear} style={{ borderColor: 'var(--border-dim)' }}>
          CLR
        </button>
      </div>
    </header>
  )
}
