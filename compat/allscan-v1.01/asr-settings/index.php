<?php
// AllScan Reimagined Settings controller
require_once('../include/common.php');
$html = new Html();
$msg = [];

define('ASR_SETTINGS_FILE', '/etc/allscan-reimagined/config.json');
define('ASR_SECRETS_FILE', '/etc/allscan-reimagined/secrets.json');
define('SAVE_REIMAGINED_SETTINGS', 'Save Reimagined Settings');
define('ASR_MAX_BRIDGES', 8);

function asrSettingsDefaultConfig() {
	return [
		'headerTitle' => '{CALLSIGN} | Node {NODE}',
		'headerLogo' => '/allscan/asr-logo-bright-r-tight.png',
		'brandByline' => 'by KE7WIL',
		'footerLogo' => '/allscan/asr-logo-bright-r-tight.png',
		'maintainFriendlyNames' => false,
		'lowPowerMode' => false,
		'bridges' => [],
	];
}

function asrSettingsReadSecrets() {
	if(!is_readable(ASR_SECRETS_FILE))
		return [];
	$data = json_decode((string) file_get_contents(ASR_SECRETS_FILE), true);
	return is_array($data) ? $data : [];
}

function asrSettingsUploadDir() {
	global $wwwroot, $asdir;
	return rtrim($wwwroot, '/') . '/' . trim($asdir, '/') . '/asr-user-content';
}

function asrSettingsUploadUrl() {
	global $urlbase;
	return rtrim($urlbase, '/') . '/asr-user-content';
}

function asrSettingsReadConfig() {
	$defaults = asrSettingsDefaultConfig();
	if(!is_readable(ASR_SETTINGS_FILE))
		return $defaults;
	$data = json_decode((string) file_get_contents(ASR_SETTINGS_FILE), true);
	if(!is_array($data))
		return $defaults;
	return array_merge($defaults, $data);
}

function asrSettingsCleanText($value, $maxLen) {
	$value = trim((string) $value);
	$value = preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/', '', $value);
	if(strlen($value) > $maxLen)
		$value = substr($value, 0, $maxLen);
	return $value;
}

function asrSettingsCleanLogo($value) {
	$value = asrSettingsCleanText($value, 160);
	if($value === '')
		return '/allscan/asr-logo-bright-r-tight.png';
	if(preg_match('#^/allscan/[A-Za-z0-9._/\-]+$#', $value))
		return $value;
	if(preg_match('#^https?://[A-Za-z0-9._~:/?#\[\]@!$&\'()*+,;=%-]+$#', $value))
		return $value;
	return null;
}

function asrSettingsHandleLogoUpload(&$error) {
	if(empty($_FILES['headerLogoUpload']) || !is_array($_FILES['headerLogoUpload']))
		return '';
	if((int) ($_FILES['headerLogoUpload']['error'] ?? UPLOAD_ERR_NO_FILE) === UPLOAD_ERR_NO_FILE)
		return '';
	if((int) $_FILES['headerLogoUpload']['error'] !== UPLOAD_ERR_OK) {
		$error = 'Header logo upload failed.';
		return null;
	}
	if((int) ($_FILES['headerLogoUpload']['size'] ?? 0) > 1048576) {
		$error = 'Header logo must be 1 MB or smaller.';
		return null;
	}
	$tmp = (string) ($_FILES['headerLogoUpload']['tmp_name'] ?? '');
	$info = @getimagesize($tmp);
	if(!$info || empty($info['mime'])) {
		$error = 'Header logo must be a PNG, JPEG, or WebP image.';
		return null;
	}
	$ext = '';
	if($info['mime'] === 'image/png') $ext = 'png';
	elseif($info['mime'] === 'image/jpeg') $ext = 'jpg';
	elseif($info['mime'] === 'image/webp') $ext = 'webp';
	else {
		$error = 'Header logo must be a PNG, JPEG, or WebP image.';
		return null;
	}
	$uploadDir = asrSettingsUploadDir();
	if(!is_dir($uploadDir) && !mkdir($uploadDir, 0775, true)) {
		$error = 'Could not create the ASR upload directory.';
		return null;
	}
	$target = $uploadDir . '/header-logo.' . $ext;
	if(!move_uploaded_file($tmp, $target)) {
		$error = 'Could not save the uploaded header logo.';
		return null;
	}
	@chmod($target, 0664);
	foreach(['png', 'jpg', 'webp'] as $oldExt) {
		$old = $uploadDir . '/header-logo.' . $oldExt;
		if($old !== $target && file_exists($old)) @unlink($old);
	}
	return asrSettingsUploadUrl() . '/header-logo.' . $ext;
}

function asrSettingsCleanBridgeId($value) {
	$value = strtolower(asrSettingsCleanText($value, 32));
	$value = preg_replace('/[^a-z0-9_-]/', '', $value);
	if(!preg_match('/^[a-z][a-z0-9_-]{1,31}$/', $value))
		return '';
	return $value;
}

function asrSettingsDefaultBridgeTitle($id) {
	switch($id) {
		case 'dmr': return 'DMR Bridge';
		case 'ysf': return 'YSF Bridge';
		case 'zello': return 'Zello Bridge';
		case 'dstar': return 'D-Star Bridge';
		case 'p25': return 'P25 Bridge';
		case 'm17': return 'M17 Bridge';
		case 'nxdn': return 'NXDN Bridge';
	}
	return strtoupper(substr($id, 0, 1)) . substr($id, 1) . ' Bridge';
}

