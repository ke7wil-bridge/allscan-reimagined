(() => {
  const hubsElement = document.querySelector('#hubs')
  const errorElement = document.querySelector('#error')
  const summaryElement = document.querySelector('#summary')
  const updatedElement = document.querySelector('#updated')
  const refreshButton = document.querySelector('#refresh')
  const talkerCardsElement = document.querySelector('#talker-cards')
  const favoritesElement = document.querySelector('#favorites')
  const favoritesToggle = document.querySelector('#favorites-toggle')
  const themeSelect = document.querySelector('#theme')
  const moduleRoot = document.querySelector('#modules')
  const favoritesContent = document.querySelector('#favorites-content')
  const favoritesHub = document.querySelector('#favorites-hub')
  const bridgeGrid = document.querySelector('#bridge-grid')
  const sortState = new Map()
  let draggedModule = null
  let currentHubs = []
  let favoritesLoaded = false
  let refreshInFlight = false
  let lastRenderedGeneration = 0

  const element = (tag, className, value) => {
    const node = document.createElement(tag)
    if (className) node.className = className
    if (value != null) node.textContent = value
    return node
  }

  const columns = [
    ['node', 'Node'], ['label', 'Node Info'], ['received', 'Received'],
    ['direction', 'Dir'], ['connected', 'Connected'], ['mode', 'Mode'], ['actions', 'Actions'],
  ]

  function duration(value) {
    const match = String(value || '').match(/^(?:(\d+)d\s+)?(\d+):(\d{2}):(\d{2})$/)
    return match ? (Number(match[1] || 0) * 86400) + (Number(match[2]) * 3600) + (Number(match[3]) * 60) + Number(match[4]) : Number.POSITIVE_INFINITY
  }

  function currentSort(hubId) {
    if (sortState.has(hubId)) return sortState.get(hubId)
    try {
      const saved = JSON.parse(localStorage.getItem(`asrplus.sort.v2.${hubId}`) || '{}')
      if (columns.some(([key]) => key === saved.key) && ['asc', 'desc'].includes(saved.direction)) {
        sortState.set(hubId, saved); return saved
      }
    } catch {}
    const initial = { key: 'received', direction: 'asc' }; sortState.set(hubId, initial); return initial
  }

  function sortedConnections(hub) {
    const sort = currentSort(hub.id)
    const factor = sort.direction === 'asc' ? 1 : -1
    return [...hub.connections].sort((a, b) => {
      let left = a[sort.key] || '', right = b[sort.key] || ''
      if (sort.key === 'node') { left = Number(left); right = Number(right) }
      if (sort.key === 'received' || sort.key === 'connected') { left = duration(left); right = duration(right) }
      if (typeof left === 'number' && typeof right === 'number' && left !== right) return (left - right) * factor
      return String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: 'base' }) * factor
    })
  }

  async function control(hub, action, node, permanent = false) {
    const verb = action === 'connect' ? 'Connect' : 'Disconnect'
    if (!/^\d{3,10}$/.test(node) || !window.confirm(`${verb} node ${node} ${action === 'connect' ? 'to' : 'from'} ${hub.name} (${hub.node})?`)) return
    try {
      const response = await fetch('api.php', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-ASRPlus-Requested-With': 'control' },
        body: JSON.stringify({ hub: hub.id, action, node, permanent }),
      })
      const payload = await response.json()
      if (!response.ok || payload.ok === false) throw new Error(payload.error || `${verb} failed.`)
      errorElement.textContent = payload.message || `${verb} requested.`
      window.setTimeout(refresh, 1500)
    } catch (error) { errorElement.textContent = error instanceof Error ? error.message : `${verb} failed.` }
  }

  function renderHub(hub) {
    const section = element('section', 'node-section')
    const heading = element('header', 'node-heading')
    const identityPending = hub.txState === 'pending' && Date.now() < Number(hub.identityPendingUntilEpochMs || 0)
    const txState = hub.txState === 'pending' ? 'unknown' : (hub.txState || 'idle')
    const statusLabel = identityPending ? 'ACTIVE · Identifying station' : (hub.txLabel || 'Idle')
    const status = element('span', `tx-status is-${txState}`, statusLabel)
    status.title = hub.txLabel || statusLabel
    status.setAttribute('aria-label', statusLabel)
    heading.append(element('h2', '', `${hub.name} · Node ${hub.node}`), status,
      element('span', 'connection-count', `${hub.connectionCount} / ${hub.capacity} connections`))
    const controls = element('form', 'node-controls')
    const input = element('input'); input.inputMode = 'numeric'; input.pattern = '[0-9]*'; input.placeholder = 'Node number'; input.setAttribute('aria-label', `Node to connect to ${hub.name}`)
    const permanentLabel = element('label'); const permanent = element('input'); permanent.type = 'checkbox'; permanentLabel.append(permanent, document.createTextNode('Permanent'))
    const connect = element('button', '', 'Connect'); connect.type = 'submit'
    controls.append(input, permanentLabel, connect)
    controls.addEventListener('submit', (event) => { event.preventDefault(); void control(hub, 'connect', input.value.trim(), permanent.checked) })
    section.append(heading, controls)
    const shell = element('div', 'table-shell')
    const table = element('table', 'connection-table')
    const tableHead = element('thead')
    const headerRow = element('tr')
    const sort = currentSort(hub.id)
    columns.forEach(([key, label]) => {
      const cell = element('th')
      if (key === 'actions') cell.textContent = label
      else {
        const button = element('button', sort.key === key ? 'is-active' : '', `${label}${sort.key === key ? (sort.direction === 'asc' ? ' ▲' : ' ▼') : ' ↕'}`)
        button.type = 'button'
        button.addEventListener('click', () => {
          const next = { key, direction: sort.key === key && sort.direction === 'asc' ? 'desc' : 'asc' }
          sortState.set(hub.id, next); localStorage.setItem(`asrplus.sort.v2.${hub.id}`, JSON.stringify(next)); void refresh()
        })
        cell.append(button)
      }
      headerRow.append(cell)
    })
    tableHead.append(headerRow); table.append(tableHead)
    const body = element('tbody')
    if (!hub.connections.length) {
      const row = element('tr', 'empty-row')
      const cell = element('td', '', hub.available ? 'No connections reported.' : 'Connection data unavailable.')
      cell.colSpan = 7; row.append(cell); body.append(row)
    } else {
      sortedConnections(hub).forEach((connection) => {
        const row = element('tr', connection.keyed ? 'is-keyed' : '')
        ;[connection.node, connection.label || 'Unknown node', connection.received || 'Never', connection.direction || '—', connection.connected || '—', connection.mode || '—'].forEach((value) => row.append(element('td', '', value)))
        const actions = element('td'); const disconnect = element('button', 'disconnect', 'Disconnect'); disconnect.type = 'button'; disconnect.addEventListener('click', () => void control(hub, 'disconnect', connection.node)); actions.append(disconnect); row.append(actions)
        body.append(row)
      })
    }
    table.append(body); shell.append(table); section.append(shell)
    return section
  }

  function renderTalkers(sourceTalkers) {
    const source = Array.isArray(sourceTalkers) ? sourceTalkers : []
    const current = source.find((talker) => talker.current) || null
    const visibleCurrent = current && Date.now() < Number(current.identityPendingUntilEpochMs || 0) ? null : current
    const talkers = [visibleCurrent, ...source.filter((talker) => !talker.current).slice(0, 4)]
    const cards = Array.from({ length: 5 }, (_, index) => {
      const talker = talkers[index]
      const card = element('article', `talker-card${index === 0 && talker ? ' is-current' : ''}${talker ? '' : ' is-empty'}`)
      let lastHeard = '--:--'
      if (talker?.eventEpoch) lastHeard = new Date(talker.eventEpoch * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
      card.append(
        element('div', 'talker-title', index === 0 ? 'Current Talker' : `Last Talker ${index} · ${lastHeard}`),
        element('div', 'talker-callsign', talker?.callsign || '\u00a0'),
        element('div', 'talker-source', talker ? `${talker.source || 'AllStar'}${talker.node ? ` · ${talker.node}` : ''}${talker.heardOn ? ` · via ${talker.heardOn}` : ''}` : (index === 0 ? 'No one talking' : 'No recent talker')),
        element('div', 'talker-description', talker?.description || '\u00a0'),
        element('div', 'talker-location', talker?.location || '\u00a0'),
      )
      let talkerDuration = talker?.duration || '—'
      if (talker?.current && talker.startedEpoch) {
        const seconds = Math.max(0, Math.floor(Date.now() / 1000) - Number(talker.startedEpoch))
        talkerDuration = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
      }
      const durationRow = element('div', 'talker-duration'); durationRow.append(element('span', '', 'Duration'), element('strong', '', talkerDuration)); card.append(durationRow)
      return card
    })
    talkerCardsElement.replaceChildren(...cards)
  }

  function renderBridges(hubs) {
    function recentActivityLabel(entry) {
      const name = entry.callsign || entry.label || entry.name || String(entry)
      const event = String(entry.event || '').toLowerCase()
      const epoch = Number(entry.epoch || 0)
      const at = epoch > 0 ? new Date(epoch * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' }) : ''
      const action = event === 'connect' ? 'CONNECTED' : event === 'disconnect' ? 'DISCONNECTED' : event === 'transmit' ? 'TRANSMITTED' : ''
      const duration = event === 'transmit' ? Number(entry.durationSeconds || 0) : 0
      const durationLabel = event === 'transmit' ? (duration >= 1 ? `${Math.round(duration)}s` : '<1s') : ''
      return [name, action, at, durationLabel].filter(Boolean).join(' · ')
    }
    const openBridges = new Set([...bridgeGrid.querySelectorAll('details[open][data-bridge-id]')].map((details) => details.dataset.bridgeId))
    const records = hubs.flatMap((hub) => (hub.bridges || []).map((bridge) => ({ hub, bridge })))
    bridgeGrid.replaceChildren(...records.map(({ hub, bridge }) => {
      const card = element('article', `bridge-card is-${bridge.status}${bridge.active ? ' is-active' : ''}`)
      const head = element('header'); head.append(element('h3', '', bridge.label), element('span', '', bridge.active ? 'Active' : bridge.status)); card.append(head)
      const rows = element('dl')
      ;[['Hub', `${hub.location || hub.name} · ${hub.node}`], ['Bridge node', bridge.node || '—'], ['Destination', bridge.destination || '—'], ['Talking', bridge.currentUser && bridge.currentUser !== '-' ? bridge.currentUser : 'Idle'], ['Clients', String(bridge.clientCount || 0)]].forEach(([label, value]) => { const row = element('div'); row.append(element('dt', '', label), element('dd', '', value)); rows.append(row) })
      card.append(rows)
      const recent = Array.isArray(bridge.recentUsers) ? bridge.recentUsers : []
      const clients = Array.isArray(bridge.clients) ? bridge.clients : []
      const details = element('details', 'bridge-details'); details.dataset.bridgeId = `${hub.id}:${bridge.id}`; details.open = openBridges.has(details.dataset.bridgeId)
      details.append(element('summary', '', 'Clients and recent activity'))
      const clientSection = element('section', 'bridge-detail-section'); clientSection.append(element('strong', '', `Connected clients (${clients.length})`))
      const clientList = element('div', 'bridge-detail-list'); if (clients.length) clients.slice(0, 50).forEach((entry) => clientList.append(element('div', '', entry.callsign || entry.label || entry.name || String(entry)))); else clientList.append(element('div', 'is-empty', 'No connected clients reported.')); clientSection.append(clientList)
      const recentSection = element('section', 'bridge-detail-section'); recentSection.append(element('strong', '', 'Recent activity'))
      const recentList = element('div', 'bridge-detail-list'); if (recent.length) recent.slice(0, 30).forEach((entry) => recentList.append(element('div', '', recentActivityLabel(entry)))); else recentList.append(element('div', 'is-empty', 'No recent activity reported.')); recentSection.append(recentList)
      details.append(clientSection, recentSection); card.append(details)
      return card
    }))
  }

  async function loadFavorites() {
    favoritesContent.textContent = 'Loading Favorites…'
    try {
      const response = await fetch('/asr/asr-api.php?action=favorites', { credentials: 'same-origin', cache: 'no-store' })
      const payload = await response.json()
      if (!response.ok || payload.ok === false) throw new Error(payload.error || 'Favorites could not be loaded.')
      const rows = Array.isArray(payload.rows) ? payload.rows : []
      const toolbar = element('div', 'favorites-toolbar')
      const fileLabel = element('span', '', payload.selectedFile || 'favorites.ini')
      const manage = element('a', '', 'Open full Favorites tools in ASR'); manage.href = '/asr/'; manage.title = 'Open normal ASR for advanced Favorites editing'
      const download = element('a', '', 'Download'); download.href = `/asr/asr-api.php?action=favorites-download&favsfile=${encodeURIComponent(payload.selectedFile || '')}`
      toolbar.append(element('strong', '', 'Favorites file:'), fileLabel, manage, download)
      const table = element('table', 'favorites-table')
      const head = element('thead'); const header = element('tr'); ['Node', 'Name', 'Description', 'Location', 'Action'].forEach((label) => header.append(element('th', '', label))); head.append(header); table.append(head)
      const body = element('tbody')
      rows.forEach((favorite) => {
        const row = element('tr'); const node = String(favorite.node || '')
        row.append(element('td', '', node), element('td', '', favorite.name || favorite.label || ''), element('td', '', favorite.description || favorite.desc || ''), element('td', '', favorite.location || ''))
        const actions = element('td'); const connect = element('button', 'favorite-connect', 'Connect'); connect.type = 'button'; connect.addEventListener('click', () => { const hub = currentHubs.find((item) => item.id === favoritesHub.value); if (hub) void control(hub, 'connect', node) }); actions.append(connect); row.append(actions); body.append(row)
      })
      if (!rows.length) { const row = element('tr'); const cell = element('td', '', 'No Favorites are configured.'); cell.colSpan = 5; row.append(cell); body.append(row) }
      table.append(body); const scroll = element('div', 'favorites-scroll'); scroll.append(table); favoritesContent.replaceChildren(toolbar, scroll); favoritesLoaded = true
    } catch (error) { favoritesContent.textContent = error instanceof Error ? error.message : 'Favorites could not be loaded.' }
  }

  function applyTheme(theme) {
    const allowed = ['netoholics', 'dark', 'light', 'ocean', 'matrix']
    const selected = allowed.includes(theme) ? theme : 'netoholics'
    document.documentElement.dataset.theme = selected
    themeSelect.value = selected; localStorage.setItem('asrplus.theme', selected)
  }

  function saveModuleOrder() {
    const order = [...moduleRoot.querySelectorAll('[data-module]')].map((module) => module.dataset.module)
    localStorage.setItem('asrplus.moduleOrder', JSON.stringify(order))
  }

  function setFavoritesVisibility(open) {
    favoritesElement.hidden = !open
    favoritesToggle.setAttribute('aria-expanded', String(open))
    favoritesToggle.textContent = open ? 'Hide Favorites' : 'Show Favorites'
    localStorage.setItem('asrplus.favoritesOpen', open ? '1' : '0')
  }

  function initializeModuleOrder() {
    const modules = [...moduleRoot.querySelectorAll('[data-module]')]
    try {
      const saved = JSON.parse(localStorage.getItem('asrplus.moduleOrder') || '[]')
      saved.forEach((id) => { const module = modules.find((item) => item.dataset.module === id); if (module) moduleRoot.append(module) })
      modules.filter((module) => !saved.includes(module.dataset.module)).forEach((module) => moduleRoot.append(module))
    } catch {}
    modules.forEach((module) => {
      const handle = module.querySelector('.module-handle')
      handle.addEventListener('pointerdown', () => { module.draggable = true })
      handle.addEventListener('pointerup', () => { module.draggable = false })
      module.addEventListener('dragstart', (event) => { draggedModule = module; module.classList.add('is-dragging'); event.dataTransfer.effectAllowed = 'move' })
      module.addEventListener('dragover', (event) => {
        if (!draggedModule || draggedModule === module) return
        event.preventDefault(); const before = event.clientY < module.getBoundingClientRect().top + module.offsetHeight / 2
        moduleRoot.insertBefore(draggedModule, before ? module : module.nextSibling)
      })
      module.addEventListener('dragend', () => { module.classList.remove('is-dragging'); module.draggable = false; draggedModule = null; saveModuleOrder() })
    })
  }

  // ASR favicon semantics, scoped to Kentucky's local node rather than
  // mixing keyed connections from all three hubs.
  function asrFaviconState(hubs) {
    const kentucky = hubs.find((hub) => hub.id === 'kentucky' || String(hub.node) === '674982')
    if (!kentucky || kentucky.stale) return 'idle'
    const keyed = (kentucky.connections || []).filter((connection) => connection.keyed)
    const ptt = keyed.some((connection) => connection.direction === 'OUT')
    const cos = keyed.some((connection) => connection.direction !== 'OUT')
    return ptt && cos ? 'both' : ptt ? 'ptt' : cos ? 'cos' : 'idle'
  }

  let faviconRevision = 0
  function setFavicon(hubs) {
    const favicon = document.querySelector('link[rel~="icon"]')
    if (!favicon) return
    const normal = favicon.dataset.normalHref || favicon.href
    favicon.dataset.normalHref = normal
    const revision = ++faviconRevision
    const state = asrFaviconState(hubs)
    if (state === 'idle') { favicon.href = normal; return }
    const colors = {
      ptt: { ring: '#ff2b2b', shadow: '#ff0000', gradient: ['rgba(255, 45, 45, 0.72)', 'rgba(255, 0, 0, 0.5)'] },
      cos: { ring: '#31e86d', shadow: '#00d64a', gradient: ['rgba(49, 232, 109, 0.68)', 'rgba(0, 214, 74, 0.46)'] },
      both: { ring: '#ffd447', shadow: '#ffb000', gradient: ['rgba(255, 212, 71, 0.72)', 'rgba(255, 176, 0, 0.48)'] },
    }[state]
    const image = new Image()
    image.onload = () => {
      if (revision !== faviconRevision) return
      const canvas = document.createElement('canvas'); canvas.width = 64; canvas.height = 64
      const context = canvas.getContext('2d'); if (!context) return
      const gradient = context.createRadialGradient(32, 32, 11, 32, 32, 32)
      gradient.addColorStop(0, colors.gradient[0]); gradient.addColorStop(0.58, colors.gradient[1]); gradient.addColorStop(1, 'rgba(255, 0, 0, 0)')
      context.fillStyle = gradient; context.fillRect(0, 0, 64, 64)
      context.beginPath(); context.arc(32, 32, 27, 0, Math.PI * 2)
      context.strokeStyle = colors.ring; context.lineWidth = 5
      context.shadowColor = colors.shadow; context.shadowBlur = 12; context.stroke()
      context.shadowBlur = 0; context.drawImage(image, 2, 1, 60, 62)
      favicon.href = canvas.toDataURL('image/png')
    }
    image.src = normal
  }

  async function refresh() {
    if (refreshInFlight) return
    refreshInFlight = true
    refreshButton.disabled = true
    try {
      const response = await fetch('api.php', { credentials: 'same-origin', cache: 'no-store' })
      if (response.status === 403) { window.location.assign('/asr/user/'); return }
      const payload = await response.json()
      if (!response.ok || payload.ok === false) throw new Error(payload.error || 'Status unavailable.')
      const generation = Number(payload.generatedEpoch || 0)
      if (generation && generation < lastRenderedGeneration) return
      const generationChanged = generation !== lastRenderedGeneration
      const identityPending = payload.hubs.some((hub) => hub.txState === 'pending')
      if (generationChanged || identityPending) hubsElement.replaceChildren(...payload.hubs.map(renderHub))
      renderTalkers(payload.talkers)
      if (generationChanged) {
        currentHubs = payload.hubs
        const selectedHub = favoritesHub.value
        favoritesHub.replaceChildren(...payload.hubs.map((hub) => { const option = element('option', '', `${hub.name} · ${hub.node}`); option.value = hub.id; return option }))
        favoritesHub.value = payload.hubs.some((hub) => hub.id === selectedHub) ? selectedHub : 'kentucky'
        renderBridges(payload.hubs)
        setFavicon(payload.hubs)
        const connected = payload.hubs.reduce((sum, hub) => sum + hub.connectionCount, 0)
        summaryElement.textContent = `${connected} connections across ${payload.hubs.length} hubs`
      }
      updatedElement.textContent = `Updated ${new Date(payload.generatedEpoch * 1000).toLocaleTimeString()}`
      lastRenderedGeneration = Math.max(lastRenderedGeneration, generation)
      window.asrPlusTelemetry = {
        ...(payload.telemetry || {}),
        apiResponseEpochMs: Number(response.headers.get('X-ASRPlus-Response-Epoch-Ms') || 0),
        browserRenderedEpochMs: Date.now(),
      }
      errorElement.textContent = ''
    } catch (error) {
      errorElement.textContent = error instanceof Error ? error.message : 'Status unavailable.'
    } finally { refreshInFlight = false; refreshButton.disabled = false }
  }
  favoritesToggle.addEventListener('click', () => {
    const open = favoritesElement.hidden
    setFavoritesVisibility(open)
    if (open) favoritesElement.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
  themeSelect.addEventListener('change', () => applyTheme(themeSelect.value))
  refreshButton.addEventListener('click', refresh)
  initializeModuleOrder(); applyTheme(localStorage.getItem('asrplus.theme') || 'netoholics'); setFavoritesVisibility(localStorage.getItem('asrplus.favoritesOpen') !== '0'); void loadFavorites()
  refresh(); window.setInterval(refresh, 1000)
})()
