import { useStore } from '../store/useStore'

function Item({ label, value, danger, warn }: { label: string; value: string | number; danger?: boolean; warn?: boolean }) {
  return (
    <div className="status-bar__item">
      <span>{label}:</span>
      <span className={`status-bar__val${danger ? ' danger' : warn ? ' warning' : ''}`}>
        {value}
      </span>
    </div>
  )
}

export default function StatusBar() {
  const {
    isProxyActive,
    wsConnected,
    totalEvents,
    totalDatagrams,
    streams,
    avgLatency,
    tamperedCount,
    suspiciousCount,
  } = useStore((s) => s)

  const streamCount = Object.keys(streams).length

  return (
    <div className="status-bar">
      <Item label="PROXY" value={isProxyActive ? 'ACTIVE' : 'INACTIVE'} />
      <Item label="WS" value={wsConnected ? 'CONNECTED' : 'DISCONNECTED'} />
      <Item label="EVENTS" value={totalEvents.toLocaleString()} />
      <Item label="DATAGRAMS" value={totalDatagrams.toLocaleString()} />
      <Item label="STREAMS" value={streamCount} />
      <Item label="AVG LATENCY" value={avgLatency > 0 ? `${avgLatency}ms` : '—'} />
      <Item label="TAMPERED" value={tamperedCount} warn={tamperedCount > 0} />
      <Item label="SUSPICIOUS" value={suspiciousCount} danger={suspiciousCount > 0} />
    </div>
  )
}