function asrSettingsBridgeRowsFromPost(&$error) {
	$ids = $_POST['bridgeId'] ?? [];
	$nodes = $_POST['bridgeNode'] ?? [];
	$titles = $_POST['bridgeTitle'] ?? [];
	$details = $_POST['bridgeDetailTitle'] ?? [];
	$friendlyNames = $_POST['bridgeFriendlyName'] ?? [];
	$clientSources = $_POST['bridgeClientSource'] ?? [];
	$clientUrls = $_POST['bridgeClientUrl'] ?? [];
	$clientUsernames = $_POST['bridgeClientUsername'] ?? [];
	$bridges = [];
	$seen = [];
	$count = min(ASR_MAX_BRIDGES, max(count($ids), count($nodes), count($titles), count($details), count($friendlyNames), count($clientSources), count($clientUrls), count($clientUsernames)));

	for($i = 0; $i < $count; $i++) {
		$rawId = asrSettingsCleanText($ids[$i] ?? '', 32);
		$rawNode = asrSettingsCleanText($nodes[$i] ?? '', 10);
		$rawTitle = asrSettingsCleanText($titles[$i] ?? '', 80);
		$rawDetail = asrSettingsCleanText($details[$i] ?? '', 80);
		$rawFriendlyName = asrSettingsCleanText($friendlyNames[$i] ?? '', 80);
		$rawClientSource = asrSettingsCleanText($clientSources[$i] ?? 'disabled', 20);
		$rawClientUrl = asrSettingsCleanText($clientUrls[$i] ?? '', 220);
		$rawClientUsername = asrSettingsCleanText($clientUsernames[$i] ?? '', 80);
		if($rawId === '' && $rawNode === '' && $rawTitle === '' && $rawDetail === '' && $rawFriendlyName === '' && $rawClientUrl === '' && $rawClientUsername === '')
			continue;

		$id = asrSettingsCleanBridgeId($rawId);
		if($id === '') {
			$error = 'Each bridge ID must start with a letter and use only letters, numbers, dashes, or underscores.';
			return [];
		}
		if(isset($seen[$id])) {
			$error = "Bridge ID \"$id\" is listed more than once.";
			return [];
		}
		if(!preg_match('/^[0-9]{3,10}$/', $rawNode)) {
			$error = "Bridge \"$id\" needs a 3-10 digit node number.";
			return [];
		}
		if(!in_array($rawClientSource, ['disabled', 'local_json', 'http_api'], true))
			$rawClientSource = 'disabled';

		$seen[$id] = true;
		$bridges[] = [
			'id' => $id,
			'node' => $rawNode,
			'title' => $rawTitle !== '' ? $rawTitle : asrSettingsDefaultBridgeTitle($id),
			'detailTitle' => $rawDetail !== '' ? $rawDetail : 'Linked Clients',
			'friendlyName' => $rawFriendlyName,
			'clientSource' => $rawClientSource,
			'clientUrl' => $rawClientUrl,
			'clientUsername' => $rawClientUsername,
		];
	}
	return $bridges;
}

