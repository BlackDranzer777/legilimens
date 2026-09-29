import { useState } from 'react'
import { Download, FolderOpen } from 'lucide-react'
import { exportCapture } from '../capture'
import { useStore } from '../store/useStore'

export default function CaptureActions({ onOpenCapture }: { onOpenCapture: () => void }) {
  const [error, setError] = useState('')
  function download() {
    if (!window.confirm('Export retained traffic? Raw payloads may contain passwords, tokens, or other sensitive data. Store and share this file carefully.')) return
    let url: string | undefined
    try {
      const text = exportCapture(useStore.getState())
      url = URL.createObjectURL(new Blob([text], { type: 'application/json' }))
      const link = document.createElement('a')
      link.href = url
      link.download = `legilimens-${new Date().toISOString().replace(/[:.]/g, '-')}.capture.json`
      document.body.appendChild(link)
      link.click()
      link.remove()
      setError('')
    } catch (e) { setError(e instanceof Error ? e.message : 'Export failed') }
    finally { if (url) { const release = url; setTimeout(() => URL.revokeObjectURL(release), 1000) } }
  }
  return <div className="capture-actions">
    <button type="button" className="icon-button" title="Export all retained events" aria-label="Export all retained events" onClick={download}>
      <Download size={18} aria-hidden="true" />
    </button>
    <button type="button" className="icon-button" title="Open offline capture" aria-label="Open offline capture" onClick={onOpenCapture}>
      <FolderOpen size={18} aria-hidden="true" />
    </button>
    {error && <span className="action-error" role="alert">{error}</span>}
  </div>
}
