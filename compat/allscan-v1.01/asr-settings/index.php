<?php
// AllScan Reimagined Settings controller
require_once('../include/common.php');
$html = new Html();
$msg = [];

define('ASR_SETTINGS_FILE', '/etc/allscan-reimagined/config.json');
define('ASR_SECRETS_FILE', '/etc/allscan-reimagined/secrets.json');
define('SAVE_REIMAGINED_SETTINGS', 'Save Reimagined Settings');
define('ASR_MAX_BRIDGES', 8);
define('ASR_ROLLBACK_HELPER', '/usr/local/sbin/allscan-reimagined-rollback');
define('ASR_ROLLBACK_CONFIRMATION', 'ROLLBACK_SELECTED_VERSION');

function asrSettingsWebPath($path = '') {
	global $urlbase;
	$base = rtrim((string) $urlbase, '/');
	$suffix = ltrim((string) $path, '/');
	return $suffix === '' ? $base . '/' : $base . '/' . $suffix;
}

function asrSettingsDefaultConfig() {
	return [
		'headerTitle' => '{CALLSIGN} | Node {NODE}',
		'headerLogo' => asrSettingsWebPath('asr-logo-bright-r-tight.png'),
		'brandByline' => 'by KE7WIL',
		'footerLogo' => asrSettingsWebPath('asr-logo-bright-r-tight.png'),
		'requireLogin' => true,
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
	$config = array_merge($defaults, $data);
	foreach(['headerLogo', 'footerLogo'] as $key) {
		$config[$key] = asrRebaseLegacyWebPath(
			$config[$key] ?? '',
			'asr-logo-bright-r-tight.png'
		);
	}
	return $config;
}

function asrSettingsCleanText($value, $maxLen) {
	$value = trim((string) $value);
	$value = preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/', '', $value);
	if(strlen($value) > $maxLen)
		$value = substr($value, 0, $maxLen);
	return $value;
}

function asrSettingsCleanLogo($value) {
	global $urlbase;
	$value = asrSettingsCleanText($value, 160);
	if($value === '')
		return asrSettingsWebPath('asr-logo-bright-r-tight.png');
	$value = asrRebaseLegacyWebPath($value);
	$localPrefix = preg_quote(rtrim((string) $urlbase, '/'), '#');
	if($localPrefix !== '' && preg_match('#^' . $localPrefix . '/[A-Za-z0-9._/\-]+$#', $value))
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
		case 'dmr_net': return 'DMR Net Bridge';
		case 'ysf': return 'YSF Bridge';
		case 'zello': return 'Zello Bridge';
		case 'dstar': return 'D-Star Bridge';
		case 'p25': return 'P25 Bridge';
		case 'm17': return 'M17 Bridge';
		case 'nxdn': return 'NXDN Bridge';
	}
	return strtoupper(substr($id, 0, 1)) . substr($id, 1) . ' Bridge';
}

