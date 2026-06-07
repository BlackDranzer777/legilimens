import { useEffect, useState } from 'react'

const API = 'http://localhost:4436'

type SaveState = 'idle' | 'saving' | 'saved' | 'error'

export default function TargetConfig() {
  const [hostPort, setHostPort] = useState('127.0.0.1:4434')
  const [certHash, setCertHash] = useState('')
  const [applied, setApplied] = useState('127.0.0.1:4434')
  const [state, setState] = useState<SaveState>('idle')
  const [message, setMessage] = useState('')

  // Load the proxy's current upstream target on mount.
  useEffect(() => {
    fetch(`${API}/target`)
      .then((r) => r.json())
      .then((t) => {
        if (t?.host && t?.port) {
          setHostPort(`${t.host}:${t.port}`)
          setApplied(`${t.host}:${t.port}`)
        }
        if (typeof t?.certHash === 'string') setCertHash(t.certHash)
      })
      .catch(() => {/* proxy not up yet — keep defaults */})
  }, [])

  async function apply() {
    const trimmed = hostPort.trim()
    const idx = trimmed.lastIndexOf(':')
    if (idx < 1) {
      setState('error')
      setMessage('Enter as host:port — e.g. 127.0.0.1:4434')
      return
    }
    const host = trimmed.slice(0, idx)
    const port = Number(trimmed.slice(idx + 1))

    setState('saving')
    setMessage('')
    try {
      const res = await fetch(`${API}/target`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host, port, certHash: certHash.trim() }),
      })
      const data = await res.json()
      if (!res.ok) {
        setState('error')
        setMessage(data?.error ?? 'Failed to set target')
        return
      }
      setApplied(`${data.host}:${data.port}`)
      setState('saved')
      setMessage(
        data.certHash
          ? 'Target set (cert pinned). Reconnect (STOP → START) to use it.'
          : 'Target set (CA-trusted). Reconnect (STOP → START) to use it.'
      )
      setTimeout(() => setState('idle'), 4000)
    } catch (e) {
      setState('error')
      setMessage(e instanceof Error ? e.message : 'Request failed — is the proxy running?')
    }
  }

  return (
    <div className="target-bar">
      <span className="target-bar__label">/ UPSTREAM TARGET</span>

      <input
        className="target-bar__input"
        value={hostPort}
        onChange={(e) => setHostPort(e.target.value)}
        placeholder="host:port"
        spellCheck={false}
        style={{ width: 180 }}
      />

      <input
        className="target-bar__input"
        value={certHash}
        onChange={(e) => setCertHash(e.target.value)}
        placeholder="cert hash (blank = CA-trusted)"
        spellCheck={false}
        style={{ flex: 1, minWidth: 160 }}
      />

      <button className="btn btn-primary" onClick={apply} disabled={state === 'saving'}>
        {state === 'saving' ? '…' : 'APPLY'}
      </button>

      <span className={`target-bar__status ${state}`}>
        {state === 'error' ? `✗ ${message}` : message ? `✓ ${message}` : `active: ${applied}`}
      </span>
    </div>
  )
}
