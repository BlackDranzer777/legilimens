import { useEffect } from 'react'
import Sidebar, { WORKSPACE_LABELS } from './components/Sidebar'
import Header from './components/Header'
import TargetConfig from './components/TargetConfig'
import TamperConfig from './components/TamperConfig'
import InterceptPanel from './components/InterceptPanel'
import ServerInfoBar from './components/ServerInfoBar'
import TrafficLog from './components/TrafficLog'
import Repeater from './components/Repeater'
import AttackSimulator from './components/AttackSimulator'
import StreamInspector from './components/StreamInspector'
import StatusBar from './components/StatusBar'
import { useStore } from './store/useStore'

const supportsWebTransport = 'WebTransport' in window

export default function App({ onOpenCapture }: { onOpenCapture: () => void }) {
  const connectWebSocket = useStore((s) => s.connectWebSocket)
  const disconnectWebSocket = useStore((s) => s.disconnectWebSocket)
  const activeView = useStore((s) => s.activeView)
  const held = useStore((s) => s.pendingIntercepts.length)
  const interceptEnabled = useStore((s) => s.manualIntercept.enabled)
  const tamperEnabled = useStore((s) => s.tamperEnabled)
  const navigate = useStore((s) => s.setActiveView)

  useEffect(() => {
    connectWebSocket()
    return disconnectWebSocket
  }, [connectWebSocket, disconnectWebSocket])

  return (
    <div className="app-shell">
      <Sidebar />
      <div className="content">
        <Header />
        {!supportsWebTransport && <div className="browser-warning">WebTransport is unavailable in this browser.</div>}
        <div className="workspace-heading">
          <h1>{WORKSPACE_LABELS[activeView]}</h1>
          <div className="workspace-indicators">
            {tamperEnabled && <button className="btn intercept-indicator" onClick={() => navigate('intercept')}>Tamper on</button>}
            {(interceptEnabled || held > 0) && <button className="btn intercept-indicator" onClick={() => navigate('intercept')}>
              {held > 0 ? `${held} messages held` : 'Intercept armed'}
            </button>}
          </div>
        </div>
        <main className="workspace">
          {/* Keep views mounted so navigation preserves filters, drafts, and selection. */}
          <section id="view-traffic" aria-label="Traffic workspace" hidden={activeView !== 'traffic'} className="workspace-view">
            <TrafficLog onOpenCapture={onOpenCapture} />
          </section>
          <section id="view-intercept" aria-label="Intercept workspace" hidden={activeView !== 'intercept'} className="workspace-view configuration-view">
            <TamperConfig />
            <InterceptPanel />
          </section>
          <section id="view-repeater" aria-label="Repeater workspace" hidden={activeView !== 'repeater'} className="workspace-view">
            <Repeater />
          </section>
          <section id="view-attacks" aria-label="Attack simulator workspace" hidden={activeView !== 'attacks'} className="workspace-view">
            <AttackSimulator />
          </section>
          <section id="view-streams" aria-label="Streams workspace" hidden={activeView !== 'streams'} className="workspace-view">
            <StreamInspector />
          </section>
          <section id="view-settings" aria-label="Connection settings workspace" hidden={activeView !== 'settings'} className="workspace-view configuration-view">
            <TargetConfig />
            <ServerInfoBar />
          </section>
        </main>
        <StatusBar />
      </div>
    </div>
  )
}
