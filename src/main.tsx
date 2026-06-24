import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { defaultRuntimeConfig, fetchRuntimeConfig } from './lib/allscanLive'

async function start() {
  let config = defaultRuntimeConfig
  try {
    config = await fetchRuntimeConfig()
  } catch {
    // The generic fallback keeps setup and recovery pages usable.
  }

  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App config={config} />
    </StrictMode>,
  )
}

void start()
