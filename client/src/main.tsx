import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import ControlAccess from './components/ControlAccess'
import OfflineCapture from './components/OfflineCapture'
import './index.css'

function WorkspaceRoot() {
  const [offline, setOffline] = React.useState(false)
  if (offline) return <OfflineCapture onClose={() => setOffline(false)} />
  return <ControlAccess onOpenCapture={() => setOffline(true)}>
    <App onOpenCapture={() => setOffline(true)} />
  </ControlAccess>
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <WorkspaceRoot />
  </React.StrictMode>
)
