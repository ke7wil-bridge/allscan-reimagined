import { StrictMode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ASR_BUILD_VERSION_LABEL, defaultRuntimeConfig, fetchRuntimeConfig, type RuntimeConfig } from './lib/allscanLive'

function RuntimeBootstrap() {
  const [config, setConfig] = useState<RuntimeConfig>(defaultRuntimeConfig)
  const [attempted, setAttempted] = useState(false)
  const [verified, setVerified] = useState(false)
  const [installedVersion, setInstalledVersion] = useState('')
  const inFlight = useRef(false)
  const lastCheck = useRef(0)

  const checkRuntime = useCallback(async (force = false) => {
    if (inFlight.current || (!force && Date.now() - lastCheck.current < 15_000)) return
    inFlight.current = true
    lastCheck.current = Date.now()
    try {
      const next = await fetchRuntimeConfig()
      setInstalledVersion(next.versionLabel)
      if (!verified) {
        setConfig(next)
        setVerified(true)
      }
    } catch {
      // Keep recovery/setup usable and retry when the tab becomes active.
    } finally {
      setAttempted(true)
      inFlight.current = false
    }
  }, [verified])

  useEffect(() => {
    void checkRuntime(true)
    const retry = window.setInterval(() => { if (!verified) void checkRuntime(true) }, 5_000)
    const onFocus = () => { void checkRuntime() }
    const onVisibility = () => { if (document.visibilityState === 'visible') void checkRuntime() }
    window.addEventListener('focus', onFocus)
    window.addEventListener('pageshow', onFocus)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      window.clearInterval(retry)
      window.removeEventListener('focus', onFocus)
      window.removeEventListener('pageshow', onFocus)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [checkRuntime, verified])

  const displayConfig = useMemo(() => ({
    ...config,
    versionLabel: verified ? ASR_BUILD_VERSION_LABEL : defaultRuntimeConfig.versionLabel,
  }), [config, verified])
  const refreshRequired = verified && installedVersion !== '' && installedVersion !== ASR_BUILD_VERSION_LABEL

  function refreshPage() {
    const url = new URL(window.location.href)
    url.searchParams.set('asr-refresh', String(Date.now()))
    window.location.replace(url.toString())
  }

  return <>
    {refreshRequired ? <div className="allscan-version-refresh" role="alert">
      <span>This tab is running {ASR_BUILD_VERSION_LABEL}; the node has {installedVersion}.</span>
      <button type="button" onClick={refreshPage}>Refresh now</button>
    </div> : null}
    {attempted ? <App key={`${displayConfig.node}:${displayConfig.versionLabel}`} config={displayConfig} /> : null}
  </>
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RuntimeBootstrap />
  </StrictMode>,
  )
