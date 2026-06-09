import { useEffect, useState } from 'react'

const API = 'http://localhost:4436'

// Rewrites a named JSON field on every datagram/stream chunk passing through the proxy.
// The headline demo: field "score" → value "99999" proves QuaffleArena's server trusts
// whatever value it receives (the "trust-the-client" vulnerability).
export default function TamperConfig() {
  const [field, setField] = useState('score')
  const [value, setValue] = useState('99999')
  const [matchField, setMatchField] = useState('')
  const [matchValue, setMatchValue] = useState('')
  const [enabled, setEnabled] = useState(false)
  const [message, setMessage] = useState('')

  // Load the proxy's current tamper rule on mount.
  useEffect(() => {
    fetch(`${API}/tamper`)
      .then((r) => r.json())
      .then((t) => {
        if (typeof t?.field === 'string') setField(t.field)
        if (t?.value != null) setValue(String(t.value))
        if (typeof t?.matchField === 'string') setMatchField(t.matchField)
        if (t?.matchValue != null) setMatchValue(String(t.matchValue))
        setEnabled(!!t?.enabled)
      })
      .catch(() => {/* proxy not up yet — keep defaults */})
  }, [])

  async function send(nextEnabled: boolean) {
    try {
      const res = await fetch(`${API}/tamper`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled: nextEnabled,
          field: field.trim(),
          value,
          matchField: matchField.trim(),
          matchValue,
        }),
      })
      const t = await res.json()
      setEnabled(!!t.enabled)
      const scope = t.matchField ? `where ${t.matchField}="${t.matchValue}"` : 'on all traffic'
      setMessage(t.enabled ? `rewriting "${t.field}" → ${t.value} ${scope}` : 'tamper off')
    } catch {
      setMessage('failed — is the proxy running?')
    }
  }

  return (
    <div className="target-bar">
      <span className="target-bar__label">/ TAMPER RULE</span>

      <input
        className="target-bar__input"
        value={field}
        onChange={(e) => setField(e.target.value)}
        placeholder="field (e.g. score)"
        spellCheck={false}
        style={{ width: 160 }}
      />

      <span style={{ color: 'var(--text-secondary)' }}>→</span>

      <input
        className="target-bar__input"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="value (e.g. 99999)"
        spellCheck={false}
        style={{ width: 120 }}
      />

      <span style={{ color: 'var(--text-secondary)', fontSize: 11, whiteSpace: 'nowrap' }}>only where</span>

      <input
        className="target-bar__input"
        value={matchField}
        onChange={(e) => setMatchField(e.target.value)}
        placeholder="field (e.g. playerName)"
        spellCheck={false}
        style={{ width: 140 }}
      />

      <span style={{ color: 'var(--text-secondary)' }}>=</span>

      <input
        className="target-bar__input"
        value={matchValue}
        onChange={(e) => setMatchValue(e.target.value)}
        placeholder="(blank = all)"
        spellCheck={false}
        style={{ width: 120 }}
      />

      <button
        className={enabled ? 'btn btn-danger' : 'btn btn-primary'}
        onClick={() => send(!enabled)}
      >
        {enabled ? '■ TAMPER ON' : '▶ ENABLE TAMPER'}
      </button>

      {enabled && (
        <button
          className="btn"
          onClick={() => send(true)}
          style={{ borderColor: 'var(--border-dim)' }}
          title="Re-apply after editing the field or value"
        >
          APPLY
        </button>
      )}

      <span className={`target-bar__status ${enabled ? 'saved' : ''}`}>
        {message || (enabled ? `active: "${field}" → ${value}` : 'inactive')}
      </span>
    </div>
  )
}