function asrSettingsWriteSecrets($secrets, &$error) {
	$dir = dirname(ASR_SECRETS_FILE);
	if(!is_dir($dir)) {
		$error = "$dir does not exist.";
		return false;
	}
	$json = json_encode($secrets, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
	if($json === false) {
		$error = 'Could not encode Reimagined secrets.';
		return false;
	}
	$tmp = ASR_SECRETS_FILE . '.tmp.' . getmypid();
	if(file_put_contents($tmp, $json . PHP_EOL) === false) {
		$error = 'Could not write temporary secrets file.';
		return false;
	}
	@chmod($tmp, 0640);
	if(!rename($tmp, ASR_SECRETS_FILE)) {
		@unlink($tmp);
		$error = 'Could not replace secrets file.';
		return false;
	}
	@chmod(ASR_SECRETS_FILE, 0640);
	return true;
}

function asrSettingsWriteConfig($config, &$error) {
	$dir = dirname(ASR_SETTINGS_FILE);
	if(!is_dir($dir)) {
		$error = "$dir does not exist.";
		return false;
	}
	$json = json_encode($config, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
	if($json === false) {
		$error = 'Could not encode Reimagined settings.';
		return false;
	}
	$tmp = ASR_SETTINGS_FILE . '.tmp.' . getmypid();
	if(file_put_contents($tmp, $json . PHP_EOL) === false) {
		$error = 'Could not write temporary settings file. Check /etc/allscan-reimagined permissions.';
		return false;
	}
	@chmod($tmp, 0664);
	if(!rename($tmp, ASR_SETTINGS_FILE)) {
		@unlink($tmp);
		$error = 'Could not replace settings file. Check /etc/allscan-reimagined permissions.';
		return false;
	}
	@chmod(ASR_SETTINGS_FILE, 0664);
	return true;
}

function asrSettingsSetPublicPermission($db, $permission) {
	if(!defined('publicPermission'))
		return 'Public Permission cfg id is unavailable.';
	$now = time();
	$current = $db->getRecord('cfg', 'cfg_id=' . publicPermission);
	if($current)
		$db->updateRow('cfg', ['val', 'updated'], [$permission, $now], 'cfg_id=' . publicPermission);
	else
		$db->insertRow('cfg', ['cfg_id', 'val', 'updated'], [publicPermission, $permission, $now]);
	return isset($db->error) ? $db->error : '';
}

function asrSettingsH($value) {
	return htmlspecialchars((string) $value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function asrSettingsSourceOption($source, $value, $label) {
	return '<option value="' . asrSettingsH($value) . '"' . ($source === $value ? ' selected' : '') . '>' . asrSettingsH($label) . '</option>';
}

function asrSettingsBridgePanel($bridge = [], $bridgePasswords = []) {
	$id = (string)($bridge['id'] ?? '');
	$source = (string)($bridge['clientSource'] ?? 'disabled');
	$passwordPlaceholder = !empty($bridgePasswords[$id]) ? 'Saved - leave blank to keep existing' : '';
	$panelTitle = (string)($bridge['title'] ?? '');
	if($panelTitle === '')
		$panelTitle = $id !== '' ? strtoupper($id) . ' Bridge' : 'New Bridge';
?>
	<div class="asr-bridge-settings-row is-collapsed">
		<div class="asr-bridge-panel-header">
			<button class="asr-bridge-toggle" type="button" aria-expanded="false">
				<span class="asr-bridge-toggle-copy">
					<strong class="asr-bridge-panel-name"><?php echo asrSettingsH($panelTitle); ?></strong>
					<span>Bridge card, Connection Status name, and optional connected-client source.</span>
				</span>
				<span class="asr-settings-toggle-icon" aria-hidden="true">+</span>
			</button>
			<button class="asr-bridge-delete" type="button">Delete</button>
		</div>

		<div class="asr-bridge-panel-body">
		<div class="asr-bridge-panel-section">
			<div class="asr-bridge-section-copy">
				<strong>Bridge Card</strong>
				<span>Controls the bridge card shown on the ASR home page.</span>
			</div>
			<div class="asr-bridge-fields-grid asr-bridge-card-grid">
				<label><span>ID</span><input name="bridgeId[]" type="text" placeholder="dmr" value="<?php echo asrSettingsH($id); ?>"></label>
				<label><span>Node</span><input name="bridgeNode[]" type="text" inputmode="numeric" placeholder="1001" value="<?php echo asrSettingsH($bridge['node'] ?? ''); ?>"></label>
				<label><span>Card Title</span><input name="bridgeTitle[]" type="text" placeholder="DMR Bridge" value="<?php echo asrSettingsH($bridge['title'] ?? ''); ?>"></label>
				<label><span>Detail Title</span><input name="bridgeDetailTitle[]" type="text" placeholder="Linked Clients" value="<?php echo asrSettingsH($bridge['detailTitle'] ?? ''); ?>"></label>
			</div>
		</div>

		<div class="asr-bridge-panel-section">
			<div class="asr-bridge-section-copy">
				<strong>Connection Status</strong>
				<span>Controls the name used for this bridge node in the Connection Status table.</span>
			</div>
			<div class="asr-bridge-fields-grid asr-bridge-status-grid">
				<label><span>Connection Status Name</span><input name="bridgeFriendlyName[]" type="text" placeholder="TGIF DMR Bridge" value="<?php echo asrSettingsH($bridge['friendlyName'] ?? ''); ?>"></label>
			</div>
		</div>

		<div class="asr-bridge-panel-section">
			<div class="asr-bridge-section-copy">
				<strong>Connected Clients</strong>
				<span>Optional. ASR caches this source separately so browsers do not repeatedly hit the bridge source.</span>
			</div>
			<div class="asr-bridge-client-source">
				<label><span>Client Source</span><select name="bridgeClientSource[]">
					<?php echo asrSettingsSourceOption($source, 'disabled', 'Disabled'); ?>
					<?php echo asrSettingsSourceOption($source, 'local_json', 'Local JSON / file'); ?>
					<?php echo asrSettingsSourceOption($source, 'http_api', 'HTTP API'); ?>
				</select></label>
				<label><span>URL / Path</span><input name="bridgeClientUrl[]" type="text" placeholder="/var/www/html/allscan/connected-clients.json" value="<?php echo asrSettingsH($bridge['clientUrl'] ?? ''); ?>"></label>
				<label><span>Username</span><input name="bridgeClientUsername[]" type="text" value="<?php echo asrSettingsH($bridge['clientUsername'] ?? ''); ?>"></label>
				<label><span>Password / Token</span><input name="bridgeClientPassword[]" type="password" placeholder="<?php echo asrSettingsH($passwordPlaceholder); ?>"></label>
			</div>
			<p class="asr-bridge-section-note">Disabled keeps any existing client data but does not create a new source. Local JSON / file should point to a readable JSON file. HTTP API should point to a JSON endpoint made for client status.</p>
		</div>
		</div>
	</div>
<?php
}

asInit($msg);
$db = dbInit();
$userCnt = checkTables($db, $msg);
if(!$userCnt)
	redirect('user/');
$cfgModel = new CfgModel($db);
$userModel = new UserModel($db);
$user = $userModel->validate();
if(empty($user) || !isset($user->user_id) || !validDbID($user->user_id))
	redirect('user/');
if(!adminUser())
	asExit('Admin permission required.');

$config = asrSettingsReadConfig();
$secrets = asrSettingsReadSecrets();
$submit = $_POST['Submit'] ?? null;

if($submit === SAVE_REIMAGINED_SETTINGS) {
	$next = $config;
	$uploadError = '';
	$uploadedLogo = asrSettingsHandleLogoUpload($uploadError);
	$headerTitle = asrSettingsCleanText($_POST['headerTitle'] ?? '', 100);
	if($headerTitle === '')
		$headerTitle = '{CALLSIGN} | Node {NODE}';
	$logo = $uploadedLogo ? $uploadedLogo : asrSettingsCleanLogo($_POST['headerLogo'] ?? '');
	$requireLogin = !empty($_POST['requireLogin']);
	$maintainFriendlyNames = !empty($_POST['maintainFriendlyNames']);
	$lowPowerMode = !empty($_POST['lowPowerMode']);

	if($uploadError) {
		$saveError = $uploadError;
	} elseif($logo === null) {
		$saveError = 'Header logo must be an /allscan/... path or an http/https URL.';
	} else {
		$bridgeError = '';
		$bridges = asrSettingsBridgeRowsFromPost($bridgeError);
		if($bridgeError) {
			$saveError = $bridgeError;
		} else {
			$next['headerTitle'] = $headerTitle;
			$next['headerLogo'] = $logo;
			$next['brandByline'] = 'by KE7WIL';
			$next['footerLogo'] = '/allscan/asr-logo-bright-r-tight.png';
			$next['maintainFriendlyNames'] = $maintainFriendlyNames;
			$next['lowPowerMode'] = $lowPowerMode;
			$next['bridges'] = $bridges;
			$saveError = '';
			$nextSecrets = $secrets;
			$nextSecrets['bridgeClientPasswords'] = is_array($nextSecrets['bridgeClientPasswords'] ?? null) ? $nextSecrets['bridgeClientPasswords'] : [];
			$postedBridgeIds = $_POST['bridgeId'] ?? [];
			$postedPasswords = $_POST['bridgeClientPassword'] ?? [];
			$allowedSecretIds = [];
			foreach($bridges as $bridge)
				$allowedSecretIds[$bridge['id']] = true;
			foreach(array_keys($nextSecrets['bridgeClientPasswords']) as $secretId) {
				if(!isset($allowedSecretIds[$secretId]))
					unset($nextSecrets['bridgeClientPasswords'][$secretId]);
			}
			$passwordCount = min(ASR_MAX_BRIDGES, max(count($postedBridgeIds), count($postedPasswords)));
			for($i = 0; $i < $passwordCount; $i++) {
				$secretId = asrSettingsCleanBridgeId($postedBridgeIds[$i] ?? '');
				$password = (string) ($postedPasswords[$i] ?? '');
				if($secretId !== '' && isset($allowedSecretIds[$secretId]) && $password !== '')
					$nextSecrets['bridgeClientPasswords'][$secretId] = $password;
			}
			$nextSecrets['qrz'] = is_array($nextSecrets['qrz'] ?? null) ? $nextSecrets['qrz'] : [];
			$qrzUsername = asrSettingsCleanText($_POST['qrzUsername'] ?? '', 80);
			$qrzPassword = asrSettingsCleanText($_POST['qrzPassword'] ?? '', 160);
			if($qrzUsername !== '')
				$nextSecrets['qrz']['username'] = $qrzUsername;
			if($qrzPassword !== '')
				$nextSecrets['qrz']['password'] = $qrzPassword;
			if(asrSettingsWriteConfig($next, $saveError) && asrSettingsWriteSecrets($nextSecrets, $saveError)) {
				$permissionError = asrSettingsSetPublicPermission($db, $requireLogin ? PERMISSION_NONE : PERMISSION_READ_ONLY);
				if($permissionError)
					$saveError = $permissionError;
				else {
					if(is_executable('/usr/local/sbin/allscan-reimagined-friendly-names'))
						@shell_exec('sudo -n /usr/local/sbin/allscan-reimagined-friendly-names --once 2>/dev/null || /usr/local/sbin/allscan-reimagined-friendly-names --once 2>/dev/null');
					if(is_executable('/usr/local/sbin/allscan-reimagined-bridge-clients'))
						@shell_exec('sudo -n /usr/local/sbin/allscan-reimagined-bridge-clients --once 2>/dev/null || /usr/local/sbin/allscan-reimagined-bridge-clients --once 2>/dev/null');
					$config = $next;
					$secrets = $nextSecrets;
					$gCfg[publicPermission] = $requireLogin ? PERMISSION_NONE : PERMISSION_READ_ONLY;
					$saveOk = true;
				}
			}
		}
	}
}

pageInit();
h1('Reimagined Settings');

if(!empty($saveOk))
	okMsg('Reimagined settings saved.');
if(!empty($saveError))
	errMsg($saveError);

$requireLogin = (int)($gCfg[publicPermission] ?? PERMISSION_READ_ONLY) <= PERMISSION_NONE;
$maintainFriendlyNames = !empty($config['maintainFriendlyNames']);
$lowPowerMode = !empty($config['lowPowerMode']);
$bridgeRows = is_array($config['bridges'] ?? null) ? $config['bridges'] : [];
$bridgePasswords = is_array($secrets['bridgeClientPasswords'] ?? null) ? $secrets['bridgeClientPasswords'] : [];
$qrzSecrets = is_array($secrets['qrz'] ?? null) ? $secrets['qrz'] : [];
?>
<form class="asr-reimagined-settings-form" method="post" action="" enctype="multipart/form-data" data-max-bridges="<?php echo ASR_MAX_BRIDGES; ?>">
	<p class="asr-reimagined-submit asr-reimagined-submit-top">
		<input type="submit" name="Submit" value="<?php echo SAVE_REIMAGINED_SETTINGS; ?>">
		<span>Saved on the node at <?php echo asrSettingsH(ASR_SETTINGS_FILE); ?>.</span>
	</p>

	<fieldset class="asr-settings-section" data-settings-section="header">
		<legend><button class="asr-settings-section-toggle" type="button" aria-expanded="true">Header <span class="asr-settings-toggle-icon" aria-hidden="true">−</span></button></legend>
		<div class="asr-settings-row">
			<label for="headerTitle">Header Title</label>
			<input id="headerTitle" name="headerTitle" type="text" value="<?php echo asrSettingsH($config['headerTitle'] ?? '{CALLSIGN} | Node {NODE}'); ?>">
		</div>
		<div class="asr-settings-row">
			<label for="headerLogo">Header Logo</label>
			<input id="headerLogo" name="headerLogo" type="text" value="<?php echo asrSettingsH($config['headerLogo'] ?? ''); ?>">
		</div>
		<div class="asr-settings-row">
			<label for="headerLogoUpload">Upload Logo</label>
			<input id="headerLogoUpload" name="headerLogoUpload" type="file" accept="image/png,image/jpeg,image/webp">
		</div>
		<p class="asr-settings-inline-note">Header title can use {CALLSIGN} and {NODE}, such as {CALLSIGN} | Node {NODE}.</p>
		<p class="asr-settings-inline-note">Use a local /allscan/... path, an http/https URL, or upload a PNG, JPEG, or WebP image under 1 MB.</p>
	</fieldset>

	<fieldset class="asr-settings-section is-collapsed" data-settings-section="bridges">
		<legend><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Bridge Cards <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></legend>
		<p class="asr-settings-help">Only active bridge cards are listed here. Use Add Bridge for another card, up to <?php echo ASR_MAX_BRIDGES; ?> total.</p>
		<p class="asr-settings-help">Use lowercase bridge IDs like dmr, ysf, zello, dstar, p25, m17, or nxdn. ASR uses the node number to match the bridge to live status.</p>
		<div class="asr-bridge-settings-table">
			<?php foreach($bridgeRows as $bridge): ?>
				<?php asrSettingsBridgePanel($bridge, $bridgePasswords); ?>
			<?php endforeach; ?>
		</div>
		<button class="asr-add-bridge-button" type="button">+ Add Bridge</button>
	</fieldset>

	<fieldset class="asr-settings-section is-collapsed" data-settings-section="bridge-help">
		<legend><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Bridge Setup Help <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></legend>
		<div class="asr-setup-help-grid">
			<section>
				<h2>What ASR Controls</h2>
				<p>ASR controls bridge display only: the bridge card, the AllStar node number to watch, the Connection Status name, diagnostics, and optional connected-client display.</p>
			</section>
			<section>
				<h2>What ASR Does Not Install</h2>
				<p>ASR does not create the bridge itself. DMR, YSF, Zello, D-Star, P25, M17, NXDN, or other bridge software must already be installed, linked to AllStar, and configured with its own services, ports, IDs, passwords, tokens, and network forwarding.</p>
			</section>
			<section>
				<h2>DMR</h2>
				<p>DMR usually depends on MMDVM_Bridge and Analog_Bridge. The DMR ID, hotspot security key, master, talkgroup, local UDP port, and router forwarding belong in the DMR bridge config, not in ASR. ASR can display TGIF client tracking when the node already has a working token helper.</p>
			</section>
			<section>
				<h2>YSF, D-Star, P25, M17, NXDN</h2>
				<p>These modes can be shown as bridge cards when their AllStar bridge nodes are installed and linked. ASR does not count local loopback bridge plumbing as a connected client. Client display depends on whether that mode exposes real remote clients in a readable local file, log, or API.</p>
			</section>
			<section>
				<h2>Zello</h2>
				<p>Zello can only show names when the Zello bridge writes talker names to a local file or API. If the bridge only reports USRP keying, ASR will show a diagnostic warning instead of inventing a user.</p>
			</section>
			<section>
				<h2>Connected Client Source</h2>
				<p>Leave this Disabled unless you have a real client list source. Use Local JSON / file for a readable JSON file under the AllScan web folder or ASR config folder. Use HTTP API only for a JSON endpoint made for client status. ASR refreshes one cached file in the background so browsers do not repeatedly hit bridge services.</p>
			</section>
		</div>
	</fieldset>

	<fieldset class="asr-settings-section is-collapsed" data-settings-section="bridge-diagnostics">
		<legend><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Bridge Diagnostics <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></legend>
		<p class="asr-settings-help">Read-only checks for bridge display, client collection, and common bridge service hints. The optional feed is an additional per-bridge JSON or API input; automatic bridge tracking can work without one.</p>
		<div id="asr-bridge-diagnostics" class="asr-bridge-diagnostics" data-loading="Loading bridge diagnostics...">Loading bridge diagnostics...</div>
	</fieldset>

	<fieldset class="asr-settings-section is-collapsed" data-settings-section="lookup-map">
		<legend><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Lookup / Map <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></legend>
		<p class="asr-settings-help">Optional QRZ credentials for lookup and map enrichment. These are stored only on the node and are not sent to the browser bundle.</p>
		<div class="asr-settings-secret-grid">
			<label><span>QRZ Username</span><input name="qrzUsername" type="text" value="<?php echo asrSettingsH($qrzSecrets['username'] ?? ''); ?>"></label>
			<label><span>QRZ Password</span><input name="qrzPassword" type="password" placeholder="<?php echo !empty($qrzSecrets['password']) ? 'Saved - leave blank to keep existing' : ''; ?>"></label>
		</div>
		<p class="asr-settings-inline-note">The password field stays blank after saving. Enter a new value only when changing it.</p>
	</fieldset>

	<fieldset class="asr-settings-section is-collapsed" data-settings-section="access">
		<legend><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Access <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></legend>
		<label class="asr-settings-check">
			<input name="requireLogin" type="checkbox" value="1"<?php echo $requireLogin ? ' checked' : ''; ?>>
			<span>Require login to view ASR</span>
		</label>
		<label class="asr-settings-check">
			<input name="lowPowerMode" type="checkbox" value="1"<?php echo $lowPowerMode ? ' checked' : ''; ?>>
			<span>Low-Power Node Mode</span>
		</label>
		<p class="asr-settings-inline-note">Reduces background work and disables animated themes for smaller nodes.</p>
	</fieldset>

	<fieldset class="asr-settings-section is-collapsed" data-settings-section="friendly-names">
		<legend><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Friendly Names <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></legend>
		<label class="asr-settings-check">
			<input name="maintainFriendlyNames" type="checkbox" value="1"<?php echo $maintainFriendlyNames ? ' checked' : ''; ?>>
			<span>Maintain bridge friendly names across updates, restarts, and reboots</span>
		</label>
		<p class="asr-settings-inline-note">When enabled, ASR keeps configured bridge node labels matching the Connection Status Name.</p>
	</fieldset>

	<p class="asr-reimagined-submit">
		<input type="submit" name="Submit" value="<?php echo SAVE_REIMAGINED_SETTINGS; ?>">
		<span>Saved on the node at <?php echo asrSettingsH(ASR_SETTINGS_FILE); ?>.</span>
	</p>
</form>
<template id="asr-bridge-row-template">
	<div class="asr-bridge-settings-row">
		<div class="asr-bridge-panel-header">
			<button class="asr-bridge-toggle" type="button" aria-expanded="true">
				<span class="asr-bridge-toggle-copy">
					<strong class="asr-bridge-panel-name">New Bridge</strong>
					<span>Bridge card, Connection Status name, and optional connected-client source.</span>
				</span>
				<span class="asr-settings-toggle-icon" aria-hidden="true">−</span>
			</button>
			<button class="asr-bridge-delete" type="button">Delete</button>
		</div>

		<div class="asr-bridge-panel-body">
		<div class="asr-bridge-panel-section">
			<div class="asr-bridge-section-copy">
				<strong>Bridge Card</strong>
				<span>Controls the bridge card shown on the ASR home page.</span>
			</div>
			<div class="asr-bridge-fields-grid asr-bridge-card-grid">
				<label><span>ID</span><input name="bridgeId[]" type="text" placeholder="dmr"></label>
				<label><span>Node</span><input name="bridgeNode[]" type="text" inputmode="numeric" placeholder="1001"></label>
				<label><span>Card Title</span><input name="bridgeTitle[]" type="text" placeholder="DMR Bridge"></label>
				<label><span>Detail Title</span><input name="bridgeDetailTitle[]" type="text" placeholder="Linked Clients"></label>
			</div>
		</div>

		<div class="asr-bridge-panel-section">
			<div class="asr-bridge-section-copy">
				<strong>Connection Status</strong>
				<span>Controls the name used for this bridge node in the Connection Status table.</span>
			</div>
			<div class="asr-bridge-fields-grid asr-bridge-status-grid">
				<label><span>Connection Status Name</span><input name="bridgeFriendlyName[]" type="text" placeholder="TGIF DMR Bridge"></label>
			</div>
		</div>

		<div class="asr-bridge-panel-section">
			<div class="asr-bridge-section-copy">
				<strong>Connected Clients</strong>
				<span>Optional. ASR caches this source separately so browsers do not repeatedly hit the bridge source.</span>
			</div>
			<div class="asr-bridge-client-source">
				<label><span>Client Source</span><select name="bridgeClientSource[]">
					<option value="disabled">Disabled</option>
					<option value="local_json">Local JSON / file</option>
					<option value="http_api">HTTP API</option>
				</select></label>
				<label><span>URL / Path</span><input name="bridgeClientUrl[]" type="text" placeholder="/var/www/html/allscan/connected-clients.json"></label>
				<label><span>Username</span><input name="bridgeClientUsername[]" type="text"></label>
				<label><span>Password / Token</span><input name="bridgeClientPassword[]" type="password"></label>
			</div>
				<p class="asr-bridge-section-note">Disabled keeps any existing client data but does not create a new source. Local JSON / file should point to a readable JSON file. HTTP API should point to a JSON endpoint made for client status.</p>
			</div>
		</div>
	</div>
</template>
<script>
(function () {
	var form = document.querySelector('.asr-reimagined-settings-form');
	var table = document.querySelector('.asr-bridge-settings-table');
	var template = document.getElementById('asr-bridge-row-template');
	var addButton = document.querySelector('.asr-add-bridge-button');
	var max = form ? parseInt(form.getAttribute('data-max-bridges') || '8', 10) : 8;
	var diagnosticsLoaded = false;
	function setSectionExpanded(section, expanded) {
		if(!section) return;
		var button = section.querySelector('.asr-settings-section-toggle');
		var icon = button ? button.querySelector('.asr-settings-toggle-icon') : null;
		section.classList.toggle('is-collapsed', !expanded);
		if(button) button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
		if(icon) icon.textContent = expanded ? '−' : '+';
		if(expanded && section.getAttribute('data-settings-section') === 'bridge-diagnostics') loadBridgeDiagnostics();
	}
	function setBridgeExpanded(row, expanded) {
		if(!row) return;
		var button = row.querySelector('.asr-bridge-toggle');
		var icon = button ? button.querySelector('.asr-settings-toggle-icon') : null;
		row.classList.toggle('is-collapsed', !expanded);
		if(button) button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
		if(icon) icon.textContent = expanded ? '−' : '+';
	}
	function rows() {
		return Array.prototype.slice.call(document.querySelectorAll('.asr-bridge-settings-row'));
	}
	function refreshBridgeTitle(row) {
		var title = row.querySelector('input[name="bridgeTitle[]"]');
		var id = row.querySelector('input[name="bridgeId[]"]');
		var name = row.querySelector('.asr-bridge-panel-name');
		if(!name) return;
		var text = title && title.value.trim() ? title.value.trim() : '';
		if(!text && id && id.value.trim()) text = id.value.trim().toUpperCase() + ' Bridge';
		name.textContent = text || 'New Bridge';
	}
	function refreshBridgeTitles() {
		rows().forEach(refreshBridgeTitle);
	}
	function updateAddButton() {
		if(addButton) addButton.disabled = rows().length >= max;
	}
	function escapeHtml(value) {
		return String(value == null ? '' : value).replace(/[&<>"']/g, function (char) {
			return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[char];
		});
	}
	function renderBridgeDiagnostics(payload) {
		var target = document.getElementById('asr-bridge-diagnostics');
		if(!target) return;
		if(!payload || payload.ok === false) {
			target.innerHTML = '<p class="asr-bridge-diagnostics-error">' + escapeHtml(payload && payload.error ? payload.error : 'Bridge diagnostics could not be loaded.') + '</p>';
			return;
		}
		var serviceState = (payload.collectorService || {}).state || 'unknown';
		var serviceLabel = serviceState === 'inactive' ? 'last run complete' : serviceState;
		var html = '<div class="asr-diagnostics-summary">'
			+ '<span>Collector timer: <strong>' + escapeHtml((payload.collectorTimer || {}).state || 'unknown') + '</strong></span>'
			+ '<span>Collector service: <strong>' + escapeHtml(serviceLabel) + '</strong></span>'
			+ '<span>External client file: <strong>' + escapeHtml(payload.connectedClientsFile || 'unknown') + '</strong></span>'
			+ '<span>ASR client file: <strong>' + escapeHtml(payload.asrConnectedClientsFile || 'unknown') + '</strong></span>'
			+ '</div>';
		var bridges = Array.isArray(payload.bridges) ? payload.bridges : [];
		if(!bridges.length) {
			target.innerHTML = html + '<p class="asr-settings-help">No bridge cards are configured.</p>';
			return;
		}
		html += '<div class="asr-diagnostics-bridge-list">';
		bridges.forEach(function (bridge) {
			var serviceList = Array.isArray(bridge.services) ? bridge.services : [];
			var activeServices = serviceList.filter(function (service) {
				return String(service.state || '').indexOf('active running') !== -1;
			});
			var inactiveServices = serviceList.filter(function (service) {
				return String(service.state || '').indexOf('active running') === -1;
			});
			var services = activeServices.length
				? activeServices.map(function (service) {
					return '<li>' + escapeHtml(service.unit || '') + ' <span>' + escapeHtml(service.state || '') + '</span></li>';
				}).join('')
				: '<li>No matching service hints found.</li>';
			var inactive = inactiveServices.length
				? '<details class="asr-diagnostics-muted"><summary>Other matching service hints</summary><ul>' + inactiveServices.map(function (service) {
					return '<li>' + escapeHtml(service.unit || '') + ' <span>' + escapeHtml(service.state || '') + '</span></li>';
				}).join('') + '</ul></details>'
				: '';
			var source = bridge.sourceStatus || {};
			var configuredSource = String(bridge.clientSource || 'disabled');
			var sourceLabel = configuredSource === 'local_json'
				? 'Local JSON / file'
				: configuredSource === 'http_api'
					? 'HTTP API'
					: 'None';
			var sourceStatusLabel = configuredSource === 'disabled'
				? 'Not configured'
				: String(source.status || 'unknown');
			var warnings = Array.isArray(bridge.warnings) && bridge.warnings.length
				? '<div class="asr-diagnostics-warning">' + bridge.warnings.map(escapeHtml).join('<br>') + '</div>'
				: '';
			var dmr = bridge.dmrUdp ? '<div class="asr-diagnostics-block"><h3>DMR Network</h3><div class="asr-diagnostics-mini"><span>Local UDP: <strong>' + escapeHtml(bridge.dmrUdp.localPort || 'unknown') + '</strong></span><span>Master: <strong>' + escapeHtml((bridge.dmrUdp.master || 'unknown') + (bridge.dmrUdp.masterPort ? ':' + bridge.dmrUdp.masterPort : '')) + '</strong></span><span>Listener: <strong>' + escapeHtml(bridge.dmrUdp.listener || 'unknown') + '</strong></span></div></div>' : '';
			var tgif = bridge.tgif ? '<div class="asr-diagnostics-block"><h3>TGIF Client Tracking</h3><div class="asr-diagnostics-mini">'
				+ '<span>Daemon: <strong>' + escapeHtml((bridge.tgif.clientDaemon || {}).state || 'unknown') + '</strong></span>'
				+ '<span>Refresh timer: <strong>' + escapeHtml((bridge.tgif.refreshTimer || {}).state || 'unknown') + '</strong></span>'
				+ '<span>Token: <strong>' + escapeHtml(bridge.tgif.tokenConfigured ? 'configured' : 'missing') + '</strong></span>'
				+ '<span>Login file: <strong>' + escapeHtml((bridge.tgif.loginEnv || {}).status || 'unknown') + '</strong></span>'
				+ '</div></div>' : '';
			html += '<section class="asr-diagnostics-bridge">'
				+ '<h2>' + escapeHtml(bridge.title || bridge.id || 'Bridge') + '</h2>'
				+ '<div class="asr-diagnostics-block"><h3>Bridge Link</h3>'
				+ '<div class="asr-diagnostics-mini">'
				+ '<span>ID: <strong>' + escapeHtml(bridge.id || '') + '</strong></span>'
				+ '<span>Node: <strong>' + escapeHtml(bridge.node || '') + '</strong></span>'
				+ '<span>Linked: <strong>' + escapeHtml(bridge.linked || 'unknown') + '</strong></span>'
				+ '</div></div>'
				+ '<div class="asr-diagnostics-block"><h3>Connected Clients</h3>'
				+ '<div class="asr-diagnostics-mini">'
				+ '<span>Optional feed: <strong>' + escapeHtml(sourceLabel) + '</strong></span>'
				+ '<span>Feed status: <strong>' + escapeHtml(sourceStatusLabel) + '</strong></span>'
				+ '<span>Clients currently listed: <strong>' + escapeHtml(bridge.clientCount || 0) + '</strong></span>'
				+ '</div></div>'
				+ warnings
				+ dmr
				+ tgif
				+ '<div class="asr-diagnostics-block"><h3>Bridge Software</h3><ul>' + services + '</ul>' + inactive + '</div>'
				+ '</section>';
		});
		html += '</div>';
		target.innerHTML = html;
	}
	function loadBridgeDiagnostics() {
		var target = document.getElementById('asr-bridge-diagnostics');
		if(diagnosticsLoaded || !target || !window.fetch) return;
		diagnosticsLoaded = true;
		fetch('/allscan/asr-api.php?action=bridge-diagnostics', { credentials: 'same-origin', cache: 'no-store' })
			.then(function (response) { return response.json(); })
			.then(renderBridgeDiagnostics)
			.catch(function (error) {
				renderBridgeDiagnostics({ ok:false, error:error && error.message ? error.message : 'Bridge diagnostics could not be loaded.' });
			});
	}
	document.addEventListener('input', function (event) {
		if(event.target && (event.target.name === 'bridgeTitle[]' || event.target.name === 'bridgeId[]')) {
			var row = event.target.closest('.asr-bridge-settings-row');
			if(row) refreshBridgeTitle(row);
		}
	});
	document.addEventListener('click', function (event) {
		if(event.target) {
			var sectionButton = event.target.closest('.asr-settings-section-toggle');
			if(sectionButton) {
				var section = sectionButton.closest('.asr-settings-section');
				setSectionExpanded(section, section.classList.contains('is-collapsed'));
				return;
			}
			var bridgeButton = event.target.closest('.asr-bridge-toggle');
			if(bridgeButton) {
				var bridgeRow = bridgeButton.closest('.asr-bridge-settings-row');
				setBridgeExpanded(bridgeRow, bridgeRow.classList.contains('is-collapsed'));
				return;
			}
		}
		if(event.target && event.target.classList.contains('asr-bridge-delete')) {
			var row = event.target.closest('.asr-bridge-settings-row');
			if(row) row.remove();
			updateAddButton();
		}
	});
	if(addButton && table && template) {
		addButton.addEventListener('click', function () {
			if(rows().length >= max) return;
			var fragment = template.content.cloneNode(true);
			table.appendChild(fragment);
			refreshBridgeTitles();
			updateAddButton();
			var addedRows = rows();
			if(addedRows.length) setBridgeExpanded(addedRows[addedRows.length - 1], true);
		});
	}
	Array.prototype.slice.call(document.querySelectorAll('.asr-settings-section')).forEach(function (section) {
		setSectionExpanded(section, !section.classList.contains('is-collapsed'));
	});
	rows().forEach(function (row) {
		setBridgeExpanded(row, !row.classList.contains('is-collapsed'));
	});
	refreshBridgeTitles();
	updateAddButton();
})();
</script>
<?php
asExit();
