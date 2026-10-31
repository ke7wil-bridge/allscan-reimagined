import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './node-diagnostics.css'
import RuntimeBootstrap from './RuntimeBootstrap.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RuntimeBootstrap />
  </StrictMode>,
  )
