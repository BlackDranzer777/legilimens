import { useEffect, useRef, useState } from 'react'
import { TrafficEvent, useStore } from '../store/useStore'

function formatTime(ts: number) {
  const d = new Date(ts)
  return (
    d.getHours().toString().padStart(2, '0') +
    ':' +
    d.getMinutes().toString().padStart(2, '0') +
    ':' +
    d.getSeconds().toString().padStart(2, '0') +
    '.' +
    d.getMilliseconds().toString().padStart(3, '0')
  )
}

function formatSize(bytes: number) {
  if (bytes === 0) return '—'
  return bytes + 'B'
}

function formatPayload(p: string) {
  try {
    return JSON.stringify(JSON.parse(p), null, 0)
  } catch {
    return p
  }
}

function FlagBadge({ flag, type }: { flag?: string; type: string }) {
  if (type === 'connection') return <span className="badge badge-connection">CONN</span>
  if (flag === 'suspicious') return <span className="badge badge-suspicious">SUSPICIOUS</span>
  if (flag === 'tampered') return <span className="badge badge-tampered">TAMPERED</span>
  return <span className="badge badge-normal">NORMAL</span>
}

function EventRow({ event }: { event: TrafficEvent }) {
  const [expanded, setExpanded] = useState(false)

  let formatted = ''
  try {
    formatted = JSON.stringify(JSON.parse(event.payload), null, 2)
  } catch {
    formatted = event.payload
  }

  return (
    <>
      <tr
        className={`traffic-row ${expanded ? 'expanded' : ''}`}
        onClick={() => setExpanded((v) => !v)}
      >
        <td style={{ color: 'var(--text-secondary)', fontSize: 10 }}>{formatTime(event.timestamp)}</td>
        <td>
          {event.direction === 'incoming' ? (
            <span className="dir-in">→</span>
          ) : (
            <span className="dir-out">←</span>
          )}
        </td>
        <td style={{ color: 'var(--text-primary)', fontWeight: 600, fontSize: 10, letterSpacing: '0.06em' }}>
          {event.type === 'stream' && event.streamId
            ? `STREAM #${event.streamId}`
            : event.type.toUpperCase()}
        </td>
        <td style={{ color: 'var(--text-secondary)', fontSize: 10 }}>{formatSize(event.rawSize)}</td>
        <td style={{ color: 'var(--text-secondary)', fontSize: 10 }}>
          {event.latency > 0 ? event.latency + 'ms' : '—'}
        </td>
        <td className="payload-cell">{formatPayload(event.payload)}</td>
        <td>
          <FlagBadge flag={event.flag} type={event.type} />
        </td>
      </tr>
      {expanded && (
        <tr className="traffic-expand">
          <td colSpan={7}>
            <pre>{formatted}</pre>
          </td>
        </tr>
      )}
    </>
  )
}

export default function TrafficLog() {
  const events = useStore((s) => s.events)
  const bottomRef = useRef<HTMLTableRowElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const [autoScroll, setAutoScroll] = useState(true)

  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ block: 'nearest' })
    }
  }, [events.length, autoScroll])

  const handleScroll = () => {
    if (!bodyRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = bodyRef.current
    setAutoScroll(scrollHeight - scrollTop - clientHeight < 40)
  }

  return (
    <div className="panel">
      <div className="panel-header">/ TRAFFIC LOG — {events.length} events</div>
      <div className="panel-body" ref={bodyRef} onScroll={handleScroll}>
        <table className="traffic-table">
          <thead>
            <tr>
              <th>TIME</th>
              <th>DIR</th>
              <th>TYPE</th>
              <th>SIZE</th>
              <th>LAT</th>
              <th>PAYLOAD</th>
              <th>FLAG</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <EventRow key={e.id} event={e} />
            ))}
            <tr ref={bottomRef} />
          </tbody>
        </table>
        {events.length === 0 && (
          <div
            style={{
              padding: '32px 16px',
              color: 'var(--text-secondary)',
              fontSize: 11,
              textAlign: 'center',
              letterSpacing: '0.08em',
            }}
          >
            NO TRAFFIC INTERCEPTED — START THE PROXY TO BEGIN
          </div>
        )}
      </div>
    </div>
  )
}
