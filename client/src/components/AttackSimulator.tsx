import { AttackState, useStore } from '../store/useStore'
import { useState } from 'react'
import { Play, Square } from 'lucide-react'

// The 5 server-side QUIC attacks, matching python/attacks/. Params are the per-attack
// defaults from the API contract. These run on the backend (POST /attack) and report
// progress over the WebSocket — no browser WebTransport channel required.
const ATTACKS: { type: string; title: string; desc: string; params: Record<string, unknown> }[] = [
  {
    type: 'flooding',
    title: 'QUIC flooding',
    desc: 'Opens 100 parallel QUIC connections, each completing the handshake then dropping. Burns CPU on connection setup.',
    params: { connections: 100 },
  },
  {
    type: 'loris',
    title: 'QUIC loris',
    desc: 'Repeated cycles of 100 handshake-and-drop connections, 30s apart. Slowloris-style pressure on the QUIC setup path.',
    params: { connections: 100, cycleDelay: 30, cycles: 3 },
  },
  {
    type: 'encapsulation',
    title: 'QUIC encapsulation',
    desc: 'Raw TCP-in-UDP, UDP-in-UDP and fragmented packets. Requires Scapy, Npcap on Windows, and elevated privileges. Unavailable in the desktop bundle.',
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
  const [target, setTarget] = useState('https://127.0.0.1:4434')
  const [pending, setPending] = useState<Record<string, boolean>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})

  async function stopRun(run: AttackState) {
    setPending((p) => ({ ...p, [run.attackType]: true }))
    setErrors((e) => ({ ...e, [run.attackType]: '' }))
    try {
      await stop(run.attackId)
    } catch (error) {
      setErrors((e) => ({ ...e, [run.attackType]: error instanceof Error ? error.message : 'Could not stop this run.' }))
    } finally {
      setPending((p) => ({ ...p, [run.attackType]: false }))
    }
  }

  async function execute(attack: typeof ATTACKS[number]) {
    try {
      if (new URL(target).protocol !== 'https:') throw new Error()
    } catch {
      setErrors((e) => ({ ...e, [attack.type]: 'Enter a valid HTTPS target URL.' }))
      return
    }
    setPending((p) => ({ ...p, [attack.type]: true }))
    setErrors((e) => ({ ...e, [attack.type]: '' }))
    try {
      const id = await launch(attack.type, attack.params, target)
      if (!id) setErrors((e) => ({ ...e, [attack.type]: 'Could not start this run. Check the backend connection.' }))
    } finally {
      setPending((p) => ({ ...p, [attack.type]: false }))
    }
  }

  const runningOf = (type: string) => Object.values(running).find((a) => a.attackType === type)
  const lastOf = (type: string) => completed.find((a) => a.attackType === type)

  return (
    <div className="panel attacks-panel" id="panel-attacks">
      <div className="attack-target">
        <label htmlFor="attack-target">Attack target</label>
        <input id="attack-target" type="url" className="target-bar__input" value={target} onChange={(e) => { setTarget(e.target.value); setErrors({}) }} />
        <span className="muted">{Object.keys(running).length} running</span>
      </div>
      <div className="panel-body attack-body">
        <div className="attack-grid">
          {ATTACKS.map((a) => {
            const run = runningOf(a.type)
            const done = lastOf(a.type)
            return (
              <div className="attack-card" key={a.type}>
                <div className="attack-card__title">{a.title}</div>
                <div className="attack-card__desc">{a.desc}</div>
                <dl className="attack-parameters">
                  {Object.entries(a.params).map(([key, value]) => <div key={key}><dt>{key === 'cycleDelay' ? 'Cycle delay (s)' : key}</dt><dd>{String(value)}</dd></div>)}
                </dl>

                {run ? (
                  <>
                    <div className="attack-card__status running">◌ RUNNING…</div>
                    <ProgressBar progress={run.progress} />
                    <button className="btn btn-danger" style={{ marginTop: 4 }} disabled={pending[a.type]} onClick={() => stopRun(run)}>
                      <Square size={14} /> {pending[a.type] ? 'Stopping...' : 'Stop'}
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
                    <button className="btn btn-primary" style={{ marginTop: 4 }} disabled={pending[a.type]} onClick={() => execute(a)} aria-label={`Run ${a.title}`}>
                      <Play size={14} /> {pending[a.type] ? 'Starting...' : 'Run attack'}
                    </button>
                  </>
                )}
                {errors[a.type] && <div className="action-error" role="alert">{errors[a.type]}</div>}
              </div>
            )
          })}
        </div>

        {completed.length > 0 && (
          <div className="attack-history">
            <h2>Recent runs</h2>
            {completed.slice(0, 8).map((a) => (
              <div key={a.attackId} className="attack-history-row">
                <span style={{ color: a.status === 'complete' ? 'var(--accent)' : a.status === 'stopped' ? 'var(--warning)' : 'var(--danger)', fontWeight: 700 }}>
                  {a.status === 'complete' ? '✓' : a.status === 'stopped' ? '■' : '✗'}
                </span>{' '}
                {a.attackType} — {summary(a)}
              </div>
            ))}
          </div>
        )}
        {completed.length === 0 && <div className="attack-history"><h2>Recent runs</h2><p className="muted">No completed runs.</p></div>}
      </div>
    </div>
  )
}
