import { FormEvent, ReactNode, useEffect, useState } from 'react'
import { FolderOpen, LockKeyhole } from 'lucide-react'
import { AUTH_REQUIRED, authenticateControl, getControlToken, setControlToken } from '../control'

export default function ControlAccess({ children, onOpenCapture }: { children: ReactNode; onOpenCapture: () => void }) {
  const [ready, setReady] = useState(false)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(!!getControlToken())
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    const expired = () => {
      setReady(false)
      setValue('')
      setError('Access expired or was rejected. Enter the current backend token.')
    }
    window.addEventListener(AUTH_REQUIRED, expired)
    if (getControlToken()) {
      authenticateControl().then(() => { if (alive) setReady(true) })
        .catch((e) => { if (alive) setError(e.message) })
        .finally(() => { if (alive) setBusy(false) })
    }
    return () => { alive = false; window.removeEventListener(AUTH_REQUIRED, expired) }
  }, [])

  async function connect(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setControlToken(value)
    try {
      await authenticateControl()
      setValue('')
      setReady(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not connect to the backend')
    } finally { setBusy(false) }
  }

  if (ready) return <>{children}</>
  return <main className="control-access">
    <form className="control-access__form" onSubmit={connect}>
      <LockKeyhole size={28} aria-hidden="true" />
      <h1>Legilimens</h1>
      <h2>Local backend access</h2>
      <label htmlFor="control-token">Backend access token</label>
      <input id="control-token" type="password" autoComplete="off" spellCheck={false}
        value={value} onChange={(e) => setValue(e.target.value)} required disabled={busy} />
      {error && <p role="alert">{error}</p>}
      <button className="btn btn-primary" disabled={busy || !value.trim()}>
        <LockKeyhole size={16} aria-hidden="true" /> {busy ? 'Connecting...' : 'Connect'}
      </button>
      <button className="btn" type="button" onClick={onOpenCapture}>
        <FolderOpen size={16} aria-hidden="true" /> Open offline capture
      </button>
    </form>
  </main>
}
