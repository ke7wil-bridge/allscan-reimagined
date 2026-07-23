<?php
// AllScan Reimagined Performance Stats page
require_once('../include/common.php');
$html = new Html();
$msg = [];

asInit($msg);
asrInitAuthenticatedUser($msg);
if(!adminUser())
	asExit('Admin permission required.');
pageInit();
?>
<section class="asr-performance-page">
	<h1 class="asr-performance-title">Performance Stats</h1>
	<div class="asr-performance-meta">
		<span id="asrPerformanceUpdated">Loading current node information...</span>
		<span>Updates every 4 seconds while this page is visible</span>
	</div>
	<div id="asrPerformanceGrid" class="asr-performance-grid" aria-live="polite">
		<div class="asr-performance-loading">Loading performance statistics...</div>
	</div>
	<p class="asr-performance-note">These read-only statistics help evaluate ASR load. This page pauses its updates when the browser tab is hidden.</p>
</section>
<script>
(function () {
	var asrBase = <?php echo json_encode(rtrim($urlbase, '/'), JSON_UNESCAPED_SLASHES); ?>;
	var grid = document.getElementById('asrPerformanceGrid');
	var updated = document.getElementById('asrPerformanceUpdated');
	var loading = false;

	function escapeHtml(value) {
		return String(value == null ? '' : value).replace(/[&<>"']/g, function (char) {
			return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[char];
		});
	}

	function viewerTime(value) {
		var date = new Date(String(value || ''));
		if(isNaN(date.getTime())) return '--';
		return date.toLocaleTimeString([], {hour:'numeric', minute:'2-digit', second:'2-digit'});
	}

	function card(label, value, detail, state) {
		return '<article class="asr-performance-card' + (state ? ' is-' + state : '') + '">'
			+ '<h2>' + escapeHtml(label) + '</h2>'
			+ '<strong>' + escapeHtml(value) + '</strong>'
			+ (detail ? '<span>' + escapeHtml(detail) + '</span>' : '')
			+ '</article>';
	}

	function render(data) {
		var load = data.load || {};
		var memory = data.memory || {};
		var disk = data.disk || {};
		grid.innerHTML = [
			card('CPU Temperature', data.cpuTemp || '--', 'Current node temperature'),
			card('System Load', String(load.one == null ? '--' : load.one), '1 / 5 / 15 min: ' + [load.one, load.five, load.fifteen].join(' / ')),
			card('Memory', memory.percent + '%', (memory.used || '--') + ' of ' + (memory.total || '--')),
			card('Disk', disk.percent + '%', (disk.used || '--') + ' of ' + (disk.total || '--')),
			card('Active ASR Viewers', data.activeViewers, 'Shared Asterisk status feed'),
			card('ASR Requests', data.requestsLastMinute, 'Requests during the last minute'),
			card('Apache Workers', data.apacheWorkers, 'Current web-server processes'),
			card('Asterisk', data.asteriskRunning ? 'Running' : 'Stopped', 'Current service process', data.asteriskRunning ? 'good' : 'warning'),
			card('Status Cache', data.statusCacheAge == null ? 'Waiting' : data.statusCacheAge + 's old', 'Stored in RAM'),
			card('Bridge Collector', data.bridgeCollector || 'unknown', 'Disabled automatically without bridges'),
			card('Integrity Check', data.integrityTimer || 'unknown', 'Low-priority update protection'),
			card('Node Uptime', data.uptime || '--', 'Performance mode: ' + (data.mode || 'Standard'))
		].join('');
		updated.textContent = 'Updated ' + viewerTime(data.updated);
	}

	function load() {
		if(loading || document.hidden) return;
		loading = true;
		fetch(asrBase + '/asr-api.php?action=performance-stats', {credentials:'same-origin', cache:'no-store'})
			.then(function (response) { if(!response.ok) throw new Error('Performance statistics unavailable'); return response.json(); })
			.then(render)
			.catch(function (error) {
				grid.innerHTML = '<div class="asr-performance-error">' + escapeHtml(error.message || 'Performance statistics unavailable.') + '</div>';
			})
			.finally(function () { loading = false; });
	}

	load();
	window.setInterval(load, 4000);
	document.addEventListener('visibilitychange', function () { if(!document.hidden) load(); });
})();
</script>
<?php pageEnd(); ?>
