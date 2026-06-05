import { useState } from 'react'
import { StreamSession, useStore } from '../store/useStore'

function elapsed(session: StreamSession) {
  const end = session.closedAt ?? Date.now()
  const ms = end - session.openedAt
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function StreamItem({ session }: { session: StreamSession }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <>
      <div className={`stream-item ${expanded ? 'expanded' : ''}`} onClick={() => setExpanded((v) => !v)}>
        <span style={{ color: 'var(--accent)', fontWeight: 700, marginRight: 8 }}>
          #{session.id}
        </span>
        <span
          style={{
            color: session.status === 'open' ? 'var(--accent)' : 'var(--text-secondary)',
            marginRight: 8,
            fontSize: 10,
            border: '1px solid',
            borderColor: session.status === 'open' ? 'var(--accent)' : 'var(--border-dim)',
            padding: '1px 4px',
            letterSpacing: '0.06em',
          }}
        >
          {session.status.toUpperCase()}
        </span>
        <span style={{ color: 'var(--text-secondary)', fontSize: 10 }}>
          ↑ {session.sentChunks} chunks &nbsp;↓ {session.receivedChunks} chunks
        </span>
        <span style={{ color: 'var(--text-secondary)', fontSize: 10, float: 'right' }}>
          {session.status === 'closed' ? `duration ${elapsed(session)}` : `open ${elapsed(session)}`}
        </span>
      </div>
      {expanded && (
        <div className="stream-chunks">
          {session.chunks.length === 0 ? (
            <div style={{ color: 'var(--text-secondary)', fontSize: 10 }}>NO CHUNKS YET</div>
          ) : (
            session.chunks.map((c, i) => (
              <div className="stream-chunk" key={i}>
                <span className="chunk-dir">{c.direction === 'sent' ? '→' : '←'}</span>
                <span className="chunk-payload">{c.payload}</span>
              </div>
            ))
          )}
        </div>
      )}
    </>
  )
}

export default function StreamInspector() {
  const streams = useStore((s) => s.streams)
  const sessionList = Object.values(streams).sort((a, b) => b.openedAt - a.openedAt)

  return (
    <div className="panel">
      <div className="panel-header">/ STREAM INSPECTOR — {sessionList.length} streams</div>
      <div className="panel-body">
        {sessionList.length === 0 ? (
          <div
            style={{
              padding: '32px 16px',
              color: 'var(--text-secondary)',
              fontSize: 11,
              textAlign: 'center',
              letterSpacing: '0.08em',
            }}
          >
            NO STREAMS OPENED YET
          </div>
        ) : (
          sessionList.map((s) => <StreamItem key={s.id} session={s} />)
        )}
      </div>
    </div>
  )
}
