import { useEffect, useRef, useState } from 'react'

import { apiFetch } from '../control'
import { useStore } from '../store/useStore'

type SaveState = 'idle' | 'saving' | 'saved' | 'error'

export default function TargetConfig() {
  const wsConnected = useStore((s) => s.wsConnected)
  const dirty = useRef(false)
  const editVersion = useRef(0)
  const connectionVersion = useRef(0)
  const [hostPort, setHostPort] = useState('127.0.0.1:4434')
  const [certHash, setCertHash] = useState('')
  const [applied, setApplied] = useState('not confirmed')
  const [state, setState] = useState<SaveState>('idle')
  const [message, setMessage] = useState('')

  // Refresh server truth after recovery, but retain unsaved target edits.
  useEffect(() => {
    let alive = true
    connectionVersion.current += 1
    setApplied('not confirmed')
    setState('idle')
    setMessage('')
    if (!wsConnected) return
    apiFetch('/target')
      .then((r) => r.json())
      .then((t) => {
        if (!alive) return
        if (t?.host && t?.port) {
          setApplied(`${t.host}:${t.port}`)
          if (!dirty.current) setHostPort(`${t.host}:${t.port}`)
        }
        if (!dirty.current && typeof t?.certHash === 'string') setCertHash(t.certHash)
      })
      .catch(() => {/* proxy not up yet — keep defaults */})
    return () => { alive = false; connectionVersion.current += 1 }
  }, [wsConnected])

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
    const savedVersion = editVersion.current
    const savedConnection = connectionVersion.current
    try {
      const res = await apiFetch('/target', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host, port, certHash: certHash.trim() }),
      })
      const data = await res.json()
      if (savedConnection !== connectionVersion.current) return
      if (!res.ok) {
        setState('error')
        setMessage(data?.error ?? 'Failed to set target')
        return
      }
      setApplied(`${data.host}:${data.port}`)
      if (savedVersion === editVersion.current) dirty.current = false
      setState('saved')
      setMessage(
        data.certHash
          ? 'Certificate pin required. Disconnect and reconnect clients to use this target.'
          : 'CA verification required. Disconnect and reconnect clients to use this target.'
      )
      setTimeout(() => {
        if (savedConnection === connectionVersion.current) setState('idle')
      }, 4000)
    } catch (e) {
      if (savedConnection !== connectionVersion.current) return
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
        aria-label="Upstream host and port"
        onChange={(e) => { dirty.current = true; editVersion.current += 1; setHostPort(e.target.value) }}
        placeholder="host:port"
        spellCheck={false}
        style={{ width: 180 }}
      />

      <input
        className="target-bar__input"
        value={certHash}
        aria-label="Upstream certificate hash"
        onChange={(e) => { dirty.current = true; editVersion.current += 1; setCertHash(e.target.value) }}
        placeholder="cert hash (blank = CA-trusted)"
        spellCheck={false}
        style={{ flex: 1, minWidth: 160 }}
      />

      <button className="btn btn-primary" onClick={apply} disabled={!wsConnected || state === 'saving'}>
        {state === 'saving' ? '…' : 'APPLY'}
      </button>

      <span className={`target-bar__status ${state}`}>
        {state === 'error' ? `✗ ${message}` : message ? `✓ ${message}` : `active: ${applied}`}
      </span>
    </div>
  )
}
