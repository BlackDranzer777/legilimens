import { useMemo, useRef, useState } from 'react'
import { ArrowLeft, FolderOpen } from 'lucide-react'
import { CaptureFile, offlineEvents, readCaptureFile } from '../capture'
import TrafficLog from './TrafficLog'

export default function OfflineCapture({ onClose }: { onClose: () => void }) {
  const [capture, setCapture] = useState<CaptureFile | null>(null)
  const [name, setName] = useState('No file selected')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const request = useRef(0)
  const events = useMemo(() => capture ? offlineEvents(capture) : [], [capture])
  const sessions = useMemo(() => new Set(events.map((e) => e.sessionId).filter(Boolean)).size, [events])
  const targets = useMemo(() => [...new Set(events.map((e) => e.target).filter(Boolean))], [events])
  async function open(file: File) {
    const current = ++request.current
    setBusy(true)
    setError('')
    try {
      const next = await readCaptureFile(file)
      if (current !== request.current) return
      setCapture(next)
      setName(file.name)
    } catch (e) {
      if (current === request.current) setError(e instanceof Error ? e.message : 'Import failed')
    } finally { if (current === request.current) setBusy(false) }
  }
  return <main className="offline-capture">
    <header className="offline-heading">
      <div><h1>Legilimens</h1><span className="offline-badge">Offline capture / Read only</span></div>
      <div className="capture-actions">
        <input ref={input} type="file" hidden accept=".json,application/json" aria-label="Capture file"
          onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void open(file) }} />
        <button className="icon-button" title="Import capture file" aria-label="Import capture file" disabled={busy} onClick={() => input.current?.click()}>
          <FolderOpen size={18} aria-hidden="true" />
        </button>
        <button className="btn" onClick={onClose}><ArrowLeft size={16} aria-hidden="true" />Back to live workspace</button>
      </div>
    </header>
    <div className="capture-metadata">
      <strong>{busy ? 'Reading capture...' : name}</strong>
      <span>The backend may still be running. Live capture is not subscribed in this view.</span>
      {error && <span className="action-error" role="alert">{error}</span>}
      {capture && <>
        <span>{events.length} retained events / {sessions} observed sessions</span>
        <span>Observation started: {new Date(capture.observationStartedAt).toISOString()} / Exported: {new Date(capture.exportedAt).toISOString()}</span>
        <span>Targets: {targets.length ? targets.join(', ') : 'not confirmed'}{events.some((e) => !e.target) && targets.length > 0 ? ' / some not confirmed' : ''}</span>
        <span className="capture-warning">Completeness: not confirmed / {capture.loss.evictedEvents} events evicted / {capture.loss.evictedStreams} stream summaries evicted / {capture.loss.omittedStreamChunks} chunks omitted from retained stream summaries</span>
        {capture.loss.warning && <span className="capture-warning">Recorded warning: {capture.loss.warning}</span>}
      </>}
    </div>
    <TrafficLog key={capture?.exportedAt.toString() + name} id="offline-traffic" title="Archived traffic" archivedEvents={events} />
  </main>
}
