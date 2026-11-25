(() => {
  const hubsElement = document.querySelector('#hubs')
  const errorElement = document.querySelector('#error')
  const summaryElement = document.querySelector('#summary')
  const menuToggle = document.querySelector('#menu-toggle')
  const menuPanel = document.querySelector('#menu-panel')
  menuToggle?.addEventListener('click', () => { const open = menuPanel.hidden; menuPanel.hidden = !open; menuToggle.setAttribute('aria-expanded', String(open)) })
  document.addEventListener('click', (event) => { if (menuPanel && !menuPanel.hidden && !event.target.closest('.menu-wrap')) { menuPanel.hidden = true; menuToggle.setAttribute('aria-expanded', 'false') } })
  const updatedElement = document.querySelector('#updated')
  const refreshButton = {disabled:false}
  const talkerCardsElement = document.querySelector('#talker-cards')
  const favoritesElement = document.querySelector('#favorites')
  const favoritesToggle = document.querySelector('#favorites-toggle')
  const themeSelect = document.querySelector('#theme')
  const moduleRoot = document.querySelector('#modules')
  const favoritesContent = document.querySelector('#favorites-content')
  const favoritesHub = document.querySelector('#favorites-hub')
  const bridgeGrid = document.querySelector('#bridge-grid')
  const mapDialog = document.querySelector('#network-map-dialog')
  const mapImage = document.querySelector('#network-map-image')
  const graphSvg = document.querySelector('#network-map-graph')
  let graphMode = true
  let graphHub = null
  const graphPositions = new Map()
  const graphManualPositions = new Set()
  let graphCanvasWidth = 1000, graphCanvasHeight = 700
  const graphCollapsed = new Set()
  let graphDrag = null
  let graphRedraw = null
  const graphInfo = document.querySelector('#network-node-info')
  let infoRequest = 0
  const mapLoading = document.querySelector('#network-map-loading')
  const mapTitle = document.querySelector('#network-map-title')
  const mapViewport = mapDialog.querySelector('.network-map-viewport')
  let mapZoom = 1
  let mapPinchDistance = 0
  let mapPinchStartZoom = 1
  let mapScrollY = 0
  let mapPreviousFocus = null
  const isBridgeConnection = (hub, connection) => {
    const node = String(connection.node || '').trim()
    const label = String(connection.label || '')
    return (hub.bridges || []).some(b => String(b.node) === node) ||
      /^(?:100[1-6]|1999|BCSTFY)$/i.test(node) ||
      /(?:\bbridge\b|broadcastify|\bBCSTFY\b)/i.test(label)
  }
  const isMapEligible = (hub, connection) =>
    /^\d{3,7}$/.test(String(connection.node || '')) &&
    !/^3\d{6}$/.test(String(connection.node)) &&
    !/echolink/i.test(String(connection.mode || '')) &&
    !isBridgeConnection(hub, connection)
  function fitNetworkMap() {
    if (!mapImage.naturalWidth || !mapImage.naturalHeight) return
    const availableWidth = mapViewport.clientWidth
    const availableHeight = mapViewport.clientHeight
    const fitScale = Math.min(availableWidth / mapImage.naturalWidth, availableHeight / mapImage.naturalHeight)
    mapImage.dataset.fitScale = String(fitScale)
    applyMapZoom()
  }
  function applyMapZoom() {
    const fitScale = Number(mapImage.dataset.fitScale || 1)
    mapImage.style.width = `${Math.round(mapImage.naturalWidth * fitScale * mapZoom)}px`
    mapImage.style.height = `${Math.round(mapImage.naturalHeight * fitScale * mapZoom)}px`
    graphSvg.style.width = `${Math.round(graphCanvasWidth * mapZoom)}px`
    graphSvg.style.height = `${Math.round(graphCanvasHeight * mapZoom)}px`
    mapImage.style.maxWidth = 'none'
  }
  function closeNetworkMap() { if (mapDialog.open) mapDialog.close() }
  async function openNetworkMap(node, hub) {
    if (!/^\d{3,7}$/.test(String(node))) return
    mapPreviousFocus = document.activeElement
    mapScrollY = window.scrollY
    mapZoom = 1
    mapImage.dataset.fitScale = ''
    mapTitle.textContent = `AllStarLink Network Map · ${node}`
    mapImage.removeAttribute('src')
    mapLoading.hidden = false
    mapLoading.textContent = 'Loading map…'
    applyMapZoom()
    mapDialog.showModal()
    document.documentElement.classList.add('network-map-open')
    document.body.classList.add('network-map-open')
    mapViewport.scrollTop = 0
    mapViewport.scrollLeft = 0
    graphHub = hub
    graphPositions.clear(); graphManualPositions.clear(); graphCollapsed.clear(); graphInfo.hidden = true
    graphMode = true
    graphSvg.removeAttribute('hidden')
    mapImage.hidden = true
    void renderCustomGraph(String(node), hub)
    mapImage.src = `https://stats.allstarlink.org/stats/${encodeURIComponent(node)}/networkMap?asrplus=${Date.now()}`
  }
  mapImage.addEventListener('load', () => { if (!graphMode) { mapLoading.hidden = true; fitNetworkMap(); mapViewport.scrollTo(0, 0) } })
  mapImage.addEventListener('error', () => { mapLoading.hidden = false; mapLoading.textContent = 'Map unavailable.' })
  mapDialog.querySelector('#network-map-close').addEventListener('click', closeNetworkMap)
  mapDialog.querySelector('#network-map-zoom-in').addEventListener('click', () => { mapZoom = Math.min(5, mapZoom * 1.25); applyMapZoom() })
  mapDialog.querySelector('#network-map-zoom-out').addEventListener('click', () => { mapZoom = Math.max(0.5, mapZoom / 1.25); applyMapZoom() })
  mapDialog.querySelector('#network-map-fill').addEventListener('click', () => { mapZoom = 1; if (!graphMode) fitNetworkMap(); else applyMapZoom(); mapViewport.scrollTo(0, 0) })
  const touchDistance = touches => Math.hypot(touches[0].clientX - touches[1].clientX, touches[0].clientY - touches[1].clientY)
  mapViewport.addEventListener('touchstart', event => {
    if (event.touches.length !== 2) return
    mapPinchDistance = touchDistance(event.touches)
    mapPinchStartZoom = mapZoom
  }, { passive: true })
  mapViewport.addEventListener('touchmove', event => {
    if (event.touches.length !== 2 || !mapPinchDistance) return
    event.preventDefault()
    const next = Math.max(0.5, Math.min(8, mapPinchStartZoom * touchDistance(event.touches) / mapPinchDistance))
    const midpointX = (event.touches[0].clientX + event.touches[1].clientX) / 2 - mapViewport.getBoundingClientRect().left
    const midpointY = (event.touches[0].clientY + event.touches[1].clientY) / 2 - mapViewport.getBoundingClientRect().top
    const ratio = next / mapZoom
    const targetX = (mapViewport.scrollLeft + midpointX) * ratio - midpointX
    const targetY = (mapViewport.scrollTop + midpointY) * ratio - midpointY
    mapZoom = next
    applyMapZoom()
    mapViewport.scrollLeft = targetX
    mapViewport.scrollTop = targetY
  }, { passive: false })
  mapViewport.addEventListener('touchend', event => { if (event.touches.length < 2) mapPinchDistance = 0 }, { passive: true })
  mapViewport.addEventListener('touchcancel', () => { mapPinchDistance = 0 }, { passive: true })
  mapViewport.addEventListener('wheel', event => {
    if (event.shiftKey && !event.ctrlKey && !event.metaKey) { event.preventDefault();mapViewport.scrollLeft+=event.deltaY;return }
    if (!event.ctrlKey && !event.metaKey) return
    event.preventDefault()
    mapZoom = Math.max(0.5, Math.min(8, mapZoom * (event.deltaY < 0 ? 1.1 : 0.9)))
    applyMapZoom()
  }, { passive: false })
  async function renderCustomGraph(root, hub) {
    const known = new Map()
    const edges = new Set()
    const children = new Map()
    const add = (n, label, kind) => { if (!known.has(n)) known.set(n, { id:n, label:label || n, kind:kind || 'node' }) }
    const edge = (a,b) => { if (a !== b) { edges.add([a,b].sort().join(':')); for (const [from,to] of [[a,b],[b,a]]) { if (!children.has(from)) children.set(from,new Set()); children.get(from).add(to) } } }
    add(root, root, 'root')
    for (const h of currentHubs) {
      const hNode = String(h.node || '')
      if (!hNode) continue
      add(hNode, h.name || hNode, hNode === root ? 'root' : 'hub')
      for (const c of h.connections || []) {
        const n = String(c.node || '')
        if (!n) continue
        add(n, c.label || n, isBridgeConnection(h,c) ? 'bridge' : 'node')
        edge(hNode,n)
      }
    }
    const drawGraph = () => {
    if (!mapDialog.open || !graphMode) return
    const svgNS = 'http://www.w3.org/2000/svg'
    const create = (tag, attrs, parent = graphSvg) => { const el = document.createElementNS(svgNS, tag); for (const [k,v] of Object.entries(attrs)) el.setAttribute(k, String(v)); parent.append(el); return el }
    graphSvg.replaceChildren()
    graphSvg.setAttribute('preserveAspectRatio', 'xMidYMid meet')
    const nodes = [...known.values()]
    // Layered breadth-first layout groups connected stations by hop distance.
    const positions = new Map()
    const depth = new Map([[root,0]])
    const q=[root]
    while(q.length){const id=q.shift();for(const next of children.get(id)||[]){if(!depth.has(next)){depth.set(next,depth.get(id)+1);q.push(next)}}}
    for(const n of nodes) if(!depth.has(n.id)) depth.set(n.id,Math.max(1,...depth.values())+1)
    const layers=new Map()
    for(const n of nodes){const d=depth.get(n.id);if(!layers.has(d))layers.set(d,[]);layers.get(d).push(n.id)}
    // Adaptive lanes: cap vertical stacking, wrap busy layers into adjacent columns.
    // Keep canvas dimensions independent of viewport, so nodes never collapse to a line.
    const maxDepth=Math.max(...layers.keys(),1)
    const maxPerColumn=11, laneWidth=158, rowHeight=66
    const layerWidths=new Map()
    for(const [level,ids] of layers){ids.sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));layerWidths.set(level,Math.max(1,Math.ceil(ids.length/maxPerColumn)))}
    const offsets=new Map();let nextX=95
    for(let level=0;level<=maxDepth;level++){offsets.set(level,nextX);nextX+=(layerWidths.get(level)||1)*laneWidth+95}
    const graphWidth=Math.max(1000,nextX+80)
    const graphHeight=Math.max(700,130+maxPerColumn*rowHeight)
    graphCanvasWidth=graphWidth;graphCanvasHeight=graphHeight
    graphSvg.setAttribute('viewBox',`0 0 ${graphWidth} ${graphHeight}`)
    for(const [level,ids] of layers){const count=layerWidths.get(level);ids.forEach((id,i)=>{
      const column=Math.floor(i/maxPerColumn),row=i%maxPerColumn
      const inColumn=Math.min(maxPerColumn,ids.length-column*maxPerColumn)
      const x=offsets.get(level)+column*laneWidth
      const y=(graphHeight-(inColumn-1)*rowHeight)/2+row*rowHeight
      if(!graphManualPositions.has(id))graphPositions.set(id,[x,y])
      positions.set(id,graphPositions.get(id))
    })}
    // Extend the drawable area for manually moved stations; never clip dragged nodes.
    const furthestX=Math.max(graphWidth,...[...positions.values()].map(p=>p[0]+95))
    const furthestY=Math.max(graphHeight,...[...positions.values()].map(p=>p[1]+95))
    graphCanvasWidth=furthestX;graphCanvasHeight=furthestY
    graphSvg.setAttribute('viewBox',`0 0 ${furthestX} ${furthestY}`)
    const colors = {
      netoholics: ['#e1ad45','#87929b','#3bc48a','#4ab9e6','#809ca9','#edf5f8','#172833'],
      dark: ['#ffc45a','#a5acb7','#61d6a4','#78bfff','#8298ac','#f4f8ff','#141b29'],
      light: ['#a96713','#626e7d','#167c53','#1765a8','#667b90','#142a3b','#f4f8fc'],
      ocean: ['#f5c56b','#85b3c5','#4be0b6','#50c6ed','#5697b1','#e4faff','#0a2c40'],
      matrix: ['#b9f74d','#69976d','#30ef69','#79ff9d','#438b58','#d9ffdf','#07180e']
    }
    const [rootColor, bridgeColor, hubColor, nodeColor, lineColor, textColor, bgColor] = colors[document.documentElement.dataset.theme] || colors.netoholics
    graphSvg.style.background = bgColor
    // Collapse hides descendants reached through this node, but preserves shared nodes
    // that remain reachable by another expanded path.
    const visible = new Set([root]); const queue = [root]
    while (queue.length) {
      const parent = queue.shift()
      if (graphCollapsed.has(parent)) continue
      for (const child of children.get(parent) || []) {
        if (!visible.has(child)) { visible.add(child); queue.push(child) }
      }
    }
    for (const key of edges) {
      const [a,b] = key.split(':'); const p=positions.get(a), q=positions.get(b)
      if (p && q && visible.has(a) && visible.has(b) && !graphCollapsed.has(a)) create('line',{x1:p[0],y1:p[1],x2:q[0],y2:q[1],stroke:lineColor,'stroke-opacity':'.6','stroke-width':'1.6'})
    }
    for (const n of nodes) {
      if (!visible.has(n.id)) continue
      const [x,y] = positions.get(n.id)
      const fill = n.kind === 'root' ? rootColor : n.kind === 'bridge' ? bridgeColor : n.kind === 'hub' ? hubColor : nodeColor
      const group = create('g',{'data-graph-node':n.id,tabindex:'0',role:'button','aria-label':`Node ${n.id}: Enter for details, C to toggle children`})
      create('circle',{cx:x,cy:y,r:n.kind==='root'?26:16,fill,stroke:bgColor,'stroke-width':2},group)
      const title = create('title',{},group);title.textContent = `${n.id} · ${n.label} (click for details, double-click to toggle children)`
      const label = create('text',{x,y:y+(n.kind==='root'?45:33),'text-anchor':'middle',fill:textColor,'font-size':'13','font-weight':'700'},group);label.textContent=n.id
    }
    mapLoading.hidden = true
    mapLoading.textContent = ''
    graphSvg.setAttribute('aria-label', `${nodes.length} stations and ${edges.size} connections; discovery continues while open`)
    applyMapZoom()
    }
    mapLoading.hidden = false; mapLoading.textContent = 'Rendering network…'
    try { drawGraph() } catch (error) { mapLoading.hidden = false; mapLoading.textContent = `Chart error: ${error.message}`; console.error('ASRPlus chart', error) } // Always render local graph first.
    // Progressive breadth-first discovery: follow every newly discovered station,
    // within a safety ceiling. The server enforces a shared 12/minute upstream budget.
    const visited=new Set(), pending=[root,...known.keys()]
    // Discovery is resumable across openings and page reloads; no fixed node ceiling.
    const cacheKey=`asrplus-network-${root}`
    try { const saved=JSON.parse(localStorage.getItem(cacheKey)||'null');if(saved&&Date.now()-saved.saved<86400000){
      for(const [a,b] of saved.edges||[]){if(/^\d{3,7}$/.test(a)&&/^\d{3,7}$/.test(b)){add(a,a,'node');add(b,b,'node');edge(a,b)}}
      pending.push(...known.keys())
    }}catch(_){}
    let queried=0, unchanged=0
    const save=()=>{try{const pairs=[...edges].map(k=>k.split(':'));localStorage.setItem(cacheKey,JSON.stringify({saved:Date.now(),edges:pairs}))}catch(_){}}
    const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms))
    const discover=async()=>{
      while(pending.length && mapDialog.open && graphMode){
        const batch=[]
        while(pending.length && batch.length<4){const n=String(pending.shift());if(visited.has(n)||!/^\d{4,7}$/.test(n)||/^100[1-6]$/.test(n))continue;visited.add(n);batch.push(n)}
        if(!batch.length)break
        if(!batch.length)break
        queried+=batch.length
        const results=await Promise.all(batch.map(async n=>{
          try{const r=await fetch(`network-graph.php?node=${encodeURIComponent(n)}`,{credentials:'same-origin'});return r.ok?{n,data:await r.json()}:null}catch{return null}
        }))
        let discovered=false
        for(const result of results){if(!result)continue;const {n,data}=result
          if(!data.reported)continue
          for(const raw of (data.links||[])){
            const linked=String(raw);if(!/^\d{3,7}$/.test(linked))continue
            if(!known.has(linked)){add(linked,linked,'node');pending.push(linked);discovered=true}
            edge(n,linked)
          }
        }
        if(discovered){unchanged=0;save()}else unchanged++
        if(mapDialog.open && graphMode)drawGraph()
        // Respect the shared upstream request budget. Pause instead of terminating discovery.
        if(unchanged>=2){await wait(61000);unchanged=0}else await wait(1200)
      }
    }
    void discover().catch(error=>console.warn('Network discovery paused',error))
    drawGraph.connectionsFor=id=>[...(children.get(id)||[])].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}))
    graphRedraw = drawGraph
  }
  const showNodeInfo = async id => {
    const request = ++infoRequest
    graphInfo.hidden = false
    graphInfo.replaceChildren()
    const heading=document.createElement('strong');heading.textContent=`Node ${id}`;graphInfo.append(heading)
    const dismiss=document.createElement('button');dismiss.type='button';dismiss.textContent='×';dismiss.setAttribute('aria-label','Close node details');dismiss.addEventListener('click',()=>{graphInfo.hidden=true;infoRequest++});graphInfo.append(dismiss)
    const details=document.createElement('div');details.textContent='Loading cached AllStarLink details…';graphInfo.append(details)
    if (!/^\d{3,7}$/.test(id)) {details.textContent='Local bridge or non-AllStar connection; no AllStarLink record.';return}
    try {
      const response=await fetch(`node-info.php?node=${encodeURIComponent(id)}`,{credentials:'same-origin'})
      if(!response.ok)throw Error(`HTTP ${response.status}`)
      const data=await response.json();if(request!==infoRequest)return
      details.replaceChildren()
      const graphLabel=graphSvg.querySelector(`[data-graph-node="${id}"] text`)?.textContent?.trim()||''
      const displayName=[data.callsign,data.name,data.description].find(v=>v&&String(v)!==id)||((graphLabel&&graphLabel!==id)?graphLabel:'')
      if(displayName)heading.textContent=`${displayName} · ${id}`
      const graphLinks=typeof graphRedraw?.connectionsFor==='function'?graphRedraw.connectionsFor(id):[]
      const reportedLinks=Array.isArray(data.links)?data.links:[]
      const visibleLinks=reportedLinks.length?reportedLinks:graphLinks
      const description=data.description||data.name||null
      const updatedDate=data.updated?new Date(data.updated):null
      const localUpdated=updatedDate&&!Number.isNaN(updatedDate.getTime())
        ?new Intl.DateTimeFormat(navigator.language||undefined,{dateStyle:'medium',timeStyle:'medium'}).format(updatedDate)
        :null
      const fields=[['Callsign',data.callsign],['Description',description],['Location',data.location],['Connections',visibleLinks.length?visibleLinks.join(', '):(data.status==='ok'&&data.reported?'None reported':'Not currently available')]]
      for(const [key,value] of fields){if(value==null||value==='')continue;const row=document.createElement('div');const label=document.createElement('b');label.textContent=key+':';row.append(label,document.createTextNode(' '+String(value)));details.append(row)}
      if(data.status!=='ok' && !data.callsign && !visibleLinks.length){const note=document.createElement('p');note.textContent='Additional node information is temporarily unavailable.';details.append(note)}
    }catch(error){if(request===infoRequest)details.textContent='Unable to load node details right now.'}
  }
  // Pointer drag repositions nodes; a stationary click toggles their children.
  const svgPoint = event => { const p=graphSvg.createSVGPoint(); p.x=event.clientX; p.y=event.clientY; const matrix=graphSvg.getScreenCTM(); return matrix ? p.matrixTransform(matrix.inverse()) : p }
  graphSvg.addEventListener('pointerdown', event => {
    const group=event.target.closest('[data-graph-node]'); if (!group || event.button !== 0) return
    const id=group.dataset.graphNode, pos=graphPositions.get(id), pt=svgPoint(event)
    if (!pos) return
    graphDrag={id,start:pt,original:[...pos],moved:false,pointer:event.pointerId}
    graphSvg.setPointerCapture(event.pointerId)
  })
  graphSvg.addEventListener('pointermove', event => {
    if (!graphDrag || graphDrag.pointer !== event.pointerId) return
    const pt=svgPoint(event),dx=pt.x-graphDrag.start.x,dy=pt.y-graphDrag.start.y
    if (Math.hypot(dx,dy)>4) graphDrag.moved=true
    if (graphDrag.moved) { graphManualPositions.add(graphDrag.id); graphPositions.set(graphDrag.id,[Math.max(25,graphDrag.original[0]+dx),Math.max(25,graphDrag.original[1]+dy)]); graphRedraw?.() }
  })
  // Pointer capture retargets click events to the SVG; use the node saved on pointerdown.
  let graphClickTimer = null
  let lastNodeTap = null
  graphSvg.addEventListener('pointerup', event => {
    if (!graphDrag || graphDrag.pointer !== event.pointerId) return
    const {id,moved}=graphDrag;graphDrag=null
    if (graphSvg.hasPointerCapture(event.pointerId)) graphSvg.releasePointerCapture(event.pointerId)
    if (moved) return
    const now=performance.now()
    if (lastNodeTap && lastNodeTap.id===id && now-lastNodeTap.time<360) {
      clearTimeout(graphClickTimer);graphClickTimer=null;lastNodeTap=null
      graphCollapsed.has(id)?graphCollapsed.delete(id):graphCollapsed.add(id);graphRedraw?.()
    } else {
      lastNodeTap={id,time:now}
      clearTimeout(graphClickTimer)
      graphClickTimer=setTimeout(()=>{lastNodeTap=null;void showNodeInfo(id)},370)
    }
  })
  graphSvg.addEventListener('pointercancel', () => { graphDrag=null })
  graphSvg.addEventListener('keydown', event => {
    const group=event.target.closest('[data-graph-node]'); if (!group) return
    const id=group.dataset.graphNode
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); void showNodeInfo(id) }
    const step={ArrowLeft:[-20,0],ArrowRight:[20,0],ArrowUp:[0,-20],ArrowDown:[0,20]}[event.key]
    if (event.key === 'c' || event.key === 'C') { event.preventDefault(); graphCollapsed.has(id) ? graphCollapsed.delete(id) : graphCollapsed.add(id); graphRedraw?.() }
    if (step) { event.preventDefault(); const [x,y]=graphPositions.get(id); graphManualPositions.add(id); graphPositions.set(id,[Math.max(25,x+step[0]),Math.max(25,y+step[1])]); graphRedraw?.(); graphSvg.querySelector(`[data-graph-node="${id}"]`)?.focus() }
  })
  mapDialog.addEventListener('click', event => { if (event.target === mapDialog) closeNetworkMap() })
  mapDialog.addEventListener('close', () => {
    document.documentElement.classList.remove('network-map-open')
    document.body.classList.remove('network-map-open')
    window.scrollTo(0, mapScrollY)
    mapPreviousFocus?.focus?.({ preventScroll: true })
  })
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
    if (hub.controlsAvailable === false) { errorElement.textContent = "Remote controls are not configured for this hub."; return }
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

  async function banConnection(hub, connection) {
    if (hub.controlsAvailable === false) return
    const duration = window.prompt('Ban duration: 15m, 1h, 1w, 30d, permanent', '1h')
    if (duration === null) return
    if (!['15m','1h','1w','30d','permanent'].includes(duration)) { window.alert('Invalid duration'); return }
    if (!window.confirm(`Ban node ${connection.node} on ${hub.name} for ${duration}?`)) return
    try {
      const response = await fetch('api.php', {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-ASRPlus-Requested-With':'control'},body:JSON.stringify({hub:hub.id,action:'ban',kind:'node',value:connection.node,duration,reason:''})})
      const data = await response.json(); if (!response.ok || !data.ok) throw Error(data.error || 'Ban failed')
      errorElement.textContent = data.message || 'Ban request queued.'
    } catch (e) { errorElement.textContent = e.message }
  }
  const banDialog = document.querySelector('#ban-dialog')
  const banHub = document.querySelector('#ban-hub')
  document.querySelector('#manage-bans').addEventListener('click', () => { banHub.replaceChildren(...currentHubs.filter(h => h.controlsAvailable !== false).map(h => {const o=element('option','',h.name);o.value=h.id;return o}));document.querySelector('#ban-results').textContent='Select a hub and load bans.';banDialog.showModal() })
  document.querySelector('#ban-close').addEventListener('click', () => banDialog.close())
  document.querySelector('#ban-load').addEventListener('click', async () => {
    const target=document.querySelector('#ban-results'); if(banHub.value==='masterhub'){target.textContent='Master hub bans are managed in ASR → Bans.';return}
    target.textContent='Loading…'
    try {const response=await fetch('api.php',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-ASRPlus-Requested-With':'control'},body:JSON.stringify({hub:banHub.value,action:'ban-list'})});const data=await response.json();if(!response.ok||!data.ok)throw Error(data.error||'Request failed');target.textContent=data.message||'Ban list requested; remote response may take a moment.'}catch(e){target.textContent=e.message}
  })
  function renderHub(hub) {
    const section = element('section', 'node-section')
    const heading = element('header', 'node-heading')
    const identityPending = hub.txState === 'pending' && Date.now() < Number(hub.identityPendingUntilEpochMs || 0)
    const txState = identityPending ? 'idle' : (hub.txState === 'pending' ? 'unknown' : (hub.txState || 'idle'))
    const status = element('span', `tx-status is-${txState}`, identityPending ? 'Idle' : (hub.txLabel || 'Idle'))
    heading.append(element('h2', '', `${hub.name} · Node ${hub.node}`), status,
      element('span', 'connection-count', `${hub.connectionCount} / ${hub.capacity} connections`))
    const controls = element('form', 'node-controls')
    const input = element('input'); input.inputMode = 'numeric'; input.pattern = '[0-9]*'; input.placeholder = 'Node number'; input.setAttribute('aria-label', `Node to connect to ${hub.name}`)
    const permanentLabel = element('label'); const permanent = element('input'); permanent.type = 'checkbox'; permanentLabel.append(permanent, document.createTextNode('Permanent'))
    const connect = element('button', '', 'Connect'); connect.type = 'submit'
    controls.append(input, permanentLabel, connect)
    if (hub.controlsAvailable === false) { input.disabled = permanent.disabled = connect.disabled = true; controls.append(element("span", "", "Monitoring only")) }
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
        const row = element('tr', connection.keyed && !isBridgeConnection(hub, connection) ? 'is-keyed' : '')
        ;[connection.node, connection.label || 'Unknown node', connection.received || 'Never', connection.direction || '—', connection.connected || '—', connection.mode || '—'].forEach((value, index) => {
          const cell = element('td')
          if ((index === 0 || index === 1) && isMapEligible(hub, connection)) {
            const link = element('a', 'node-map-link', value)
            link.href = `https://stats.allstarlink.org/stats/${encodeURIComponent(connection.node)}/networkMap`
            link.removeAttribute('href')
            link.href = '#network-map-dialog'
            link.title = 'View AllStarLink bubble network map'
            link.addEventListener('click', event => { event.preventDefault(); openNetworkMap(connection.node, hub) })
            cell.append(link)
          } else cell.textContent = value
          row.append(cell)
        })
        const actions = element('td'); const ban = element('button', 'ban-action', 'Ban'); ban.type = 'button'; ban.disabled = hub.controlsAvailable === false; ban.addEventListener('click', () => void banConnection(hub, connection)); actions.append(ban); const disconnect = element('button', 'disconnect', 'Disconnect'); disconnect.type = 'button'; disconnect.disabled = hub.controlsAvailable === false; disconnect.addEventListener('click', () => void control(hub, 'disconnect', connection.node)); actions.append(disconnect); row.append(actions)
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

  // #134: Only timestamped, verifiable events belong in Recent Activity.
  // 12-hour retention matches standard ASR; never turn a roster into TX history.
  function recentBridgeEvents(bridge) {
    const now = Date.now() / 1000
    const events = Array.isArray(bridge.activityEvents) ? bridge.activityEvents :
      (Array.isArray(bridge.txEvents) ? bridge.txEvents : [])
    return events.map((event) => {
      if (!event || typeof event !== 'object') return null
      const type = String(event.type || event.event || (event.direction === 'incoming' ? 'TX' : '')).toUpperCase()
      if (!['TX', 'CONNECT', 'DISCONNECT'].includes(type)) return null
      if (type === 'TX' && event.direction && event.direction !== 'incoming') return null
      const epoch = Number(event.epoch || event.eventEpoch || event.timestamp || 0)
      if (!Number.isFinite(epoch) || epoch <= 0 || epoch > now + 60 || now - epoch > 12 * 3600) return null
      const who = String(event.callsign || event.name || event.label || '').trim().slice(0, 80)
      if (!who) return null
      const when = new Date(epoch * 1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'})
      const duration = Number(event.duration_seconds ?? event.durationSeconds ?? event.duration)
      const label = type === "CONNECT" ? "CONN" : type === "DISCONNECT" ? "DISC" : "TX"
      const detail = who + (type === "TX" && Number.isFinite(duration) && duration >= 0 ? " — " + Math.round(duration) + "s" : "")
      return {epoch, type, when, label, detail}
    }).filter(Boolean).sort((a,b)=>b.epoch-a.epoch).slice(0,100)
  }

  // Match ASR duration semantics: native values are exact, observed values approximate.
  function bridgeClientDuration(entry, now = Date.now() / 1000) {
    const source = entry.connectionTimeSource
    const since = Number(entry.connectedSinceEpoch)
    const seen = Number(entry.connectionObservedEpoch)
    if (!Number.isFinite(since) || since <= 0 || since > now + 300) return ""
    if (source !== "native" && source !== "observed") return ""
    if (source === "observed" && (!Number.isFinite(seen) || seen < since || seen > now || now - seen > 45)) return ""
    const minutes = Math.max(0, Math.floor((now - since) / 60))
    const prefix = source === "observed" ? "~" : ""
    return prefix + (minutes < 1 ? "<1m" : minutes < 60 ? minutes + "m"
      : minutes < 1440 ? Math.floor(minutes / 60) + "h " + minutes % 60 + "m"
      : Math.floor(minutes / 1440) + "d " + Math.floor(minutes / 60) % 24 + "h")
  }

  // ASR owns permissions, capability discovery and every moderation action.
  const bridgeAdmin = document.body.dataset.bridgeAdmin === "1"
  const bridgeActionStatus = document.querySelector("#bridge-action-status")
  let bridgeCapabilities = new Map()
  let protectedBridgeIdentities = new Set()
  let bridgeCapabilityChecked = 0
  let bridgeCapabilityLoading = false
  let bridgeActionBusy = false
  async function refreshBridgeCapabilities() {
    if (!bridgeAdmin || bridgeCapabilityLoading || Date.now() - bridgeCapabilityChecked < 15000) return
    bridgeCapabilityLoading = true
    bridgeCapabilityChecked = Date.now()
    try {
      const response = await fetch("/asr/asr-api.php?action=runtime-config", { credentials:"same-origin", cache:"no-store" })
      const data = await response.json()
      if (!response.ok || !data.ok || String(data.node) !== "674982") throw Error("ASR capabilities unavailable")
      bridgeCapabilities = new Map((data.bridges || []).map(b => [String(b.mode).toLowerCase(), b]))
      protectedBridgeIdentities = new Set((data.protectedBanIdentities || []).map(v => String(v).toUpperCase()))
    } catch {
      bridgeCapabilities.clear()
      protectedBridgeIdentities.clear()
    } finally { bridgeCapabilityLoading = false }
    renderBridges(currentHubs)
  }
  function bridgeClientActionOptions(bridge, entry) {
    const config = bridgeCapabilities.get(String(bridge.id).toLowerCase())
    const capabilities = config?.adminCapabilities?.clientAdmin || []
    const mode = String(config?.mode || "").toLowerCase()
    const callsign = String(entry.callsign || "").replace(/\s+[A-Z]$/i, "").trim().toUpperCase()
    const match = /^([A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7})[-/][A-Z0-9]{1,4}$/.exec(callsign)
    const rule = match ? match[1] : callsign
    const valid = /^[A-Z0-9][A-Z0-9_./-]{0,14}$/
    const clientId = String(entry.clientId || "")
    const protectedTarget = protectedBridgeIdentities.has(callsign) || protectedBridgeIdentities.has(rule)
    const current = bridgeAdmin && config && valid.test(callsign) && Date.now() - bridgeCapabilityChecked <= 30000
    // ASR excludes its own DMR transport from the current roster. A distinct
    // DMR session can be kicked by exact client ID even if its call is protected
    // against a global ban. Preserve protection for call-only operations.
    const exactDmrSession = mode === "dmr" && /^[0-9]{1,9}$/.test(clientId) && Number(clientId) > 0
    return {config, mode, callsign, rule, clientId,
      kick: !!current && capabilities.includes("kickClient") && (mode === "dmr" ? exactDmrSession : !protectedTarget),
      ban: !!current && !protectedTarget && valid.test(rule) && capabilities.includes("banClient")}
  }
  async function moderateBridgeClient(bridge, entry, verb) {
    if (bridgeActionBusy) return
    const target = bridgeClientActionOptions(bridge, entry)
    if (!target[verb]) return
    let action, fields
    if (verb === "kick") {
      const effect = target.mode === "zello" ? "Mute the current incoming Zello stream" : "Disconnect this client using the ASR bridge backend"
      if (!window.confirm(effect + ": " + target.callsign + (target.mode === "dmr" ? " (DMR ID " + target.clientId + ")" : "") + "?")) return
      action = target.mode === "zello" ? "zello-client-kick" : "standalone-client-kick"
      fields = target.mode === "zello" ? {callsign:target.callsign} :
        {bridgeId:target.config.id, callsign:target.callsign, clientId:target.mode === "dmr" ? target.clientId : ""}
    } else {
      const duration = window.prompt("Global ban for " + target.rule + " across all ASR modes. Duration: 15m, 1h, 1w, 30d, permanent", "1h")
      if (duration === null) return
      if (!["15m","1h","1w","30d","permanent"].includes(duration)) { bridgeActionStatus.textContent = "Invalid ban duration."; return }
      if (!window.confirm("Apply GLOBAL ban to " + target.rule + " for " + duration + "?")) return
      action = "global-ban-control"
      fields = {verb:"ban",rule:target.rule,duration}
    }
    bridgeActionBusy = true
    bridgeActionStatus.textContent = (verb === "kick" ? "Kicking " : "Banning ") + target.callsign + "…"
    renderBridges(currentHubs)
    try {
      const response = await fetch("/asr/asr-api.php?action=" + action, {
        method:"POST", credentials:"same-origin", cache:"no-store",
        headers:{"Content-Type":"application/x-www-form-urlencoded","X-ASR-Requested-With":action},
        body:new URLSearchParams(fields).toString()
      })
      const data = await response.json()
      if (!response.ok || !data.ok) throw Error(data.error || "ASR moderation request failed.")
      if (verb === "kick") {
        if (data.verified !== true || Number(data.removed || 0) < 1) throw Error("Kick was not verified by the ASR backend.")
        bridgeActionStatus.textContent = target.callsign + ": " + (data.tgif && data.tgif.ok !== true ? "Local audio blocked; TGIF disconnect not verified: " + (data.tgif.error || "unknown error") : "Kick verified by ASR.")
      } else {
        const skipped = Array.isArray(data.standalone?.skipped) ? data.standalone.skipped : null
        const verified = data.verified === true && skipped && skipped.length === 0
        bridgeActionStatus.textContent = target.rule + ": " + (verified ? "Global ban enforcement verified." : "Global ban saved; full enforcement not verified." + (skipped?.length ? " Skipped: " + skipped.join(", ") : ""))
      }
    } catch (error) {
      bridgeActionStatus.textContent = error instanceof Error ? error.message : "ASR moderation request failed."
    } finally { bridgeActionBusy = false; renderBridges(currentHubs); void refresh() }
  }

  // Persist each bridge expansion independently in this browser.
  const bridgeExpansionKey = "asrplus.openBridges.v1"
  function loadBridgeExpansion() {
    try {
      const saved = JSON.parse(localStorage.getItem(bridgeExpansionKey) || "[]")
      return new Set(Array.isArray(saved) ? saved.filter(id => typeof id === "string") : [])
    } catch { return new Set() }
  }
  const openBridges = loadBridgeExpansion()
  function rememberBridgeExpansion(details) {
    const id = details.dataset.bridgeId
    if (!id) return
    if (openBridges.has(id) === details.open) return
    if (details.open) openBridges.add(id)
    else openBridges.delete(id)
    try { localStorage.setItem(bridgeExpansionKey, JSON.stringify([...openBridges])) } catch {}
  }
  function syncBridgeExpansion() {
    for (const details of bridgeGrid.querySelectorAll("details[data-bridge-id]")) rememberBridgeExpansion(details)
  }
  window.addEventListener("pagehide", syncBridgeExpansion)

  function renderBridges(hubs) {
    const previousDetails = [...bridgeGrid.querySelectorAll("details[data-bridge-id]")]
    for (const details of previousDetails) rememberBridgeExpansion(details)
    // A status refresh replaces the cards; retain each list's own scroll offset.
    const listScroll = new Map(previousDetails.map((details) => [details.dataset.bridgeId,
      [...details.querySelectorAll('.bridge-detail-list')].map((list) => list.scrollTop)]))
    const records = hubs.flatMap((hub) => (hub.bridges || []).map((bridge) => ({ hub, bridge })))
    bridgeGrid.replaceChildren(...records.map(({ hub, bridge }) => {
      const protocolNames = {dmr:'DMR',ysf:'YSF',p25:'P25',m17:'M17',nxdn:'NXDN',zello:'Zello'}
      const protocol = protocolNames[String(bridge.id || '').toLowerCase()] || String(bridge.id || '').toUpperCase()
      const statusText = ({online:'ONLINE',offline:'OFFLINE',unknown:'UNKNOWN'}[bridge.status] || 'UNKNOWN')
      const card = element('article', `bridge-card is-${bridge.status}${bridge.active ? ' is-active' : ''}`)
      const head = element('header'); head.append(element('h3', '', `${protocol} Bridge`), element('span', '', statusText)); card.append(head)
      const rows = element('dl')
      ;[['Bridge node', bridge.node || '—'], ['Talking', bridge.currentUser && !['-', 'Idle', 'Relay'].includes(String(bridge.currentUser).trim()) ? bridge.currentUser : '–']].forEach(([label, value]) => { const row = element('div'); row.append(element('dt', '', label), element('dd', '', value)); rows.append(row) })
      card.append(rows)
      const recent = recentBridgeEvents(bridge)
      const clients = Array.isArray(bridge.clients) ? bridge.clients : []
      const details = element('details', 'bridge-details'); details.dataset.bridgeId = `${hub.id}:${bridge.id}`; details.open = openBridges.has(details.dataset.bridgeId)
      details.addEventListener("toggle", () => {
        if (details.isConnected) rememberBridgeExpansion(details)
      })
      details.append(element('summary', '', `Connected Clients (${bridge.clientCount ?? 0})`))
      const clientSection = element('section', 'bridge-detail-section')
      const clientList = element('div', 'bridge-detail-list'); if (clients.length) clients.slice(0, 50).forEach((entry) => {
        const row = element("div", "bridge-client-duration-row")
        row.append(element("span", "bridge-client-name", entry.callsign || entry.label || entry.name || String(entry)))
        const elapsed = bridgeClientDuration(entry)
        if (elapsed) {
          const time = element("span", "bridge-client-duration", elapsed)
          time.title = entry.connectionTimeSource === "native" ? "Time connected reported by the bridge" : "Approximate time since ASR first observed this connection"
          row.append(time)
        } else row.append(element("span", "bridge-client-duration", ""))
        const options = bridgeClientActionOptions(bridge, entry)
        if (options.kick || options.ban) {
          const actions = element("div", "bridge-client-actions")
          for (const verb of ["kick","ban"]) {
            if (!options[verb]) continue
            const button = element("button", verb === "kick" ? "bridge-kick-action" : "ban-action", verb === "kick" ? "Kick" : "Ban")
            button.type = "button"
            button.disabled = bridgeActionBusy
            button.title = verb === "kick" ? "Kick this client through ASR" : "Global ASR ban across all supported modes"
            button.setAttribute("aria-label", (verb === "kick" ? "Kick " : "Ban ") + options.callsign)
            button.addEventListener("click", () => void moderateBridgeClient(bridge, entry, verb))
            actions.append(button)
          }
          row.classList.add("bridge-client-with-actions")
          row.append(actions); clientList.append(row)
        } else clientList.append(row)
      }); else clientList.append(element('div', 'is-empty', 'No connected clients reported.')); clientSection.append(clientList)
      const recentSection = element('section', 'bridge-detail-section'); recentSection.append(element('strong', '', 'Recent activity'))
      const recentList = element('div', 'bridge-detail-list bridge-recent-activity'); if (recent.length) recent.forEach((entry) => {
        const row = element("div", "bridge-activity-row")
        row.append(element("span", "bridge-activity-time", entry.when),
          element("span", "bridge-activity-label activity-" + entry.type.toLowerCase(), entry.label),
          element("span", "bridge-activity-detail", entry.detail))
        recentList.append(row)
      }); else recentList.append(element('div', 'is-empty', 'No recent timestamped activity reported.')); recentSection.append(recentList)
      details.append(clientSection, recentSection); card.append(details)
      return card
    }))
    for (const details of bridgeGrid.querySelectorAll('details[data-bridge-id]')) {
      const positions = listScroll.get(details.dataset.bridgeId)
      if (!positions) continue
      details.querySelectorAll('.bridge-detail-list').forEach((list, index) => {
        list.scrollTop = positions[index] || 0
      })
    }
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
    if (mapDialog.open && graphMode && graphHub) void renderCustomGraph(String(mapTitle.textContent.split(' · ').pop()), graphHub)
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
        favoritesHub.replaceChildren(...payload.hubs.filter(hub => hub.controlsAvailable !== false).map((hub) => { const option = element('option', '', `${hub.name} · ${hub.node}`); option.value = hub.id; return option }))
        favoritesHub.value = payload.hubs.some((hub) => hub.id === selectedHub && hub.controlsAvailable !== false) ? selectedHub : 'kentucky'
        renderBridges(payload.hubs)
        setFavicon(payload.hubs)
        const connected = payload.hubs.reduce((sum, hub) => sum + hub.connectionCount, 0)
        const bridges = payload.hubs.flatMap(h => h.bridges || [])
        const onlineBridges = bridges.filter(b => b.status === 'online').length
        summaryElement.textContent = `● ${payload.hubs.filter(h => h.available).length} of ${payload.hubs.length} Hubs Online · ${onlineBridges} of ${bridges.length} Bridges Online · ${connected} Connections`
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
  document.querySelector('#favorites-close').addEventListener('click', () => setFavoritesVisibility(false))
  favoritesToggle.addEventListener('click', () => {
    const open = favoritesElement.hidden
    setFavoritesVisibility(open)
    if (open) favoritesElement.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
  themeSelect.addEventListener('change', () => applyTheme(themeSelect.value))

  initializeModuleOrder(); applyTheme(localStorage.getItem('asrplus.theme') || 'netoholics'); setFavoritesVisibility(localStorage.getItem('asrplus.favoritesOpen') !== '0'); void loadFavorites()
  refresh(); window.setInterval(refresh, 1000)
  void refreshBridgeCapabilities(); window.setInterval(refreshBridgeCapabilities, 15000)
})()
