<?php
// AllScan Reimagined Lookup page
require_once('../include/common.php');
$html = new Html();
$msg = [];

asInit($msg);
asrInitAuthenticatedUser($msg);
pageInit();
?>
<section class="asr-lookup-page">
	<h1 class="asr-lookup-title">Lookup</h1>

	<div class="asr-lookup-grid asr-lookup-grid-single">
		<section class="asr-lookup-list-panel">
			<div class="asr-lookup-panel-head">
				<h2>Current Lookup Targets</h2>
				<div class="asr-lookup-refresh-meta">
					<span><span id="asrLookupCount">0 found</span><span id="asrLookupUpdated"></span></span>
					<span class="asr-lookup-refresh-interval">Refreshes every 15 seconds</span>
				</div>
			</div>
			<p class="asr-lookup-help">Click a callsign to open its QRZ listing. Click an AllStar node number for AllStarLink or an EchoLink number for EchoLink lookup.</p>
			<div id="asrLookupList" class="asr-lookup-list"></div>
		</section>

		<div class="asr-map-actions">
			<button id="asrStationMapOpen" type="button" class="asr-map-button" aria-pressed="false">View Station Map</button>
			<button id="asrNetworkMapOpen" type="button" class="asr-map-button" aria-expanded="false" disabled>View Network Map</button>
		</div>
		<section id="asrStationMapPanel" class="asr-map-panel" hidden>
			<div class="asr-map-head">
				<strong>Connected Station Map</strong>
				<button id="asrStationMapClose" type="button" class="asr-map-close">Close</button>
			</div>
			<div class="asr-map-body">
				<p class="asr-map-instruction">Click an orange dot to view station information.</p>
				<div id="asrStationMapFrame" class="asr-map-frame">
					<span class="asr-map-loading">Loading Map</span>
					<div id="asrStationMapCanvas" class="asr-map-canvas"></div>
					<div id="asrStationMapCard" class="asr-map-station-card" hidden></div>
				</div>
				<p id="asrStationMapSummary" class="asr-map-summary">Loading connected station locations...</p>
				<p id="asrStationMapUnmapped" class="asr-map-unmapped"></p>
				<p class="asr-map-note">Locations are approximate.</p>
				<p class="asr-map-style">Map style: Esri Dark Gray Canvas. Fallback location data © OpenStreetMap contributors.</p>
			</div>
		</section>
		<dialog id="asrNetworkMapPanel" class="asr-map-panel asr-network-dialog" aria-label="AllStarLink bubble network map">
			<div class="asr-map-head">
				<strong id="asrNetworkMapTitle">AllStarLink Network Map</strong>
				<span class="asr-network-controls"><button id="asrNetworkZoomOut" type="button" class="asr-map-button" aria-label="Zoom out">−</button><button id="asrNetworkZoomIn" type="button" class="asr-map-button" aria-label="Zoom in">+</button><button id="asrNetworkFit" type="button" class="asr-map-button">Fill</button><button id="asrNetworkMapClose" type="button" class="asr-map-close">Close</button></span>
			</div>
			<div class="asr-map-body">

				<aside id="asrNetworkNodeInfo" class="asr-network-node-info" hidden aria-live="polite"></aside>
				<div id="asrNetworkMapFrame" class="asr-map-frame asr-network-map-frame">
					<div class="asr-network-map-help"><b>Click</b> for details · <b>Double-click</b> to expand/collapse · <b>Drag</b> to move stations</div>
					<span id="asrNetworkMapLoading" class="asr-map-loading">Loading Map</span>
					<svg id="asrNetworkGraph" role="group" aria-label="Interactive AllStarLink network graph" hidden></svg>
					<img id="asrNetworkMapImage" alt="" loading="eager" decoding="async">
				</div>
				<p id="asrNetworkGraphStatus" class="asr-map-note" aria-live="polite">Interactive topology loads from AllStarLink.</p>
				<p class="asr-map-note"><button id="asrNetworkMapFallback" type="button" class="asr-map-button">Open Full Map</button></p>
			</div>
		</dialog>
	</div>
</section>

