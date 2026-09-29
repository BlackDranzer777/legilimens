import { useEffect, useRef, useState } from 'react'
import { TrafficEvent, useStore } from '../store/useStore'
import CaptureActions from './CaptureActions'

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
  if (flag === 'replay') return <span className="badge badge-replay">REPLAY</span>
  if (flag === 'suspicious') return <span className="badge badge-suspicious">SUSPICIOUS</span>
  if (flag === 'tampered') return <span className="badge badge-tampered">TAMPERED</span>
  return <span className="badge badge-normal">NORMAL</span>
}

function EventRow({ event, offline = false }: { event: TrafficEvent; offline?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const sendToRepeater = useStore((s) => s.sendToRepeater)

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
        tabIndex={0}
        aria-expanded={expanded}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpanded((v) => !v) } }}
      >
        <td title={new Date(event.timestamp).toISOString()} style={{ color: 'var(--text-secondary)', fontSize: 10 }}>{formatTime(event.timestamp)}</td>
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
        <td className="payload-cell">{event.payloadPreview ?? formatPayload(event.payload).slice(0, 300)}</td>
        <td>
          <FlagBadge flag={event.flag} type={event.type} />
        </td>
      </tr>
      {expanded && (
        <tr className="traffic-expand">
          <td colSpan={6}>
            {event.sessionId && <div>Session: {event.sessionId}</div>}
            <div>Target: {event.target || 'not confirmed'}</div>
            {offline && <div>Payload encoding: {event.payloadEncoding || 'not confirmed'}</div>}
            {event.payloadEncoding === 'base64' && <div>Binary payload (Base64)</div>}
            <pre>{offline ? event.payload : formatted}</pre>
            {!offline && event.type !== 'connection' && (
              <button
                disabled={!event.replayable || !event.sessionId || event.payloadEncoding === 'base64' || event.type !== 'datagram'}
                title={!event.replayable || !event.sessionId || event.payloadEncoding === 'base64' || event.type !== 'datagram' ? 'Replay requires a captured text datagram with session identity' : 'Send to Repeater'}
                className="btn"
                style={{ margin: '4px 16px 8px', borderColor: 'var(--accent)', color: 'var(--accent)' }}
                onClick={(e) => {
                  e.stopPropagation()
                  sendToRepeater(event)
                }}
              >
                ⟳ SEND TO REPEATER
              </button>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

type FilterKey = 'all' | 'normal' | 'suspicious' | 'tampered' | 'replay' | 'connection'

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'all', label: 'ALL' },
  { key: 'suspicious', label: 'SUS' },
  { key: 'tampered', label: 'TAMPERED' },
  { key: 'replay', label: 'REPLAY' },
  { key: 'normal', label: 'NORMAL' },
  { key: 'connection', label: 'CONN' },
]

function pillStyle(active: boolean): React.CSSProperties {
  return {
    fontSize: 10,
    letterSpacing: '0.06em',
    padding: '3px 9px',
    border: `1px solid ${active ? 'var(--accent)' : 'var(--border-dim)'}`,
    background: active ? 'var(--accent)' : 'transparent',
    color: active ? '#090909' : 'var(--text-secondary)',
    cursor: 'pointer',
    fontFamily: 'inherit',
    fontWeight: active ? 700 : 400,
  }
}

function matchesFlag(e: TrafficEvent, filter: FilterKey) {
  if (filter === 'all') return true
  if (filter === 'connection') return e.type === 'connection'
  return e.flag === filter
}

export default function TrafficLog({ id = 'panel-traffic', title = 'Traffic log', archivedEvents, onOpenCapture }: {
  id?: string; title?: string; archivedEvents?: TrafficEvent[]; onOpenCapture?: () => void
}) {
  const liveEvents = useStore((s) => s.events)
  const events = archivedEvents ?? liveEvents
  const offline = archivedEvents !== undefined
  const bottomRef = useRef<HTMLTableRowElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const [autoScroll, setAutoScroll] = useState(true)
  const [flagFilter, setFlagFilter] = useState<FilterKey>('all')
  const [search, setSearch] = useState('')

  const q = search.trim().toLowerCase()
  const filtered = events.filter(
    (e) => matchesFlag(e, flagFilter) && (q === '' || e.payload.toLowerCase().includes(q))
  )

  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ block: 'nearest' })
    }
  }, [events, flagFilter, search, autoScroll])

  const handleScroll = () => {
    if (!bodyRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = bodyRef.current
    setAutoScroll(scrollHeight - scrollTop - clientHeight < 40)
  }

  return (
    <div className="panel traffic-panel" id={id}>
      <div className="panel-header">
        <span>{title} — {filtered.length}
        {filtered.length !== events.length && <span style={{ color: 'var(--text-secondary)' }}> / {events.length}</span>} events
        </span>
        {onOpenCapture && !offline && <CaptureActions onOpenCapture={onOpenCapture} />}
      </div>

      {/* Filter bar: flag pills + payload search. Filtering also cuts render load. */}
      <div
        className="traffic-filters"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '6px 12px',
          borderBottom: '1px solid var(--border-dim)',
          background: 'var(--bg-secondary)',
          flexShrink: 0,
        }}
      >
        {FILTERS.map((f) => (
          <button key={f.key} aria-pressed={flagFilter === f.key} style={pillStyle(flagFilter === f.key)} onClick={() => setFlagFilter(f.key)}>
            {f.label}
          </button>
        ))}
        <input
          aria-label="Search traffic payloads"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="search payload (e.g. token, score)…"
          spellCheck={false}
          style={{
            marginLeft: 'auto',
            width: 220,
            padding: '4px 8px',
            fontSize: 10,
            background: 'var(--bg-primary)',
            border: '1px solid var(--border-dim)',
            color: 'var(--text-primary)',
            outline: 'none',
            fontFamily: 'inherit',
          }}
        />
      </div>

      <div className="panel-body" ref={bodyRef} onScroll={handleScroll}>
        <table className="traffic-table">
          <thead>
            <tr>
              <th>TIME</th>
              <th>DIR</th>
              <th>TYPE</th>
              <th>SIZE</th>
              <th>PAYLOAD</th>
              <th>FLAG</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((e) => (
              <EventRow key={e.id} event={e} offline={offline} />
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
            {offline ? 'NO ARCHIVED EVENTS' : 'NO TRAFFIC INTERCEPTED — START THE PROXY TO BEGIN'}
          </div>
        )}
        {events.length > 0 && filtered.length === 0 && (
          <div
            style={{
              padding: '32px 16px',
              color: 'var(--text-secondary)',
              fontSize: 11,
              textAlign: 'center',
              letterSpacing: '0.08em',
            }}
          >
            NO EVENTS MATCH THIS FILTER
          </div>
        )}
      </div>
    </div>
  )
}