function asrSettingsBridgeRowsFromPost(&$error, $existingBridges = [], $localNode = '') {
	$ids = $_POST['bridgeId'] ?? [];
	$nodes = $_POST['bridgeNode'] ?? [];
	$titles = $_POST['bridgeTitle'] ?? [];
	$details = $_POST['bridgeDetailTitle'] ?? [];
	$friendlyNames = $_POST['bridgeFriendlyName'] ?? [];
	$clientSources = $_POST['bridgeClientSource'] ?? [];
	$clientUrls = $_POST['bridgeClientUrl'] ?? [];
	$clientUsernames = $_POST['bridgeClientUsername'] ?? [];
	$cardTypes = $_POST['bridgeCardType'] ?? [];
	$abinfoPaths = $_POST['bridgeAbinfoPath'] ?? [];
	$dvswitchScripts = $_POST['bridgeDvswitchScript'] ?? [];
	$analogConfigs = $_POST['bridgeAnalogConfig'] ?? [];
	$bridges = [];
	$seen = [];
	$seenNodes = [];
	$seenControlPaths = [];
	$existingById = [];
	$expectedLinkAlias = preg_match('/^[0-9]{3,6}$/D', (string)$localNode)
		? '999' . str_pad((string)$localNode, 6, '0', STR_PAD_LEFT)
		: '';
	if(is_array($existingBridges)) {
		foreach($existingBridges as $existingBridge) {
			if(!is_array($existingBridge))
				continue;
			$existingId = asrSettingsCleanBridgeId($existingBridge['id'] ?? '');
			if($existingId !== '')
				$existingById[$existingId] = $existingBridge;
		}
	}
	$count = min(ASR_MAX_BRIDGES, max(count($ids), count($nodes), count($titles), count($details), count($friendlyNames), count($clientSources), count($clientUrls), count($clientUsernames), count($cardTypes), count($abinfoPaths), count($dvswitchScripts), count($analogConfigs)));

	for($i = 0; $i < $count; $i++) {
		$rawId = asrSettingsCleanText($ids[$i] ?? '', 32);
		$rawNode = asrSettingsCleanText($nodes[$i] ?? '', 10);
		$rawTitle = asrSettingsCleanText($titles[$i] ?? '', 80);
		$rawDetail = asrSettingsCleanText($details[$i] ?? '', 80);
		$rawFriendlyName = asrSettingsCleanText($friendlyNames[$i] ?? '', 80);
		$rawClientSource = asrSettingsCleanText($clientSources[$i] ?? 'disabled', 20);
		$rawClientUrl = asrSettingsCleanText($clientUrls[$i] ?? '', 220);
		$rawClientUsername = asrSettingsCleanText($clientUsernames[$i] ?? '', 80);
		$rawCardType = asrSettingsCleanText($cardTypes[$i] ?? 'standard', 20);
		$rawAbinfoPath = asrSettingsCleanText($abinfoPaths[$i] ?? '', 180);
		$rawDvswitchScript = asrSettingsCleanText($dvswitchScripts[$i] ?? '', 220);
		$rawAnalogConfig = asrSettingsCleanText($analogConfigs[$i] ?? '', 220);
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
		if(isset($seenNodes[$rawNode])) {
			$error = "Node $rawNode is already assigned to bridge \"{$seenNodes[$rawNode]}\".";
			return [];
		}
		if(!in_array($rawClientSource, ['disabled', 'local_json', 'http_api'], true))
			$rawClientSource = 'disabled';
		if(!in_array($rawCardType, ['standard', 'dmr_net'], true))
			$rawCardType = 'standard';
		if($rawCardType === 'dmr_net') {
			if(!preg_match('#^/tmp/ABInfo_[0-9]{2,5}\.json$#D', $rawAbinfoPath)) {
				$error = "DMR Net Bridge \"$id\" needs an ABInfo path such as /tmp/ABInfo_34004.json.";
				return [];
			}
			if(!preg_match('#^/opt/MMDVM_Bridge[A-Za-z0-9_-]+/dvswitch\.sh$#D', $rawDvswitchScript)) {
				$error = "DMR Net Bridge \"$id\" needs its own dedicated /opt/MMDVM_Bridge.../dvswitch.sh path.";
				return [];
			}
			if(!preg_match('#^/opt/Analog_Bridge[A-Za-z0-9_-]+/Analog_Bridge\.ini$#D', $rawAnalogConfig)) {
				$error = "DMR Net Bridge \"$id\" needs its own dedicated Analog_Bridge.ini path.";
				return [];
			}
			foreach([$rawAbinfoPath, $rawDvswitchScript, $rawAnalogConfig] as $controlPath) {
				if(isset($seenControlPaths[$controlPath])) {
					$error = "DMR control path \"$controlPath\" is already used by bridge \"{$seenControlPaths[$controlPath]}\".";
					return [];
				}
				$seenControlPaths[$controlPath] = $id;
			}
		}

		$seen[$id] = true;
		$seenNodes[$rawNode] = $id;
		$bridge = [
			'id' => $id,
			'node' => $rawNode,
			'title' => $rawTitle !== '' ? $rawTitle : asrSettingsDefaultBridgeTitle($id),
			'detailTitle' => $rawDetail !== '' ? $rawDetail : 'Connected Clients',
			'friendlyName' => $rawFriendlyName,
			'clientSource' => $rawClientSource,
			'clientUrl' => $rawClientUrl,
			'clientUsername' => $rawClientUsername,
			'cardType' => $rawCardType,
			'abinfoPath' => $rawCardType === 'dmr_net' ? $rawAbinfoPath : '',
			'dvswitchScript' => $rawCardType === 'dmr_net' ? $rawDvswitchScript : '',
			'analogConfig' => $rawCardType === 'dmr_net' ? $rawAnalogConfig : '',
		];
		$existingBridge = $existingById[$id] ?? null;
		$existingLinkAlias = is_array($existingBridge)
			? asrSettingsCleanText($existingBridge['linkAlias'] ?? '', 32)
			: '';
		if(
			$rawCardType === 'dmr_net'
			&& is_array($existingBridge)
			&& (string)($existingBridge['node'] ?? '') === $rawNode
			&& $expectedLinkAlias !== ''
			&& hash_equals($expectedLinkAlias, $existingLinkAlias)
		)
			$bridge['linkAlias'] = $existingLinkAlias;
		$bridges[] = $bridge;
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

function asrSettingsH($value) {
	return htmlspecialchars((string) $value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function asrSettingsRollbackCsrfToken($user) {
	$userId = isset($user->user_id) ? (string) $user->user_id : '';
	$loginSecret = (string) ($_COOKIE['cpass'] ?? '');
	if($userId === '' || $loginSecret === '')
		return '';
	return hash_hmac('sha256', 'asr-settings-rollback-v1|' . $userId, $loginSecret);
}

function asrSettingsRollbackPostIsSameOrigin() {
	$fetchSite = strtolower(trim((string) ($_SERVER['HTTP_SEC_FETCH_SITE'] ?? '')));
	if($fetchSite !== '' && !in_array($fetchSite, ['same-origin', 'none'], true))
		return false;

	$source = trim((string) ($_SERVER['HTTP_ORIGIN'] ?? ''));
	if($source === '')
		$source = trim((string) ($_SERVER['HTTP_REFERER'] ?? ''));
	if($source === '')
		return true;

	$normalizeOrigin = function ($value) {
		$parts = parse_url((string) $value);
		if(!is_array($parts))
			return '';
		$scheme = strtolower((string) ($parts['scheme'] ?? ''));
		$host = strtolower((string) ($parts['host'] ?? ''));
		if(!in_array($scheme, ['http', 'https'], true) || $host === '')
			return '';
		$port = isset($parts['port']) ? (int) $parts['port'] : ($scheme === 'https' ? 443 : 80);
		return $scheme . '://' . $host . ':' . $port;
	};
	$requestScheme = !empty($_SERVER['HTTPS']) && strtolower((string) $_SERVER['HTTPS']) !== 'off' ? 'https' : 'http';
	$requestOrigin = $normalizeOrigin($requestScheme . '://' . trim((string) ($_SERVER['HTTP_HOST'] ?? '')));
	$sourceOrigin = $normalizeOrigin($source);
	return $sourceOrigin !== '' && $requestOrigin !== '' && hash_equals($requestOrigin, $sourceOrigin);
}

function asrSettingsRunRollbackHelper($operation, $id, &$error) {
	$error = '';
	if(!function_exists('exec')) {
		$error = 'Rollback is unavailable because command execution is disabled.';
		return null;
	}
	if(!is_executable(ASR_ROLLBACK_HELPER)) {
		$error = 'The ASR rollback service is not installed yet.';
		return null;
	}

	if($operation === 'list') {
		$command = 'sudo -n ' . escapeshellarg(ASR_ROLLBACK_HELPER) . ' --list-json 2>/dev/null';
	} elseif($operation === 'queue' && preg_match('/^\d{8}-\d{6}$/D', (string) $id)) {
		$command = 'sudo -n ' . escapeshellarg(ASR_ROLLBACK_HELPER) . ' --queue-rollback ' . escapeshellarg((string) $id) . ' 2>/dev/null';
	} else {
		$error = 'Invalid rollback request.';
		return null;
	}

	$output = [];
	$status = 1;
	exec($command, $output, $status);
	$json = implode("\n", $output);
	if(strlen($json) > 1048576) {
		$error = 'The rollback service returned too much data.';
		return null;
	}
	$data = json_decode($json, true);
	if($status !== 0 || !is_array($data) || empty($data['ok'])) {
		$error = 'The rollback service could not complete the request.';
		if(is_array($data) && isset($data['error']) && is_string($data['error'])) {
			$detail = asrSettingsCleanText($data['error'], 180);
			if($detail !== '')
				$error .= ' ' . $detail;
		}
		return null;
	}
	return $data;
}

function asrSettingsRollbackCandidates($currentVersion, &$error) {
	$data = asrSettingsRunRollbackHelper('list', '', $error);
	if(!$data)
		return [];

	$rows = isset($data['backups']) && is_array($data['backups']) ? $data['backups'] : [];
	usort($rows, function ($a, $b) {
		return strcmp((string) ($b['id'] ?? ''), (string) ($a['id'] ?? ''));
	});

	$currentKey = strtolower(trim((string) $currentVersion));
	$seenVersions = [];
	$candidates = [];
	foreach($rows as $row) {
		if(!is_array($row))
			continue;
		$id = (string) ($row['id'] ?? '');
		$version = trim((string) ($row['version'] ?? ''));
		$label = trim((string) ($row['label'] ?? ''));
		$createdAt = trim((string) ($row['createdAt'] ?? ($row['created_at'] ?? '')));
		if(!preg_match('/^\d{8}-\d{6}$/D', $id))
			continue;
		if($version === '' || strlen($version) > 80 || preg_match('/[\x00-\x1F\x7F]/', $version))
			continue;

		$versionKey = strtolower($version);
		if($versionKey === $currentKey || isset($seenVersions[$versionKey]))
			continue;
		$seenVersions[$versionKey] = true;

		$label = asrSettingsCleanText($label, 140);
		$createdAt = asrSettingsCleanText($createdAt, 80);
		if($label === '')
			$label = $version . ($createdAt !== '' ? ' — ' . $createdAt : '');
		$candidates[] = [
			'id' => $id,
			'version' => $version,
			'label' => $label,
			'createdAt' => $createdAt,
		];
		if(count($candidates) >= 5)
			break;
	}
	return $candidates;
}

function asrSettingsSourceOption($source, $value, $label) {
	return '<option value="' . asrSettingsH($value) . '"' . ($source === $value ? ' selected' : '') . '>' . asrSettingsH($label) . '</option>';
}

function asrSettingsBridgePanel($bridge = [], $bridgePasswords = []) {
	$id = (string)($bridge['id'] ?? '');
	$source = (string)($bridge['clientSource'] ?? 'disabled');
	$cardType = (string)($bridge['cardType'] ?? 'standard');
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
					<span class="asr-bridge-panel-summary">Node <?php echo asrSettingsH($bridge['node'] ?? 'not set'); ?> · Bridge card, Connection Status name, and optional connected-client source.</span>
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
				<label><span>Card Type</span><select name="bridgeCardType[]">
					<?php echo asrSettingsSourceOption($cardType, 'standard', 'Standard Bridge'); ?>
					<?php echo asrSettingsSourceOption($cardType, 'dmr_net', 'DMR Net Bridge'); ?>
				</select></label>
				<label><span>ID</span><input name="bridgeId[]" type="text" placeholder="dmr" value="<?php echo asrSettingsH($id); ?>"></label>
				<label><span>Node</span><input name="bridgeNode[]" type="text" inputmode="numeric" placeholder="1001" value="<?php echo asrSettingsH($bridge['node'] ?? ''); ?>"></label>
				<label><span>Card Title</span><input name="bridgeTitle[]" type="text" placeholder="DMR Bridge" value="<?php echo asrSettingsH($bridge['title'] ?? ''); ?>"></label>
				<label><span>Detail Title</span><input name="bridgeDetailTitle[]" type="text" placeholder="Connected Clients" value="<?php echo asrSettingsH($bridge['detailTitle'] ?? ''); ?>"></label>
			</div>
		</div>

		<div class="asr-bridge-panel-section asr-dmr-net-settings"<?php echo $cardType === 'dmr_net' ? '' : ' hidden'; ?>>
			<div class="asr-bridge-section-copy">
				<strong>DMR Net Controls</strong>
				<span>Connect selects the entered talkgroup and links the AllStar node above. Disconnect exits the DMR talkgroup and unlinks the AllStar node.</span>
			</div>
			<div class="asr-bridge-fields-grid">
				<label><span>ABInfo Path</span><input name="bridgeAbinfoPath[]" type="text" placeholder="/tmp/ABInfo_34004.json" value="<?php echo asrSettingsH($bridge['abinfoPath'] ?? ''); ?>"></label>
				<label><span>DVSwitch Script</span><input name="bridgeDvswitchScript[]" type="text" placeholder="/opt/MMDVM_Bridge_DMRNet/dvswitch.sh" value="<?php echo asrSettingsH($bridge['dvswitchScript'] ?? ''); ?>"></label>
				<label><span>Analog Bridge Config</span><input name="bridgeAnalogConfig[]" type="text" placeholder="/opt/Analog_Bridge_DMRNet/Analog_Bridge.ini" value="<?php echo asrSettingsH($bridge['analogConfig'] ?? ''); ?>"></label>
			</div>
			<p class="asr-bridge-section-note">The bridge installer must first provision and validate the dedicated AllStar node, internal ASR link identity, ABInfo path, DVSwitch script, Analog Bridge config, ports, and services. Connect changes the talkgroup for everyone using this bridge. Controls are shown only to logged-in operators with node-control permission.</p>
		</div>

		<div class="asr-bridge-panel-section">
			<div class="asr-bridge-section-copy">
				<strong>Connection Status</strong>
				<span>Controls the name used for this bridge node in the Connection Status table.</span>
			</div>
			<div class="asr-bridge-fields-grid asr-bridge-status-grid">
				<label><span>Connection Status Name</span><input name="bridgeFriendlyName[]" type="text" placeholder="<?php echo asrSettingsH($panelTitle); ?>" value="<?php echo asrSettingsH($bridge['friendlyName'] ?? ''); ?>"></label>
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
				<label><span>URL / Path</span><input name="bridgeClientUrl[]" type="text" placeholder="<?php echo asrSettingsH(dirname(asrSettingsUploadDir()) . '/connected-clients.json'); ?>" value="<?php echo asrSettingsH($bridge['clientUrl'] ?? ''); ?>"></label>
				<label><span>Username</span><input name="bridgeClientUsername[]" type="text" value="<?php echo asrSettingsH($bridge['clientUsername'] ?? ''); ?>"></label>
				<label><span>Password / Token</span><input name="bridgeClientPassword[]" type="password" placeholder="<?php echo asrSettingsH($passwordPlaceholder); ?>"></label>
			</div>
			<p class="asr-bridge-section-note">Disabled uses any current external connected-client data and does not start ASR's optional collector. Local JSON / file should point to a readable JSON file. HTTP API should point to a JSON endpoint made for client status.</p>
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
$currentAsrVersion = defined('ASR_REIMAGINED_VERSION_LABEL') ? ASR_REIMAGINED_VERSION_LABEL : 'Current ASR version';
$rollbackListError = '';
$rollbackCandidates = asrSettingsRollbackCandidates($currentAsrVersion, $rollbackListError);
$rollbackCandidateById = [];
foreach($rollbackCandidates as $candidate)
	$rollbackCandidateById[$candidate['id']] = $candidate;
$rollbackCsrfToken = asrSettingsRollbackCsrfToken($user);
$submit = $_POST['Submit'] ?? null;
$asrAction = $_POST['asrAction'] ?? null;

if($asrAction === 'queue-rollback') {
	$rollbackId = trim((string) ($_POST['rollbackId'] ?? ''));
	$postedToken = (string) ($_POST['rollbackCsrf'] ?? '');
	$postedConfirmation = (string) ($_POST['rollbackConfirmation'] ?? '');
	if(($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
		$rollbackError = 'Rollback requires a POST request.';
	} elseif(!asrSettingsRollbackPostIsSameOrigin()) {
		$rollbackError = 'Rollback was blocked because the request did not come from this node.';
	} elseif($rollbackCsrfToken === '' || $postedToken === '' || !hash_equals($rollbackCsrfToken, $postedToken)) {
		$rollbackError = 'The rollback confirmation was invalid. Reload this page and try again.';
	} elseif($postedConfirmation !== ASR_ROLLBACK_CONFIRMATION) {
		$rollbackError = 'Rollback was not confirmed.';
	} elseif(!preg_match('/^\d{8}-\d{6}$/D', $rollbackId) || !isset($rollbackCandidateById[$rollbackId])) {
		$rollbackError = 'Select one of the available rollback versions.';
	} else {
		$target = $rollbackCandidateById[$rollbackId];
		$helperError = '';
		$result = asrSettingsRunRollbackHelper('queue', $rollbackId, $helperError);
		if(!$result) {
			$rollbackError = $helperError;
		} else {
			$rollbackQueuedJobId = (string) ($result['jobId'] ?? '');
			$rollbackQueuedVersion = $target['version'];
			if(!preg_match('/^\d{8}-\d{6}-[a-f0-9]{8}$/D', $rollbackQueuedJobId)) {
				$rollbackQueuedJobId = '';
				$rollbackError = 'The rollback service returned an invalid job number.';
			} else {
				$rollbackOk = 'Rollback to ' . $target['version'] . ' has started. Keep this page open while ASR creates a safety backup and restores the selected version.';
			}
		}
	}
} elseif($submit === SAVE_REIMAGINED_SETTINGS) {
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
		$saveError = 'Header logo must be a local ASR path or an http/https URL.';
	} else {
		$bridgeError = '';
		$bridges = asrSettingsBridgeRowsFromPost(
			$bridgeError,
			$config['bridges'] ?? [],
			$config['node'] ?? ''
		);
		if($bridgeError) {
			$saveError = $bridgeError;
		} else {
			$next['headerTitle'] = $headerTitle;
				$next['headerLogo'] = $logo;
				$next['brandByline'] = 'by KE7WIL';
				$next['footerLogo'] = asrSettingsWebPath('asr-logo-bright-r-tight.png');
				$next['requireLogin'] = $requireLogin;
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
					if($saveError === '') {
						if(is_executable('/usr/local/sbin/allscan-reimagined-friendly-names'))
							@shell_exec('sudo -n /usr/local/sbin/allscan-reimagined-friendly-names --once 2>/dev/null || /usr/local/sbin/allscan-reimagined-friendly-names --once 2>/dev/null');
						if(is_executable('/usr/local/sbin/allscan-reimagined-bridge-clients'))
							@shell_exec('sudo -n /usr/local/sbin/allscan-reimagined-bridge-clients --once 2>/dev/null || /usr/local/sbin/allscan-reimagined-bridge-clients --once 2>/dev/null');
						$config = $next;
						$secrets = $nextSecrets;
						if(function_exists('asrApplyAccessPolicy'))
							asrApplyAccessPolicy();
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
if(!empty($rollbackOk))
	okMsg($rollbackOk);
if(!empty($rollbackError))
	errMsg($rollbackError);

$requireLogin = !array_key_exists('requireLogin', $config) || !empty($config['requireLogin']);
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
		<p class="asr-settings-inline-note">Use a local <?php echo asrSettingsH(rtrim($urlbase, '/') . '/...'); ?> path, an http/https URL, or upload a PNG, JPEG, or WebP image under 1 MB.</p>
	</fieldset>

	<fieldset class="asr-settings-section is-collapsed" data-settings-section="bridges">
		<legend><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Bridge Cards <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></legend>
		<p class="asr-settings-help">Only active bridge cards are listed here. Use Add Bridge for another card, up to <?php echo ASR_MAX_BRIDGES; ?> total.</p>
		<p class="asr-settings-help">Use lowercase bridge IDs like dmr, dmr_net, ysf, zello, dstar, p25, m17, or nxdn. ASR uses the node number to match the bridge to live status. The DMR Net Bridge option requires its bridge installer to provision and validate the dedicated resources and internal ASR link identity; ASR still shows the node entered here.</p>
		<label class="asr-settings-check">
			<input name="maintainFriendlyNames" type="checkbox" value="1"<?php echo $maintainFriendlyNames ? ' checked' : ''; ?>>
			<span>Maintain bridge friendly names across updates, restarts, and reboots</span>
		</label>
		<p class="asr-settings-inline-note">When enabled, ASR keeps configured bridge node labels matching the Connection Status Name.</p>
		<div class="asr-bridge-settings-table">
			<?php foreach($bridgeRows as $bridge): ?>
				<?php asrSettingsBridgePanel($bridge, $bridgePasswords); ?>
			<?php endforeach; ?>
		</div>
		<button class="asr-add-bridge-button" type="button">+ Add Bridge</button>
		<p class="asr-settings-inline-note">After saving bridge changes, refresh the main ASR page. If an old name remains, perform a hard refresh: Ctrl+Shift+R on Windows/Linux or Command+Shift+R on Mac. On a phone, close the ASR tab and reopen it.</p>
		<p class="asr-settings-help-action"><a class="asr-settings-help-button" href="<?php echo asrSettingsH(asrSettingsWebPath('asr-instructions/#bridge-cards')); ?>">Open Full Reimagined Help</a></p>
	</fieldset>

	<fieldset class="asr-settings-section is-collapsed" data-settings-section="bridge-help">
		<legend><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Bridge Setup Help <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></legend>
		<div class="asr-setup-help-grid">
			<section>
				<h2>Before Adding a Card</h2>
				<p>The bridge and its private AllStar node must already be installed and working. ASR displays and monitors the bridge; it does not create the bridge software, ports, IDs, credentials, or network forwarding.</p>
			</section>
			<section>
				<h2>Card Basics</h2>
				<p>Choose the card type, enter the bridge ID and node, then set the card and Connection Status names. Leave Connected Client Source disabled unless the bridge provides a real JSON file or API.</p>
			</section>
			<section>
				<h2>DMR Net Bridge</h2>
				<p>This card type needs a separately installed, tunable DMR bridge. Authorized operators can enter a talkgroup, connect it to the main node, and disconnect it when the net ends.</p>
			</section>
		</div>
		<p class="asr-settings-help-action"><a class="asr-settings-help-button" href="<?php echo asrSettingsH(asrSettingsWebPath('asr-instructions/#bridge-setup')); ?>">Read Detailed Bridge Setup Help</a></p>
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

	<fieldset class="asr-settings-section asr-rollback-section is-collapsed" data-settings-section="rollback">
		<legend><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Roll Back ASR Version <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></legend>
		<p class="asr-settings-help">Restore one of the five newest valid previous ASR versions. Users, Favorites, the database, Reimagined settings, bridge settings, map cache, and protected secrets are preserved.</p>
		<div class="asr-rollback-current">
			<span>Currently installed</span>
			<strong><?php echo asrSettingsH($currentAsrVersion); ?></strong>
		</div>
		<div class="asr-rollback-controls">
			<label for="asrRollbackSelect">
				<span>Previous Version</span>
				<select id="asrRollbackSelect"<?php echo empty($rollbackCandidates) ? ' disabled' : ''; ?>>
					<option value="">Select a previous version</option>
					<?php foreach($rollbackCandidates as $candidate): ?>
						<option value="<?php echo asrSettingsH($candidate['id']); ?>" data-version="<?php echo asrSettingsH($candidate['version']); ?>"><?php echo asrSettingsH($candidate['label']); ?></option>
					<?php endforeach; ?>
				</select>
			</label>
			<button id="asrRollbackReview" class="asr-rollback-button" type="button" disabled>Roll Back to Selected Version</button>
		</div>
		<?php if($rollbackListError): ?>
			<p class="asr-rollback-status"><?php echo asrSettingsH($rollbackListError); ?></p>
		<?php elseif(empty($rollbackCandidates)): ?>
			<p class="asr-rollback-status">No valid previous ASR versions are currently available.</p>
		<?php endif; ?>
		<p id="asrRollbackProgress" class="asr-rollback-status"<?php echo empty($rollbackQueuedJobId) ? ' hidden' : ''; ?>><?php echo !empty($rollbackQueuedJobId) ? 'Rollback queued. Waiting for the safety backup to begin…' : ''; ?></p>
		<p class="asr-rollback-warning"><strong>Important:</strong> Rollback has its own button. The Save Reimagined Settings button does not perform a rollback, and any unsaved settings edits will not be saved during rollback. If the selected older version predates this feature, the rollback menu will no longer appear there; the safety backup and command-line recovery helper remain available.</p>
	</fieldset>

	<p class="asr-reimagined-submit">
		<input type="submit" name="Submit" value="<?php echo SAVE_REIMAGINED_SETTINGS; ?>">
		<span>Saved on the node at <?php echo asrSettingsH(ASR_SETTINGS_FILE); ?>.</span>
	</p>
</form>
<form id="asrRollbackForm" class="asr-rollback-hidden-form" method="post" action="">
	<input type="hidden" name="asrAction" value="queue-rollback">
	<input id="asrRollbackId" type="hidden" name="rollbackId" value="">
	<input type="hidden" name="rollbackCsrf" value="<?php echo asrSettingsH($rollbackCsrfToken); ?>">
	<input id="asrRollbackConfirmation" type="hidden" name="rollbackConfirmation" value="">
</form>
<div id="asrRollbackDialog" class="asr-rollback-dialog" role="dialog" aria-modal="true" aria-labelledby="asrRollbackDialogTitle" hidden>
	<div class="asr-rollback-dialog-card">
		<h2 id="asrRollbackDialogTitle">Confirm ASR Rollback</h2>
		<div class="asr-rollback-version-change" aria-label="Rollback version change">
			<div><span>Current version</span><strong><?php echo asrSettingsH($currentAsrVersion); ?></strong></div>
			<span class="asr-rollback-arrow" aria-hidden="true">→</span>
			<div><span>Restore version</span><strong id="asrRollbackTargetVersion"></strong></div>
		</div>
		<p>ASR will create a fresh safety backup and then restore the selected version. Asterisk and bridge services should not be restarted.</p>
		<p class="asr-rollback-dialog-warning"><strong>Unsaved settings edits on this page will not be saved.</strong></p>
		<div class="asr-rollback-dialog-actions">
			<button id="asrRollbackCancel" type="button">Cancel</button>
			<button id="asrRollbackConfirm" class="asr-rollback-button" type="button">Confirm Rollback</button>
		</div>
	</div>
</div>
<template id="asr-bridge-row-template">
	<div class="asr-bridge-settings-row">
		<div class="asr-bridge-panel-header">
			<button class="asr-bridge-toggle" type="button" aria-expanded="true">
				<span class="asr-bridge-toggle-copy">
					<strong class="asr-bridge-panel-name">New Bridge</strong>
					<span class="asr-bridge-panel-summary">Node not set · Bridge card, Connection Status name, and optional connected-client source.</span>
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
					<label><span>Card Type</span><select name="bridgeCardType[]">
						<option value="standard">Standard Bridge</option>
						<option value="dmr_net">DMR Net Bridge</option>
					</select></label>
					<label><span>ID</span><input name="bridgeId[]" type="text" placeholder="dmr"></label>
				<label><span>Node</span><input name="bridgeNode[]" type="text" inputmode="numeric" placeholder="1001"></label>
				<label><span>Card Title</span><input name="bridgeTitle[]" type="text" placeholder="DMR Bridge"></label>
					<label><span>Detail Title</span><input name="bridgeDetailTitle[]" type="text" placeholder="Connected Clients"></label>
				</div>
			</div>

			<div class="asr-bridge-panel-section asr-dmr-net-settings" hidden>
				<div class="asr-bridge-section-copy">
					<strong>DMR Net Controls</strong>
					<span>Connect selects the entered talkgroup and links the AllStar node above. Disconnect exits the DMR talkgroup and unlinks the AllStar node.</span>
				</div>
				<div class="asr-bridge-fields-grid">
					<label><span>ABInfo Path</span><input name="bridgeAbinfoPath[]" type="text" placeholder="/tmp/ABInfo_34004.json"></label>
					<label><span>DVSwitch Script</span><input name="bridgeDvswitchScript[]" type="text" placeholder="/opt/MMDVM_Bridge_DMRNet/dvswitch.sh"></label>
					<label><span>Analog Bridge Config</span><input name="bridgeAnalogConfig[]" type="text" placeholder="/opt/Analog_Bridge_DMRNet/Analog_Bridge.ini"></label>
				</div>
					<p class="asr-bridge-section-note">The bridge installer must first provision and validate the dedicated AllStar node, internal ASR link identity, ABInfo path, DVSwitch script, Analog Bridge config, ports, and services. Connect changes the talkgroup for everyone using this bridge. Controls are shown only to logged-in operators with node-control permission.</p>
			</div>

			<div class="asr-bridge-panel-section">
			<div class="asr-bridge-section-copy">
				<strong>Connection Status</strong>
				<span>Controls the name used for this bridge node in the Connection Status table.</span>
			</div>
			<div class="asr-bridge-fields-grid asr-bridge-status-grid">
				<label><span>Connection Status Name</span><input name="bridgeFriendlyName[]" type="text" placeholder="Same as Card Title"></label>
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
				<label><span>URL / Path</span><input name="bridgeClientUrl[]" type="text" placeholder="<?php echo asrSettingsH(dirname(asrSettingsUploadDir()) . '/connected-clients.json'); ?>"></label>
				<label><span>Username</span><input name="bridgeClientUsername[]" type="text"></label>
				<label><span>Password / Token</span><input name="bridgeClientPassword[]" type="password"></label>
			</div>
					<p class="asr-bridge-section-note">Disabled uses any current external connected-client data and does not start ASR's optional collector. Local JSON / file should point to a readable JSON file. HTTP API should point to a JSON endpoint made for client status.</p>
			</div>
		</div>
	</div>
</template>
<script>
(function () {
	var asrBase = <?php echo json_encode(rtrim($urlbase, '/'), JSON_UNESCAPED_SLASHES); ?>;
	var form = document.querySelector('.asr-reimagined-settings-form');
	var table = document.querySelector('.asr-bridge-settings-table');
	var template = document.getElementById('asr-bridge-row-template');
	var addButton = document.querySelector('.asr-add-bridge-button');
	var max = form ? parseInt(form.getAttribute('data-max-bridges') || '8', 10) : 8;
	var diagnosticsLoaded = false;
	var rollbackSelect = document.getElementById('asrRollbackSelect');
	var rollbackReview = document.getElementById('asrRollbackReview');
	var rollbackForm = document.getElementById('asrRollbackForm');
	var rollbackId = document.getElementById('asrRollbackId');
	var rollbackConfirmation = document.getElementById('asrRollbackConfirmation');
	var rollbackDialog = document.getElementById('asrRollbackDialog');
	var rollbackTargetVersion = document.getElementById('asrRollbackTargetVersion');
	var rollbackCancel = document.getElementById('asrRollbackCancel');
	var rollbackConfirm = document.getElementById('asrRollbackConfirm');
	var rollbackProgress = document.getElementById('asrRollbackProgress');
	var rollbackFocusReturn = null;
	var pendingRollbackId = '';
	var rollbackJobId = <?php echo json_encode((string) ($rollbackQueuedJobId ?? ''), JSON_UNESCAPED_SLASHES); ?>;
	var rollbackQueuedVersion = <?php echo json_encode((string) ($rollbackQueuedVersion ?? ''), JSON_UNESCAPED_SLASHES); ?>;
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
		var node = row.querySelector('input[name="bridgeNode[]"]');
		var cardType = row.querySelector('select[name="bridgeCardType[]"]');
		var name = row.querySelector('.asr-bridge-panel-name');
		var summary = row.querySelector('.asr-bridge-panel-summary');
		if(!name) return;
		var text = title && title.value.trim() ? title.value.trim() : '';
		if(!text && cardType && cardType.value === 'dmr_net') text = 'DMR Net Bridge';
		if(!text && id && id.value.trim()) text = id.value.trim().toUpperCase() + ' Bridge';
		name.textContent = text || 'New Bridge';
		if(summary) summary.textContent = 'Node ' + (node && node.value.trim() ? node.value.trim() : 'not set') + ' · Bridge card, Connection Status name, and optional connected-client source.';
	}
		function refreshBridgeTitles() {
			rows().forEach(refreshBridgeTitle);
		}
		function refreshBridgeType(row) {
			var select = row.querySelector('select[name="bridgeCardType[]"]');
			var settings = row.querySelector('.asr-dmr-net-settings');
			if(settings) settings.hidden = !select || select.value !== 'dmr_net';
			refreshBridgeTitle(row);
		}
		function refreshBridgeTypes() {
			rows().forEach(refreshBridgeType);
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
		var collectorRequired = payload.collectorRequired !== false;
		var serviceState = (payload.collectorService || {}).state || 'unknown';
		var serviceLabel = serviceState === 'inactive' ? 'last run complete' : serviceState;
		var html = '<div class="asr-diagnostics-summary">'
			+ '<span>Collector timer: <strong>' + escapeHtml(collectorRequired ? ((payload.collectorTimer || {}).state || 'unknown') : 'not needed') + '</strong></span>'
			+ '<span>Collector service: <strong>' + escapeHtml(collectorRequired ? serviceLabel : 'not needed') + '</strong></span>'
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
				+ '<span>Credential file: <strong>' + escapeHtml((bridge.tgif.tokenEnvironment || {}).status || 'unknown') + '</strong></span>'
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
		fetch(asrBase + '/asr-api.php?action=bridge-diagnostics', { credentials: 'same-origin', cache: 'no-store' })
			.then(function (response) { return response.json(); })
			.then(renderBridgeDiagnostics)
			.catch(function (error) {
				renderBridgeDiagnostics({ ok:false, error:error && error.message ? error.message : 'Bridge diagnostics could not be loaded.' });
			});
	}
	function selectedRollbackOption() {
		if(!rollbackSelect || rollbackSelect.selectedIndex < 1) return null;
		var option = rollbackSelect.options[rollbackSelect.selectedIndex];
		if(!option || !/^\d{8}-\d{6}$/.test(option.value)) return null;
		return option;
	}
	function updateRollbackButton() {
		if(rollbackReview) rollbackReview.disabled = !selectedRollbackOption();
	}
	function closeRollbackDialog() {
		if(!rollbackDialog) return;
		rollbackDialog.hidden = true;
		document.body.classList.remove('asr-rollback-dialog-open');
		pendingRollbackId = '';
		if(rollbackFocusReturn && typeof rollbackFocusReturn.focus === 'function')
			rollbackFocusReturn.focus();
		rollbackFocusReturn = null;
	}
	function openRollbackDialog() {
		var option = selectedRollbackOption();
		if(!option || !rollbackDialog || !rollbackTargetVersion) return;
		pendingRollbackId = option.value;
		rollbackTargetVersion.textContent = option.getAttribute('data-version') || option.textContent || 'Selected version';
		rollbackFocusReturn = document.activeElement;
		rollbackDialog.hidden = false;
		document.body.classList.add('asr-rollback-dialog-open');
		if(rollbackCancel) rollbackCancel.focus();
	}
	function pollRollbackStatus() {
		if(!rollbackJobId || !/^\d{8}-\d{6}-[a-f0-9]{8}$/.test(rollbackJobId) || !window.fetch)
			return;
		var attempts = 0;
		var poll = function () {
			attempts++;
			fetch(asrBase + '/asr-settings/rollback-status.php?job=' + encodeURIComponent(rollbackJobId), {
				credentials: 'same-origin',
				cache: 'no-store'
			})
				.then(function (response) {
					if(!response.ok) throw new Error('status unavailable');
					return response.json();
				})
				.then(function (payload) {
					var state = payload && payload.state ? String(payload.state) : '';
					if(rollbackProgress) {
						rollbackProgress.hidden = false;
						rollbackProgress.textContent = state === 'queued'
							? 'Rollback queued. Waiting for the safety backup to begin…'
							: state === 'running'
								? 'Rollback in progress. ASR is creating a safety backup and restoring ' + (rollbackQueuedVersion || 'the selected version') + '…'
								: state === 'succeeded'
									? 'Rollback completed. Reloading ASR…'
									: state === 'failed'
										? 'Rollback failed. The previous installation was restored.'
										: 'Checking rollback status…';
					}
					if(state === 'succeeded') {
						window.setTimeout(function () { window.location.assign(asrBase + '/'); }, 1200);
						return;
					}
					if(state === 'failed')
						return;
					window.setTimeout(poll, 2000);
				})
				.catch(function () {
					if(attempts < 450)
						window.setTimeout(poll, 2000);
					else if(rollbackProgress)
						rollbackProgress.textContent = 'Rollback status could not be confirmed. Reopen ASR and verify the installed version.';
				});
		};
		poll();
	}
	if(rollbackSelect)
		rollbackSelect.addEventListener('change', updateRollbackButton);
	if(rollbackReview)
		rollbackReview.addEventListener('click', openRollbackDialog);
	if(rollbackCancel)
		rollbackCancel.addEventListener('click', closeRollbackDialog);
	if(rollbackDialog) {
		rollbackDialog.addEventListener('click', function (event) {
			if(event.target === rollbackDialog) closeRollbackDialog();
		});
	}
	document.addEventListener('keydown', function (event) {
		if(event.key === 'Escape' && rollbackDialog && !rollbackDialog.hidden) {
			event.preventDefault();
			closeRollbackDialog();
		}
	});
	if(rollbackConfirm) {
		rollbackConfirm.addEventListener('click', function () {
			if(!rollbackForm || !rollbackId || !rollbackConfirmation || !/^\d{8}-\d{6}$/.test(pendingRollbackId))
				return;
			rollbackId.value = pendingRollbackId;
			rollbackConfirmation.value = '<?php echo ASR_ROLLBACK_CONFIRMATION; ?>';
			rollbackConfirm.disabled = true;
			rollbackConfirm.textContent = 'Starting Rollback…';
			if(rollbackReview) rollbackReview.disabled = true;
			HTMLFormElement.prototype.submit.call(rollbackForm);
		});
	}
		document.addEventListener('input', function (event) {
		if(event.target && (event.target.name === 'bridgeTitle[]' || event.target.name === 'bridgeId[]' || event.target.name === 'bridgeNode[]')) {
			var row = event.target.closest('.asr-bridge-settings-row');
			if(row) refreshBridgeTitle(row);
			}
		});
		document.addEventListener('change', function (event) {
			if(event.target && event.target.name === 'bridgeCardType[]') {
				var row = event.target.closest('.asr-bridge-settings-row');
				if(row) refreshBridgeType(row);
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
				refreshBridgeTypes();
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
		refreshBridgeTypes();
		updateAddButton();
	updateRollbackButton();
	pollRollbackStatus();
})();
</script>
<?php
asExit();
