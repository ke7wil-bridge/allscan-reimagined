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
		<section id="asrNetworkMapPanel" class="asr-map-panel" hidden>
			<div class="asr-map-head">
				<strong>AllStarLink Network Map</strong>
				<button id="asrNetworkMapClose" type="button" class="asr-map-close">Close</button>
			</div>
			<div class="asr-map-body">
				<div id="asrNetworkMapFrame" class="asr-map-frame asr-network-map-frame">
					<span id="asrNetworkMapLoading" class="asr-map-loading">Loading Map</span>
					<img id="asrNetworkMapImage" alt="" loading="eager" decoding="async">
				</div>
				<p class="asr-map-note">
					<button id="asrNetworkMapFallback" type="button" class="asr-map-button">Open Full Map</button>
				</p>
			</div>
		</section>
	</div>
</section>

<style>
.asr-network-map-frame {
	background:#fff;
	height:clamp(300px, 34vw, 430px);
}

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

@media (max-width:760px) {
	.asr-network-map-frame {
		height:260px;
		min-height:180px;
	}
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
	var networkMapClose = document.getElementById('asrNetworkMapClose');
	var networkMapPanel = document.getElementById('asrNetworkMapPanel');
	var networkMapFrame = document.getElementById('asrNetworkMapFrame');
	var networkMapLoading = document.getElementById('asrNetworkMapLoading');
	var networkMapImage = document.getElementById('asrNetworkMapImage');
	var networkMapFallback = document.getElementById('asrNetworkMapFallback');
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
		if(networkMapPanel.hidden) return;
		var freshUrl = freshNetworkMapUrl();
		if(!freshUrl) return;
		networkMapImage.src = freshUrl;
		networkMapImage.alt = 'AllStarLink network map for node ' + networkMapNode;
		syncNetworkMapLoader();
	}

	function scheduleNetworkMapRefresh() {
		if(networkMapRefreshTimer) window.clearTimeout(networkMapRefreshTimer);
		networkMapRefreshTimer = window.setTimeout(function () {
			if(networkMapPanel.hidden) return;
			refreshNetworkMap();
			scheduleNetworkMapRefresh();
		}, 300000);
	}

	function stopNetworkMapRefresh() {
		if(networkMapRefreshTimer) window.clearTimeout(networkMapRefreshTimer);
		networkMapRefreshTimer = null;
	}

	function setNetworkMapNode(node) {
		var nextNode = String(node || '').trim();
		if(!networkMapBaseUrl(nextNode)) return;
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
		if(window.matchMedia && window.matchMedia('(max-width: 760px)').matches) {
			window.open(freshNetworkMapUrl(), '_blank', 'noopener');
			return;
		}
		if(!networkMapPanel.hidden) return;
		networkMapPanel.hidden = false;
		networkMapOpen.setAttribute('aria-expanded', 'true');
		refreshNetworkMap();
		scheduleNetworkMapRefresh();
	});
	networkMapClose.addEventListener('click', function () {
		networkMapPanel.hidden = true;
		networkMapOpen.setAttribute('aria-expanded', 'false');
		stopNetworkMapRefresh();
		networkMapUrl = '';
	});
	networkMapImage.addEventListener('load', function () {
		networkMapLoading.hidden = true;
	});
	networkMapImage.addEventListener('error', function () {
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
				setNetworkMapNode(payload.node);
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
