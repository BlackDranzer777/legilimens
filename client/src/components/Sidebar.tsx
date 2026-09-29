import { Activity, ArrowLeftRight, Layers, Settings2, Shield, Target } from 'lucide-react'
import { useStore, WorkspaceView } from '../store/useStore'

export const WORKSPACE_LABELS: Record<WorkspaceView, string> = {
  traffic: 'Traffic', intercept: 'Intercept', repeater: 'Repeater',
  attacks: 'Attack simulator', streams: 'Streams', settings: 'Connection settings',
}

const NAV = [
  { key: 'traffic', label: 'Traffic', icon: Activity },
  { key: 'intercept', label: 'Intercept', icon: Shield },
  { key: 'repeater', label: 'Repeater', icon: ArrowLeftRight },
  { key: 'attacks', label: 'Attacks', icon: Target },
  { key: 'streams', label: 'Streams', icon: Layers },
  { key: 'settings', label: 'Settings', icon: Settings2 },
] as const

export default function Sidebar() {
  const active = useStore((s) => s.activeView)
  const navigate = useStore((s) => s.setActiveView)
  const held = useStore((s) => s.pendingIntercepts.length)
  const running = useStore((s) => Object.keys(s.runningAttacks).length)

  return (
    <aside className="sidebar">
      <div className="sidebar__logo" aria-label="Legilimens">L</div>
      <nav className="sidebar__nav" aria-label="Workspace">
        {NAV.map(({ key, label, icon: Icon }) => {
          const count = key === 'intercept' ? held : key === 'attacks' ? running : 0
          return (
            <button key={key} type="button" className={`side-ico ${active === key ? 'active' : ''}`}
              aria-label={WORKSPACE_LABELS[key]} aria-current={active === key ? 'page' : undefined}
              aria-controls={`view-${key}`} title={WORKSPACE_LABELS[key]} onClick={() => navigate(key)}>
              <Icon size={20} aria-hidden="true" />
              <span>{label}</span>
              {count > 0 && <span className="nav-count" aria-label={`${count} ${key === 'intercept' ? 'held' : 'running'}`}>{count}</span>}
            </button>
          )
        })}
      </nav>
    </aside>
  )
}
