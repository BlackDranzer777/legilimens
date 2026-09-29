import { useEffect, useState } from 'react'
import { ArrowLeft, ArrowRight, Braces, Send } from 'lucide-react'
import { useStore } from '../store/useStore'
import TrafficLog from './TrafficLog'
import { apiFetch } from '../control'

export default function Repeater() {
  const repeater = useStore((s) => s.repeater)
  const status = useStore((s) => s.repeaterStatus)
  const setRepeater = useStore((s) => s.setRepeater)
  const send = useStore((s) => s.replaySend)
  const [formatError, setFormatError] = useState('')
  const activeView = useStore((s) => s.activeView)
  const [sessions, setSessions] = useState<{ id: string; target: string }[]>([])
  const [sessionError, setSessionError] = useState('')
  const sessionIsLive = sessions.some((session) => session.id === repeater.sessionId)
  const sending = status === 'sending…'

  useEffect(() => {
    if (activeView !== 'repeater') return
    let alive = true
    let busy = false
    const poll = async () => {
      if (busy) return
      busy = true
      try {
        const response = await apiFetch('/sessions')
        const data = await response.json()
        if (!Array.isArray(data.items)) throw new Error('Invalid session list')
        if (alive) { setSessions(data.items); setSessionError('') }
      } catch (error) {
        if (alive) {
          setSessions([])
          setSessionError(error instanceof Error ? error.message : 'Could not load sessions.')
        }
      } finally { busy = false }
    }
    setSessions([])
    void poll()
    const timer = setInterval(poll, 2000)
    return () => { alive = false; clearInterval(timer) }
  }, [activeView])

  function format() {
    try {
      setRepeater({ payload: JSON.stringify(JSON.parse(repeater.payload), null, 2) })
      setFormatError('')
    } catch {
      setFormatError('Payload is not valid JSON.')
    }
  }

  return (
    <div className="repeater-workspace">
      <div className="panel repeater-editor" id="panel-repeater">
        <div className="panel-header"><span>Message</span><span className="muted">Datagram</span></div>
        <div className="repeater-session">
          <label htmlFor="replay-session">Session</label>
          <select id="replay-session" value={repeater.sessionId} disabled={sending}
            onChange={(e) => setRepeater({ sessionId: e.target.value })}>
            <option value="">Select a live session</option>
            {repeater.sessionId && !sessionIsLive && <option value={repeater.sessionId} disabled>
              {repeater.sessionId.slice(0, 8)} (unavailable)
            </option>}
            {sessions.map((session) => <option key={session.id} value={session.id}>
              {session.id.slice(0, 8)} - {session.target}
            </option>)}
          </select>
        </div>
        <div className="repeater-toolbar">
          <div className="segmented" role="group" aria-label="Message destination">
            <button aria-pressed={repeater.direction === 'incoming'} onClick={() => setRepeater({ direction: 'incoming' })}>
              <ArrowRight size={15} /> To server
            </button>
            <button aria-pressed={repeater.direction === 'outgoing'} onClick={() => setRepeater({ direction: 'outgoing' })}>
              <ArrowLeft size={15} /> To client
            </button>
          </div>
          <button className="icon-button" aria-label="Format JSON" title="Format JSON" onClick={format} disabled={!repeater.payload.trim()}><Braces size={18} /></button>
        </div>
        <textarea className="repeater-payload" aria-label="Repeater payload" value={repeater.payload}
          onChange={(e) => { setRepeater({ payload: e.target.value }); setFormatError('') }}
          spellCheck={false} placeholder="Message payload" />
        <div className="repeater-footer">
          <div className="repeater-feedback" role="status">{sessionError || formatError || status || `${new TextEncoder().encode(repeater.payload).length} bytes`}</div>
          <button className="btn btn-primary" onClick={send} disabled={!sessionIsLive || sending || repeater.messageType !== 'datagram'}>
            <Send size={15} /> {sending ? 'Sending...' : 'Send'}
          </button>
        </div>
      </div>
      <TrafficLog id="repeater-traffic" title="Traffic" />
    </div>
  )
}
