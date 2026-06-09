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

type FilterKey = 'all' | 'normal' | 'suspicious' | 'tampered' | 'connection'

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'all', label: 'ALL' },
  { key: 'suspicious', label: 'SUS' },
  { key: 'tampered', label: 'TAMPERED' },
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

export default function TrafficLog() {
  const events = useStore((s) => s.events)
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
  }, [filtered.length, autoScroll])

  const handleScroll = () => {
    if (!bodyRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = bodyRef.current
    setAutoScroll(scrollHeight - scrollTop - clientHeight < 40)
  }

  return (
    <div className="panel">
      <div className="panel-header">
        / TRAFFIC LOG — {filtered.length}
        {filtered.length !== events.length && <span style={{ color: 'var(--text-secondary)' }}> / {events.length}</span>} events
      </div>

      {/* Filter bar: flag pills + payload search. Filtering also cuts render load. */}
      <div
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
          <button key={f.key} style={pillStyle(flagFilter === f.key)} onClick={() => setFlagFilter(f.key)}>
            {f.label}
          </button>
        ))}
        <input
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
              <th>LAT</th>
              <th>PAYLOAD</th>
              <th>FLAG</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((e) => (
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