<style>
 .asr-network-dialog { position:fixed;inset:0;margin:auto;transform:none;display:flex;flex-direction:column;width:min(1100px,96vw);max-width:96vw;height:min(800px,92dvh);max-height:92dvh;box-sizing:border-box;overflow:hidden;padding:0;border:1px solid var(--border-color,#648092);border-radius:10px;background:#17252f;color:#f0f7fb; }
.asr-network-dialog:not([open]) {display:none}
.asr-network-dialog::backdrop {background:#000b}
.asr-network-node-info {position:absolute;z-index:12;top:65px;right:16px;width:min(360px,calc(100% - 32px));max-height:65%;overflow:auto;padding:14px;border:1px solid var(--border-color,#648092);border-radius:8px;background:var(--surface,var(--card-bg,#203745));color:inherit;box-shadow:0 8px 26px #0008;font-size:13px}
.asr-network-node-info[hidden] {display:none!important}
.asr-network-node-info button {float:right}
.asr-network-map-frame {position:relative;flex:1 1 auto;height:auto;min-height:0;overflow:auto;overscroll-behavior:contain;touch-action:pan-x pan-y;background:#172833;}
.asr-network-dialog>.asr-map-head {flex:0 0 auto;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;padding:10px 14px;background:#243c4b;}
.asr-network-dialog>.asr-map-body {position:relative;display:flex;flex:1 1 auto;flex-direction:column;min-height:0;overflow:hidden;padding:0;}

.asr-network-dialog .asr-network-map-frame {flex:1 1 auto;width:100%;min-height:0;height:auto;}
.asr-network-map-help {position:absolute;z-index:8;left:50%;bottom:8px;transform:translateX(-50%);width:max-content;max-width:calc(100% - 38px);padding:3px 8px;border:1px solid #a5c6d7;border-radius:7px;background:#f0f5f8;color:#183448;box-shadow:0 2px 12px #0008;text-align:center;font-size:13px;font-weight:500;line-height:1.2;pointer-events:none;}
.asr-network-map-help b {font-weight:800;color:#0a557f;}
.asr-network-dialog #asrNetworkGraph:not([hidden]) {position:relative;z-index:3;display:block;min-width:0;min-height:0;max-width:none;max-height:none;flex:none;opacity:1;visibility:visible;}
.asr-network-dialog #asrNetworkGraph circle,.asr-network-dialog #asrNetworkGraph line,.asr-network-dialog #asrNetworkGraph text {opacity:1;visibility:visible;}
.asr-network-dialog .asr-network-node-info {top:65px;}

#asrNetworkGraph { display:block; width:100%; height:100%; overflow:visible; background:#172833; touch-action:pan-x pan-y; }
#asrNetworkGraph[hidden], #asrNetworkMapImage[hidden] { display:none !important; }
#asrNetworkGraph [data-node] { cursor:pointer; outline:none; }
#asrNetworkGraph [data-node] text { text-decoration:none!important; paint-order:normal; }
#asrNetworkGraph [data-node]:focus circle { stroke-width:4; }
.asr-network-controls { display:flex; align-items:center; gap:7px; }
.asr-network-controls .asr-map-button,.asr-network-controls .asr-map-close {padding:7px 12px;border:1px solid #718f9e;border-radius:5px;background:#345366;color:#fff;font-weight:750;cursor:pointer;}
.asr-network-dialog #asrNetworkGraphStatus,.asr-network-dialog #asrNetworkMapFallback {display:none!important;}
.asr-network-map-frame img {
	position:relative;
	z-index:1;
	display:block;
	width:100%;
	height:100%;
	object-fit:contain;
	object-position:center center;
}

.asr-map-button:disabled {
	cursor:default;
	opacity:.72;
}

@media (max-width:650px) {
  .asr-network-dialog {width:98vw;max-width:98vw;height:90dvh;max-height:90dvh;}
  .asr-network-dialog>.asr-map-head {padding:7px;font-size:12px;}
  .asr-network-controls {gap:5px;}
  .asr-network-controls button {padding:6px 8px;}
  .asr-network-map-help {font-size:12px;white-space:normal;}
}
</style>

<script>
(function () {
	var asrBase = <?php echo json_encode(rtrim($urlbase, '/'), JSON_UNESCAPED_SLASHES); ?>;
	var list = document.getElementById('asrLookupList');
	var count = document.getElementById('asrLookupCount');
	var updated = document.getElementById('asrLookupUpdated');
	var items = [];
	var bridgeNodes = {};
	var mapOpen = document.getElementById('asrStationMapOpen');
	var mapClose = document.getElementById('asrStationMapClose');
	var mapPanel = document.getElementById('asrStationMapPanel');
	var mapFrame = document.getElementById('asrStationMapFrame');
	var mapCanvas = document.getElementById('asrStationMapCanvas');
	var mapCard = document.getElementById('asrStationMapCard');
	var mapSummary = document.getElementById('asrStationMapSummary');
	var mapUnmapped = document.getElementById('asrStationMapUnmapped');
	var networkMapOpen = document.getElementById('asrNetworkMapOpen');
    var networkMapTitle = document.getElementById('asrNetworkMapTitle');
	var networkMapClose = document.getElementById('asrNetworkMapClose');
	var networkMapPanel = document.getElementById('asrNetworkMapPanel');
	var networkMapFrame = document.getElementById('asrNetworkMapFrame');
	var networkMapLoading = document.getElementById('asrNetworkMapLoading');
	var networkMapImage = document.getElementById('asrNetworkMapImage');
	var networkMapFallback = document.getElementById('asrNetworkMapFallback');
	var networkGraph = document.getElementById('asrNetworkGraph');
	var networkNodeInfo = document.getElementById('asrNetworkNodeInfo');
	var networkInfoRequest = 0;
	var networkGraphStatus = document.getElementById('asrNetworkGraphStatus');
	var networkGraphZoom = 1;
	var networkGraphRequest = 0;
	var networkGraphEdges = new Set();
	var networkGraphChildren = new Map();
	var networkGraphNodes = new Set();
	var networkGraphCollapsed = new Set();
	var networkGraphPositions = new Map();
	var networkGraphRoot = '';
    var networkGraphSeeds = [];
	var networkGraphDragging = null;
	var networkGraphLastDrag = 0;
	var networkGraphClickTimer = null;
	var networkGraphLastTap = null;
	var networkGraphUnavailable = false;
	var networkGraphTouchDistance = 0;
	var networkGraphTouchZoom = 1;
	var networkGraphColors = function () {
        return {root:'#e1ad45',bridge:'#87929b',hub:'#3bc48a',node:'#4ab9e6',edge:'#809ca9',foreground:'#edf5f8',background:'#172833'};
	};
    var networkGraphKinds = new Map();
    var networkGraphLabels = new Map();
    var networkGraphCanvasWidth = 1000;
    var networkGraphCanvasHeight = 700;
    var networkGraphAutoFit = true;

	function renderNetworkGraph() {
		var colors = networkGraphColors();
		var nodes = Array.from(networkGraphNodes);
		var depth = new Map([[networkGraphRoot,0]]);
		var queue = [networkGraphRoot];
		while(queue.length) {
			var current = queue.shift();
			if(networkGraphCollapsed.has(current)) continue;
			Array.from(networkGraphChildren.get(current)||[]).forEach(function(next){if(!depth.has(next)){depth.set(next,depth.get(current)+1);queue.push(next);}});
		}
		var visible = nodes.filter(function (n) { return depth.has(n); });
		var levels = new Map();
		visible.forEach(function(n) { var d=depth.get(n); if(!levels.has(d)) levels.set(d,[]); levels.get(d).push(n); });
		var maxPerColumn=11,laneWidth=158,rowHeight=66,layerWidths=new Map(),offsets=new Map(),nextX=95;
        levels.forEach(function(ids,level){ids.sort(function(a,b){return a.localeCompare(b,undefined,{numeric:true});});layerWidths.set(level,Math.max(1,Math.ceil(ids.length/maxPerColumn)));});
        var maxDepth=Math.max(1,...Array.from(levels.keys()));
        for(var level=0;level<=maxDepth;level++){offsets.set(level,nextX);nextX+=(layerWidths.get(level)||1)*laneWidth+95;}
        var width=Math.max(1000,nextX+80),height=Math.max(700,130+maxPerColumn*rowHeight);
        levels.forEach(function(ids,level){ids.forEach(function(n,i){
            if(networkGraphPositions.has(n))return;
            var col=Math.floor(i/maxPerColumn),row=i%maxPerColumn;
            var inColumn=Math.min(maxPerColumn,ids.length-col*maxPerColumn);
            networkGraphPositions.set(n,[offsets.get(level)+col*laneWidth,(height-(inColumn-1)*rowHeight)/2+row*rowHeight]);
        });});
        networkGraphPositions.forEach(function(pos){width=Math.max(width,pos[0]+95);height=Math.max(height,pos[1]+95);});
        var ns='http://www.w3.org/2000/svg',frame=document.createDocumentFragment();
        function el(tag,attributes,parent) { var node=document.createElementNS(ns,tag); Object.keys(attributes).forEach(function(k){node.setAttribute(k,attributes[k]);}); (parent||frame).appendChild(node); return node; }
        networkGraphCanvasWidth=width;networkGraphCanvasHeight=height;
        var fit=Math.min(1,(networkMapFrame.clientWidth-24)/width,(networkMapFrame.clientHeight-24)/height);
        var scale=(networkGraphAutoFit?fit:1)*networkGraphZoom;
        networkGraph.setAttribute('viewBox','0 0 '+width+' '+height);
        networkGraph.style.width=Math.max(1,Math.round(width*scale))+'px';networkGraph.style.height=Math.max(1,Math.round(height*scale))+'px';
		networkGraph.style.background=colors.background;

		networkGraphEdges.forEach(function(key) {
			var pair=key.split(':'); if(!depth.has(pair[0])||!depth.has(pair[1])||(networkGraphCollapsed.has(pair[0])||networkGraphCollapsed.has(pair[1]))) return;
			var a=networkGraphPositions.get(pair[0]),b=networkGraphPositions.get(pair[1]);
			el('line',{x1:a[0],y1:a[1],x2:b[0],y2:b[1],stroke:colors.edge,'stroke-opacity':'.6','stroke-width':'1.6'});
		});
		visible.forEach(function(n) {
			var xy=networkGraphPositions.get(n),g=el('g',{'data-node':n,tabindex:'0',role:'button','aria-label':'Node '+n+'. Enter opens AllStarLink details; C collapses connections'});
			var kind=networkGraphKinds.get(n)|| (n===networkGraphRoot?'root':'node');
            var fill=kind==='root'?colors.root:kind==='bridge'?colors.bridge:kind==='hub'?colors.hub:colors.node;
            el('circle',{cx:xy[0],cy:xy[1],r:n===networkGraphRoot?26:16,fill:fill,stroke:colors.background,'stroke-width':'2'},g);
            var title=el('title',{},g);title.textContent=n+' · '+(networkGraphLabels.get(n)||n)+' (click for details, double-click to toggle children)';
			var label=el('text',{x:xy[0],y:xy[1]+33,'text-anchor':'middle',fill:colors.foreground,'font-size':'13','font-weight':'700'},g);label.textContent=n;
		});
        // Swap the complete SVG tree once per update to avoid visible intermediate frames.
        networkGraph.replaceChildren(frame);
        if(!networkGraphUnavailable)networkGraphStatus.textContent=visible.length+' stations · '+networkGraphEdges.size+' connections. Select a node to open its AllStarLink details; press C to collapse.';
	}
	function showNetworkNodeInfo(id) {
        var request=++networkInfoRequest;
        networkNodeInfo.hidden=false;
        networkNodeInfo.replaceChildren();
        var title=document.createElement('strong');title.textContent='Node '+id;networkNodeInfo.appendChild(title);
        var close=document.createElement('button');close.type='button';close.textContent='×';close.setAttribute('aria-label','Close node details');close.addEventListener('click',function(){networkNodeInfo.hidden=true;networkInfoRequest++;});networkNodeInfo.appendChild(close);
        var details=document.createElement('div');details.textContent='Loading AllStarLink details…';networkNodeInfo.appendChild(details);
        fetch('node-info.php?node='+encodeURIComponent(id),{credentials:'same-origin'}).then(function(r){if(!r.ok)throw Error('Unavailable');return r.json();}).then(function(data){
            if(request!==networkInfoRequest)return;
            details.replaceChildren();
            if(data.callsign)title.textContent=data.callsign+' · '+id;
            [['Callsign',data.callsign],['Description',data.description||data.name],['Location',data.location],['Connections',(data.links||[]).join(', ')||'Not currently available']].forEach(function(field){
                if(!field[1])return;var row=document.createElement('div'),label=document.createElement('b');label.textContent=field[0]+': ';row.appendChild(label);row.appendChild(document.createTextNode(String(field[1])));details.appendChild(row);
            });
        }).catch(function(){if(request===networkInfoRequest)details.textContent='Unable to load node details right now.';});
    }
	function loadNetworkGraph(root,seeds) {
		var request=++networkGraphRequest;
		networkGraphRoot=root;networkMapTitle.textContent='AllStarLink Network Map · '+root;networkGraphEdges.clear();networkGraphChildren.clear();networkGraphNodes=new Set([root]);networkGraphPositions.clear();networkGraphCollapsed.clear();networkGraphKinds.clear();networkGraphLabels.clear();networkGraphKinds.set(root,'root');networkGraphLabels.set(root,root);
		networkGraphZoom=1;networkGraphAutoFit=true;networkGraphUnavailable=false;networkGraphSeeds=Array.isArray(seeds)?seeds.filter(function(seed){return seed&&/^\d{3,7}$/.test(String(seed.node))&&String(seed.node)!==root;}):networkGraphSeeds;networkGraph.removeAttribute('hidden');networkMapImage.hidden=true;
		networkMapLoading.hidden=true;renderNetworkGraph();
        var pending=[root],visited=new Set(),unchanged=0,temporaryFailures=new Map();
        var cacheKey='asr-standard-network-'+root;
        function removeEdge(node,linked){
            var key=[node,linked].sort().join(':');networkGraphEdges.delete(key);
            [node,linked].forEach(function(id){var other=id===node?linked:node;var set=networkGraphChildren.get(id);if(set){set.delete(other);if(!set.size)networkGraphChildren.delete(id);}});
        }
        function addEdge(node,linked,meta){
            if(!/^\d{3,7}$/.test(linked)||linked===node)return false;
            if(linked===networkGraphRoot)networkGraphKinds.set(linked,'root');
            else if(meta&&meta.kind){var priorKind=networkGraphKinds.get(linked);if(!priorKind||priorKind==='node'||meta.kind==='bridge'||meta.kind==='hub')networkGraphKinds.set(linked,meta.kind);}
            else if(!networkGraphKinds.has(linked))networkGraphKinds.set(linked,/^100[1-6]$/.test(linked)?'bridge':'node');
            if(meta&&meta.label&&!networkGraphLabels.has(linked))networkGraphLabels.set(linked,meta.label);else if(!networkGraphLabels.has(linked))networkGraphLabels.set(linked,linked);
            var edge=[node,linked].sort().join(':');networkGraphEdges.add(edge);
            for(var pair of [[node,linked],[linked,node]]){
                if(!networkGraphChildren.has(pair[0]))networkGraphChildren.set(pair[0],new Set());
                networkGraphChildren.get(pair[0]).add(pair[1]);
            }
            if(networkGraphNodes.has(linked))return false;
            networkGraphNodes.add(linked);pending.push(linked);return true;
        }
        try{
            var saved=JSON.parse(localStorage.getItem(cacheKey)||'null');
            if(saved&&Date.now()-saved.saved<86400000&&Array.isArray(saved.edges)){
                saved.edges.forEach(function(pair){if(Array.isArray(pair)&&pair.length===2&&/^\d{3,7}$/.test(pair[0])&&/^\d{3,7}$/.test(pair[1]))addEdge(pair[0],pair[1]);});
                renderNetworkGraph();
            }
        }catch(_){}
        Array.from(networkGraphChildren.get(root)||[]).forEach(function(old){if(!networkGraphSeeds.some(function(seed){return String(seed.node)===old;}))removeEdge(root,old);});
        networkGraphSeeds.forEach(function(seed){addEdge(root,String(seed.node),seed);});
        function save(){try{localStorage.setItem(cacheKey,JSON.stringify({saved:Date.now(),edges:Array.from(networkGraphEdges,function(key){return key.split(':');})}));}catch(_){}}
        async function discover(){
            while(request===networkGraphRequest&&networkMapPanel.open&&pending.length){
                var batch=[];
                while(pending.length&&batch.length<4){
                    var node=String(pending.shift());
                    if(visited.has(node)||!/^\d{4,7}$/.test(node)||/^100[1-6]$/.test(node))continue;
                    visited.add(node);batch.push(node);
                }
                if(!batch.length)break;
                var results=await Promise.all(batch.map(async function(node){
                    try{var response=await fetch('network-graph.php?node='+encodeURIComponent(node)+'&refresh='+Date.now(),{credentials:'same-origin',cache:'no-store'});return {node:node,data:response.ok?await response.json():null};}catch(_){return {node:node,data:null};}
                }));
                if(request!==networkGraphRequest||!networkMapPanel.open)return;
                var discovered=false,reported=false,throttled=false;
                results.forEach(function(result){
                    if(!result||!result.data||!result.data.reported){
                        if(result && result.node && (result.data?.status==='rate-limited'||result.data?.status==='busy'||(temporaryFailures.get(result.node)||0)<2)){
                            if(result.data?.status==='rate-limited')throttled=true;
                            if(result.data?.status!=='rate-limited'&&result.data?.status!=='busy')temporaryFailures.set(result.node,(temporaryFailures.get(result.node)||0)+1);
                            pending.push(result.node);visited.delete(result.node);
                        }
                        return;
                    }
                    reported=true;
                    var currentLinks=new Set((result.data.links||[]).map(String).filter(function(id){return /^\d{3,7}$/.test(id)&&id!==result.node;}));
                    if(result.node===networkGraphRoot)networkGraphSeeds.forEach(function(seed){currentLinks.add(String(seed.node));});
                    Array.from(networkGraphChildren.get(result.node)||[]).forEach(function(old){if(!currentLinks.has(old))removeEdge(result.node,old);});
                    currentLinks.forEach(function(linked){if(addEdge(result.node,linked,{kind:/^100[1-6]$/.test(linked)?'bridge':'node',label:linked}))discovered=true;});
                    // Persist successful refreshes too, including removals, not only newly found nodes.
                    save();
                });
                if(discovered){unchanged=0;}else unchanged++;
                networkGraphUnavailable=!reported&&!networkGraphEdges.size;
                renderNetworkGraph();
                if(networkGraphUnavailable)networkGraphStatus.textContent='No live connection report available. Waiting for updates.';
                if(pending.length){await new Promise(function(resolve){setTimeout(resolve,throttled||unchanged>=2?61000:1200);});}
                if(unchanged>=2)unchanged=0;
            }
        }
        void discover().catch(function(){if(request===networkGraphRequest)networkGraphStatus.textContent='Network discovery paused. Reopen to retry.';});

	}
	document.getElementById('asrNetworkZoomIn').addEventListener('click',function(){networkGraphZoom=Math.min(5,networkGraphZoom*1.25);renderNetworkGraph();});
	document.getElementById('asrNetworkZoomOut').addEventListener('click',function(){networkGraphZoom=Math.max(.5,networkGraphZoom/1.25);renderNetworkGraph();});
	document.getElementById('asrNetworkFit').addEventListener('click',function(){networkGraphAutoFit=true;networkGraphZoom=1;renderNetworkGraph();networkMapFrame.scrollTo(0,0);});
	function graphSvgPoint(event) {
		var point=networkGraph.createSVGPoint();point.x=event.clientX;point.y=event.clientY;
		var matrix=networkGraph.getScreenCTM();
		return matrix ? point.matrixTransform(matrix.inverse()) : point;
	}
	networkGraph.addEventListener('pointerdown',function(event){
		var group=event.target.closest('[data-node]');
		if(!group || event.button!==0)return;
		var id=group.getAttribute('data-node'),pos=networkGraphPositions.get(id);
		if(!pos)return;
		networkGraphDragging={id:id,origin:pos.slice(),start:graphSvgPoint(event),pointer:event.pointerId,moved:false};
		networkGraph.setPointerCapture(event.pointerId);
	});
	networkGraph.addEventListener('pointermove',function(event){
		var drag=networkGraphDragging;if(!drag || drag.pointer!==event.pointerId)return;
		var pt=graphSvgPoint(event),dx=pt.x-drag.start.x,dy=pt.y-drag.start.y;
		if(Math.hypot(dx,dy)>4)drag.moved=true;
		if(drag.moved){networkGraphPositions.set(drag.id,[Math.max(30,drag.origin[0]+dx),Math.max(30,drag.origin[1]+dy)]);renderNetworkGraph();}
	});
	networkGraph.addEventListener('pointerup',function(event){
		if(networkGraphDragging && networkGraphDragging.pointer===event.pointerId){
			var id=networkGraphDragging.id,moved=networkGraphDragging.moved;
            if(moved)networkGraphLastDrag=Date.now();
			networkGraphDragging=null;
            if(!moved){
                var now=performance.now();
                if(networkGraphLastTap && networkGraphLastTap.id===id && now-networkGraphLastTap.time<360){
                    clearTimeout(networkGraphClickTimer);networkGraphLastTap=null;
                    networkGraphCollapsed.has(id)?networkGraphCollapsed.delete(id):networkGraphCollapsed.add(id);renderNetworkGraph();
                }else{
                    networkGraphLastTap={id:id,time:now};clearTimeout(networkGraphClickTimer);
                    networkGraphClickTimer=setTimeout(function(){networkGraphLastTap=null;showNetworkNodeInfo(id);},370);
                }
            }
		}
	});
	networkGraph.addEventListener('pointercancel',function(){networkGraphDragging=null;});
	networkGraph.addEventListener('wheel',function(event){
		if(!event.ctrlKey && !event.metaKey)return;
		event.preventDefault();networkGraphZoom=Math.max(.5,Math.min(4,networkGraphZoom*(event.deltaY<0?1.1:.9)));renderNetworkGraph();
	},{passive:false});
	function graphTouchDistance(touches){return Math.hypot(touches[0].clientX-touches[1].clientX,touches[0].clientY-touches[1].clientY);}
	networkGraph.addEventListener('touchstart',function(event){if(event.touches.length===2){networkGraphTouchDistance=graphTouchDistance(event.touches);networkGraphTouchZoom=networkGraphZoom;}},{passive:true});
	networkGraph.addEventListener('touchmove',function(event){if(event.touches.length===2 && networkGraphTouchDistance){event.preventDefault();networkGraphZoom=Math.max(.5,Math.min(4,networkGraphTouchZoom*graphTouchDistance(event.touches)/networkGraphTouchDistance));renderNetworkGraph();}},{passive:false});
	networkGraph.addEventListener('touchend',function(event){if(event.touches.length<2)networkGraphTouchDistance=0;},{passive:true});
	networkGraph.addEventListener('keydown',function(event){var group=event.target.closest('[data-node]');if(!group)return;var id=group.getAttribute('data-node');if(event.key.toLowerCase()==='c'){networkGraphCollapsed.has(id)?networkGraphCollapsed.delete(id):networkGraphCollapsed.add(id);renderNetworkGraph();var replacement=Array.from(networkGraph.querySelectorAll('[data-node]')).find(function(g){return g.getAttribute('data-node')===id;});if(replacement)replacement.focus();event.preventDefault();}else if(event.key==='Enter'||event.key===' '){event.preventDefault();showNetworkNodeInfo(id);}});


	networkMapPanel.addEventListener('click',function(event){if(event.target===networkMapPanel)networkMapPanel.close();});
	networkMapPanel.addEventListener('close',function(){stopNetworkMapRefresh();networkGraphRequest++;networkInfoRequest++;networkNodeInfo.hidden=true;networkMapOpen.setAttribute('aria-expanded','false');});
	new MutationObserver(function(){if(networkMapPanel.open)renderNetworkGraph();}).observe(document.documentElement,{attributes:true,attributeFilter:['class','data-theme','style']});
    new MutationObserver(function(){if(networkMapPanel.open)renderNetworkGraph();}).observe(document.body,{attributes:true,attributeFilter:['class','data-theme','style']});
	var leafletPromise = null;
	var stationMap = null;
	var stationMarkers = null;
	var stationPointsKey = '';
	var stationMapHasValidData = false;
	var stationMapIsStale = false;
	var stationMapRequestSequence = 0;
	var lookupLoading = false;
	var networkMapNode = '';
	var networkMapUrl = '';
	var networkMapRefreshTimer = null;

	function escapeHtml(value) {
		return String(value == null ? '' : value).replace(/[&<>"']/g, function (char) {
			return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[char];
		});
	}

	function viewerTime(value) {
		var date = new Date(String(value || ''));
		if(isNaN(date.getTime())) return '';
		return date.toLocaleTimeString([], {hour:'numeric', minute:'2-digit', second:'2-digit'});
	}

	function isCallsign(value) {
		return /^[A-Z]{1,2}[0-9][A-Z0-9]{1,4}$/i.test(String(value || '').trim());
	}

	function qrzUrl(value) {
		var text = String(value || '').trim().toUpperCase();
		return isCallsign(text) ? 'https://www.qrz.com/db/' + encodeURIComponent(text) : 'https://www.qrz.com/';
	}

	function echolinkLookupValue(node) {
		var match = String(node || '').trim().match(/^3(\d{6})$/);
		return match ? match[1] : '';
	}

	function allstarUrl(node) {
		var text = String(node || '').trim();
		return /^\d{3,10}$/.test(text) && !echolinkLookupValue(text)
			? 'http://stats.allstarlink.org/stats/' + encodeURIComponent(text)
			: '';
	}

	function networkMapBaseUrl(node) {
		var text = String(node || '').trim();
		return /^\d{3,10}$/.test(text)
			? 'https://stats.allstarlink.org/stats/' + encodeURIComponent(text) + '/networkMap'
			: '';
	}

	function freshNetworkMapUrl() {
		var baseUrl = networkMapBaseUrl(networkMapNode);
		if(!baseUrl) return '';
		networkMapUrl = baseUrl + '?nh_refresh=' + encodeURIComponent(Date.now());
		return networkMapUrl;
	}

	function syncNetworkMapLoader() {
		if(networkMapImage.complete && networkMapImage.naturalWidth > 0) {
			networkMapLoading.hidden = true;
			return;
		}
		networkMapLoading.hidden = false;
		networkMapLoading.textContent = 'Loading Map';
	}

	function refreshNetworkMap() {
		if(!networkMapPanel.open) return;
		var freshUrl = freshNetworkMapUrl();
		if(!freshUrl) return;
		networkMapImage.src = freshUrl;
		networkMapImage.alt = 'AllStarLink network map for node ' + networkMapNode;
		syncNetworkMapLoader();
	}

	function scheduleNetworkMapRefresh() {
		if(networkMapRefreshTimer) window.clearTimeout(networkMapRefreshTimer);
		networkMapRefreshTimer = window.setTimeout(function () {
			if(!networkMapPanel.open) return;
			refreshNetworkMap();
			scheduleNetworkMapRefresh();
		}, 300000);
	}

	function stopNetworkMapRefresh() {
		if(networkMapRefreshTimer) window.clearTimeout(networkMapRefreshTimer);
		networkMapRefreshTimer = null;
	}

	function setNetworkMapNode(node,seeds) {
		var nextNode = String(node || '').trim();
        var nextSeeds=Array.isArray(seeds)?seeds.filter(function(seed){return seed&&/^\d{3,7}$/.test(String(seed.node))&&String(seed.node)!==nextNode;}):networkGraphSeeds;
        var seedKey=function(items){return items.map(function(x){return [x.node,x.kind,x.label].join('|');}).sort().join(';');};
        var seedsChanged=seedKey(nextSeeds)!==seedKey(networkGraphSeeds);
		if(!networkMapBaseUrl(nextNode)) return;
		if((networkMapNode !== nextNode||seedsChanged)&&networkMapPanel.open)loadNetworkGraph(nextNode,nextSeeds);
        networkGraphSeeds=nextSeeds;
		networkMapNode = nextNode;
		networkMapOpen.disabled = false;
	}

	function extractCallsign(value) {
		var text = String(value || '');
		var match = text.match(/\b([A-Z]{1,2}[0-9][A-Z0-9]{1,4})\b/i);
		if(match) return match[1].toUpperCase();
		match = text.match(/\b([A-Z]{1,2}[0-9][A-Z0-9]{1,4})(?=(?:IAX|DMR|YSF|ZELLO|ECHOLINK|EL)(?:\b|$))/i);
		return match ? match[1].toUpperCase() : '';
	}

	function isIaxClient(rowNode, label, detail) {
		var nodeText = String(rowNode || '').trim();
		var text = [rowNode, label, detail].join(' ');
		return !/^\d+$/.test(nodeText) || /\b(IAX|IaxRpt|Web Transceiver|WebTransceiver)\b/i.test(text);
	}

	function isPrivateNode(node) {
		var text = String(node || '').trim();
		return /^\d+$/.test(text) && Number(text) > 0 && Number(text) < 2000;
	}

	function groupTitle(source) {
		var text = String(source || '').trim();
		if(/EchoLink/i.test(text)) return 'EchoLink Connections';
		if(/IAX|Web/i.test(text)) return 'IAX / Web Clients';
		if(/DMR Client/i.test(text)) return 'DMR Clients';
		if(/YSF Client/i.test(text)) return 'YSF Clients';
		if(/ZELLO Client/i.test(text)) return 'Zello Clients';
		if(/Client/i.test(text)) return text.replace(/\s+Client$/i, '') + ' Clients';
		return 'Connected Nodes';
	}

	function groupedItems(rows) {
		var groups = {};
		rows.forEach(function (item) {
			var title = groupTitle(item.source);
			if(!groups[title]) groups[title] = [];
			groups[title].push(item);
		});
		return Object.keys(groups).sort(function (a, b) {
			if(a === 'Connected Nodes') return -1;
			if(b === 'Connected Nodes') return 1;
			return a.localeCompare(b);
		}).map(function (title) {
			return { title:title, rows:groups[title] };
		});
	}

	function upsertItem(item) {
		var nextKey = String(item.source || '') + '|' + String(item.node || '') + '|' + String(item.callsign || '') + '|' + String(item.label || '');
		var node = String(item.node || '');
		for(var index = 0; index < items.length; index += 1) {
			var existing = items[index];
			var existingKey = String(existing.source || '') + '|' + String(existing.node || '') + '|' + String(existing.callsign || '') + '|' + String(existing.label || '');
			if((node && String(existing.node || '') === node) || existingKey === nextKey) {
				var incomingLabel = String(item.label || '').trim();
				var incomingMissing = !item.callsign && /^(?:Node\s+)?not\s+in\s+(?:the\s+)?database$|^Node\s+\d+$/i.test(incomingLabel);
				if(incomingMissing && existing.callsign) {
					items[index] = Object.assign({}, item, {
						label: existing.label,
						callsign: existing.callsign,
						qrzUrl: existing.qrzUrl,
						locationHint: existing.locationHint || item.locationHint || ''
					});
				} else {
					items[index] = item;
				}
				return;
			}
		}
		items.push(item);
	}

	function render() {
		var rows = items.slice();
		count.textContent = rows.length + ' found';

		if(!rows.length) {
			list.innerHTML = '<p class="asr-lookup-empty">No current lookup targets match.</p>';
			return;
		}

		list.innerHTML = groupedItems(rows).map(function (group) {
			var body = group.rows.map(function (item) {
				var isIax = /IAX|Web/i.test(String(item.source || ''));
				var detailText = String(item.detail || '');
				var iaxParts = isIax ? detailText.split(/\s+·\s+/) : [];
				var iaxClient = isIax ? (iaxParts[0] || '') : '';
				var iaxDescription = isIax ? (iaxParts.slice(1).join(' · ') || item.label || '') : '';
				var callsignText = escapeHtml(item.callsign || item.label || 'Unknown');
				var qrzHref = item.qrzUrl || qrzUrl(item.callsign || item.label || '');
				var callsign = qrzHref && qrzHref !== 'https://www.qrz.com/'
					? '<a class="asr-lookup-card-title" href="' + escapeHtml(qrzHref) + '" target="_blank" rel="noreferrer">' + callsignText + '</a>'
					: '<strong class="asr-lookup-card-title">' + callsignText + '</strong>';
				var number = item.node ? escapeHtml(item.node) : escapeHtml(isIax ? iaxClient : detailText);
				if(item.echolinkLookup) {
						number = '<a class="asr-lookup-inline-link" href="' + asrBase + '/echolink-lookup/?lookup=' + encodeURIComponent(item.echolinkLookup) + '">' + escapeHtml(item.echolinkLookup) + '</a>';
				} else if(item.node && item.allstarUrl) {
					number = '<a class="asr-lookup-inline-link" href="' + escapeHtml(item.allstarUrl) + '" target="_blank" rel="noreferrer">' + escapeHtml(item.node) + '</a>';
				}
				var label = escapeHtml(item.label || '');
				var meta = number ? '<span class="asr-lookup-number">' + number + '</span>' : '';
				var detail = isIax && iaxDescription
					? '<p>' + escapeHtml(iaxDescription) + '</p>'
					: label && label.toUpperCase() !== String(item.callsign || '').toUpperCase()
					? '<p>' + label + '</p>'
					: '';
				return '<article class="asr-lookup-card">'
					+ '<div class="asr-lookup-card-head">' + callsign + meta + '</div>'
					+ detail
					+ '</article>';
			}).join('');
			return '<section class="asr-lookup-group"><h3>' + escapeHtml(group.title) + '</h3>' + body + '</section>';
		}).join('');

	}

	function loadLeaflet() {
		if(window.L) return Promise.resolve(window.L);
		if(leafletPromise) return leafletPromise;
		leafletPromise = new Promise(function (resolve, reject) {
			if(!document.querySelector('link[data-asr-leaflet]')) {
				var link = document.createElement('link');
				link.rel = 'stylesheet';
				link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
				link.setAttribute('data-asr-leaflet', '1');
				document.head.appendChild(link);
			}
			var script = document.createElement('script');
			script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
			script.async = true;
			script.onload = function () { resolve(window.L); };
			script.onerror = function () { reject(new Error('Map library unavailable')); };
			document.head.appendChild(script);
		});
		return leafletPromise;
	}

	function stationCardHtml(point) {
		var name = String(point.name || '').replace(/\s+/g, ' ').trim();
		return '<button type="button" aria-label="Close station information" data-asr-map-card-close>&times;</button>'
			+ '<strong>' + escapeHtml(point.callsign || '-') + '</strong>'
			+ '<span>' + escapeHtml(name || 'Name unavailable') + '</span>'
			+ '<span>' + escapeHtml(point.location || 'Location unavailable') + '</span>';
	}

	function pointsKey(points) {
		return points.map(function (point) {
			return [point.callsign, point.name, point.node, point.location, point.lat, point.lng].join('|');
		}).sort().join('~');
	}

	function applyStationMapData(payload, fitMap) {
		var points = payload && Array.isArray(payload.points) ? payload.points : [];
		var missed = payload && Array.isArray(payload.unmapped) ? payload.unmapped : [];
		var nextKey = pointsKey(points) + '||' + missed.map(function (item) {
			return [item.callsign, item.node, item.label].join('|');
		}).sort().join('~');
		if(nextKey === stationPointsKey && !stationMapIsStale) return;
		stationPointsKey = nextKey;
		stationMapHasValidData = true;
		stationMapIsStale = false;
		stationMarkers.clearLayers();
		mapCard.hidden = true;

		var bounds = [];
		points.forEach(function (point) {
			var lat = Number(point.lat);
			var lng = Number(point.lng);
			if(!isFinite(lat) || !isFinite(lng)) return;
			bounds.push([lat, lng]);
			window.L.marker([lat, lng], {
				icon: window.L.divIcon({className:'asr-map-marker', iconSize:[18,18], iconAnchor:[9,9]}),
				keyboard:false
			}).addTo(stationMarkers).on('click', function (event) {
				if(event && event.originalEvent) window.L.DomEvent.stopPropagation(event.originalEvent);
				mapCard.innerHTML = stationCardHtml(point);
				mapCard.hidden = false;
			});
		});

		if(fitMap) {
			if(bounds.length > 1) stationMap.fitBounds(bounds, {padding:[28,28], maxZoom:4});
			else if(bounds.length === 1) stationMap.setView(bounds[0], 4);
		}
		mapSummary.textContent = bounds.length
			? bounds.length + ' connected station location' + (bounds.length === 1 ? '' : 's') + ' mapped.'
			: 'No connected station locations are available right now.';
		mapUnmapped.textContent = missed.length
			? 'Not mapped: ' + missed.map(function (item) { return item.callsign || item.node || item.label || ''; }).filter(Boolean).slice(0,8).join(', ')
			: '';
	}

	function applyStationMapResult(result, fitMap) {
		if(!result || result.requestSequence !== stationMapRequestSequence) return false;
		applyStationMapData(result.payload, fitMap);
		return true;
	}

	function markStationMapUnavailable(error) {
		if(error && error.asrMapRequestSequence && error.asrMapRequestSequence !== stationMapRequestSequence) return;
		stationMapIsStale = stationMapHasValidData;
		mapSummary.textContent = stationMapHasValidData
			? 'Station map update unavailable; showing last known map data.'
			: 'Station origin map is unavailable right now.';
	}

	function currentMapStations() {
		var seen = {};
		return items.map(function (item) {
			return {
				callsign:String(item && item.callsign || '').trim().toUpperCase(),
				locationHint:String(item && item.locationHint || '').trim()
			};
		}).filter(function (station) {
			var callsign = station.callsign;
			if(!isCallsign(callsign) || seen[callsign]) return false;
			seen[callsign] = true;
			return true;
		}).slice(0,30);
	}

	function fetchStationMap() {
		var stations = JSON.stringify(currentMapStations());
		var requestSequence = ++stationMapRequestSequence;
			return fetch(asrBase + '/asr-api.php?action=station-map&stations=' + encodeURIComponent(stations) + '&t=' + Date.now(), {credentials:'same-origin', cache:'no-store'})
			.then(function (response) {
				if(!response.ok) throw new Error('Station map unavailable');
				return response.json();
			})
			.then(function (payload) {
				if(!payload || payload.ok !== true) {
					throw new Error(payload && payload.error ? payload.error : 'Station map unavailable');
				}
				return { requestSequence:requestSequence, payload:payload };
			})
			.catch(function (error) {
				if(error && typeof error === 'object') error.asrMapRequestSequence = requestSequence;
				throw error;
			});
	}

	function refreshStationMap() {
		if(mapPanel.hidden || !stationMap) return;
		fetchStationMap().then(function (result) {
			applyStationMapResult(result, false);
		}).catch(markStationMapUnavailable);
	}

	function initializeStationMap() {
		if(stationMap) {
			window.setTimeout(function () { stationMap.invalidateSize(); }, 100);
			fetchStationMap().then(function (result) {
				applyStationMapResult(result, false);
			}).catch(markStationMapUnavailable);
			return;
		}
		Promise.all([loadLeaflet(), fetchStationMap()])
			.then(function (results) {
				var L = results[0];
				mapFrame.classList.add('is-loaded');
				stationMap = L.map(mapCanvas, {boxZoom:false, keyboard:false, scrollWheelZoom:false, worldCopyJump:true}).setView([20,0], 2);
				L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {maxZoom:16, attribution:'Tiles &copy; Esri'}).addTo(stationMap);
				L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}', {maxZoom:16, pane:'overlayPane'}).addTo(stationMap);
				stationMarkers = L.layerGroup().addTo(stationMap);
				applyStationMapResult(results[1], true);
				window.setTimeout(function () { stationMap.invalidateSize(); }, 150);
			})
			.catch(function (error) {
				mapFrame.classList.add('is-loaded');
				markStationMapUnavailable(error);
				mapUnmapped.textContent = '';
			});
	}

	mapOpen.addEventListener('click', function () {
		if(!mapPanel.hidden) return;
		mapPanel.hidden = false;
		mapOpen.classList.add('is-open');
		mapOpen.setAttribute('aria-pressed', 'true');
		mapOpen.disabled = true;
		initializeStationMap();
	});
	mapClose.addEventListener('click', function () {
		mapPanel.hidden = true;
		mapOpen.classList.remove('is-open');
		mapOpen.setAttribute('aria-pressed', 'false');
		mapOpen.disabled = false;
	});
	mapCard.addEventListener('click', function (event) {
		if(event.target.closest('[data-asr-map-card-close]')) mapCard.hidden = true;
	});
	networkMapOpen.addEventListener('click', function () {
		if(!networkMapNode) return;
		if(networkMapPanel.open) return;
		networkMapPanel.showModal();
		networkMapOpen.setAttribute('aria-expanded', 'true');
		loadNetworkGraph(networkMapNode,networkGraphSeeds);
		refreshNetworkMap();
		scheduleNetworkMapRefresh();
	});
	networkMapClose.addEventListener('click', function () {
		networkMapPanel.close();
		networkMapOpen.setAttribute('aria-expanded', 'false');
		stopNetworkMapRefresh();
		networkGraphRequest++;
		networkMapUrl = '';
	});
	networkMapImage.addEventListener('load', function () {
		if(networkGraphUnavailable){networkGraph.removeAttribute('hidden');networkMapImage.hidden=true;}
		networkMapLoading.hidden = true;
	});
	networkMapImage.addEventListener('error', function () {
		if(!networkGraph.hasAttribute('hidden')) return;
		networkMapLoading.hidden = false;
		networkMapLoading.textContent = 'Map unavailable';
	});
	networkMapFallback.addEventListener('click', function () {
		var fallbackUrl = networkMapUrl || freshNetworkMapUrl();
		if(fallbackUrl) window.open(fallbackUrl, '_blank', 'noopener');
	});

	function load() {
		if(lookupLoading || document.hidden) return;
		lookupLoading = true;
			fetch(asrBase + '/asr-api.php?action=lookup-data', { credentials: 'same-origin', cache: 'no-store' })
			.then(function (response) { return response.json(); })
			.then(function (payload) {
				if(!payload || payload.ok === false) throw new Error(payload && payload.error ? payload.error : 'Lookup data unavailable.');
				items = Array.isArray(payload.items) ? payload.items : [];
				bridgeNodes = {};
				(payload.bridgeNodes || []).forEach(function (bridgeNode) {
					bridgeNodes[String(bridgeNode)] = true;
				});
				var localUpdated = viewerTime(payload.generatedAt);
				updated.textContent = localUpdated ? ' · Updated ' + localUpdated : '';
				render();
				setNetworkMapNode(payload.node,(payload.items||[]).filter(function(item){return item&&item.source==='Connection Status'&&item.node;}).map(function(item){return {node:String(item.node),label:String(item.label||item.node),kind:'node'};}));
				loadLiveConnectionRows(payload.node);
				refreshStationMap();
			})
			.catch(function (error) {
				list.innerHTML = '<p class="asr-lookup-error">' + escapeHtml(error && error.message ? error.message : 'Lookup data unavailable.') + '</p>';
				updated.textContent = 'Error';
			})
			.then(function () {
				lookupLoading = false;
			});
	}

	function setNetworkGraphLocalLinks(root,seeds) {
        if(String(root)!==String(networkMapNode))return;
        var next=Array.isArray(seeds)?seeds:[];
        var signature=function(items){return items.map(function(x){return [x.node,x.kind,x.label].join('|');}).sort().join(';');};
        if(signature(next)===signature(networkGraphSeeds))return;
        networkGraphSeeds=next;
        if(networkMapPanel.open)loadNetworkGraph(String(root),networkGraphSeeds);
    }

	function loadLiveConnectionRows(node) {
		if(!node || typeof EventSource === 'undefined') return;
			var source = new EventSource(asrBase + '/astapi/server.php?nodes=' + encodeURIComponent(node));
		var closed = false;
		var close = function () {
			if(closed) return;
			closed = true;
			source.close();
		};
		var timer = window.setTimeout(close, 4000);

		source.addEventListener('nodes', function (event) {
			var payload;
			try {
				payload = JSON.parse(event.data || '{}');
			} catch (error) {
				return;
			}
			var rows = payload && payload[node] && Array.isArray(payload[node].remote_nodes)
				? payload[node].remote_nodes
				: [];
            var localSeeds=rows.map(function(row){
                var id=String(row&&row.node||'').trim();
                var label=String(row&&row.info||'').replace(/<[^>]*>/g,' ').replace(/\s+/g,' ').trim();
                if(!/^\d{3,7}$/.test(id)||id==='1'||label==='NO CONNECTION'||/echolink/i.test(label))return null;
                var kind=bridgeNodes[id]||/^100[1-6]$/.test(id)||/\bbridge\b|broadcastify/i.test(label)?'bridge':(/Netoholics.*Hub|Hub.*Netoholics/i.test(label)?'hub':'node');
                return {node:id,label:label||id,kind:kind};
            }).filter(Boolean);
            setNetworkGraphLocalLinks(node,localSeeds);
			rows.forEach(function (row) {
				var rowNode = String(row && row.node || '').trim();
				if(!rowNode || rowNode === '1' || bridgeNodes[rowNode] || isPrivateNode(rowNode)) return;
				var label = String(row.info || '').replace(/<[^>]*>/g, '').trim();
				if(!label || label === 'NO CONNECTION') return;
				var callsign = extractCallsign(rowNode + ' ' + label);
				if(isIaxClient(rowNode, label, row.ip || '')) {
					upsertItem({
						source: 'IAX Client',
						label: callsign || rowNode,
						node: '',
						callsign: callsign,
						detail: rowNode + (label ? ' · ' + label : ''),
						qrzUrl: callsign ? qrzUrl(callsign) : '',
						allstarUrl: '',
						echolinkLookup: ''
					});
					return;
				}
				var echolink = echolinkLookupValue(rowNode);
				upsertItem({
					source: echolink ? 'EchoLink Connection' : 'Connection Status',
					label: label,
					node: rowNode,
					callsign: callsign,
					detail: String(row.ip || ''),
					qrzUrl: callsign ? qrzUrl(callsign) : '',
					allstarUrl: allstarUrl(rowNode),
					echolinkLookup: echolink
				});
			});
			render();
			refreshStationMap();
			window.clearTimeout(timer);
			close();
		});

		source.onerror = function () {
			window.clearTimeout(timer);
			close();
		};
	}

	load();
	window.setInterval(load, 15000);
	document.addEventListener('visibilitychange', function () {
		if(!document.hidden) load();
	});
})();
</script>
<?php
asExit();
