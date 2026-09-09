<?php
// Per-user TGIF authentication for connected-client tracking.
require_once('../include/common.php');
$html = new Html();
$msg = [];
asInit($msg);
asrInitAuthenticatedUser($msg);
if(empty($user) || !isset($user->user_id) || !validDbID($user->user_id))
	asExit('Login required.');
pageInit();
?>
<section class="asr-tgif-page">
	<h1>TGIF Client Tracking</h1>
	<p class="asr-tgif-copy">Sign in with your own TGIF Network account to view authenticated DMR connected sessions. Your TGIF password is sent only to TGIF during sign-in and is not stored by ASR. The temporary TGIF web session is kept only in node RAM and is removed by reboot or Sign Out.</p>
	<div id="asrTgifStatus" class="asr-tgif-status" aria-live="polite">Checking TGIF status...</div>
	<form id="asrTgifForm" class="asr-tgif-form" autocomplete="on">
		<label><span>Callsign</span><input id="asrTgifCallsign" name="callsign" autocomplete="username" autocapitalize="characters" maxlength="10" required></label>
		<label><span>TGIF Password</span><input id="asrTgifPassword" name="password" type="password" autocomplete="current-password" maxlength="128" required></label>
		<label><span>Talkgroup</span><input id="asrTgifTalkgroup" name="talkgroup" inputmode="numeric" pattern="[1-9][0-9]{0,7}" maxlength="8" required></label>
		<div class="asr-tgif-actions"><button type="submit">Sign In to TGIF</button><button type="button" id="asrTgifLogout">Sign Out</button></div>
	</form>
	<p class="asr-tgif-note">Each AllScan user authenticates separately. One user's TGIF session is never used to supply another user's connected-client list.</p>
</section>
<script>
(function () {
	var base = <?php echo json_encode(rtrim($urlbase, '/'), JSON_UNESCAPED_SLASHES); ?>;
	var form = document.getElementById('asrTgifForm');
	var statusBox = document.getElementById('asrTgifStatus');
	var callsign = document.getElementById('asrTgifCallsign');
	var secret = document.getElementById('asrTgifPassword');
	var talkgroup = document.getElementById('asrTgifTalkgroup');
	var logout = document.getElementById('asrTgifLogout');

	function render(data) {
		var configured = !!data.configured;
		var age = Number(data.updatedEpoch || 0);
		var count = Array.isArray(data.clients) ? data.clients.length : 0;
		var label = configured ? ('Signed in as ' + (data.callsign || 'TGIF user')) : 'Not signed in to TGIF';
		if(configured && age) label += ' · ' + count + ' connected session' + (count === 1 ? '' : 's');
		if(data.error) label += ' · ' + data.error;
		statusBox.textContent = label;
		statusBox.classList.toggle('is-error', !!data.error);
		logout.disabled = !configured;
		if(data.callsign && !callsign.value) callsign.value = data.callsign;
		if(data.talkgroup && !talkgroup.value) talkgroup.value = data.talkgroup;
	}

	function request(action, options) {
		return fetch(base + '/asr-api.php?action=' + encodeURIComponent(action), options || {credentials:'same-origin', cache:'no-store'})
			.then(function (response) { return response.json().then(function (data) {
				if(!response.ok || data.ok === false) throw new Error(data.error || 'TGIF request failed.');
				return data;
			}); });
	}

	function refresh() {
		request('tgif-user-status').then(render).catch(function (error) {
			statusBox.textContent = error.message || 'TGIF status unavailable.';
			statusBox.classList.add('is-error');
		});
	}

	form.addEventListener('submit', function (event) {
		event.preventDefault();
		statusBox.textContent = 'Signing in to TGIF...';
		var body = new URLSearchParams({callsign:callsign.value.trim().toUpperCase(), password:secret.value, talkgroup:talkgroup.value.trim()});
		request('tgif-user-login', {method:'POST', credentials:'same-origin', cache:'no-store',
			headers:{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8','X-ASR-Requested-With':'tgif-user-session'}, body:body.toString()})
			.then(render)
			.catch(function (error) { statusBox.textContent = error.message || 'TGIF sign-in failed.'; statusBox.classList.add('is-error'); })
			.finally(function () { secret.value = ''; });
	});

	logout.addEventListener('click', function () {
		statusBox.textContent = 'Signing out of TGIF...';
		request('tgif-user-logout', {method:'POST', credentials:'same-origin', cache:'no-store',
			headers:{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8','X-ASR-Requested-With':'tgif-user-session'}, body:''})
			.then(render)
			.catch(function (error) { statusBox.textContent = error.message || 'TGIF sign-out failed.'; statusBox.classList.add('is-error'); });
	});

	callsign.addEventListener('input', function () { callsign.value = callsign.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 10); });
	talkgroup.addEventListener('input', function () { talkgroup.value = talkgroup.value.replace(/\D/g, '').slice(0, 8); });
	refresh();
})();
</script>
<?php pageEnd(); ?>
