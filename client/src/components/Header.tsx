import { useStore } from '../store/useStore'
import { Pause, Play, Trash2, Unplug } from 'lucide-react'

export default function Header() {
  const status = useStore((s) => s.connectionStatus)
  const wsConnected = useStore((s) => s.wsConnected)
  const isActive = useStore((s) => s.isProxyActive)
  const start = useStore((s) => s.startWebTransport)
  const stop = useStore((s) => s.stopWebTransport)
  const disconnect = useStore((s) => s.disconnectProxy)
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
        <span className="site-header__title">Legili<b>mens</b></span>
        <span className="site-header__tagline">Reading what others cannot see.</span>
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
          <Play size={14} /> Start
        </button>

        <button className="btn btn-danger" onClick={stop} disabled={!isActive && status !== 'connecting'}>
          <Pause size={14} /> Pause
        </button>

        <button className="btn btn-danger" onClick={disconnect} title="Hard-cut all sessions — the target must redial">
          <Unplug size={14} /> Disconnect
        </button>

        <button className="icon-button" onClick={clear} aria-label="Clear traffic log" title="Clear traffic log">
          <Trash2 size={16} />
        </button>
      </div>
    </header>
  )
}
