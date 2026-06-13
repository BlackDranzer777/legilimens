import { useEffect } from 'react'
import Header from './components/Header'
import TargetConfig from './components/TargetConfig'
import TamperConfig from './components/TamperConfig'
import ServerInfoBar from './components/ServerInfoBar'
import TrafficLog from './components/TrafficLog'
import LatencyGraph from './components/LatencyGraph'
import AttackSimulator from './components/AttackSimulator'
import StreamInspector from './components/StreamInspector'
import StatusBar from './components/StatusBar'
import { useStore } from './store/useStore'

const isChrome = /Chrome/.test(navigator.userAgent) && /Google Inc/.test(navigator.vendor)

export default function App() {
  const connectWebSocket = useStore((s) => s.connectWebSocket)

  useEffect(() => {
    connectWebSocket()
  }, [connectWebSocket])

  return (
    <div className="app-shell">
      {!isChrome && (
        <div className="browser-warning">
          ⚠ LEGILIMENS REQUIRES CHROME OR EDGE — WebTransport is not supported in this browser.
        </div>
      )}
      <Header />
      <TargetConfig />
      <TamperConfig />
      <ServerInfoBar />
      <main className="main-grid">
        <TrafficLog />
        <LatencyGraph />
        <AttackSimulator />
        <StreamInspector />
      </main>
      <StatusBar />
    </div>
  )
}
