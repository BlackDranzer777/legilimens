import { AttackState, useStore } from '../store/useStore'

// The 5 server-side QUIC attacks, matching python/attacks/. Params are the per-attack
// defaults from the API contract. These run on the backend (POST /attack) and report
// progress over the WebSocket — no browser WebTransport channel required.
const ATTACKS: { type: string; title: string; desc: string; params: Record<string, unknown> }[] = [
  {
    type: 'flooding',
    title: '/ QUIC-FLOODING',
    desc: 'Opens 100 parallel QUIC connections, each completing the handshake then dropping. Burns CPU on connection setup.',
    params: { connections: 100 },
  },
  {
    type: 'loris',
    title: '/ QUIC-LORIS',
    desc: 'Repeated cycles of 100 handshake-and-drop connections, 30s apart. Slowloris-style pressure on the QUIC setup path.',
    params: { connections: 100, cycleDelay: 30, cycles: 3 },
  },
  {
    type: 'fuzz',
    title: '/ QUIC-FUZZ',
    desc: 'Sends 1000 mutated QUIC packets over raw UDP. (Unprotected Initials — a compliant server discards them, so 0 responses is expected.)',
    params: { packets: 1000, mutationStrategy: 'random' },
  },
  {
    type: 'out_of_joint',
    title: '/ QUIC-OUT-OF-JOINT',
    desc: 'Injects forbidden/out-of-order frames (STREAM in Initial, CRYPTO overlap, post-handshake inject).',
    params: { probes: 4 },
  },
  {
    type: 'encapsulation',
    title: '/ QUIC-ENCAPSULATION',
    desc: 'Scapy-crafted TCP-in-UDP / UDP-in-UDP / fragmented packets. Requires root — fails with a clear error otherwise.',
    params: { packets: 100 },
  },
]

function pct(p?: AttackState['progress']): number {
  if (!p || p.total <= 0) return 0
  return Math.min(100, Math.round((p.current / p.total) * 100))
}

function summary(a: AttackState): string {
  if (a.status === 'failed') return a.error ?? 'failed'
  if (a.status === 'stopped') return 'stopped by user'
  const r = (a.result ?? {}) as Record<string, any>
  switch (a.attackType) {
    case 'flooding':
      return `${r.handshakesCompleted ?? '?'} handshakes, ${r.failed ?? '?'} failed, ${r.duration ?? '?'}s`
    case 'loris':
      return `${r.cyclesCompleted ?? '?'} cycles, ${r.totalConnections ?? '?'} connections`
    case 'fuzz':
      return `${r.packetsSent ?? '?'} packets sent, ${r.responsesObserved ?? 0} responses observed`
    case 'out_of_joint':
      return `${r.probesAttempted ?? '?'} probes, ${r.probesResponded ?? 0} responded`
    case 'encapsulation':
      return `${r.packetsSent ?? '?'} packets sent`
    default:
      return JSON.stringify(r)
  }
}

function ProgressBar({ progress }: { progress?: AttackState['progress'] }) {
  return (
    <div>
      <div style={{ height: 6, border: '1px solid var(--border-dim)', background: 'var(--bg-card)' }}>
        <div style={{ height: '100%', width: `${pct(progress)}%`, background: 'var(--accent)', transition: 'width 0.2s' }} />
      </div>
      <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 3, lineHeight: 1.3 }}>
        {progress?.message ?? 'starting…'}
      </div>
    </div>
  )
}

export default function AttackSimulator() {
  const running = useStore((s) => s.runningAttacks)
  const completed = useStore((s) => s.completedAttacks)
  const launch = useStore((s) => s.launchAttack)
  const stop = useStore((s) => s.stopAttack)

  const runningOf = (type: string) => Object.values(running).find((a) => a.attackType === type)
  const lastOf = (type: string) => completed.find((a) => a.attackType === type)

  return (
    <div className="panel">
      <div className="panel-header">/ ATTACK SIMULATOR</div>
      <div className="panel-body" style={{ display: 'flex', flexDirection: 'column' }}>
        <div className="attack-grid">
          {ATTACKS.map((a) => {
            const run = runningOf(a.type)
            const done = lastOf(a.type)
            return (
              <div className="attack-card" key={a.type}>
                <div className="attack-card__title">{a.title}</div>
                <div className="attack-card__desc">{a.desc}</div>

                {run ? (
                  <>
                    <div className="attack-card__status running">◌ RUNNING…</div>
                    <ProgressBar progress={run.progress} />
                    <button className="btn btn-danger" style={{ marginTop: 4 }} onClick={() => stop(run.attackId)}>
                      ■ STOP
                    </button>
                  </>
                ) : (
                  <>
                    <div
                      className={`attack-card__status ${
                        done ? (done.status === 'complete' ? 'complete' : done.status === 'stopped' ? 'ready' : 'error') : 'ready'
                      }`}
                    >
                      {!done && '● READY'}
                      {done?.status === 'complete' && '✓ COMPLETE'}
                      {done?.status === 'failed' && '✗ FAILED'}
                      {done?.status === 'stopped' && '■ STOPPED'}
                    </div>
                    {done && <div className="attack-result">{summary(done)}</div>}
                    <button className="btn btn-primary" style={{ marginTop: 4 }} onClick={() => launch(a.type, a.params)}>
                      EXECUTE
                    </button>
                  </>
                )}
              </div>
            )
          })}
        </div>

        {completed.length > 0 && (
          <div style={{ borderTop: '1px solid var(--border-dim)', padding: '6px 12px', flexShrink: 0, maxHeight: 96, overflowY: 'auto' }}>
            <div style={{ fontSize: 10, color: 'var(--text-secondary)', letterSpacing: '0.08em', marginBottom: 4 }}>
              RECENT
            </div>
            {completed.slice(0, 8).map((a) => (
              <div key={a.attackId} style={{ fontSize: 10, color: 'var(--text-secondary)', padding: '1px 0', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                <span style={{ color: a.status === 'complete' ? 'var(--accent)' : a.status === 'stopped' ? 'var(--warning)' : 'var(--danger)', fontWeight: 700 }}>
                  {a.status === 'complete' ? '✓' : a.status === 'stopped' ? '■' : '✗'}
                </span>{' '}
                {a.attackType} — {summary(a)}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
