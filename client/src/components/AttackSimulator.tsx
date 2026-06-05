import { useState } from 'react'
import { useStore } from '../store/useStore'

type AttackStatus = 'ready' | 'running' | 'complete' | 'error'

interface AttackState {
  status: AttackStatus
  result: string
}

const INITIAL: AttackState = { status: 'ready', result: '' }

export default function AttackSimulator() {
  const isActive = useStore((s) => s.isProxyActive)
  const harvestedTokens = useStore((s) => s.harvestedTokens)
  const flood = useStore((s) => s.floodAttack)
  const inject = useStore((s) => s.payloadInjection)
  const unauth = useStore((s) => s.unauthorizedStream)

  const [floodState, setFlood] = useState<AttackState>(INITIAL)
  const [harvestState, setHarvest] = useState<AttackState>(INITIAL)
  const [injectState, setInject] = useState<AttackState>(INITIAL)
  const [unauthState, setUnauth] = useState<AttackState>(INITIAL)

  async function runFlood() {
    setFlood({ status: 'running', result: '' })
    const start = Date.now()
    try {
      await flood()
      const elapsed = Date.now() - start
      setFlood({ status: 'complete', result: `1000 datagrams sent in ${elapsed}ms. Server accepted all — no rate limiting.` })
    } catch (e) {
      setFlood({ status: 'error', result: String(e) })
    }
  }

  async function runHarvest() {
    setHarvest({ status: 'running', result: '' })
    const before = harvestedTokens.length
    await new Promise((r) => setTimeout(r, 5000))
    const current = useStore.getState().harvestedTokens
    const gained = current.length - before
    setHarvest({
      status: 'complete',
      result:
        current.length === 0
          ? 'No tokens found. Is the proxy intercepting heartbeats?'
          : `${gained} new token(s) harvested. Total: ${current.length}\n${current.slice(-3).join('\n')}`,
    })
  }

  async function runInject() {
    setInject({ status: 'running', result: '' })
    try {
      await inject()
      await new Promise((r) => setTimeout(r, 800))
      setInject({ status: 'complete', result: 'Malicious payload sent. Server echoed it back unvalidated — prototype pollution + XSS string accepted.' })
    } catch (e) {
      setInject({ status: 'error', result: String(e) })
    }
  }

  async function runUnauth() {
    setUnauth({ status: 'running', result: '' })
    try {
      await unauth()
      await new Promise((r) => setTimeout(r, 600))
      setUnauth({ status: 'complete', result: 'Stream opened without any auth header. Server responded normally — no authentication check exists.' })
    } catch (e) {
      setUnauth({ status: 'error', result: String(e) })
    }
  }

  const attacks = [
    {
      key: 'flood',
      title: '/ FLOOD_ATTACK',
      desc: 'Sends 1000 datagrams in rapid succession. Demonstrates that the server applies no rate limiting or circuit breaking.',
      state: floodState,
      run: runFlood,
    },
    {
      key: 'harvest',
      title: '/ TOKEN_HARVESTER',
      desc: 'Listens for 5s and extracts all session tokens from intercepted heartbeat datagrams sent by the vulnerable server.',
      state: harvestState,
      run: runHarvest,
    },
    {
      key: 'inject',
      title: '/ PAYLOAD_INJECTION',
      desc: 'Sends a crafted payload with prototype pollution and XSS strings. Server echoes it back unvalidated.',
      state: injectState,
      run: runInject,
    },
    {
      key: 'unauth',
      title: '/ UNAUTHORIZED_STREAM',
      desc: 'Opens a bidirectional stream with no auth credentials. Server accepts and responds — no auth check on streams.',
      state: unauthState,
      run: runUnauth,
    },
  ]

  return (
    <div className="panel">
      <div className="panel-header">/ ATTACK SIMULATOR</div>
      <div className="attack-grid">
        {attacks.map((a) => (
          <div className="attack-card" key={a.key}>
            <div className="attack-card__title">{a.title}</div>
            <div className="attack-card__desc">{a.desc}</div>
            <div className={`attack-card__status ${a.state.status}`}>
              {a.state.status === 'ready' && '● READY'}
              {a.state.status === 'running' && '◌ RUNNING…'}
              {a.state.status === 'complete' && '✓ COMPLETE'}
              {a.state.status === 'error' && '✗ ERROR'}
            </div>
            {a.state.result && (
              <div className="attack-result">{a.state.result}</div>
            )}
            <button
              className="btn btn-primary"
              style={{ marginTop: 4 }}
              disabled={!isActive || a.state.status === 'running'}
              onClick={a.run}
            >
              {a.state.status === 'running' ? '…' : 'EXECUTE'}
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
