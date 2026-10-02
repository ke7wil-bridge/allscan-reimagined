<?php
// AllScan Reimagined Settings controller
$asrSettingsFunctionsOnly = defined('ASR_SETTINGS_FUNCTIONS_ONLY') && ASR_SETTINGS_FUNCTIONS_ONLY;
if(!$asrSettingsFunctionsOnly)
	require_once('../include/common.php');
$html = $asrSettingsFunctionsOnly ? null : new Html();
$msg = [];

define('ASR_SETTINGS_FILE', '/etc/allscan-reimagined/config.json');
define('ASR_SECRETS_FILE', '/etc/allscan-reimagined/secrets.json');
define('SAVE_REIMAGINED_SETTINGS', 'Save Reimagined Settings');
define('ASR_MAX_BRIDGES', 16);
define('ASR_MAX_CUSTOM_YSF_REFLECTORS', 32);
define('ASR_MAX_APPROVED_DESTINATIONS', 256);
define('ASR_ROLLBACK_HELPER', '/usr/local/sbin/allscan-reimagined-rollback');
define('ASR_YSF_BRIDGE_HELPER', '/usr/local/sbin/allscan-reimagined-ysf-bridge-control');
define('ASR_BRIDGE_LIFECYCLE_HELPER', '/usr/local/sbin/allscan-reimagined-bridge-lifecycle');
define('ASR_URF_ADMIN_HELPER', '/usr/local/sbin/allscan-reimagined-urf-admin');
define('ASR_BRIDGE_SETUP_HELPER', '/usr/local/libexec/allscan-reimagined/asr-bridge-setup-helper.py');
define('ASR_MAX_YSF_HOSTS_UPLOAD_BYTES', 2000000);
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
		'announceStartupBridgeSummary' => false,
		'announceNoConnectedBridges' => false,
		'lowPowerMode' => false,
		'filterServiceStations' => true,
		'filteredStations' => [],
		'dmrId' => '',
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

function asrSettingsCleanFilteredStations($value, &$error = '') {
	$values = preg_split('/[\s,]+/', strtoupper((string) $value), -1, PREG_SPLIT_NO_EMPTY) ?: [];
	$result = [];
	foreach($values as $station) {
		$station = trim($station);
		if(!preg_match('/^[A-Z0-9][A-Z0-9_.\/-]{0,14}$/D', $station)) {
			$error = 'Filtered stations must use 1–15 characters: A-Z, 0-9, _, ., /, or -.';
			return [];
		}
		$result[$station] = true;
		if(count($result) > 64) {
			$error = 'No more than 64 custom filtered stations may be saved.';
			return [];
		}
	}
	return array_keys($result);
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

function asrSettingsSupportedBridgeModes() {
	return ['dmr', 'ysf', 'dstar', 'zello', 'p25', 'nxdn', 'm17'];
}

function asrSettingsBridgeMode($bridge) {
	$candidates = [
		is_array($bridge) ? ($bridge['mode'] ?? '') : '',
		is_array($bridge) ? ($bridge['id'] ?? '') : '',
	];
	foreach($candidates as $candidate) {
		$compact = preg_replace('/[^a-z0-9]/', '', strtolower((string)$candidate));
		foreach(asrSettingsSupportedBridgeModes() as $mode) {
			if(strpos($compact, $mode) === 0)
				return $mode;
		}
	}
	return 'dmr';
}

function asrSettingsNewBridgeId($mode, $cardType, $seen) {
	$base = $mode . ($cardType === 'standard' ? '' : '_net');
	if(!isset($seen[$base])) return $base;
	for($suffix = 2; $suffix <= ASR_MAX_BRIDGES; $suffix++) {
		$candidate = $base . '_' . $suffix;
		if(!isset($seen[$candidate])) return $candidate;
	}
	return '';
}

function asrSettingsDesignatorIsAllowed($value, $mode) {
	$number = (int)$value;
	if(!preg_match('/^[0-9]{1,5}$/D', (string)$value) || $number < 11 || $number > 65534)
		return false;
	$reserved = $mode === 'p25' ? [20, 9999, 10999] : [20, 9999];
	return !in_array($number, $reserved, true);
}

function asrSettingsApprovedDesignators($value, &$error, $label, $mode) {
	$tokens = preg_split('/[\s,]+/', trim((string)$value), -1, PREG_SPLIT_NO_EMPTY);
	$tokens = is_array($tokens) ? array_values(array_unique($tokens)) : [];
	if(count($tokens) > ASR_MAX_APPROVED_DESTINATIONS) {
		$error = "$label supports at most " . ASR_MAX_APPROVED_DESTINATIONS . ' approved destinations.';
		return [];
	}
	foreach($tokens as $token) {
		if(!asrSettingsDesignatorIsAllowed($token, $mode)) {
			$error = "$label approved destinations must be valid 11-65534 designators and cannot use reserved disconnect/control values.";
			return [];
		}
	}
	return $tokens;
}

function asrSettingsApprovedDesignatorsText($destinations) {
	return implode(', ', array_map('strval', is_array($destinations) ? $destinations : []));
}

function asrSettingsParseM17Destinations($value, &$error, $label) {
	$lines = preg_split('/\R/', trim((string)$value), -1, PREG_SPLIT_NO_EMPTY);
	$lines = is_array($lines) ? array_values(array_map('trim', $lines)) : [];
	if(count($lines) > ASR_MAX_APPROVED_DESTINATIONS) {
		$error = "$label supports at most " . ASR_MAX_APPROVED_DESTINATIONS . ' approved destinations.';
		return [];
	}
	$result = [];
	$seen = [];
	foreach($lines as $line) {
		$parts = array_map('trim', explode('|', $line));
		if(count($parts) !== 4) {
			$error = "$label destinations must use REFLECTOR | HOST | PORT | MODULE, one per line.";
			return [];
		}
		[$reflector, $host, $port, $module] = $parts;
		$reflector = strtoupper($reflector);
		$module = strtoupper($module);
		if(!preg_match('/^M17-[A-Z0-9]{3}$/D', $reflector)
			|| !preg_match('/^[A-Za-z0-9.-]{1,253}$/D', $host)
			|| !preg_match('/^[0-9]{1,5}$/D', $port)
			|| (int)$port < 1 || (int)$port > 65535
			|| !preg_match('/^[A-Z]$/D', $module)) {
			$error = "$label has an invalid reflector, host, port, or module.";
			return [];
		}
		$key = $reflector . '|' . $module;
		if(isset($seen[$key])) {
			$error = "$label repeats $reflector module $module.";
			return [];
		}
		$seen[$key] = true;
		$result[] = ['reflector' => $reflector, 'host' => $host, 'port' => (int)$port, 'module' => $module, 'encrypted' => false];
	}
	return $result;
}

function asrSettingsM17DestinationsText($destinations) {
	$lines = [];
	foreach(is_array($destinations) ? $destinations : [] as $target) {
		if(!is_array($target)) continue;
		$lines[] = implode(' | ', [
			(string)($target['reflector'] ?? ''),
			(string)($target['host'] ?? ''),
			(string)($target['port'] ?? ''),
			(string)($target['module'] ?? ''),
		]);
	}
	return implode("\n", $lines);
}

function asrSettingsParseCustomYsfReflectors($value, &$error, $bridgeId) {
	$value = str_replace(["\r\n", "\r"], "\n", (string)$value);
	$lines = array_values(array_filter(array_map('trim', explode("\n", $value)), function($line) {
		return $line !== '';
	}));
	if(count($lines) > ASR_MAX_CUSTOM_YSF_REFLECTORS) {
		$error = "YSF Net Bridge \"$bridgeId\" supports at most " . ASR_MAX_CUSTOM_YSF_REFLECTORS . ' custom reflectors.';
		return [];
	}
	$reflectors = [];
	$seenIds = [];
	$seenNames = [];
	foreach($lines as $index => $line) {
		$parts = array_map('trim', explode('|', $line));
		if(count($parts) < 4 || count($parts) > 5) {
			$error = 'Each custom YSF reflector must use: NAME | 5-DIGIT ID | HOSTNAME OR IP | PORT | OPTIONAL DESCRIPTION.';
			return [];
		}
		$name = strtoupper(preg_replace('/\s+/', ' ', asrSettingsCleanText($parts[0], 16)));
		$id = asrSettingsCleanText($parts[1], 5);
		$host = asrSettingsCleanText($parts[2], 253);
		$portText = asrSettingsCleanText($parts[3], 5);
		$description = asrSettingsCleanText($parts[4] ?? 'Custom ASR reflector', 120);
		$validIp = filter_var($host, FILTER_VALIDATE_IP) !== false;
		$validHost = preg_match('/(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/D', $host) === 1;
		$port = ctype_digit($portText) ? (int)$portText : 0;
		if(!preg_match('/^[A-Z0-9][A-Z0-9 _.-]{0,15}$/D', $name) || preg_match('/^[0-9]{5}$/D', $name)) {
			$error = "Custom YSF reflector line " . ($index + 1) . ' has an invalid name.';
			return [];
		}
		if(!preg_match('/^[0-9]{5}$/D', $id) || $id === '00000') {
			$error = "Custom YSF reflector \"$name\" needs a five-digit ID other than 00000.";
			return [];
		}
		if((!$validIp && !$validHost) || preg_match('/[;\x00-\x20\/\\\\\[\]@]/', $host)) {
			$error = "Custom YSF reflector \"$name\" has an invalid hostname or IP address.";
			return [];
		}
		if($port < 1 || $port > 65535) {
			$error = "Custom YSF reflector \"$name\" needs a port from 1 through 65535.";
			return [];
		}
		if($description === '' || strpos($description, ';') !== false) {
			$error = "Custom YSF reflector \"$name\" has an invalid description.";
			return [];
		}
		$nameKey = strtolower($name);
		if(isset($seenIds[$id]) || isset($seenNames[$nameKey])) {
			$error = 'Custom YSF reflector names and IDs must be unique within each bridge.';
			return [];
		}
		$seenIds[$id] = true;
		$seenNames[$nameKey] = true;
		$reflectors[] = [
			'id' => $id,
			'name' => $name,
			'host' => $host,
			'port' => $port,
			'description' => $description,
		];
	}
	return $reflectors;
}

function asrSettingsCustomYsfReflectorsText($reflectors) {
	$lines = [];
	foreach(is_array($reflectors) ? $reflectors : [] as $reflector) {
		if(!is_array($reflector)) continue;
		$lines[] = implode(' | ', [
			(string)($reflector['name'] ?? ''),
			(string)($reflector['id'] ?? ''),
			(string)($reflector['host'] ?? ''),
			(string)($reflector['port'] ?? ''),
			(string)($reflector['description'] ?? 'Custom ASR reflector'),
		]);
	}
	return implode("\n", $lines);
}

function asrSettingsDefaultBridgeTitle($id) {
	switch($id) {
		case 'dmr': return 'DMR Bridge';
		case 'dmr_net': return 'DMR Net Bridge';
		case 'ysf': return 'YSF Bridge';
		case 'ysf_net': return 'YSF Net Bridge';
		case 'dstar': return 'D-Star Bridge';
		case 'zello': return 'Zello Bridge';
		case 'p25': return 'P25 Bridge';
		case 'm17': return 'M17 Bridge';
		case 'nxdn': return 'NXDN Bridge';
	}
	return strtoupper(substr($id, 0, 1)) . substr($id, 1) . ' Bridge';
}

function asrSettingsDefaultModeTitle($mode, $cardType) {
	$label = strtoupper($mode);
	if($mode === 'dstar') $label = 'D-Star';
	if($mode === 'zello') $label = 'Zello';
	return $label . ($cardType === 'standard' ? ' Bridge' : ' Net Bridge');
}

function asrSettingsDefaultDetailTitle($mode) {
	if($mode === 'dstar') return 'Bridge Status';
	if($mode === 'zello') return 'Recent Talkers';
	if($mode === 'p25' || $mode === 'nxdn' || $mode === 'm17') return 'Linked Clients';
	return 'Connected Clients';
}

function asrSettingsInstanceSlug($id) {
	$slug = strtolower(preg_replace('/[^a-z0-9]+/', '_', (string)$id));
	return trim($slug, '_');
}

function asrSettingsDerivedDigitalResources($mode, $id) {
	$instance = asrSettingsInstanceSlug($id);
	if($mode === 'p25' || $mode === 'nxdn') {
		$prefix = $mode === 'p25' ? 'P25' : 'NXDN';
		$service = strtolower($prefix) . 'gateway-' . $instance . '.service';
		return [
			'instance' => $instance,
			'gatewayConfig' => '/opt/' . $prefix . 'Gateway_' . $instance . '/' . $prefix . 'Gateway.ini',
			'gatewayService' => $service,
			'mmdvmService' => 'mmdvm_bridge_' . $instance . '.service',
			'analogBridgeService' => 'analog_bridge_' . $instance . '.service',
			'emulatorService' => $mode === 'nxdn' ? 'md380-emu-' . $instance . '.service' : '',
			'mqttName' => $mode . '_gateway_' . $instance,
			'mmdvmMqttName' => $mode . '_mmdvm_' . $instance,
		];
	}
	return ['instance' => $instance];
}

function asrSettingsDerivedM17Ports($id) {
	$base = 17100 + ((int)sprintf('%u', crc32((string)$id)) % 190) * 10;
	return ['bind' => $base, 'usrpRx' => $base + 1, 'usrpTx' => $base + 2];
}

function asrSettingsApprovedDmrTalkgroups($value, &$error, $label) {
	$tokens = preg_split('/[\s,]+/', trim((string)$value), -1, PREG_SPLIT_NO_EMPTY);
	$tokens = is_array($tokens) ? array_values(array_unique($tokens)) : [];
	if(count($tokens) > ASR_MAX_APPROVED_DESTINATIONS) {
		$error = "$label supports at most " . ASR_MAX_APPROVED_DESTINATIONS . ' approved talkgroups.';
		return [];
	}
	foreach($tokens as $token) {
		if(!preg_match('/^[0-9]{1,8}$/D', $token) || (int)$token < 1 || (int)$token > 16777215 || (int)$token === 4000) {
			$error = "$label approved talkgroups must be 1-16777215 and cannot use disconnect TG 4000.";
			return [];
		}
	}
	return $tokens;
}

function asrSettingsApprovedYsfTargets($value, &$error, $label) {
	$lines = preg_split('/\R/', trim((string)$value), -1, PREG_SPLIT_NO_EMPTY);
	$lines = is_array($lines) ? array_values(array_unique(array_map('trim', $lines))) : [];
	if(count($lines) > ASR_MAX_APPROVED_DESTINATIONS) {
		$error = "$label supports at most " . ASR_MAX_APPROVED_DESTINATIONS . ' approved reflectors.';
		return [];
	}
	foreach($lines as $line) {
		if(!preg_match('/^(?:[0-9]{5}|[A-Za-z0-9][A-Za-z0-9 _.-]{0,79})$/D', $line) || $line === '00000') {
			$error = "$label approved reflectors must be exact reflector names or five-digit IDs, one per line.";
			return [];
		}
	}
	return $lines;
}

function asrSettingsClientPayloadHasSupportedShape($payload) {
	if(!is_array($payload)) return false;
	if(array_is_list($payload)) return true;
	foreach($payload as $value) {
		if(is_array($value)) return true;
	}
	return false;
}

function asrSettingsBridgeRowsFromPost(&$error, $existingBridges = [], $localNode = '') {
	$ids = $_POST['bridgeId'] ?? [];
	$modes = $_POST['bridgeMode'] ?? [];
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
	$ysfGatewayConfigs = $_POST['bridgeYsfGatewayConfig'] ?? [];
	$mmdvmConfigs = $_POST['bridgeMmdvmConfig'] ?? [];
	$ysfGatewayServices = $_POST['bridgeYsfGatewayService'] ?? [];
	$mmdvmServices = $_POST['bridgeMmdvmService'] ?? [];
	$analogBridgeServices = $_POST['bridgeAnalogBridgeService'] ?? [];
	$emulatorServices = $_POST['bridgeEmulatorService'] ?? [];
	$ysfHostsPaths = $_POST['bridgeYsfHostsPath'] ?? [];
	$ysfCustomReflectorTexts = $_POST['bridgeYsfCustomReflectors'] ?? [];
	$allowTuneValues = $_POST['bridgeAllowTune'] ?? [];
	$fixedRecoveryValues = $_POST['bridgeFixedRecovery'] ?? [];
	$permissionValues = $_POST['bridgePermission'] ?? [];
	$backendModeValues = $_POST['bridgeBackendMode'] ?? [];
	$instanceValues = $_POST['bridgeInstance'] ?? [];
	$gatewayConfigValues = $_POST['bridgeGatewayConfig'] ?? [];
	$gatewayServiceValues = $_POST['bridgeGatewayService'] ?? [];
	$digitalMmdvmServiceValues = $_POST['bridgeDigitalMmdvmService'] ?? [];
	$digitalAnalogServiceValues = $_POST['bridgeDigitalAnalogService'] ?? [];
	$digitalEmulatorServiceValues = $_POST['bridgeDigitalEmulatorService'] ?? [];
	$mqttNameValues = $_POST['bridgeMqttName'] ?? [];
	$mmdvmMqttNameValues = $_POST['bridgeMmdvmMqttName'] ?? [];
	$fixedDestinationValues = $_POST['bridgeFixedDestination'] ?? [];
	$approvedDestinationValues = $_POST['bridgeApprovedDestinations'] ?? [];
	$m17CallsignValues = $_POST['bridgeM17Callsign'] ?? [];
	$m17BindPortValues = $_POST['bridgeM17BindPort'] ?? [];
	$m17UsrpRxPortValues = $_POST['bridgeM17UsrpRxPort'] ?? [];
	$m17UsrpTxPortValues = $_POST['bridgeM17UsrpTxPort'] ?? [];
	$m17ReflectorValues = $_POST['bridgeM17Reflector'] ?? [];
	$m17HostValues = $_POST['bridgeM17Host'] ?? [];
	$m17PortValues = $_POST['bridgeM17Port'] ?? [];
	$m17ModuleValues = $_POST['bridgeM17Module'] ?? [];
	$passwords = $_POST['bridgeClientPassword'] ?? [];
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
	$count = max(count($ids), count($modes), count($nodes), count($titles), count($details), count($friendlyNames), count($clientSources), count($clientUrls), count($clientUsernames), count($cardTypes), count($abinfoPaths), count($dvswitchScripts), count($analogConfigs), count($ysfGatewayConfigs), count($mmdvmConfigs), count($ysfGatewayServices), count($mmdvmServices), count($analogBridgeServices), count($emulatorServices), count($ysfHostsPaths), count($ysfCustomReflectorTexts), count($allowTuneValues), count($fixedRecoveryValues), count($permissionValues), count($backendModeValues), count($instanceValues), count($gatewayConfigValues), count($gatewayServiceValues), count($digitalMmdvmServiceValues), count($digitalAnalogServiceValues), count($digitalEmulatorServiceValues), count($mqttNameValues), count($mmdvmMqttNameValues), count($fixedDestinationValues), count($approvedDestinationValues), count($m17CallsignValues), count($m17BindPortValues), count($m17UsrpRxPortValues), count($m17UsrpTxPortValues), count($m17ReflectorValues), count($m17HostValues), count($m17PortValues), count($m17ModuleValues), count($passwords));
	if($count > ASR_MAX_BRIDGES) {
		$error = 'A maximum of ' . ASR_MAX_BRIDGES . ' bridge cards is supported. Remove extra bridge cards before saving. No settings were saved.';
		return [];
	}

	for($i = 0; $i < $count; $i++) {
		$rawId = asrSettingsCleanText($ids[$i] ?? '', 32);
		$rawMode = strtolower(asrSettingsCleanText($modes[$i] ?? '', 12));
		$rawNode = asrSettingsCleanText($nodes[$i] ?? '', 10);
		$rawTitle = asrSettingsCleanText($titles[$i] ?? '', 80);
		$rawDetail = asrSettingsCleanText($details[$i] ?? '', 80);
		$rawFriendlyName = asrSettingsCleanText($friendlyNames[$i] ?? '', 80);
		$rawClientSource = asrSettingsCleanText($clientSources[$i] ?? 'auto', 20);
		$rawClientUrl = asrSettingsCleanText($clientUrls[$i] ?? '', 220);
		$rawClientUsername = asrSettingsCleanText($clientUsernames[$i] ?? '', 80);
		$rawCardType = asrSettingsCleanText($cardTypes[$i] ?? 'standard', 20);
		$rawAbinfoPath = asrSettingsCleanText($abinfoPaths[$i] ?? '', 180);
		$rawDvswitchScript = asrSettingsCleanText($dvswitchScripts[$i] ?? '', 220);
		$rawAnalogConfig = asrSettingsCleanText($analogConfigs[$i] ?? '', 220);
		$rawYsfGatewayConfig = asrSettingsCleanText($ysfGatewayConfigs[$i] ?? '', 220);
		$rawMmdvmConfig = asrSettingsCleanText($mmdvmConfigs[$i] ?? '', 220);
		$rawYsfGatewayService = asrSettingsCleanText($ysfGatewayServices[$i] ?? '', 80);
		$rawMmdvmService = asrSettingsCleanText($mmdvmServices[$i] ?? '', 80);
		$rawAnalogBridgeService = asrSettingsCleanText($analogBridgeServices[$i] ?? '', 80);
		$rawEmulatorService = asrSettingsCleanText($emulatorServices[$i] ?? '', 80);
		$rawYsfHostsPath = asrSettingsCleanText($ysfHostsPaths[$i] ?? '', 220);
		$rawYsfCustomReflectors = (string)($ysfCustomReflectorTexts[$i] ?? '');
		$rawAllowTune = asrSettingsCleanText($allowTuneValues[$i] ?? '0', 4);
		$rawFixedRecovery = asrSettingsCleanText($fixedRecoveryValues[$i] ?? '0', 4);
		$rawPermission = asrSettingsCleanText($permissionValues[$i] ?? '', 20);
		$rawBackendMode = asrSettingsCleanText($backendModeValues[$i] ?? '', 20);
		$rawInstance = asrSettingsCleanText($instanceValues[$i] ?? '', 40);
		$rawGatewayConfig = asrSettingsCleanText($gatewayConfigValues[$i] ?? '', 220);
		$rawGatewayService = asrSettingsCleanText($gatewayServiceValues[$i] ?? '', 80);
		$rawDigitalMmdvmService = asrSettingsCleanText($digitalMmdvmServiceValues[$i] ?? '', 80);
		$rawDigitalAnalogService = asrSettingsCleanText($digitalAnalogServiceValues[$i] ?? '', 80);
		$rawDigitalEmulatorService = asrSettingsCleanText($digitalEmulatorServiceValues[$i] ?? '', 80);
		$rawMqttName = asrSettingsCleanText($mqttNameValues[$i] ?? '', 80);
		$rawMmdvmMqttName = asrSettingsCleanText($mmdvmMqttNameValues[$i] ?? '', 80);
		$rawFixedDestination = asrSettingsCleanText($fixedDestinationValues[$i] ?? '', 12);
		$rawApprovedDestinations = (string)($approvedDestinationValues[$i] ?? '');
		$rawM17Callsign = strtoupper(asrSettingsCleanText($m17CallsignValues[$i] ?? '', 9));
		$rawM17BindPort = asrSettingsCleanText($m17BindPortValues[$i] ?? '', 5);
		$rawM17UsrpRxPort = asrSettingsCleanText($m17UsrpRxPortValues[$i] ?? '', 5);
		$rawM17UsrpTxPort = asrSettingsCleanText($m17UsrpTxPortValues[$i] ?? '', 5);
		$rawM17Reflector = strtoupper(asrSettingsCleanText($m17ReflectorValues[$i] ?? '', 7));
		$rawM17Host = asrSettingsCleanText($m17HostValues[$i] ?? '', 253);
		$rawM17Port = asrSettingsCleanText($m17PortValues[$i] ?? '', 5);
		$rawM17Module = strtoupper(asrSettingsCleanText($m17ModuleValues[$i] ?? '', 1));
		if($rawMode === '' && $rawId !== '') $rawMode = asrSettingsBridgeMode(['id' => $rawId]);
		if($rawId === '' && $rawNode === '' && $rawTitle === '' && $rawDetail === '' && $rawFriendlyName === '' && $rawClientUrl === '' && $rawClientUsername === '')
			continue;

		if(!in_array($rawMode, asrSettingsSupportedBridgeModes(), true)) {
			$error = 'Choose a supported Digital Mode: DMR, YSF, D-Star, Zello, P25, NXDN, or M17.';
			return [];
		}
		if(!in_array($rawCardType, ['standard', 'net', 'dmr_net', 'ysf_net', 'p25_net', 'nxdn_net', 'm17_net'], true))
			$rawCardType = 'standard';
		$rawCardType = $rawCardType === 'standard' ? 'standard' : $rawMode . '_net';
		if(in_array($rawMode, ['dstar', 'zello'], true) && $rawCardType !== 'standard') {
			$error = ($rawMode === 'dstar' ? 'D-Star' : 'Zello') . ' supports a Standard Bridge card only.';
			return [];
		}
		$id = asrSettingsCleanBridgeId($rawId);
		if($id !== '' && asrSettingsBridgeMode(['id' => $id]) !== $rawMode) $id = '';
		if($id === $rawMode && $rawCardType !== 'standard') $id = '';
		if($id === '') {
			$reservedIds = $seen + array_fill_keys(array_keys($existingById), true);
			$id = asrSettingsNewBridgeId($rawMode, $rawCardType, $reservedIds);
		}
		if($id === '') {
			$error = 'ASR could not create a unique internal ID for this bridge card.';
			return [];
		}
		if(isset($seen[$id])) {
			$error = "Internal bridge ID \"$id\" is listed more than once.";
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
		if($rawClientSource === 'disabled') $rawClientSource = 'auto';
		if(!in_array($rawClientSource, ['auto', 'local_json', 'http_api'], true))
			$rawClientSource = 'auto';
		if($rawClientSource === 'local_json') {
			if($rawClientUrl === '' || $rawClientUrl[0] !== '/' || strpos($rawClientUrl, '..') !== false) {
				$error = "Bridge \"$id\" needs an absolute local JSON path without parent-directory traversal.";
				return [];
			}
			if(!is_file($rawClientUrl) || !is_readable($rawClientUrl)) {
				$error = "Bridge \"$id\" custom JSON source is not a readable regular file.";
				return [];
			}
			$sourcePayload = json_decode((string)file_get_contents($rawClientUrl), true);
			if(!asrSettingsClientPayloadHasSupportedShape($sourcePayload)) {
				$error = "Bridge \"$id\" custom JSON source must contain a client list or an object containing client lists.";
				return [];
			}
			$sourceMtime = (int)@filemtime($rawClientUrl);
			if($sourceMtime <= 0 || time() - $sourceMtime > 300) {
				$error = "Bridge \"$id\" custom JSON source is stale. Confirm its collector is updating before saving.";
				return [];
			}
		} elseif($rawClientSource === 'http_api') {
			$parts = parse_url($rawClientUrl);
			if(!is_array($parts) || !in_array(strtolower((string)($parts['scheme'] ?? '')), ['http', 'https'], true) || empty($parts['host'])) {
				$error = "Bridge \"$id\" needs a complete HTTP or HTTPS client-status URL.";
				return [];
			}
		} else {
			$rawClientUrl = '';
			$rawClientUsername = '';
		}

		$isNextDigitalMode = in_array($rawMode, ['p25', 'nxdn', 'm17'], true);
		$existingForMode = is_array($existingById[$id] ?? null) ? $existingById[$id] : [];
		if($rawCardType !== 'standard' && $rawBackendMode === '') $rawBackendMode = 'managed';
		if($rawBackendMode === '' && $rawCardType === 'standard' && $isNextDigitalMode) {
			$rawBackendMode = in_array((string)($existingForMode['backendMode'] ?? ''), ['display_only', 'managed'], true)
				? (string)$existingForMode['backendMode']
				: ((isset($existingForMode['bridgePermission']) || isset($existingForMode['instance']) || isset($existingForMode['m17Callsign'])) ? 'managed' : 'display_only');
		}
		if(!$isNextDigitalMode) $rawBackendMode = 'managed';
		if(!in_array($rawBackendMode, ['display_only', 'managed'], true)) {
			$error = "Choose Display only or Managed backend for " . asrSettingsDefaultModeTitle($rawMode, $rawCardType) . '.';
			return [];
		}

		$derived = asrSettingsDerivedDigitalResources($rawMode, $id);
		if(in_array($rawMode, ['p25', 'nxdn'], true) && $rawBackendMode === 'managed') {
			if($rawInstance === '') $rawInstance = (string)$derived['instance'];
			if($rawGatewayConfig === '') $rawGatewayConfig = (string)$derived['gatewayConfig'];
			if($rawGatewayService === '') $rawGatewayService = (string)$derived['gatewayService'];
			if($rawDigitalMmdvmService === '') $rawDigitalMmdvmService = (string)$derived['mmdvmService'];
			if($rawDigitalAnalogService === '') $rawDigitalAnalogService = (string)$derived['analogBridgeService'];
			if($rawMode === 'nxdn' && $rawDigitalEmulatorService === '') $rawDigitalEmulatorService = (string)$derived['emulatorService'];
			if($rawMode === 'p25') $rawDigitalEmulatorService = '';
			if($rawMqttName === '') $rawMqttName = (string)$derived['mqttName'];
			if($rawMmdvmMqttName === '') $rawMmdvmMqttName = (string)$derived['mmdvmMqttName'];
		}
		if($rawMode === 'm17' && $rawBackendMode === 'managed') {
			$ports = asrSettingsDerivedM17Ports($id);
			if($rawM17BindPort === '') $rawM17BindPort = (string)$ports['bind'];
			if($rawM17UsrpRxPort === '') $rawM17UsrpRxPort = (string)$ports['usrpRx'];
			if($rawM17UsrpTxPort === '') $rawM17UsrpTxPort = (string)$ports['usrpTx'];
		}
		$approvedDestinations = [];
		if($rawCardType === 'dmr_net') {
			if(!in_array($rawPermission, ['self_owned', 'approved'], true)) {
				$error = "DMR Net Bridge \"$id\" requires confirmed permission.";
				return [];
			}
			$approvedDestinations = asrSettingsApprovedDmrTalkgroups($rawApprovedDestinations, $error, "DMR Net Bridge \"$id\"");
			if($error !== '') return [];
			if(!preg_match('#^/tmp/ABInfo_[0-9]{2,5}\.json$#D', $rawAbinfoPath)) {
				$error = "DMR Net Bridge \"$id\" needs an ABInfo path such as /tmp/ABInfo_12345.json.";
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
		if($rawCardType === 'ysf_net') {
			if(!in_array($rawPermission, ['self_owned', 'approved'], true)) {
				$error = "YSF Net Bridge \"$id\" requires confirmed permission.";
				return [];
			}
			$approvedDestinations = asrSettingsApprovedYsfTargets($rawApprovedDestinations, $error, "YSF Net Bridge \"$id\"");
			if($error !== '') return [];
			$customYsfReflectors = asrSettingsParseCustomYsfReflectors($rawYsfCustomReflectors, $error, $id);
			if($error !== '') return [];
			if(!preg_match('#^/opt/YSFGateway_([A-Za-z0-9_-]+)/YSFGateway\.ini$#D', $rawYsfGatewayConfig, $gatewayMatch)) {
				$error = "YSF Net Bridge \"$id\" needs its dedicated /opt/YSFGateway_.../YSFGateway.ini path.";
				return [];
			}
			if(!preg_match('#^/opt/MMDVM_Bridge_([A-Za-z0-9_-]+)/MMDVM_Bridge\.ini$#D', $rawMmdvmConfig, $mmdvmMatch)) {
				$error = "YSF Net Bridge \"$id\" needs its dedicated /opt/MMDVM_Bridge_.../MMDVM_Bridge.ini path.";
				return [];
			}
			if(strcasecmp($gatewayMatch[1], $mmdvmMatch[1]) !== 0) {
				$error = "YSF Net Bridge \"$id\" Gateway and MMDVM instance names must match.";
				return [];
			}
			foreach([
				'YSF Gateway' => $rawYsfGatewayService,
				'MMDVM Bridge' => $rawMmdvmService,
			] as $serviceLabel => $serviceName) {
				if(!preg_match('/^[a-z0-9][a-z0-9@_.-]{0,79}\.service$/D', $serviceName)) {
					$error = "YSF Net Bridge \"$id\" needs a valid $serviceLabel service name.";
					return [];
				}
			}
			foreach([$rawAnalogBridgeService, $rawEmulatorService] as $optionalService) {
				if($optionalService !== '' && !preg_match('/^[a-z0-9][a-z0-9@_.-]{0,79}\.service$/D', $optionalService)) {
					$error = "YSF Net Bridge \"$id\" has an invalid optional service name.";
					return [];
				}
			}
			if($rawYsfHostsPath !== '' && !preg_match('#^/var/lib/mmdvm/[A-Za-z0-9_.-]*YSF[A-Za-z0-9_.-]*Hosts[A-Za-z0-9_.-]*$#D', $rawYsfHostsPath)) {
				$error = "YSF Net Bridge \"$id\" has an invalid YSF hosts path.";
				return [];
			}
			if(!empty($customYsfReflectors) && $rawYsfHostsPath === '') {
				$error = "YSF Net Bridge \"$id\" needs its updater-owned YSF Hosts Path before custom reflectors can be added.";
				return [];
			}
			foreach([
				$rawYsfGatewayConfig,
				$rawMmdvmConfig,
				$rawYsfGatewayService,
				$rawMmdvmService,
				$rawAnalogBridgeService,
				$rawEmulatorService,
			] as $controlResource) {
				if($controlResource === '') continue;
				$resourceKey = strtolower($controlResource);
				if(isset($seenControlPaths[$resourceKey])) {
					$error = "YSF path or service \"$controlResource\" is already used by bridge \"{$seenControlPaths[$resourceKey]}\".";
					return [];
				}
				$seenControlPaths[$resourceKey] = $id;
			}
		}
		$managedNewDigital = $isNextDigitalMode && $rawBackendMode === 'managed';
		if($managedNewDigital) {
			if(!in_array($rawPermission, ['self_owned', 'approved'], true)) {
				$error = asrSettingsDefaultModeTitle($rawMode, $rawCardType) . ' requires confirmed permission: Self-owned target or Target owner approved.';
				return [];
			}
			if($rawMode === 'm17') {
				$approvedDestinations = asrSettingsParseM17Destinations($rawApprovedDestinations, $error, "M17 bridge \"$id\"");
				if($error !== '') return [];
				foreach([$rawM17BindPort, $rawM17UsrpRxPort, $rawM17UsrpTxPort] as $port) {
					if(!preg_match('/^[0-9]{1,5}$/D', $port) || (int)$port < 1 || (int)$port > 65535) {
						$error = "M17 bridge \"$id\" needs valid, dedicated UDP ports.";
						return [];
					}
					$collisionKey = 'udp:' . $port;
					if(isset($seenControlPaths[$collisionKey])) {
						$error = "M17 bridge \"$id\" shares UDP port $port with bridge \"{$seenControlPaths[$collisionKey]}\".";
						return [];
					}
					$seenControlPaths[$collisionKey] = $id;
				}
				if(!preg_match('/^[A-Z0-9][A-Z0-9.\/-]{2,8}$/D', $rawM17Callsign)
					|| !preg_match('/[A-Z]/', $rawM17Callsign)
					|| !preg_match('/[0-9]/', $rawM17Callsign)) {
					$error = "M17 bridge \"$id\" needs a valid M17 callsign.";
					return [];
				}
				$callsignKey = 'm17-callsign:' . $rawM17Callsign;
				if(isset($seenControlPaths[$callsignKey])) {
					$error = "M17 callsign $rawM17Callsign is already used by bridge \"{$seenControlPaths[$callsignKey]}\".";
					return [];
				}
				$seenControlPaths[$callsignKey] = $id;
				if($rawCardType === 'm17_net' && empty($approvedDestinations)) {
					$error = "M17 Net Bridge \"$id\" needs at least one approved destination.";
					return [];
				}
				if($rawCardType === 'standard' && ($rawM17Reflector === '' || $rawM17Host === '' || $rawM17Port === '' || $rawM17Module === '')) {
					$error = "M17 Standard Bridge \"$id\" needs its approved fixed reflector, host, port, and module.";
					return [];
				}
				if($rawCardType === 'standard') {
					$fixedM17 = asrSettingsParseM17Destinations(
						$rawM17Reflector . ' | ' . $rawM17Host . ' | ' . $rawM17Port . ' | ' . $rawM17Module,
						$error,
						"M17 Standard Bridge \"$id\" fixed destination"
					);
					if($error !== '' || count($fixedM17) !== 1) return [];
					$rawM17Reflector = $fixedM17[0]['reflector'];
					$rawM17Host = $fixedM17[0]['host'];
					$rawM17Port = (string)$fixedM17[0]['port'];
					$rawM17Module = $fixedM17[0]['module'];
				}
			} else {
				if(!preg_match('/^[a-z0-9][a-z0-9_-]{0,39}$/D', $rawInstance)) {
					$error = strtoupper($rawMode) . " bridge \"$id\" needs a dedicated gateway instance name.";
					return [];
				}
				$modeDirectory = $rawMode === 'p25' ? 'P25Gateway_' : 'NXDNGateway_';
				$modeFile = $rawMode === 'p25' ? 'P25Gateway.ini' : 'NXDNGateway.ini';
				if($rawGatewayConfig !== '/opt/' . $modeDirectory . $rawInstance . '/' . $modeFile) {
					$error = strtoupper($rawMode) . " bridge \"$id\" needs its dedicated /opt/$modeDirectory.../$modeFile path.";
					return [];
				}
				$gatewayPrefix = $rawMode === 'p25' ? 'p25gateway' : 'nxdngateway';
				$allowedGatewayServices = [
					$gatewayPrefix . '-' . $rawInstance . '.service',
					$gatewayPrefix . '_' . $rawInstance . '.service',
					$gatewayPrefix . '@' . $rawInstance . '.service',
				];
				if(!in_array($rawGatewayService, $allowedGatewayServices, true)) {
					$error = strtoupper($rawMode) . " bridge \"$id\" Gateway service must match its dedicated instance.";
					return [];
				}
				foreach([$rawDigitalMmdvmService, $rawDigitalAnalogService] as $serviceName) {
					if(!preg_match('/^[a-z0-9][a-z0-9@_.-]{0,79}\.service$/D', $serviceName)) {
						$error = strtoupper($rawMode) . " bridge \"$id\" needs valid dedicated service names.";
						return [];
					}
				}
				if($rawDigitalEmulatorService !== '' && !preg_match('/^[a-z0-9][a-z0-9@_.-]{0,79}\.service$/D', $rawDigitalEmulatorService)) {
					$error = strtoupper($rawMode) . " bridge \"$id\" has an invalid emulator service name.";
					return [];
				}
				if(!preg_match('/^[a-z0-9][a-z0-9_.-]{0,79}$/D', $rawMqttName)) {
					$error = strtoupper($rawMode) . " bridge \"$id\" needs a unique local MQTT name.";
					return [];
				}
				if(!preg_match('/^[a-z0-9][a-z0-9_.-]{0,79}$/D', $rawMmdvmMqttName)
					|| $rawMmdvmMqttName === $rawMqttName) {
					$error = strtoupper($rawMode) . " bridge \"$id\" needs a separate, unique MMDVM activity MQTT name.";
					return [];
				}
				foreach([$rawGatewayConfig, $rawGatewayService, $rawDigitalMmdvmService, $rawDigitalAnalogService, $rawDigitalEmulatorService, 'mqtt:' . $rawMqttName, 'mqtt:' . $rawMmdvmMqttName] as $resource) {
					if($resource === '') continue;
					$resourceKey = strtolower($resource);
					if(isset($seenControlPaths[$resourceKey])) {
						$error = strtoupper($rawMode) . " bridge \"$id\" shares a path, service, or MQTT name with bridge \"{$seenControlPaths[$resourceKey]}\".";
						return [];
					}
					$seenControlPaths[$resourceKey] = $id;
				}
				$approvedDestinations = asrSettingsApprovedDesignators($rawApprovedDestinations, $error, strtoupper($rawMode) . " bridge \"$id\"", $rawMode);
				if($error !== '') return [];
				if($rawCardType === 'standard' && !asrSettingsDesignatorIsAllowed($rawFixedDestination, $rawMode)) {
					$error = strtoupper($rawMode) . " Standard Bridge \"$id\" needs an approved fixed destination.";
					return [];
				}
				if($rawCardType !== 'standard' && empty($approvedDestinations)) {
					$error = strtoupper($rawMode) . " Net Bridge \"$id\" needs at least one approved destination.";
					return [];
				}
			}
		}

		$seen[$id] = true;
		$seenNodes[$rawNode] = $id;
		$bridge = [
			'id' => $id,
			'mode' => $rawMode,
			'node' => $rawNode,
			'title' => $rawTitle !== '' ? $rawTitle : asrSettingsDefaultModeTitle($rawMode, $rawCardType),
			'detailTitle' => $rawCardType === 'standard'
				? ($rawDetail !== '' ? $rawDetail : asrSettingsDefaultDetailTitle($rawMode))
				: '',
			'friendlyName' => $rawFriendlyName,
			'clientSource' => $rawCardType === 'standard' ? $rawClientSource : 'auto',
			'clientUrl' => $rawCardType === 'standard' ? $rawClientUrl : '',
			'clientUsername' => $rawCardType === 'standard' ? $rawClientUsername : '',
			'cardType' => $rawCardType,
			'fixedBridgeRecovery' => $rawCardType === 'standard' && $rawFixedRecovery === '1',
			'backendMode' => $rawCardType === 'standard' && $isNextDigitalMode ? $rawBackendMode : 'managed',
			'abinfoPath' => $rawCardType === 'dmr_net' ? $rawAbinfoPath : '',
			'dvswitchScript' => $rawCardType === 'dmr_net' ? $rawDvswitchScript : '',
			'analogConfig' => $rawCardType === 'dmr_net' ? $rawAnalogConfig : '',
			'allowTune' => $rawCardType === 'ysf_net' && $rawAllowTune === '1',
			'ysfGatewayConfig' => $rawCardType === 'ysf_net' ? $rawYsfGatewayConfig : '',
			'mmdvmConfig' => $rawCardType === 'ysf_net' ? $rawMmdvmConfig : '',
			'ysfGatewayService' => $rawCardType === 'ysf_net' ? $rawYsfGatewayService : '',
			'mmdvmService' => $rawCardType === 'ysf_net' ? $rawMmdvmService : '',
			'analogBridgeService' => $rawCardType === 'ysf_net' ? $rawAnalogBridgeService : '',
			'emulatorService' => $rawCardType === 'ysf_net' ? $rawEmulatorService : '',
			'ysfHostsPath' => $rawCardType === 'ysf_net' ? $rawYsfHostsPath : '',
			'ysfCustomReflectors' => $rawCardType === 'ysf_net' ? $customYsfReflectors : [],
			'commandTransport' => $rawCardType === 'ysf_net' ? 'remote_command' : '',
		];
		if($rawCardType === 'dmr_net' || $rawCardType === 'ysf_net') {
			$bridge['bridgePermission'] = $rawPermission;
			$bridge['approvedDestinations'] = $approvedDestinations;
		}
		if($managedNewDigital && in_array($rawMode, ['p25', 'nxdn'], true)) {
			$bridge = array_merge($bridge, [
				'digitalMode' => $rawMode,
				'bridgeRole' => $rawCardType === 'standard' ? 'standard' : 'net',
				'instance' => $rawInstance,
				'gatewayConfig' => $rawGatewayConfig,
				'gatewayService' => $rawGatewayService,
				'mmdvmService' => $rawDigitalMmdvmService,
				'analogBridgeService' => $rawDigitalAnalogService,
				'emulatorService' => $rawMode === 'nxdn' ? $rawDigitalEmulatorService : '',
				'mqttHost' => '127.0.0.1',
				'mqttPort' => 1883,
				'mqttName' => $rawMqttName,
				'mmdvmMqttName' => $rawMmdvmMqttName,
				'bridgePermission' => $rawPermission,
				'fixedDestination' => $rawCardType === 'standard' ? $rawFixedDestination : '',
				'approvedDestinations' => $rawCardType === 'standard' ? [] : $approvedDestinations,
				'allowTune' => $rawCardType !== 'standard',
			]);
		}
		if($managedNewDigital && $rawMode === 'm17') {
			$bridge = array_merge($bridge, [
				'bridgePermission' => $rawPermission,
				'm17Callsign' => $rawM17Callsign,
				'm17BindAddress' => '127.0.0.1',
				'm17BindPort' => (int)$rawM17BindPort,
				'm17UsrpBindAddress' => '127.0.0.1',
				'm17UsrpRxPort' => (int)$rawM17UsrpRxPort,
				'm17UsrpRemoteAddress' => '127.0.0.1',
				'm17UsrpTxPort' => (int)$rawM17UsrpTxPort,
				'm17AudioQualified' => false,
				'm17QualificationState' => 'not_qualified',
				'm17Reflector' => $rawCardType === 'standard' ? $rawM17Reflector : '',
				'm17Host' => $rawCardType === 'standard' ? $rawM17Host : '',
				'm17Port' => $rawCardType === 'standard' ? (int)$rawM17Port : 0,
				'm17Module' => $rawCardType === 'standard' ? $rawM17Module : '',
				'm17Encrypted' => false,
				'approvedDestinations' => $rawCardType === 'm17_net' ? $approvedDestinations : [],
				'allowTune' => $rawCardType === 'm17_net',
			]);
		}
		if($rawCardType === 'dmr_net') {
			if($expectedLinkAlias === '' || $expectedLinkAlias === $rawNode) {
				$error = "DMR Net Bridge \"$id\" could not generate a safe internal link alias from the main node.";
				return [];
			}
			$bridge['linkAlias'] = $expectedLinkAlias;
		}
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
		$error = 'Could not encode Settings.';
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

function asrSettingsModernSection() {
	$allowed = ['account', 'home', 'appearance', 'bridges', 'lookup', 'access', 'system'];
	$section = strtolower(trim((string)($_GET['section'] ?? 'home')));
	if($section === 'integrations') $section = 'lookup';
	return in_array($section, $allowed, true) ? $section : 'home';
}

function asrSettingsModernHidden($category, $activeSection) {
	if(!defined('ASR_SETTINGS_MODERN_UI') || !ASR_SETTINGS_MODERN_UI)
		return '';
	return $category === $activeSection ? '' : ' hidden';
}

function asrSettingsModernNav($activeSection) {
	if(!defined('ASR_SETTINGS_MODERN_UI') || !ASR_SETTINGS_MODERN_UI)
		return;
	$items = [
		'home' => ['Settings Home', 'Overview and common tasks'],
		'appearance' => ['Appearance & Display', 'Branding, filters, and node display'],
		'bridges' => ['Bridges', 'Digital bridges, clients, and diagnostics'],
		'lookup' => ['Lookup & Map', 'QRZ lookup and map settings'],
		'access' => ['Access & Administration', 'Login policy and administrator tools'],
		'system' => ['System & Recovery', 'Updates, backups, and rollback'],
	];
	echo '<nav class="asr-settings-nav" aria-label="Settings categories">';
	echo '<div class="asr-settings-nav-heading"><strong>Settings</strong><span>Choose a task</span></div>';
	$accountClass = $activeSection === 'account' ? ' class="asr-settings-nav-account is-active" aria-current="page"' : ' class="asr-settings-nav-account"';
	echo '<a' . $accountClass . ' href="?section=account"><strong>My Account</strong><span>Profile, nodes, timezone, and password</span></a>';
	if(!adminUser()) {
		echo '</nav>';
		return;
	}
	foreach($items as $key => $item) {
		$class = $key === $activeSection ? ' class="is-active" aria-current="page"' : '';
		echo '<a' . $class . ' href="?section=' . asrSettingsH($key) . '"><strong>' . asrSettingsH($item[0]) . '</strong><span>' . asrSettingsH($item[1]) . '</span></a>';
	}
	echo '</nav>';
}

function asrSettingsRenderAccount($userModel, &$user) {
	global $timezoneDef;
	$updateSettings = 'Update Settings';
	$changePassword = 'Change Password';
	$post = arrayToObj($_POST, ['name', 'email', 'location', 'nodenums', 'permission', 'timezone_id', 'pass', 'confirm']);
	$post->nodenums = isset($post->nodenums) ? parseIntList($post->nodenums) : [];
	$post->permission = isset($post->permission) ? (int)$post->permission : userPermission();
	$submit = (string)($_POST['Submit'] ?? '');
	$accountMessage = '';
	$accountError = '';
	$passwordError = '';
	if($submit === $updateSettings) {
		if((int)$post->permission !== (int)userPermission()) {
			$accountError = 'Your permission level cannot be changed from My Account.';
		} elseif(!$userModel->validateFields($post)) {
			$accountError = (string)$userModel->error;
			unset($userModel->error);
		} else {
			$newUser = $user;
			$newUser->name = $post->name;
			$newUser->email = $post->email;
			$newUser->location = $post->location;
			$newUser->nodenums = $post->nodenums;
			$newUser->permission = $post->permission;
			$newUser->timezone_id = $post->timezone_id;
			if($userModel->update($newUser) === null) {
				$accountError = 'Account settings could not be saved: ' . (string)$userModel->error;
				unset($userModel->error);
			} else {
				$accountMessage = 'Account settings saved.';
				$user = $userModel->getUserById($user->user_id);
			}
		}
	} elseif($submit === $changePassword) {
		if(!$userModel->validatePassword($post->pass)) {
			$passwordError = (string)$userModel->error;
			unset($userModel->error);
		} elseif(!$userModel->changePassword($_POST)) {
			$passwordError = (string)$userModel->error;
		} else {
			$accountMessage = 'Password changed.';
		}
	}
	pageInit();
	h1('Settings');
	if($accountMessage !== '') okMsg($accountMessage);
	if(isset($userModel->error)) errMsg($userModel->error);
	$permissionName = $userModel->getPermissionName(userPermission());
	?>
	<div class="asr-settings-modern-shell" data-settings-category="account">
		<?php asrSettingsModernNav('account'); ?>
		<main class="asr-settings-modern-content">
			<header class="asr-settings-page-intro"><p class="asr-settings-eyebrow">Personal settings</p><h2>My Account</h2><p>Update your profile, managed nodes, time zone, or password.</p></header>
			<form id="editUserForm" class="asr-account-flat-form" method="post" action="?section=account">
				<?php if($accountError !== ''): ?><p class="error"><?php echo asrSettingsH($accountError); ?></p><?php endif; ?>
				<section><header><h3>Profile</h3><p>Your operator identity and optional contact details.</p></header><div class="asr-account-grid">
					<label><span>Name / Call Sign</span><input name="name" type="text" value="<?php echo asrSettingsH($user->name ?? ''); ?>" required></label>
					<label><span>Email</span><input name="email" type="email" value="<?php echo asrSettingsH($user->email ?? ''); ?>"></label>
					<label><span>Location</span><input name="location" type="text" value="<?php echo asrSettingsH($user->location ?? ''); ?>"></label>
				</div></section>
				<section><header><h3>Nodes &amp; Permissions</h3><p>Nodes you manage and your current access level.</p></header><div class="asr-account-grid">
					<label><span>Managed Node Numbers</span><input name="nodenums" type="text" value="<?php echo asrSettingsH(implode(' ', $user->nodenums ?? [])); ?>"><small>Separate multiple AllStar node numbers with spaces.</small></label>
					<label><span>Permission</span><input type="text" value="<?php echo asrSettingsH($permissionName); ?>" readonly><small>An authorized administrator changes permissions in Users.</small></label>
					<input name="permission" type="hidden" value="<?php echo (int)userPermission(); ?>">
				</div></section>
				<section><header><h3>Preferences</h3><p>Times throughout AllScan use this time zone.</p></header><label><span>Time Zone</span><select name="timezone_id">
				<?php foreach($timezoneDef as $timezoneId=>$timezoneLabel): ?><option value="<?php echo (int)$timezoneId; ?>"<?php echo (int)$timezoneId === (int)$user->timezone_id ? ' selected' : ''; ?>><?php echo asrSettingsH($timezoneLabel); ?></option><?php endforeach; ?>
				</select></label></section>
				<div class="asr-account-actions"><button type="submit" name="Submit" value="<?php echo asrSettingsH($updateSettings); ?>">Save Account Settings</button></div>
			</form>
			<form id="changePassForm" class="asr-account-flat-form" method="post" action="?section=account"><section><header><h3>Security</h3><p>Choose a 6–16 character password for this account.</p></header>
				<?php if($passwordError !== ''): ?><p class="error"><?php echo asrSettingsH($passwordError); ?></p><?php endif; ?>
				<div class="asr-account-grid"><label><span>New Password</span><input name="pass" type="password" autocomplete="new-password" required></label><label><span>Confirm New Password</span><input name="confirm" type="password" autocomplete="new-password" required></label></div></section>
				<div class="asr-account-actions"><button type="submit" name="Submit" value="<?php echo asrSettingsH($changePassword); ?>">Change Password</button></div>
			</form>
		</main>
	</div>
	<script src="<?php echo asrSettingsH(asrSettingsWebPath('js/asr-settings-modern.js')); ?>"></script>
	<?php
	asExit();
}

function asrSettingsRollbackCsrfToken($user) {
	$userId = isset($user->user_id) ? (string) $user->user_id : '';
	$loginSecret = (string) ($_COOKIE['cpass'] ?? '');
	if($userId === '' || $loginSecret === '')
		return '';
	return hash_hmac('sha256', 'asr-settings-rollback-v1|' . $userId, $loginSecret);
}

function asrSettingsSaveCsrfToken($user) {
	$userId = isset($user->user_id) ? (string) $user->user_id : '';
	$loginSecret = (string) ($_COOKIE['cpass'] ?? '');
	if($userId === '' || $loginSecret === '')
		return '';
	return hash_hmac('sha256', 'asr-settings-save-v1|' . $userId, $loginSecret);
}

function asrSettingsRollbackPostIsSameOrigin($requireSource = false) {
	$fetchSite = strtolower(trim((string) ($_SERVER['HTTP_SEC_FETCH_SITE'] ?? '')));
	if($fetchSite !== '' && !in_array($fetchSite, ['same-origin', 'none'], true))
		return false;

	$source = trim((string) ($_SERVER['HTTP_ORIGIN'] ?? ''));
	if($source === '')
		$source = trim((string) ($_SERVER['HTTP_REFERER'] ?? ''));
	if($source === '')
		return !$requireSource || $fetchSite === 'same-origin';

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
		$error = 'Rollback: Unavailable. This installation cannot run rollback commands.';
		return null;
	}
	if(!is_executable(ASR_ROLLBACK_HELPER)) {
		$error = 'Rollback: Unavailable. Install or repair the current release to restore browser rollback.';
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

function asrSettingsBridgeLifecyclePreviews(&$error) {
	$error = '';
	if(!function_exists('exec') || !is_executable(ASR_BRIDGE_LIFECYCLE_HELPER)) {
		$error = 'Bridge ownership information is unavailable. Saved bridge deletion is disabled.';
		return ['available' => false, 'bridges' => []];
	}
	$output = [];
	$status = 1;
	exec('sudo -n ' . escapeshellarg(ASR_BRIDGE_LIFECYCLE_HELPER) . ' preview-all 2>/dev/null', $output, $status);
	$json = implode("\n", $output);
	if(strlen($json) > 262144) {
		$error = 'Bridge ownership information was too large to display safely.';
		return ['available' => false, 'bridges' => []];
	}
	$data = json_decode($json, true);
	if($status !== 0 || !is_array($data) || empty($data['ok']) || !is_array($data['bridges'] ?? null)) {
		$error = 'Bridge ownership information is temporarily unavailable. ASR will not assume it owns an external bridge stack.';
		return ['available' => false, 'bridges' => []];
	}
	$result = [];
	foreach($data['bridges'] as $id => $preview) {
		$cleanId = asrSettingsCleanBridgeId($id);
		if($cleanId === '' || !is_array($preview) || empty($preview['owned']))
			continue;
		$creationId = strtolower((string)($preview['creationId'] ?? ''));
		$digest = strtolower((string)($preview['manifestDigest'] ?? ''));
		$token = strtolower((string)($preview['deletionToken'] ?? ''));
		if(!preg_match('/^[a-f0-9]{32}$/D', $creationId)
			|| !preg_match('/^[a-f0-9]{64}$/D', $digest)
			|| !preg_match('/^[a-f0-9]{64}$/D', $token)
			|| !is_array($preview['resources'] ?? null)
			|| !is_array($preview['willNotTouch'] ?? null)) {
			$error = 'Bridge ownership information was incomplete. Saved bridge deletion is disabled.';
			return ['available' => false, 'bridges' => []];
		}
		$resources = [];
		foreach(array_slice($preview['resources'], 0, 64) as $resource) {
			$clean = asrSettingsCleanText($resource, 140);
			if($clean !== '') $resources[] = $clean;
		}
		$willNotTouch = [];
		foreach(array_slice($preview['willNotTouch'], 0, 16) as $resource) {
			$clean = asrSettingsCleanText($resource, 140);
			if($clean !== '') $willNotTouch[] = $clean;
		}
		if(empty($resources) || empty($willNotTouch)) {
			$error = 'Bridge ownership information had no exact resource list. Saved bridge deletion is disabled.';
			return ['available' => false, 'bridges' => []];
		}
		$result[$cleanId] = [
			'bridgeId' => $cleanId,
			'creationId' => $creationId,
			'manifestDigest' => $digest,
			'deletionToken' => $token,
			'owned' => true,
			'resources' => $resources,
			'willNotTouch' => $willNotTouch,
		];
	}
	return ['available' => true, 'bridges' => $result];
}

function asrSettingsValidateDeletionPlan($existingBridges, $nextBridges, $rawConfirmations, $lifecycle, &$error) {
	$error = '';
	$existing = [];
	foreach((array)$existingBridges as $bridge) {
		$id = is_array($bridge) ? asrSettingsCleanBridgeId($bridge['id'] ?? '') : '';
		if($id !== '') $existing[$id] = true;
	}
	$remaining = [];
	foreach((array)$nextBridges as $bridge) {
		$id = is_array($bridge) ? asrSettingsCleanBridgeId($bridge['id'] ?? '') : '';
		if($id !== '') $remaining[$id] = true;
	}
	$missing = array_values(array_diff(array_keys($existing), array_keys($remaining)));
	sort($missing, SORT_STRING);
	if(!is_string($rawConfirmations) || strlen($rawConfirmations) > 65536) {
		$error = 'Bridge deletion confirmations were invalid.';
		return null;
	}
	$confirmations = json_decode($rawConfirmations === '' ? '[]' : $rawConfirmations, true);
	if(!is_array($confirmations) || count($confirmations) > ASR_MAX_BRIDGES) {
		$error = 'Bridge deletion confirmations were invalid.';
		return null;
	}
	$byId = [];
	foreach($confirmations as $confirmation) {
		if(!is_array($confirmation)) { $error = 'Bridge deletion confirmations were invalid.'; return null; }
		$id = asrSettingsCleanBridgeId($confirmation['bridgeId'] ?? '');
		if($id === '' || isset($byId[$id])) { $error = 'Bridge deletion confirmations were invalid.'; return null; }
		$byId[$id] = $confirmation;
	}
	$confirmed = array_keys($byId);
	sort($confirmed, SORT_STRING);
	if($confirmed !== $missing) {
		$error = 'Every removed saved bridge must have one exact deletion confirmation. Reload Settings and try again.';
		return null;
	}
	if(!empty($missing) && empty($lifecycle['available'])) {
		$error = 'Bridge ownership is unknown, so saved bridge deletion is disabled.';
		return null;
	}
	$previews = is_array($lifecycle['bridges'] ?? null) ? $lifecycle['bridges'] : [];
	$queue = [];
	foreach($missing as $id) {
		$confirmation = $byId[$id];
		if(isset($previews[$id])) {
			$preview = $previews[$id];
			$expected = [
				'bridgeId' => $id,
				'creationId' => $preview['creationId'],
				'manifestDigest' => $preview['manifestDigest'],
				'deletionToken' => $preview['deletionToken'],
			];
			foreach($expected as $key => $value) {
				if(!isset($confirmation[$key]) || !is_string($confirmation[$key]) || !hash_equals((string)$value, $confirmation[$key])) {
					$error = 'A managed bridge deletion confirmation is missing, forged, or stale.';
					return null;
				}
			}
			$queue[] = $expected;
		} elseif(!empty($confirmation['owned'])) {
			$error = 'An external bridge deletion was incorrectly marked as ASR-owned.';
			return null;
		}
	}
	return ['missingIds' => $missing, 'queue' => $queue];
}

function asrSettingsValidateOwnedBridgeMutations($existingBridges, $nextBridges, $postedIds, $lifecycle, &$error) {
	$error = '';
	$previews = is_array($lifecycle['bridges'] ?? null) ? $lifecycle['bridges'] : [];
	$existing = [];
	foreach((array)$existingBridges as $bridge) {
		$id = is_array($bridge) ? asrSettingsCleanBridgeId($bridge['id'] ?? '') : '';
		if($id !== '') $existing[$id] = $bridge;
	}
	foreach((array)$postedIds as $index => $postedId) {
		$id = asrSettingsCleanBridgeId($postedId);
		$mustLock = isset($previews[$id]) || empty($lifecycle['available']);
		if($id === '' || !$mustLock || !isset($existing[$id]) || !isset($nextBridges[$index])) continue;
		$before = $existing[$id];
		$after = $nextBridges[$index];
		$beforeRole = (($before['cardType'] ?? 'standard') === 'standard') ? 'standard' : 'net';
		$afterRole = (($after['cardType'] ?? 'standard') === 'standard') ? 'standard' : 'net';
		$beforeBackend = (string)($before['backendMode'] ?? 'managed');
		$afterBackend = (string)($after['backendMode'] ?? 'managed');
		if((string)($after['id'] ?? '') !== $id
			|| asrSettingsBridgeMode($before) !== asrSettingsBridgeMode($after)
			|| $beforeRole !== $afterRole
			|| $beforeBackend !== $afterBackend) {
			$error = 'A managed ASR-owned bridge cannot change Digital Mode, role, or backend in place. Delete and save it with the ownership confirmation, then add the replacement card.';
			return false;
		}
	}
	return true;
}

function asrSettingsQueueBridgeDeletion($request, &$error) {
	$error = '';
	if(!function_exists('proc_open') || !is_executable(ASR_BRIDGE_LIFECYCLE_HELPER)) {
		$error = 'Bridge deletion cannot be queued because the lifecycle service is unavailable.';
		return false;
	}
	$payload = json_encode($request, JSON_UNESCAPED_SLASHES);
	if(!is_string($payload) || strlen($payload) > 65536) { $error = 'Bridge deletion request was too large.'; return false; }
	$process = proc_open(
		['sudo', '-n', ASR_BRIDGE_LIFECYCLE_HELPER, 'queue-deletion'],
		[['pipe', 'r'], ['pipe', 'w'], ['pipe', 'w']], $pipes
	);
	if(!is_resource($process)) { $error = 'Bridge deletion could not start.'; return false; }
	fwrite($pipes[0], $payload);
	fclose($pipes[0]);
	$output = stream_get_contents($pipes[1]); fclose($pipes[1]);
	$detail = stream_get_contents($pipes[2]); fclose($pipes[2]);
	$status = proc_close($process);
	$data = is_string($output) && strlen($output) <= 262144 ? json_decode($output, true) : null;
	if($status !== 0 || !is_array($data) || empty($data['ok']) || empty($data['queued'])) {
		$error = 'ASR could not queue the exact bridge deletion intent.';
		if(is_array($data) && is_string($data['error'] ?? null)) $detail = $data['error'];
		$detail = asrSettingsCleanText($detail, 180);
		if($detail !== '') $error .= ' ' . $detail;
		return false;
	}
	return true;
}

function asrSettingsBridgeLifecycleFailureSummary() {
	if(!function_exists('exec') || !is_executable(ASR_BRIDGE_LIFECYCLE_HELPER))
		return '';
	$output = [];
	$status = 1;
	exec('sudo -n ' . escapeshellarg(ASR_BRIDGE_LIFECYCLE_HELPER) . ' status 2>/dev/null', $output, $status);
	$data = json_decode(implode("\n", $output), true);
	if(!is_array($data) || empty($data['pending']) || !is_array($data['results'] ?? null))
		return '';
	$remaining = [];
	foreach($data['results'] as $result) {
		if(!is_array($result) || !empty($result['ok'])) continue;
		foreach((array)($result['remaining'] ?? []) as $message) {
			$clean = asrSettingsCleanText($message, 180);
			if($clean !== '') $remaining[] = $clean;
			if(count($remaining) >= 5) break 2;
		}
	}
	return empty($remaining) ? '' : ' Deleted-bridge cleanup still needs attention: ' . implode(' ', $remaining);
}

function asrSettingsRunYsfCatalogHelper($operation, $bridgeId, $content, &$error) {
	$error = '';
	$bridgeId = asrSettingsCleanBridgeId($bridgeId);
	if($bridgeId === '') {
		$error = 'Select a saved YSF Net Bridge before importing a reflector list.';
		return null;
	}
	if(!is_executable(ASR_YSF_BRIDGE_HELPER)) {
		$error = 'YSF reflector-list management is not installed yet.';
		return null;
	}
	if($operation === 'status') {
		if(!function_exists('exec')) {
			$error = 'YSF reflector-list status is unavailable because command execution is disabled.';
			return null;
		}
		$output = [];
		$status = 1;
		$command = 'sudo -n ' . escapeshellarg(ASR_YSF_BRIDGE_HELPER)
			. ' --catalog-status ' . escapeshellarg($bridgeId) . ' 2>/dev/null';
		exec($command, $output, $status);
		$json = implode("\n", $output);
	} elseif($operation === 'import') {
		if(!function_exists('proc_open')) {
			$error = 'YSF reflector-list import is unavailable because command execution is disabled.';
			return null;
		}
		if(!is_string($content) || $content === '' || strlen($content) > ASR_MAX_YSF_HOSTS_UPLOAD_BYTES) {
			$error = 'Choose a non-empty YSFHosts.txt file no larger than 2 MB.';
			return null;
		}
		$descriptors = [
			0 => ['pipe', 'r'],
			1 => ['pipe', 'w'],
			2 => ['pipe', 'w'],
		];
		$command = 'sudo -n ' . escapeshellarg(ASR_YSF_BRIDGE_HELPER)
			. ' --import-hosts ' . escapeshellarg($bridgeId);
		$process = proc_open($command, $descriptors, $pipes);
		if(!is_resource($process)) {
			$error = 'The YSF reflector-list import service could not start.';
			return null;
		}
		$offset = 0;
		$contentLength = strlen($content);
		while($offset < $contentLength) {
			$written = @fwrite($pipes[0], substr($content, $offset));
			if($written === false || $written === 0)
				break;
			$offset += $written;
		}
		fclose($pipes[0]);
		$json = (string)stream_get_contents($pipes[1]);
		$stderr = (string)stream_get_contents($pipes[2]);
		fclose($pipes[1]);
		fclose($pipes[2]);
		$status = proc_close($process);
		if($offset !== $contentLength) {
			$error = 'The complete YSFHosts.txt file could not be sent to the import service.';
			return null;
		}
		if(strlen($stderr) > 65536)
			$stderr = substr($stderr, 0, 65536);
	} else {
		$error = 'Invalid YSF reflector-list request.';
		return null;
	}
	if(strlen($json) > 65536) {
		$error = 'The YSF reflector-list service returned too much data.';
		return null;
	}
	$data = json_decode($json, true);
	if($status !== 0 || !is_array($data) || empty($data['ok'])) {
		$error = 'The YSF reflector-list request failed.';
		if(is_array($data) && isset($data['error']) && is_string($data['error'])) {
			$detail = asrSettingsCleanText($data['error'], 220);
			if($detail !== '')
				$error .= ' ' . $detail;
		}
		return null;
	}
	return $data;
}

function asrSettingsReadYsfHostsUpload($bridgeId, &$error) {
	$error = '';
	$key = 'ysfHostsUpload_' . asrSettingsCleanBridgeId($bridgeId);
	$file = $_FILES[$key] ?? null;
	if(!is_array($file) || (int)($file['error'] ?? UPLOAD_ERR_NO_FILE) === UPLOAD_ERR_NO_FILE) {
		$error = 'Choose the downloaded YSFHosts.txt file before importing.';
		return null;
	}
	if((int)($file['error'] ?? UPLOAD_ERR_NO_FILE) !== UPLOAD_ERR_OK) {
		$error = 'The YSFHosts.txt upload failed.';
		return null;
	}
	$size = (int)($file['size'] ?? 0);
	if($size < 1 || $size > ASR_MAX_YSF_HOSTS_UPLOAD_BYTES) {
		$error = 'YSFHosts.txt must be non-empty and no larger than 2 MB.';
		return null;
	}
	$tmp = (string)($file['tmp_name'] ?? '');
	if($tmp === '' || !is_uploaded_file($tmp)) {
		$error = 'The uploaded YSFHosts.txt file could not be verified.';
		return null;
	}
	$content = file_get_contents($tmp);
	if(!is_string($content) || strlen($content) !== $size) {
		$error = 'The uploaded YSFHosts.txt file could not be read completely.';
		return null;
	}
	return $content;
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

function asrSettingsBridgeOrderControls($deleteDisabled = false) {
?>
	<div class="asr-bridge-panel-actions">
		<button class="asr-bridge-drag-handle" type="button" draggable="true" aria-label="Drag bridge to reorder" title="Drag bridge to reorder"><span aria-hidden="true">↕</span> Drag</button>
		<button class="asr-bridge-move-up" type="button" aria-label="Move bridge up" title="Move bridge up"><span aria-hidden="true">↑</span> Up</button>
		<button class="asr-bridge-move-down" type="button" aria-label="Move bridge down" title="Move bridge down"><span aria-hidden="true">↓</span> Down</button>
		<button class="asr-bridge-delete" type="button"<?php echo $deleteDisabled ? ' disabled title="Removal is disabled while bridge ownership is unknown."' : ''; ?>>Remove</button>
	</div>
<?php
}

function asrSettingsRenderTgifAccount($title = 'TGIF Account') {
	?>
	<div class="asr-dmr-tgif-settings">
		<div class="asr-bridge-section-copy">
			<strong><?php echo asrSettingsH($title); ?></strong>
			<span>Sign in with the same TGIF account and callsign used by this bridge. ASR uses the session to show current TGIF users; it does not change the bridge destination.</span>
		</div>
		<div class="asr-tgif-card-status" data-tgif-status aria-live="polite">Checking TGIF account status...</div>
		<details class="asr-progressive-details asr-tgif-configuration">
			<summary><span class="asr-disclosure-chevron">&gt;</span> Configure TGIF</summary>
			<div class="asr-bridge-fields-grid asr-tgif-card-fields">
				<label><span>Callsign <small class="asr-field-requirement">Required</small></span><input data-tgif-callsign type="text" autocomplete="username" autocapitalize="characters" maxlength="10" placeholder="KE7WIL"><small>Your TGIF account callsign.</small></label>
				<label><span>TGIF Password <small class="asr-field-requirement">Required to sign in</small></span><input data-tgif-password type="password" autocomplete="current-password" maxlength="128"><small>Sent only to TGIF for this sign-in and never saved by ASR.</small></label>
				<label><span>Session Talkgroup <small class="asr-field-requirement">Required</small></span><input data-tgif-talkgroup type="text" inputmode="numeric" pattern="[1-9][0-9]{0,7}" maxlength="8" placeholder="86753"><small>The TGIF talkgroup whose active sessions ASR should display.</small></label>
			</div>
			<div class="asr-tgif-captcha" data-tgif-captcha-wrap hidden>
				<img data-tgif-captcha-image alt="TGIF CAPTCHA" referrerpolicy="no-referrer">
				<label><span>CAPTCHA text</span><input data-tgif-captcha type="text" autocomplete="off" autocapitalize="none" maxlength="32" placeholder="all lower case"></label>
			</div>
			<div class="asr-tgif-card-actions">
				<button type="button" class="asr-primary-action" data-tgif-login>Sign In and Save TGIF Session</button>
				<button type="button" data-tgif-logout>Sign Out</button>
			</div>
		</details>
		<p class="asr-bridge-section-note">This account session is separate from the bridge card save. Root-only TGIF session cookies persist across node reboots until you sign out or TGIF expires them.</p>
	</div>
	<?php
}

function asrSettingsRenderUrfMode($mode, $label, $values, $enabled) {
	$summary = asrSettingsUrfModeSummary($mode, $values);
	$field = static function($mode, $key, $label, $value, $help, $requirement = 'Optional', $options = '') {
		echo '<label><span>' . asrSettingsH($label) . ' <small class="asr-field-requirement">' . asrSettingsH($requirement) . '</small></span><input name="urfConfig[' . asrSettingsH($mode) . '][' . asrSettingsH($key) . ']" type="text" value="' . asrSettingsH($value) . '" ' . $options . '><small>' . asrSettingsH($help) . '</small></label>';
	};
	$guidance = [
		'dmr' => ['DMR / TGIF connection', 'DMR carries reflector audio through a DMR network. For TGIF, choose the talkgroup that should carry traffic between TGIF and this URF reflector. TGIF account sign-in below is separate and supplies authenticated session data.'],
		'ysf' => ['YSF reflector connection', 'YSF links this URF reflector to a named YSF reflector. Use the exact reflector name published in your YSF host list, such as US-KE7WIL-YSF; the five-digit ID is the matching network identifier.'],
		'p25' => ['P25 destination', 'P25 routes reflector traffic to one numeric destination. Enter the P25 talkgroup or reflector designator assigned for this bridge; this is not an AllStar node number.'],
		'nxdn' => ['NXDN destination', 'NXDN routes reflector traffic to one numeric talkgroup or reflector designator. Use the value assigned by the NXDN network or reflector operator.'],
		'm17' => ['M17 reflector connection', 'M17 connects by reflector name and module. A name such as M17-WIL identifies the reflector; the one-letter module selects the room on that reflector.'],
	][$mode];
	?>
	<div class="asr-urf-mode-row<?php echo $enabled ? '' : ' is-disabled'; ?>" data-urf-mode="<?php echo asrSettingsH($mode); ?>">
		<div class="asr-urf-mode-heading">
			<label class="asr-settings-check"><input name="urfModes[]" type="checkbox" value="<?php echo asrSettingsH($mode); ?>"<?php echo $enabled ? ' checked' : ''; ?>><span><strong><?php echo asrSettingsH($label); ?> Bridge</strong><small><?php echo asrSettingsH($summary); ?></small></span></label>
			<details class="asr-progressive-details asr-urf-mode-details">
				<summary><span class="asr-disclosure-chevron">&gt;</span> Configure <span aria-hidden="true">›</span></summary>
				<div class="asr-bridge-section-copy"><strong><?php echo asrSettingsH($guidance[0]); ?></strong><span><?php echo asrSettingsH($guidance[1]); ?> These values are saved with the entire URFWIL reflector configuration.</span></div>
				<div class="asr-bridge-fields-grid">
				<?php if($mode === 'dmr'): ?>
					<?php $field($mode,'network','DMR Network',$values['network'] ?? '', 'Usually TGIF. Change this only when the installed runtime uses another supported DMR network.', 'Required', 'placeholder="TGIF" maxlength="80"'); ?>
					<?php $field($mode,'talkgroup','Destination Talkgroup',$values['talkgroup'] ?? '', 'The network talkgroup that will exchange audio with URFWIL, for example 86753.', 'Required', 'placeholder="86753" inputmode="numeric" maxlength="8"'); ?>
				<?php elseif($mode === 'ysf'): ?>
					<?php $field($mode,'reflector','YSF Reflector Name',$values['reflector'] ?? '', 'Enter the exact published name, such as US-KE7WIL-YSF.', 'Required', 'placeholder="US-KE7WIL-YSF" maxlength="80"'); ?>
					<?php $field($mode,'reflectorId','YSF Reflector ID',$values['reflectorId'] ?? '', 'The matching five-digit ID from YSFHosts.txt or the reflector operator.', 'Required when assigned', 'placeholder="64189" inputmode="numeric" maxlength="5"'); ?>
				<?php elseif(in_array($mode, ['p25','nxdn'], true)): ?>
					<?php $field($mode,'destination',$label . ' Destination',$values['destination'] ?? '', 'Enter the network talkgroup or reflector designator supplied for this bridge.', 'Required', 'placeholder="' . ($mode === 'p25' ? '64189' : '15846') . '" inputmode="numeric" maxlength="5"'); ?>
				<?php else: ?>
					<?php $field($mode,'reflector','M17 Reflector',$values['reflector'] ?? '', 'Use the M17-XXX name published by the reflector operator, for example M17-WIL.', 'Required', 'placeholder="M17-WIL" maxlength="7"'); ?>
					<?php $field($mode,'module','Module',$values['module'] ?? '', 'One letter identifying the reflector room, commonly A.', 'Required', 'placeholder="A" maxlength="1"'); ?>
				<?php endif; ?>
				</div>
				<?php if($mode === 'dmr'): ?>
					<p class="asr-bridge-section-note">The destination talkgroup controls bridge traffic. It is separate from the TGIF web account used to view authenticated session information.</p>
					<?php asrSettingsRenderTgifAccount('TGIF Account / Session Authentication'); ?>
				<?php endif; ?>
				<?php if(!in_array($mode, ['p25','nxdn'], true)): ?>
				<details class="asr-progressive-details asr-urf-mode-advanced">
					<summary><span class="asr-disclosure-chevron">&gt;</span> Advanced runtime fields</summary>
					<p class="asr-bridge-section-note">These values connect ASR to the installed bridge runtime. Most operators should keep the installer-provided values unchanged.</p>
					<div class="asr-bridge-fields-grid">
					<?php if($mode === 'dmr'): ?>
						<?php $field($mode,'dmrId','DMR ID',$values['dmrId'] ?? '', 'Your registered RadioID/DMR ID. Leave unchanged when already supplied by the runtime.', 'Runtime dependent', 'placeholder="3224939" inputmode="numeric" maxlength="7"'); ?>
						<?php $field($mode,'host','TGIF Host',$values['host'] ?? '', 'TGIF server hostname. Normally leave the installer-provided host unchanged.', 'Advanced', 'placeholder="tgif.network" maxlength="253"'); ?>
						<?php $field($mode,'port','TGIF Port',$values['port'] ?? '', 'Network port used by the installed DMR bridge. Normally leave unchanged.', 'Advanced', 'inputmode="numeric" maxlength="5"'); ?>
						<?php $field($mode,'urfTalkgroup','URF-side Talkgroup',$values['urfTalkgroup'] ?? '', 'Internal talkgroup used on the reflector side when the runtime requires one.', 'Advanced', 'inputmode="numeric" maxlength="8"'); ?>
					<?php elseif($mode === 'ysf'): ?>
						<?php $field($mode,'host',$label . ' Host',$values['host'] ?? '', 'Direct reflector hostname override. Leave blank or unchanged when the host list resolves the selected name.', 'Advanced', 'maxlength="253"'); ?>
						<?php $field($mode,'port',$label . ' Port',$values['port'] ?? '', 'Direct reflector port override. Normally leave unchanged.', 'Advanced', 'inputmode="numeric" maxlength="5"'); ?>
						<?php $field($mode,'module','Module',$values['module'] ?? '', 'Optional runtime-specific module letter; most YSF reflectors do not require this.', 'Optional', 'maxlength="1"'); ?>
					<?php else: ?>
						<?php $field($mode,'callsign','M17 Callsign',$values['callsign'] ?? '', 'Callsign sent by the M17 gateway. Use the value provisioned for this bridge.', 'Runtime dependent', 'placeholder="KE7WIL-M" maxlength="9"'); ?>
						<?php $field($mode,'host','Reflector Host',$values['host'] ?? '', 'Hostname or IP supplied by the reflector operator. Normally installer-managed.', 'Advanced', 'maxlength="253"'); ?>
						<?php $field($mode,'port','Reflector Port',$values['port'] ?? '', 'UDP port supplied by the reflector operator. Normally installer-managed.', 'Advanced', 'inputmode="numeric" maxlength="5"'); ?>
						<?php $field($mode,'bindPort','M17 Bind Port',$values['bindPort'] ?? '', 'Local UDP listening port. Change only to resolve a verified port conflict.', 'Advanced', 'inputmode="numeric" maxlength="5"'); ?>
						<?php $field($mode,'usrpRxPort','USRP Receive Port',$values['usrpRxPort'] ?? '', 'Local audio port receiving from the bridge runtime. Normally leave unchanged.', 'Advanced', 'inputmode="numeric" maxlength="5"'); ?>
						<?php $field($mode,'usrpTxPort','USRP Transmit Port',$values['usrpTxPort'] ?? '', 'Local audio port sending to the bridge runtime. Normally leave unchanged.', 'Advanced', 'inputmode="numeric" maxlength="5"'); ?>
					<?php endif; ?>
					</div>
				</details>
				<?php else: ?>
				<p class="asr-bridge-section-note">ASR needs only the destination above. Gateway files, host lists, ports, and services are installed and maintained outside this editor.</p>
				<?php endif; ?>
			</details>
		</div>
	</div>
	<?php
}

function asrSettingsBridgePanel($bridge = [], $bridgePasswords = [], $ysfCatalogStatuses = [], $lifecycle = []) {
	$modernUi = defined('ASR_SETTINGS_MODERN_UI') && ASR_SETTINGS_MODERN_UI;
	$id = (string)($bridge['id'] ?? '');
	$mode = asrSettingsBridgeMode($bridge);
	$source = (string)($bridge['clientSource'] ?? 'auto');
	if($source === 'disabled') $source = 'auto';
	$cardType = (string)($bridge['cardType'] ?? 'standard');
	$cardRole = $cardType === 'standard' ? 'standard' : 'net';
	$permission = (string)($bridge['bridgePermission'] ?? '');
	$backendMode = (string)($bridge['backendMode'] ?? '');
	$isNewDigitalMode = in_array($mode, ['p25', 'nxdn', 'm17'], true);
	if($backendMode === '') $backendMode = $isNewDigitalMode && $cardType === 'standard'
		? (!empty($bridge['bridgePermission']) ? 'managed' : 'display_only')
		: 'managed';
	$approvedDestinationText = $mode === 'm17'
		? asrSettingsM17DestinationsText($bridge['approvedDestinations'] ?? [])
		: ($mode === 'ysf'
			? implode("\n", array_map('strval', (array)($bridge['approvedDestinations'] ?? [])))
			: asrSettingsApprovedDesignatorsText($bridge['approvedDestinations'] ?? []));
	$passwordPlaceholder = !empty($bridgePasswords[$id]) ? 'Saved - leave blank to keep existing' : '';
	$panelTitle = (string)($bridge['title'] ?? '');
	$ysfCatalog = is_array($ysfCatalogStatuses[$id] ?? null) ? $ysfCatalogStatuses[$id] : [];
	$lifecyclePreviews = is_array($lifecycle['bridges'] ?? null) ? $lifecycle['bridges'] : [];
	$ownershipAvailable = !empty($lifecycle['available']);
	$lifecyclePreview = is_array($lifecyclePreviews[$id] ?? null) ? $lifecyclePreviews[$id] : [];
	$isAsrOwned = $id !== '' && !empty($lifecyclePreview['owned']);
	$lockLifecycleShape = $isAsrOwned || ($id !== '' && !$ownershipAvailable);
	$ownershipState = $id === '' ? 'new' : ($isAsrOwned ? 'owned' : ($ownershipAvailable ? 'external' : 'unknown'));
	$deletePreviewJson = json_encode($lifecyclePreview, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
	if(!is_string($deletePreviewJson)) $deletePreviewJson = '{}';
	if($panelTitle === '') $panelTitle = $id !== '' ? strtoupper($id) . ' Bridge' : 'New Digital Bridge';
	$modeLabel = $mode === 'dstar' ? 'D-Star' : ($mode === 'zello' ? 'Zello' : strtoupper($mode));
	$modePurpose = [
		'dmr' => 'Connects an AllStar bridge node to DMR. A Standard bridge normally uses a fixed destination; a Net Bridge lets authorized operators change talkgroups.',
		'ysf' => 'Connects an AllStar bridge node to YSF. A Standard bridge stays on its configured reflector; a Net Bridge provides controlled reflector selection.',
		'p25' => 'Connects an AllStar bridge node to a P25 reflector or talkgroup. Managed control requires an installed P25 gateway backend.',
		'nxdn' => 'Connects an AllStar bridge node to an NXDN reflector or talkgroup. Managed control requires an installed NXDN gateway backend.',
		'm17' => 'Connects an AllStar bridge node to an M17 reflector and module. Managed control requires a qualified M17 audio path.',
		'dstar' => 'Displays the installed D-Star bridge, its reflector/module, gateway link evidence, and recent radio activity. ASR does not retune it here.',
		'zello' => 'Displays the installed Zello bridge and recent or currently transmitting Zello identities. ASR does not manage Zello account sign-in here.',
	][$mode] ?? 'Connects a private AllStar bridge node to a digital network.';
	$clientTabLabel = $mode === 'zello' ? 'Recent Talkers' : ($mode === 'dmr' ? 'TGIF Sessions' : 'Client Data');
?>
	<div class="asr-bridge-settings-row is-collapsed" data-saved-bridge-id="<?php echo asrSettingsH($id); ?>" data-ownership-state="<?php echo asrSettingsH($ownershipState); ?>" data-delete-preview="<?php echo asrSettingsH($deletePreviewJson); ?>">
		<div class="asr-bridge-panel-header">
			<button class="asr-bridge-toggle" type="button" aria-expanded="false">
				<?php if($modernUi): ?><span class="asr-settings-toggle-icon" aria-hidden="true">&gt;</span><?php endif; ?>
				<span class="asr-bridge-toggle-copy">
					<strong class="asr-bridge-panel-name"><?php echo asrSettingsH($panelTitle); ?></strong>
					<span class="asr-bridge-panel-summary">Node <?php echo asrSettingsH($bridge['node'] ?? 'not set'); ?> · <?php echo $modernUi ? 'Select to configure this bridge.' : 'Bridge card, Connection Status name, and optional connected-client source.'; ?></span>
				</span>
				<?php if(!$modernUi): ?><span class="asr-settings-toggle-icon" aria-hidden="true">+</span><?php endif; ?>
			</button>
			<?php asrSettingsBridgeOrderControls($ownershipState === 'unknown'); ?>
		</div>

		<div class="asr-bridge-panel-body">
		<div class="asr-bridge-panel-section asr-card-basics-section" data-bridge-tab-label="Basics">
			<div class="asr-bridge-section-copy">
				<strong><?php echo asrSettingsH($modeLabel); ?> bridge basics</strong>
				<span><?php echo asrSettingsH($modePurpose); ?> Configure the private AllStar node that carries audio for this bridge.</span>
			</div>
			<div class="asr-bridge-fields-grid asr-bridge-card-grid">
				<?php if($lockLifecycleShape): ?><input name="bridgeMode[]" type="hidden" value="<?php echo asrSettingsH($mode); ?>"><?php endif; ?>
				<label><span>Digital Mode <small class="asr-field-requirement">Required</small></span><select name="bridgeMode[]"<?php echo $lockLifecycleShape ? ' disabled title="Delete and save this managed bridge before changing its Digital Mode."' : ''; ?>>
					<?php echo asrSettingsSourceOption($mode, 'dmr', 'DMR'); ?>
					<?php echo asrSettingsSourceOption($mode, 'ysf', 'YSF'); ?>
					<?php echo asrSettingsSourceOption($mode, 'dstar', 'D-Star'); ?>
					<?php echo asrSettingsSourceOption($mode, 'zello', 'Zello'); ?>
					<?php echo asrSettingsSourceOption($mode, 'p25', 'P25'); ?>
					<?php echo asrSettingsSourceOption($mode, 'nxdn', 'NXDN'); ?>
					<?php echo asrSettingsSourceOption($mode, 'm17', 'M17'); ?>
				</select><small>The radio network or digital protocol used by this bridge.</small></label>
				<?php if($lockLifecycleShape): ?><input name="bridgeCardType[]" type="hidden" value="<?php echo asrSettingsH($cardRole); ?>"><?php endif; ?>
				<label><span>Bridge Role <small class="asr-field-requirement">Required</small></span><select name="bridgeCardType[]"<?php echo $lockLifecycleShape ? ' disabled title="Delete and save this managed bridge before changing its role."' : ''; ?>>
					<?php echo asrSettingsSourceOption($cardRole, 'standard', 'Standard Bridge'); ?>
					<?php if(!in_array($mode, ['dstar', 'zello'], true)): ?><?php echo asrSettingsSourceOption($cardRole, 'net', 'Net Bridge'); ?><?php endif; ?>
				</select><small>Standard represents one installed bridge. Net Bridge adds supported destination controls for authorized operators.</small></label>
				<input name="bridgeId[]" type="hidden" value="<?php echo asrSettingsH($id); ?>">
				<label><span>Bridge AllStar Node <small class="asr-field-requirement">Required</small></span><input name="bridgeNode[]" type="text" inputmode="numeric" placeholder="1001" value="<?php echo asrSettingsH($bridge['node'] ?? ''); ?>"><small>The private local AllStar node assigned to this bridge—not your main node and not a talkgroup.</small></label>
				<label><span>Dashboard Name <small class="asr-field-requirement">Optional</small></span><input name="bridgeTitle[]" type="text" placeholder="New Digital Bridge" value="<?php echo asrSettingsH($bridge['title'] ?? ''); ?>"><small>The name operators see on the Bridges dashboard. Leave blank while adding a card to keep the draft label until you choose its mode.</small></label>
				<label><span>Connection Status Name <small class="asr-field-requirement">Optional</small></span><input name="bridgeFriendlyName[]" type="text" placeholder="Same as Dashboard Name" value="<?php echo asrSettingsH($bridge['friendlyName'] ?? ''); ?>"><small>Used in connection summaries and announcements. Leave blank to reuse the dashboard name.</small></label>
			</div>
		</div>

		<div class="asr-bridge-panel-section asr-backend-choice-section" data-bridge-tab-label="Basics"<?php echo $isNewDigitalMode && $cardType === 'standard' ? '' : ' hidden'; ?>>
			<div class="asr-bridge-section-copy"><strong>How ASR manages this bridge</strong><span>Choose Display only when the bridge was installed outside ASR or is being monitored only. Choose Managed only when the ASR backend installer has provisioned and qualified its runtime.</span></div>
			<?php if($lockLifecycleShape): ?><input name="bridgeBackendMode[]" type="hidden" value="<?php echo asrSettingsH($backendMode); ?>"><?php endif; ?>
			<label><span>Backend</span><select name="bridgeBackendMode[]"<?php echo $lockLifecycleShape ? ' disabled title="Delete and save this managed bridge before changing its backend."' : ''; ?>>
				<?php echo asrSettingsSourceOption($backendMode, 'display_only', 'Display only - no backend controls'); ?>
				<?php echo asrSettingsSourceOption($backendMode, 'managed', 'Managed - use installed backend controls'); ?>
			</select><small>Display only never starts, stops, or retunes the external service. Managed enables controls only after backend checks pass.</small></label>
		</div>

		<div class="asr-bridge-panel-section asr-destination-permission-section" data-bridge-tab-label="Controls &amp; Permissions"<?php echo ($cardRole === 'net' || ($isNewDigitalMode && $backendMode === 'managed')) ? '' : ' hidden'; ?>>
			<div class="asr-bridge-section-copy"><strong>Destination permission</strong><span>ASR exposes destination controls only for reflectors or talkgroups you own, or whose owner has explicitly approved this bridge.</span></div>
			<div class="asr-bridge-fields-grid">
				<label><span>Bridge Permission</span><select name="bridgePermission[]">
					<?php echo asrSettingsSourceOption($permission, '', 'Choose confirmed permission'); ?>
					<?php echo asrSettingsSourceOption($permission, 'self_owned', 'Self-owned target'); ?>
					<?php echo asrSettingsSourceOption($permission, 'approved', 'Target owner approved'); ?>
				</select><small>Select only after the target owner has authorized the bridge.</small></label>
				<label class="asr-ysf-allow-tune-field"<?php echo $cardRole === 'net' && $mode === 'ysf' ? '' : ' hidden'; ?>><span>Dashboard Reflector Controls <small class="asr-field-requirement">Required choice</small></span><select name="bridgeAllowTune[]">
					<?php echo asrSettingsSourceOption(!empty($bridge['allowTune']) ? '1' : '0', '0', 'Disabled'); ?>
					<?php echo asrSettingsSourceOption(!empty($bridge['allowTune']) ? '1' : '0', '1', 'Enabled'); ?>
				</select><small>Enable only after the dedicated YSF Net gateway and its reflector list are installed and verified.</small></label>
				<label class="asr-digital-fixed-field asr-numeric-fixed-field"><span>Fixed Destination <small class="asr-field-requirement">Required for managed Standard</small></span><input name="bridgeFixedDestination[]" inputmode="numeric" type="text" placeholder="10200" value="<?php echo asrSettingsH($bridge['fixedDestination'] ?? ''); ?>"><small>The P25 or NXDN talkgroup/reflector designator this Standard bridge stays connected to.</small></label>
				<label class="asr-m17-field asr-digital-fixed-field"><span>Fixed M17 Reflector <small class="asr-field-requirement">Required</small></span><input name="bridgeM17Reflector[]" type="text" placeholder="M17-WIL" value="<?php echo asrSettingsH($bridge['m17Reflector'] ?? ''); ?>"><small>The published M17-XXX reflector name.</small></label>
				<label class="asr-m17-field asr-digital-fixed-field"><span>Fixed M17 Host <small class="asr-field-requirement">Required</small></span><input name="bridgeM17Host[]" type="text" placeholder="m17.example.net" value="<?php echo asrSettingsH($bridge['m17Host'] ?? ''); ?>"><small>Hostname or IP supplied by the reflector operator.</small></label>
				<label class="asr-m17-field asr-digital-fixed-field"><span>Fixed M17 Port <small class="asr-field-requirement">Required</small></span><input name="bridgeM17Port[]" inputmode="numeric" type="text" placeholder="17000" value="<?php echo asrSettingsH($bridge['m17Port'] ?? ''); ?>"><small>UDP port published for the reflector.</small></label>
				<label class="asr-m17-field asr-digital-fixed-field"><span>Fixed M17 Module <small class="asr-field-requirement">Required</small></span><input name="bridgeM17Module[]" type="text" maxlength="1" placeholder="A" value="<?php echo asrSettingsH($bridge['m17Module'] ?? ''); ?>"><small>One-letter room on the reflector.</small></label>
				<label class="asr-m17-field"><span>M17 Callsign <small class="asr-field-requirement">Required</small></span><input name="bridgeM17Callsign[]" type="text" placeholder="KE7WIL" maxlength="9" value="<?php echo asrSettingsH($bridge['m17Callsign'] ?? ''); ?>"><small>The callsign the M17 gateway sends to the reflector.</small></label>
				<label class="asr-approved-destinations-field"<?php echo $cardRole === 'net' && !in_array($mode, ['dmr', 'ysf'], true) ? '' : ' hidden'; ?>><span>Approved Net Destinations <small class="asr-field-requirement">Required for Net Bridge</small></span><textarea name="bridgeApprovedDestinations[]" rows="4" placeholder="One approved destination per line"><?php echo asrSettingsH($approvedDestinationText); ?></textarea><small>Only these destinations will be offered to operators. P25/NXDN use numbers; M17 uses REFLECTOR | HOST | PORT | MODULE.</small></label>
			</div>
			<p class="asr-bridge-section-note asr-approved-destination-help">DMR talkgroups and YSF reflector names or IDs are entered manually on the dashboard. P25/NXDN use approved numeric designators. M17 uses REFLECTOR | HOST | PORT | MODULE. Catalog availability alone is not permission.</p>
		</div>

		<div class="asr-bridge-panel-section asr-backend-readiness-section" data-bridge-tab-label="Status">
			<div class="asr-bridge-section-copy"><strong>Bridge status and ownership</strong><span>This page checks the installed bridge service, its AllStar link, and whether ASR has current client or talker data. It also explains which parts ASR is allowed to manage.</span></div>
			<div class="asr-backend-readiness" data-bridge-readiness-id="<?php echo asrSettingsH($id); ?>" data-backend-mode="<?php echo asrSettingsH($backendMode); ?>">
				<strong><?php echo $backendMode === 'display_only' ? 'Monitoring only' : 'Checking bridge services'; ?></strong>
				<span><?php echo $backendMode === 'display_only' ? 'ASR can display this bridge but will not start, stop, or retune its externally installed service.' : 'Save the bridge first. ASR will then report whether the required service and AllStar link can be verified.'; ?></span>
			</div>
			<div class="asr-bridge-ownership <?php echo $ownershipState === 'owned' ? 'is-owned' : ($ownershipState === 'unknown' ? 'is-unknown' : 'is-external'); ?>">
				<strong><?php echo $ownershipState === 'owned' ? 'Managed by ASR' : ($ownershipState === 'unknown' ? 'Management state unavailable' : 'Installed outside ASR'); ?></strong>
				<span><?php echo $ownershipState === 'owned'
					? 'ASR may operate this bridge and can remove only the dedicated resources recorded in its ownership manifest.'
					: ($ownershipState === 'unknown'
						? 'ASR cannot safely determine whether it owns the runtime. Removal remains disabled until the ownership record can be checked.'
						: 'ASR can display status, but it will not start, stop, retune, or delete the external bridge service. Removing the card removes ASR metadata only.'); ?></span>
			</div>
		</div>


		<div class="asr-bridge-panel-section asr-standard-dmr-tgif" data-bridge-tab-label="TGIF Sessions"<?php echo ($cardType === 'standard' && $mode === 'dmr') ? '' : ' hidden'; ?>><?php asrSettingsRenderTgifAccount(); ?></div>


		<div class="asr-bridge-panel-section asr-standard-bridge-settings" data-bridge-tab-label="Link Recovery"<?php echo $cardType === 'standard' ? '' : ' hidden'; ?>>
			<div class="asr-bridge-section-copy">
				<strong>Fixed Bridge Recovery</strong>
				<span>Optional. Keeps this Standard Bridge linked to the main AllStar node.</span>
			</div>
			<input name="bridgeFixedRecovery[]" type="hidden" value="<?php echo !empty($bridge['fixedBridgeRecovery']) && $cardType === 'standard' ? '1' : '0'; ?>">
			<label class="asr-settings-check">
				<input data-fixed-recovery-checkbox type="checkbox" value="1"<?php echo !empty($bridge['fixedBridgeRecovery']) && $cardType === 'standard' ? ' checked' : ''; ?>>
				<span>Automatically restore this fixed bridge link if it drops</span>
			</label>
			<p class="asr-bridge-section-note">ASR checks only the configured local bridge node. If Asterisk already maintains it as a native permanent link, ASR recognizes that and does not create a second recovery loop. Net Bridges are never managed here.</p>
		</div>

		<div class="asr-bridge-panel-section asr-dmr-net-settings" data-bridge-tab-label="Advanced"<?php echo $cardType === 'dmr_net' ? '' : ' hidden'; ?>>
			<details class="asr-progressive-details asr-advanced-details">
			<summary>Advanced Details</summary>
			<div class="asr-bridge-section-copy">
				<strong>DMR backend resources</strong>
				<span>Installer-managed paths and the generated internal link alias. MQTT secrets are never shown.</span>
			</div>
			<div class="asr-bridge-fields-grid">
				<label><span>Internal Link Alias</span><input type="text" readonly value="<?php echo asrSettingsH($bridge['linkAlias'] ?? 'Generated when saved'); ?>"><small>Generated by ASR; shown only for installer diagnostics.</small></label>
				<label><span>ABInfo Path</span><input name="bridgeAbinfoPath[]" data-expert-field type="text" readonly placeholder="/tmp/ABInfo_12345.json" value="<?php echo asrSettingsH($bridge['abinfoPath'] ?? ''); ?>"><small>Normally leave unchanged. The DMR runtime writes current state here.</small></label>
				<label><span>DVSwitch Script</span><input name="bridgeDvswitchScript[]" data-expert-field type="text" readonly placeholder="/opt/MMDVM_Bridge_DMRNet/dvswitch.sh" value="<?php echo asrSettingsH($bridge['dvswitchScript'] ?? ''); ?>"><small>Normally leave unchanged. ASR uses this installed script for destination changes.</small></label>
				<label><span>Analog Bridge Config</span><input name="bridgeAnalogConfig[]" data-expert-field type="text" readonly placeholder="/opt/Analog_Bridge_DMRNet/Analog_Bridge.ini" value="<?php echo asrSettingsH($bridge['analogConfig'] ?? ''); ?>"><small>Normally leave unchanged. This identifies the dedicated audio bridge instance.</small></label>
			</div>
			<button class="asr-expert-edit-button" type="button">Expert Edit</button>
			<p class="asr-bridge-section-note">The bridge installer must first provision and validate the dedicated AllStar node, internal ASR link identity, ABInfo path, DVSwitch script, Analog Bridge config, ports, and services. Connect changes the talkgroup for everyone using this bridge. Controls are shown only to logged-in operators with node-control permission.</p>
			</details>
		</div>

		<div class="asr-bridge-panel-section asr-ysf-net-settings" data-bridge-tab-label="Advanced"<?php echo $cardType === 'ysf_net' ? '' : ' hidden'; ?>>
			<details class="asr-progressive-details asr-advanced-details">
			<summary>Advanced Details</summary>
			<div class="asr-bridge-section-copy">
				<strong>YSF Net Controls</strong>
				<span>The dashboard accepts an exact reflector name or five-digit ID, verifies the Gateway link, and then links the dedicated AllStar node.</span>
			</div>
			<div class="asr-bridge-fields-grid">
				<label><span>YSF Gateway Config</span><input name="bridgeYsfGatewayConfig[]" data-expert-field type="text" readonly placeholder="/opt/YSFGateway_YSFNet/YSFGateway.ini" value="<?php echo asrSettingsH($bridge['ysfGatewayConfig'] ?? ''); ?>"><small>Normally leave the installer-provided configuration path unchanged.</small></label>
				<label><span>MMDVM Bridge Config</span><input name="bridgeMmdvmConfig[]" data-expert-field type="text" readonly placeholder="/opt/MMDVM_Bridge_YSFNet/MMDVM_Bridge.ini" value="<?php echo asrSettingsH($bridge['mmdvmConfig'] ?? ''); ?>"><small>Normally leave the dedicated MMDVM configuration path unchanged.</small></label>
				<label><span>YSF Gateway Service</span><input name="bridgeYsfGatewayService[]" data-expert-field type="text" readonly placeholder="ysfgateway_ysfnet.service" value="<?php echo asrSettingsH($bridge['ysfGatewayService'] ?? ''); ?>"><small>Installed system service; change only when your installer used another unit.</small></label>
				<label><span>MMDVM Bridge Service</span><input name="bridgeMmdvmService[]" data-expert-field type="text" readonly placeholder="mmdvm_bridge_ysfnet.service" value="<?php echo asrSettingsH($bridge['mmdvmService'] ?? ''); ?>"><small>Installed system service; normally leave unchanged.</small></label>
				<label><span>Analog Bridge Service</span><input name="bridgeAnalogBridgeService[]" data-expert-field type="text" readonly placeholder="analog_bridge_ysfnet.service" value="<?php echo asrSettingsH($bridge['analogBridgeService'] ?? ''); ?>"><small>Installed audio service; normally leave unchanged.</small></label>
				<label><span>Emulator Service</span><input name="bridgeEmulatorService[]" data-expert-field type="text" readonly placeholder="md380-emu-ysfnet.service" value="<?php echo asrSettingsH($bridge['emulatorService'] ?? ''); ?>"><small>Installed codec service; normally leave unchanged.</small></label>
				<label><span>YSF Hosts Path</span><input name="bridgeYsfHostsPath[]" data-expert-field type="text" readonly placeholder="/var/lib/mmdvm/YSFHosts.txt" value="<?php echo asrSettingsH($bridge['ysfHostsPath'] ?? ''); ?>"><small>Reflector catalog used by this dedicated gateway.</small></label>
				<label class="asr-ysf-custom-reflectors"><span>Custom Reflectors <small class="asr-field-requirement">Optional</small></span><textarea name="bridgeYsfCustomReflectors[]" rows="4" placeholder="US-CUSTOM-TEST | 12345 | ysf.example.net | 42000 | My YSF Reflector"><?php echo asrSettingsH(asrSettingsCustomYsfReflectorsText($bridge['ysfCustomReflectors'] ?? [])); ?></textarea><small>Add only a reflector missing from the imported list, using NAME | ID | HOST | PORT | DESCRIPTION.</small></label>
			</div>
			<button class="asr-expert-edit-button" type="button">Expert Edit</button>
			<div class="asr-ysf-catalog-import">
				<strong>YSF Reflector List</strong>
				<?php if(($ysfCatalog['state'] ?? '') === 'valid'): ?>
					<p><?php echo (int)($ysfCatalog['count'] ?? 0); ?> valid reflectors · List date <?php echo asrSettingsH((string)($ysfCatalog['importedAt'] ?? 'unknown')); ?></p>
				<?php elseif(($ysfCatalog['state'] ?? '') === 'no_valid_list'): ?>
					<p>No valid YSF reflector list is installed. Dashboard YSF destination controls remain unavailable until a valid list is imported.</p>
				<?php else: ?>
					<p>YSF reflector-list status is unavailable. Save and apply this bridge configuration before importing a list.</p>
				<?php endif; ?>
				<?php if($id !== ''): ?>
					<label><span>Import YSFHosts.txt</span><input name="ysfHostsUpload_<?php echo asrSettingsH($id); ?>" type="file" accept=".txt,text/plain"></label>
					<button type="submit" name="ysfImportBridgeId" value="<?php echo asrSettingsH($id); ?>">Import reflector list</button>
				<?php else: ?>
					<p>Save this bridge card before importing its reflector list.</p>
				<?php endif; ?>
			</div>
			<p class="asr-bridge-section-note">Download <strong>YSF Plain Text</strong> from <a href="https://hostfiles.refcheck.radio/" target="_blank" rel="noopener noreferrer">RefCheck</a>, then import the downloaded YSFHosts.txt file here. Importing the reflector list does not save other unsaved Settings changes. ASR validates it before replacing the prior list. Re-import after RefCheck adds a reflector that your current list does not contain. Enter one custom reflector per line as NAME | 5-DIGIT ID | HOSTNAME OR IP | PORT | OPTIONAL DESCRIPTION. Custom entries are merged into a separate root-owned effective catalog. Enable controls only after the dedicated bridge stack is verified. The fixed/home YSF Bridge must remain a separate Standard Bridge.</p>
			</details>
		</div>

		<div class="asr-bridge-panel-section asr-next-digital-settings" data-bridge-tab-label="Advanced"<?php echo $isNewDigitalMode ? '' : ' hidden'; ?>>
			<details class="asr-progressive-details asr-advanced-details">
			<summary>Advanced Details</summary>
			<div class="asr-bridge-section-copy"><strong>Installer-generated backend resources</strong><span>These values are derived from the mode and internal instance. They are read-only and never include MQTT credentials.</span></div>
			<div class="asr-bridge-fields-grid">
				<label class="asr-digital-instance-field"><span>Gateway Instance</span><input name="bridgeInstance[]" type="text" readonly value="<?php echo asrSettingsH($bridge['instance'] ?? ''); ?>"><small>Generated internal name for this dedicated gateway.</small></label>
				<label class="asr-digital-instance-field"><span>Gateway Config</span><input name="bridgeGatewayConfig[]" type="text" readonly value="<?php echo asrSettingsH($bridge['gatewayConfig'] ?? ''); ?>"><small>Generated configuration path; read-only.</small></label>
				<label class="asr-digital-instance-field"><span>Gateway Service</span><input name="bridgeGatewayService[]" type="text" readonly value="<?php echo asrSettingsH($bridge['gatewayService'] ?? ''); ?>"><small>System service checked by Status; read-only.</small></label>
				<label class="asr-digital-instance-field"><span>MMDVM Service</span><input name="bridgeDigitalMmdvmService[]" type="text" readonly value="<?php echo asrSettingsH($bridge['mmdvmService'] ?? ''); ?>"><small>Dedicated network bridge service; read-only.</small></label>
				<label class="asr-digital-instance-field"><span>Analog Bridge Service</span><input name="bridgeDigitalAnalogService[]" type="text" readonly value="<?php echo asrSettingsH($bridge['analogBridgeService'] ?? ''); ?>"><small>Dedicated AllStar audio service; read-only.</small></label>
				<label class="asr-digital-instance-field asr-nxdn-emulator-field"><span>Emulator Service (NXDN only)</span><input name="bridgeDigitalEmulatorService[]" type="text" readonly value="<?php echo asrSettingsH($bridge['emulatorService'] ?? ''); ?>"><small>Codec service required by this NXDN runtime; read-only.</small></label>
				<label class="asr-digital-instance-field"><span>Local MQTT Topic Name</span><input name="bridgeMqttName[]" type="text" readonly value="<?php echo asrSettingsH($bridge['mqttName'] ?? ''); ?>"><small>Generated local status topic; credentials remain hidden.</small></label>
				<label class="asr-digital-instance-field"><span>MMDVM Activity MQTT Topic Name</span><input name="bridgeMmdvmMqttName[]" type="text" readonly value="<?php echo asrSettingsH($bridge['mmdvmMqttName'] ?? ''); ?>"><small>Generated activity topic used for talker state.</small></label>
				<label class="asr-m17-field"><span>M17 UDP Port</span><input name="bridgeM17BindPort[]" inputmode="numeric" type="text" readonly value="<?php echo asrSettingsH($bridge['m17BindPort'] ?? ''); ?>"><small>Installer-assigned local port; normally leave unchanged.</small></label>
				<label class="asr-m17-field"><span>USRP Receive Port</span><input name="bridgeM17UsrpRxPort[]" inputmode="numeric" type="text" readonly value="<?php echo asrSettingsH($bridge['m17UsrpRxPort'] ?? ''); ?>"><small>Installer-assigned incoming audio port; read-only.</small></label>
				<label class="asr-m17-field"><span>USRP Transmit Port</span><input name="bridgeM17UsrpTxPort[]" inputmode="numeric" type="text" readonly value="<?php echo asrSettingsH($bridge['m17UsrpTxPort'] ?? ''); ?>"><small>Installer-assigned outgoing audio port; read-only.</small></label>
			</div>
			<p class="asr-bridge-section-note asr-m17-field"><strong>M17 live audio: <?php echo !empty($bridge['m17AudioQualified']) ? 'Verified' : 'Pending'; ?></strong>. This result is read-only. Managed startup verifies the software and Codec2 path; live keyed two-way audio qualification remains separate.</p>
			<p class="asr-bridge-section-note">Authenticated MQTT credentials and ACLs are checked by the backend helper but are never displayed or stored in Settings.</p>
			</details>
		</div>

		<div class="asr-bridge-panel-section asr-dstar-status-settings" data-bridge-tab-label="Status"<?php echo $cardRole === 'standard' && $mode === 'dstar' ? '' : ' hidden'; ?>>
			<div class="asr-bridge-section-copy">
				<strong>D-Star Live Status</strong>
				<span>ASR reads the managed D-Star runtime, gateway link log, and current local reflector snapshot. It reports only evidence-backed gateway links and recent transmissions; this card has no reflector controls.</span>
			</div>
			<p class="asr-bridge-section-note">Runtime health, XRF reflector/module, linked gateways, and recent D-Star activity are collected automatically. Missing or stale evidence is shown as offline, unlinked, or empty rather than inferred.</p>
		</div>

		<div class="asr-bridge-panel-section asr-connected-client-settings" data-bridge-tab-label="<?php echo asrSettingsH($clientTabLabel); ?>"<?php echo $cardRole === 'standard' && !in_array($mode, ['dstar','dmr'], true) ? '' : ' hidden'; ?>>
			<details class="asr-progressive-details asr-connected-client-details">
			<summary><?php echo $mode === 'zello' ? 'Recent Zello Talker Source' : 'Client Data Source'; ?></summary>
			<div class="asr-bridge-section-copy">
				<strong><?php echo $mode === 'zello' ? 'Recent or active Zello talkers' : $modeLabel . ' client data'; ?></strong>
				<span><?php echo $mode === 'zello'
					? 'This is not a list of signed-in Zello users. Auto-detect reports the identity currently transmitting, or recent talker data when the installed bridge exposes it. ASR does not kick or disconnect Zello accounts.'
					: 'Auto-detect reads the installed reflector runtime for current links or clients. Choose a custom source only when your bridge exposes compatible current JSON data elsewhere.'; ?></span>
			</div>
			<div class="asr-bridge-client-source">
				<label><span>Client/Talker Source</span><select name="bridgeClientSource[]">
					<?php echo asrSettingsSourceOption($source, 'auto', 'Auto-detect'); ?>
					<?php echo asrSettingsSourceOption($source, 'local_json', 'Custom local JSON / file'); ?>
					<?php echo asrSettingsSourceOption($source, 'http_api', 'Custom HTTP API'); ?>
				</select><small>Auto-detect is correct for supported local bridge runtimes.</small></label>
				<label class="asr-custom-client-source-field"<?php echo in_array($source, ['local_json', 'http_api'], true) ? '' : ' hidden'; ?>><span>URL / Path <small class="asr-field-requirement">Required for Custom</small></span><input name="bridgeClientUrl[]" type="text" placeholder="<?php echo asrSettingsH(dirname(asrSettingsUploadDir()) . '/connected-clients.json'); ?>" value="<?php echo asrSettingsH($bridge['clientUrl'] ?? ''); ?>"><small>A readable local JSON file or HTTPS endpoint that returns current records.</small></label>
				<label class="asr-custom-client-source-field asr-http-client-source-field"<?php echo $source === 'http_api' ? '' : ' hidden'; ?>><span>Username <small class="asr-field-requirement">Optional</small></span><input name="bridgeClientUsername[]" type="text" autocomplete="username" value="<?php echo asrSettingsH($bridge['clientUsername'] ?? ''); ?>"><small>Use only when the custom HTTP endpoint requires Basic authentication.</small></label>
				<label class="asr-custom-client-source-field asr-http-client-source-field"<?php echo $source === 'http_api' ? '' : ' hidden'; ?>><span>Password / Token <small class="asr-field-requirement">Optional</small></span><input name="bridgeClientPassword[]" type="password" autocomplete="new-password" placeholder="<?php echo asrSettingsH($passwordPlaceholder); ?>"><small>Leave blank to keep the saved secret. ASR never displays it again.</small></label>
			</div>
			<p class="asr-bridge-section-note">Custom sources must return current JSON data. ASR checks the path or URL, data shape, freshness, and availability before showing it as current. Most installations should leave Auto-detect selected.</p>
			</details>
		</div>

		<div class="asr-bridge-panel-section asr-bridge-advanced-section" data-bridge-tab-label="Advanced">
			<details class="asr-progressive-details asr-advanced-details">
				<summary><span class="asr-disclosure-chevron">&gt;</span> Technical bridge identity</summary>
				<div class="asr-bridge-section-copy"><strong>Internal bridge details</strong><span>These identifiers help diagnostics and installers match this card to its backend. They do not change radio behavior.</span></div>
				<dl class="asr-bridge-technical-identity"><dt>Bridge ID</dt><dd><code><?php echo asrSettingsH($id !== '' ? $id : 'assigned when saved'); ?></code></dd><dt>Digital mode</dt><dd><?php echo asrSettingsH(strtoupper($mode)); ?></dd><dt>Configuration ownership</dt><dd><?php echo asrSettingsH($ownershipState === 'owned' ? 'ASR managed' : ($ownershipState === 'unknown' ? 'Unable to determine' : 'External or display only')); ?></dd></dl>
			</details>
			<label><span>Client/Talker Heading <small class="asr-field-requirement">Optional</small></span><input name="bridgeDetailTitle[]" type="text" placeholder="Use the mode-specific default" value="<?php echo asrSettingsH($bridge['detailTitle'] ?? ''); ?>"><small>Advanced display-only override for the dashboard heading. It does not change what data the bridge supplies.</small></label>
		</div>
		</div>
	</div>
<?php
}

function asrSettingsM17SetupPayload(&$error) {
	$error = '';
	$payload = [
		'bridgeId' => asrSettingsCleanBridgeId($_POST['setupBridgeId'] ?? ''),
		'title' => asrSettingsCleanText($_POST['setupTitle'] ?? '', 80),
		'bridgeNode' => trim((string)($_POST['setupBridgeNode'] ?? '')),
		'bridgeRole' => strtolower(asrSettingsCleanText($_POST['setupBridgeRole'] ?? 'standard', 12)),
		'callsign' => strtoupper(asrSettingsCleanText($_POST['setupCallsign'] ?? '', 9)),
		'reflector' => strtoupper(asrSettingsCleanText($_POST['setupReflector'] ?? '', 7)),
		'host' => asrSettingsCleanText($_POST['setupHost'] ?? '', 253),
		'port' => (int)($_POST['setupPort'] ?? 0),
		'module' => strtoupper(asrSettingsCleanText($_POST['setupModule'] ?? '', 1)),
	];
	if(!in_array($payload['bridgeRole'], ['standard', 'net'], true)) $error = 'Select Standard Bridge or Net Bridge.';
	elseif($payload['bridgeId'] === '') $error = 'Bridge ID must begin with a letter and use only lowercase letters, numbers, _ or -.';
	elseif($payload['title'] === '') $error = 'Bridge title is required.';
	elseif(!preg_match('/^[A-Z0-9][A-Z0-9.\/-]{2,8}$/D', $payload['callsign']) || !preg_match('/[A-Z]/', $payload['callsign']) || !preg_match('/[0-9]/', $payload['callsign'])) $error = 'Enter a valid M17 callsign.';
	elseif(!preg_match('/^M17-[A-Z0-9]{3}$/D', $payload['reflector'])) $error = 'Reflector must use the M17-XXX format.';
	elseif($payload['host'] === '' || preg_match('/\s/', $payload['host'])) $error = 'Enter a valid M17 reflector hostname or address.';
	elseif($payload['port'] < 1 || $payload['port'] > 65535) $error = 'M17 reflector port must be 1-65535.';
	elseif(!preg_match('/^[A-Z]$/D', $payload['module'])) $error = 'M17 module must be A-Z.';
	$planDigest = strtolower(asrSettingsCleanText($_POST['setupPlanDigest'] ?? '', 64));
	if($planDigest !== '') {
		if(!preg_match('/^[a-f0-9]{64}$/D', $planDigest)) $error = 'The M17 installation preview is invalid; preview again.';
		else $payload['planDigest'] = $planDigest;
	}
	return $payload;
}

function asrSettingsDigitalSetupPayload($mode, &$error) {
	$error = '';
	if(!in_array($mode, ['p25', 'nxdn', 'ysf'], true)) { $error = 'Unsupported digital bridge type.'; return []; }
	$payload = [
		'bridgeId' => asrSettingsCleanBridgeId($_POST['setupBridgeId'] ?? ''),
		'title' => asrSettingsCleanText($_POST['setupTitle'] ?? '', 80),
		'bridgeNode' => trim((string)($_POST['setupBridgeNode'] ?? '')),
		'bridgeRole' => strtolower(asrSettingsCleanText($_POST['setupBridgeRole'] ?? 'standard', 12)),
		'callsign' => strtoupper(asrSettingsCleanText($_POST['setupCallsign'] ?? '', 10)),
		'digitalId' => (int)($_POST['setupDigitalId'] ?? 0),
		'destination' => (int)($_POST['setupDestination'] ?? 0),
		'host' => asrSettingsCleanText($_POST['setupHost'] ?? '', 253),
		'port' => (int)($_POST['setupPort'] ?? 0),
	];
	if(!in_array($payload['bridgeRole'], ['standard', 'net'], true)) $error = 'Select Standard Bridge or Net Bridge.';
	elseif($payload['bridgeId'] === '') $error = 'Bridge ID must begin with a letter and use only lowercase letters, numbers, _ or -.';
	elseif($payload['title'] === '') $error = 'Bridge title is required.';
	elseif($payload['callsign'] !== 'SCRATCH' && (!preg_match('/^[A-Z0-9]{3,10}$/D', $payload['callsign']) || !preg_match('/[A-Z]/', $payload['callsign']) || !preg_match('/[0-9]/', $payload['callsign']))) $error = 'Enter a valid station callsign.';
	elseif($payload['digitalId'] < 1 || $payload['digitalId'] > 9999999) $error = 'Enter a valid 1-7 digit digital ID.';
	elseif($payload['destination'] < 11 || $payload['destination'] > 65534) $error = 'Enter a supported destination.';
	elseif($mode === 'ysf' && $payload['destination'] < 10000) $error = 'YSF destination must have five digits.';
	elseif($payload['host'] === '' || preg_match('/\s/', $payload['host'])) $error = 'Enter a valid reflector hostname or address.';
	elseif($payload['port'] < 1 || $payload['port'] > 65535) $error = 'Reflector port must be 1-65535.';
	$planDigest = strtolower(asrSettingsCleanText($_POST['setupPlanDigest'] ?? '', 64));
	if($planDigest !== '') {
		if(!preg_match('/^[a-f0-9]{64}$/D', $planDigest)) $error = 'The installation preview is invalid; preview again.';
		else $payload['planDigest'] = $planDigest;
	}
	return $payload;
}


function asrSettingsDmrSetupPayload($requireSecret, &$error) {
	$error = '';
	$authMode = strtolower(asrSettingsCleanText($_POST['setupTgifAuthMode'] ?? 'legacy', 12));
	$stationCallsign = (string) (asrSettingsReadConfig()['callsign'] ?? '');
	if($stationCallsign === 'SCRATCH')
		$stationCallsign = (string) ($_POST['setupCallsign'] ?? '');
	$payload = [
		'bridgeId' => asrSettingsCleanBridgeId($_POST['setupBridgeId'] ?? ''),
		'title' => asrSettingsCleanText($_POST['setupTitle'] ?? '', 80),
		'bridgeNode' => trim((string)($_POST['setupBridgeNode'] ?? '')),
		'bridgeRole' => strtolower(asrSettingsCleanText($_POST['setupBridgeRole'] ?? 'standard', 12)),
		'callsign' => strtoupper(asrSettingsCleanText($stationCallsign ?: ($_POST['setupCallsign'] ?? ''), 10)),
		'digitalId' => (int)($_POST['setupDigitalId'] ?? 0),
		'destination' => (int)($_POST['setupDestination'] ?? 0),
		'network' => strtolower(asrSettingsCleanText($_POST['setupDmrNetwork'] ?? 'tgif', 24)),
		'authMode' => $authMode,
		'networkUsername' => asrSettingsCleanText($_POST['setupDmrUsername'] ?? '', 80),
	];
	$password = (string)($_POST['setupTgifPassword'] ?? '');
	if($requireSecret && $authMode === 'secured') $payload['tgifPassword'] = $password;
	if(!in_array($payload['bridgeRole'], ['standard', 'net'], true)) $error = 'Select Standard Bridge or Net Bridge.';
	elseif($payload['bridgeId'] === '') $error = 'Bridge ID must begin with a letter and use only lowercase letters, numbers, _ or -.';
	elseif($payload['title'] === '') $error = 'Bridge title is required.';
	elseif(!in_array($authMode, ['legacy', 'secured'], true)) $error = 'Select a valid TGIF DMR connection method.';
	elseif($payload['callsign'] === '') $error = 'The node callsign is not configured.';
	elseif($payload['digitalId'] < 1 || $payload['digitalId'] > 9999999) $error = 'Enter a valid 1-7 digit DMR ID.';
	elseif($payload['destination'] < 1 || $payload['destination'] > 16777215 || $payload['destination'] === 4000) $error = 'Enter a valid TGIF talkgroup other than 4000.';
	elseif($requireSecret && $authMode === 'secured' && ($password === '' || strlen($password) > 128 || preg_match('/[\x00-\x1F\x7F]/', $password))) $error = 'Enter a valid TGIF hotspot key (1-128 characters; control characters are not allowed).';
	$planDigest = strtolower(asrSettingsCleanText($_POST['setupPlanDigest'] ?? '', 64));
	if($planDigest !== '') {
		if(!preg_match('/^[a-f0-9]{64}$/D', $planDigest)) $error = 'The DMR installation preview is invalid; preview again.';
		else $payload['planDigest'] = $planDigest;
	}
	return $payload;
}


function asrSettingsZelloSetupPayload($requireSecret, &$error) {
	$error = '';
	$payload = [
		'bridgeId' => asrSettingsCleanBridgeId($_POST['setupBridgeId'] ?? ''),
		'title' => asrSettingsCleanText($_POST['setupTitle'] ?? '', 80),
		'bridgeNode' => trim((string)($_POST['setupBridgeNode'] ?? '')),
		'username' => asrSettingsCleanText($_POST['setupZelloUsername'] ?? '', 80),
		'channel' => asrSettingsCleanText($_POST['setupZelloChannel'] ?? '', 120),
		'issuer' => asrSettingsCleanText($_POST['setupZelloIssuer'] ?? '', 160),
		'wsEndpoint' => asrSettingsCleanText($_POST['setupZelloWsEndpoint'] ?? 'wss://zello.io/ws', 253),
	];
	if($requireSecret) {
		$payload['password'] = (string)($_POST['setupZelloPassword'] ?? '');
		$payload['privateKey'] = (string)($_POST['setupZelloPrivateKey'] ?? '');
	}
	if($payload['bridgeId'] === '') $error = 'Bridge ID must begin with a letter and use only lowercase letters, numbers, _ or -.';
	elseif($payload['title'] === '' || $payload['username'] === '' || $payload['channel'] === '' || $payload['issuer'] === '') $error = 'Zello username, channel and issuer are required.';
	elseif(!preg_match('#^wss://[^\s/@:]+(?:\.[^\s/@:]+)*(?::[0-9]+)?(?:/[^\s]*)?$#D', $payload['wsEndpoint'])) $error = 'Enter a valid Zello wss:// WebSocket endpoint.';
	elseif($requireSecret && ($payload['password'] === '' || strlen($payload['password']) > 256)) $error = 'Enter the Zello account password.';
	elseif($requireSecret && (strlen($payload['privateKey']) > 16384 || !str_contains($payload['privateKey'], 'PRIVATE KEY-----'))) $error = 'Paste the Zello developer private key in PEM format.';
	$planDigest = strtolower(asrSettingsCleanText($_POST['setupPlanDigest'] ?? '', 64));
	if($planDigest !== '') {
		if(!preg_match('/^[a-f0-9]{64}$/D', $planDigest)) $error = 'The Zello installation preview is invalid; preview again.';
		else $payload['planDigest'] = $planDigest;
	}
	return $payload;
}


function asrSettingsDstarSetupPayload(&$error) {
	$error = '';
	$payload = [
		'bridgeId' => asrSettingsCleanBridgeId($_POST['setupBridgeId'] ?? ''),
		'title' => asrSettingsCleanText($_POST['setupTitle'] ?? '', 80),
		'bridgeNode' => trim((string)($_POST['setupBridgeNode'] ?? '')),
		'callsign' => strtoupper(asrSettingsCleanText($_POST['setupCallsign'] ?? '', 8)),
		'dmrId' => (int)($_POST['setupDigitalId'] ?? 0),
		'reflector' => strtoupper(asrSettingsCleanText($_POST['setupReflector'] ?? '', 9)),
		'module' => strtoupper(asrSettingsCleanText($_POST['setupModule'] ?? 'A', 1)),
	];
	if($payload['bridgeId'] === '') $error = 'Bridge ID must begin with a letter and use only lowercase letters, numbers, _ or -.';
	elseif(!preg_match('/^[A-Z0-9]{3,8}$/D', $payload['callsign'])) $error = 'Enter a valid D-Star callsign.';
	elseif($payload['dmrId'] < 1 || $payload['dmrId'] > 9999999) $error = 'Enter a valid 1-7 digit DMR ID for the bridge metadata.';
	elseif(!preg_match('/^(?:XRF|XLX|REF|DCS)[0-9A-Z]{3,6}$/D', $payload['reflector'])) $error = 'Enter a D-Star reflector such as XRF641.';
	elseif(!preg_match('/^[A-Z]$/D', $payload['module'])) $error = 'Enter a D-Star module A-Z.';
	$planDigest = strtolower(asrSettingsCleanText($_POST['setupPlanDigest'] ?? '', 64));
	if($planDigest !== '') {
		if(!preg_match('/^[a-f0-9]{64}$/D', $planDigest)) $error = 'The D-Star installation preview is invalid; preview again.';
		else $payload['planDigest'] = $planDigest;
	}
	return $payload;
}

function asrSettingsRunBridgeSetup($action, $payload, &$error) {
	$error = '';
	$allowed = ['m17-plan', 'm17-install', 'p25-plan', 'p25-install', 'nxdn-plan', 'nxdn-install', 'ysf-plan', 'ysf-install', 'dmr-plan', 'dmr-install', 'zello-plan', 'zello-install', 'dstar-plan', 'dstar-install'];
	if(!in_array($action, $allowed, true)) { $error = 'Unsupported bridge setup action.'; return null; }
	if(!function_exists('proc_open') || !is_executable(ASR_BRIDGE_SETUP_HELPER)) { $error = 'Bridge setup helper is not installed.'; return null; }
	$hostSocket = '/run/allscan-reimagined-host/bridge-setup.sock';
	$command = @filetype($hostSocket) === 'socket'
		? [ASR_BRIDGE_SETUP_HELPER, $action]
		: ['sudo', '-n', ASR_BRIDGE_SETUP_HELPER, $action];
	$process = proc_open($command, [
		0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w'],
	], $pipes);
	if(!is_resource($process)) { $error = 'Bridge setup helper could not be started.'; return null; }
	fwrite($pipes[0], json_encode($payload, JSON_UNESCAPED_SLASHES));
	fclose($pipes[0]);
	$output = stream_get_contents($pipes[1]); fclose($pipes[1]);
	$stderr = stream_get_contents($pipes[2]); fclose($pipes[2]);
	$status = proc_close($process);
	$data = json_decode((string)$output, true);
	if(!is_array($data)) $data = json_decode((string)$stderr, true);
	if($status !== 0 || !is_array($data)) {
		$error = asrSettingsCleanText($data['error'] ?? 'Bridge setup helper failed.', 240);
		return null;
	}
	return $data;
}

if(defined('ASR_SETTINGS_FUNCTIONS_ONLY') && ASR_SETTINGS_FUNCTIONS_ONLY)
	return;

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
$modernSettings = defined('ASR_SETTINGS_MODERN_UI') && ASR_SETTINGS_MODERN_UI;
$modernSettingsSection = $modernSettings ? asrSettingsModernSection() : '';
if($modernSettings && $modernSettingsSection === 'account')
	asrSettingsRenderAccount($userModel, $user);
if(!adminUser())
	asExit('Admin permission required.');

$config = asrSettingsReadConfig();
$secrets = asrSettingsReadSecrets();
$setupCallsignDefault = strtoupper(asrSettingsCleanText($config['callsign'] ?? '', 10));
$setupDmrIdDefault = (int)($config['dmrId'] ?? 0);
if($setupDmrIdDefault < 1 || $setupDmrIdDefault > 9999999) {
	foreach((array)($config['bridges'] ?? []) as $configuredBridge) {
		$candidateDmrId = (int)($configuredBridge['dmrId'] ?? $configuredBridge['digitalId'] ?? 0);
		if($candidateDmrId >= 1 && $candidateDmrId <= 9999999) { $setupDmrIdDefault = $candidateDmrId; break; }
	}
}
$currentAsrVersion = defined('ASR_REIMAGINED_VERSION_LABEL') ? ASR_REIMAGINED_VERSION_LABEL : 'Current ASR version';
$rollbackListError = '';
$rollbackCandidates = asrSettingsRollbackCandidates($currentAsrVersion, $rollbackListError);
$rollbackCandidateById = [];
foreach($rollbackCandidates as $candidate)
	$rollbackCandidateById[$candidate['id']] = $candidate;
$rollbackCsrfToken = asrSettingsRollbackCsrfToken($user);
$saveCsrfToken = asrSettingsSaveCsrfToken($user);
$bridgeSetupAvailable = function_exists('proc_open') && is_executable(ASR_BRIDGE_SETUP_HELPER);
$bridgeLifecycleError = '';
$bridgeLifecyclePreviews = asrSettingsBridgeLifecyclePreviews($bridgeLifecycleError);
function asrSettingsRunUrfAdmin($action, $rule, &$error) {
	$error = '';
	if(!in_array($action, ['list','ban','unban'], true)) { $error = 'Invalid URF admin action.'; return null; }
	if(!function_exists('exec') || !is_executable(ASR_URF_ADMIN_HELPER)) { $error = 'URF administration is not installed yet.'; return null; }
	$command = 'sudo -n ' . escapeshellarg(ASR_URF_ADMIN_HELPER) . ' ' . escapeshellarg($action);
	if($rule !== '') $command .= ' ' . escapeshellarg($rule);
	$output = []; $status = 1; exec($command . ' 2>/dev/null', $output, $status);
	$data = json_decode(implode("\n", $output), true);
	if($status !== 0 || !is_array($data) || empty($data['ok'])) { $error = asrSettingsCleanText($data['error'] ?? 'URF administration command failed.', 180); return null; }
	return $data;
}

function asrSettingsUrfFieldAliases($mode, $field) {
	$aliases = [
		'dmr' => [
			'network' => [['network'], ['dmrNetwork']],
			'talkgroup' => [['talkgroup','tgifTalkgroup','fixedDestination'], ['tgifTalkgroup','fixedDestination']],
			'dmrId' => [['dmrId'], ['dmrId']],
			'host' => [['host','tgifHost'], ['tgifHost']],
			'port' => [['port','tgifPort'], ['tgifPort']],
			'urfTalkgroup' => [['urfTalkgroup','urfTg'], ['urfTalkgroup','urfTg']],
		],
		'ysf' => [
			'reflector' => [['reflector','ysfReflector'], ['ysfReflector']],
			'reflectorId' => [['reflectorId','ysfReflectorId'], ['ysfReflectorId']],
			'host' => [['host','ysfHost'], ['ysfHost']], 'port' => [['port','ysfPort'], ['ysfPort']],
			'module' => [['module','ysfModule'], ['ysfModule']],
		],
		'p25' => [
			'destination' => [['destination','fixedDestination','reflectorId'], ['p25Destination','p25ReflectorId']],
		],
		'nxdn' => [
			'destination' => [['destination','fixedDestination','reflectorId'], ['nxdnDestination','nxdnReflectorId']],
		],
		'm17' => [
			'reflector' => [['reflector','m17Reflector'], ['m17Reflector']],
			'host' => [['host','m17Host'], ['m17Host']], 'port' => [['port','m17Port'], ['m17Port']],
			'module' => [['module','m17Module'], ['m17Module']], 'callsign' => [['callsign','m17Callsign'], ['m17Callsign']],
			'bindPort' => [['bindPort','m17BindPort'], ['m17BindPort']],
			'usrpRxPort' => [['usrpRxPort','m17UsrpRxPort'], ['m17UsrpRxPort']],
			'usrpTxPort' => [['usrpTxPort','m17UsrpTxPort'], ['m17UsrpTxPort']],
		],
	];
	return $aliases[$mode][$field] ?? [[$field], [$mode . ucfirst($field)]];
}

function asrSettingsUrfModeFields() {
	return [
		'dmr' => ['network','talkgroup','dmrId','host','port','urfTalkgroup'],
		'ysf' => ['reflector','reflectorId','host','port','module'],
		'p25' => ['destination'],
		'nxdn' => ['destination'],
		'm17' => ['reflector','host','port','module','callsign','bindPort','usrpRxPort','usrpTxPort'],
	];
}

function asrSettingsUrfAggregateRecord($config) {
	foreach((array)($config['bridges'] ?? []) as $candidate) {
		if(is_array($candidate) && !empty($candidate['urfReflector']) && strtolower((string)($candidate['mode'] ?? '')) === 'urf') return $candidate;
	}
	return [];
}

function asrSettingsUrfModeValue($record, $mode, $field, &$location = null) {
	[$nestedAliases, $topAliases] = asrSettingsUrfFieldAliases($mode, $field);
	foreach(['modeConfig','modeSettings'] as $container) {
		$modeValues = is_array($record[$container][$mode] ?? null) ? $record[$container][$mode] : [];
		foreach($nestedAliases as $alias) if(array_key_exists($alias, $modeValues)) {
			$location = ['kind'=>'nested','container'=>$container,'key'=>$alias];
			return $modeValues[$alias];
		}
	}
	foreach($topAliases as $alias) if(array_key_exists($alias, $record)) {
		$location = ['kind'=>'top','key'=>$alias];
		return $record[$alias];
	}
	$location = null;
	return '';
}

function asrSettingsUrfModeConfig($record, $mode) {
	$result = [];
	foreach(asrSettingsUrfModeFields()[$mode] ?? [] as $field) $result[$field] = asrSettingsUrfModeValue($record, $mode, $field);
	return $result;
}

function asrSettingsUrfModeSummary($mode, $values) {
	$clean = static fn($value) => trim((string)$value);
	if($mode === 'dmr') {
		$network = $clean($values['network'] ?? '') ?: (!empty($values['talkgroup']) ? 'TGIF' : '');
		$parts = array_filter([$network, !empty($values['talkgroup']) ? 'TG ' . $clean($values['talkgroup']) : '']);
	} elseif($mode === 'ysf') {
		$parts = array_filter([$clean($values['reflector'] ?? ''), !empty($values['reflectorId']) ? 'ID ' . $clean($values['reflectorId']) : '']);
	} elseif(in_array($mode, ['p25','nxdn'], true)) {
		$parts = array_filter([!empty($values['destination']) ? 'Destination ' . $clean($values['destination']) : '', $clean($values['module'] ?? '')]);
	} else {
		$parts = array_filter([$clean($values['reflector'] ?? ''), !empty($values['module']) ? 'Module ' . $clean($values['module']) : '']);
	}
	return $parts ? implode(' · ', $parts) : 'Destination not configured';
}

function asrSettingsUrfValidHost($value) {
	return filter_var($value, FILTER_VALIDATE_IP) !== false
		|| preg_match('/(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/D', $value);
}

function asrSettingsUrfValidateModeField($mode, $field, $value, &$error) {
	if($value === '') return true;
	$label = strtoupper($mode) . ' ' . preg_replace('/(?<!^)[A-Z]/', ' $0', $field);
	if(in_array($field, ['port','bindPort','usrpRxPort','usrpTxPort'], true) && (!ctype_digit($value) || (int)$value < 1 || (int)$value > 65535)) $error = "$label must be a port from 1 through 65535.";
	elseif($field === 'talkgroup' && (!ctype_digit($value) || (int)$value < 1 || (int)$value > 16777215 || (int)$value === 4000)) $error = 'DMR TGIF talkgroup must be 1-16777215 and cannot be disconnect TG 4000.';
	elseif($field === 'urfTalkgroup' && (!ctype_digit($value) || (int)$value < 1 || (int)$value > 16777215)) $error = 'DMR URF talkgroup must be 1-16777215.';
	elseif($field === 'dmrId' && (!ctype_digit($value) || (int)$value < 1 || (int)$value > 9999999)) $error = 'DMR ID must contain 1-7 digits.';
	elseif($field === 'reflectorId' && (!preg_match('/^[0-9]{5}$/D', $value) || $value === '00000')) $error = 'YSF reflector ID must be five digits and cannot be 00000.';
	elseif($field === 'destination' && !asrSettingsDesignatorIsAllowed($value, $mode)) $error = "$label must be a valid 11-65534 designator and cannot use a reserved control value.";
	elseif($field === 'host' && !asrSettingsUrfValidHost($value)) $error = "$label must be a valid hostname or IP address.";
	elseif($field === 'module' && !preg_match('/^[A-Z]$/D', $value)) $error = "$label must be one letter.";
	elseif($mode === 'm17' && $field === 'reflector' && !preg_match('/^M17-[A-Z0-9]{3}$/D', $value)) $error = 'M17 reflector must use the M17-XXX designator format.';
	elseif($field === 'callsign' && (!preg_match('/^[A-Z0-9][A-Z0-9.\/-]{2,8}$/D', $value) || !preg_match('/[A-Z]/', $value) || !preg_match('/[0-9]/', $value))) $error = 'M17 callsign is invalid.';
	elseif(in_array($field, ['network','reflector'], true) && (!preg_match('/^[A-Za-z0-9][A-Za-z0-9 _.\/-]{0,79}$/D', $value))) $error = "$label contains unsupported characters.";
	return $error === '';
}

function asrSettingsUrfApplyModePost($record, &$error) {
	$posted = is_array($_POST['urfConfig'] ?? null) ? $_POST['urfConfig'] : [];
	foreach(asrSettingsUrfModeFields() as $mode => $fields) foreach($fields as $field) {
		if(!array_key_exists($field, (array)($posted[$mode] ?? []))) continue;
		$value = asrSettingsCleanText($posted[$mode][$field], in_array($field, ['host'], true) ? 253 : 80);
		if(in_array($field, ['module','callsign'], true) || ($mode === 'm17' && $field === 'reflector')) $value = strtoupper($value);
		$location = null;
		$current = asrSettingsUrfModeValue($record, $mode, $field, $location);
		if((string)$current === $value) continue;
		if(!asrSettingsUrfValidateModeField($mode, $field, $value, $error)) return [];
		if($location && $location['kind'] === 'top') {
			if($value === '') unset($record[$location['key']]); else $record[$location['key']] = $value;
			continue;
		}
		if(!$location) {
			$topAliases = asrSettingsUrfFieldAliases($mode, $field)[1];
			$key = $topAliases[0];
			if($value !== '') $record[$key] = $value;
			continue;
		}
		$container = $location['container'];
		$key = $location['key'];
		if($value === '') unset($record[$container][$mode][$key]); else $record[$container][$mode][$key] = $value;
		if(isset($record[$container][$mode]) && $record[$container][$mode] === []) unset($record[$container][$mode]);
		if(isset($record[$container]) && $record[$container] === []) unset($record[$container]);
	}
	return $record;
}

function asrSettingsUrfConfigFromPost(&$error, $config = []) {
	$enabled = !empty($_POST['urfEnabled']);
	$node = asrSettingsCleanText($_POST['urfNode'] ?? '', 10);
	$modes = is_array($_POST['urfModes'] ?? null) ? $_POST['urfModes'] : [];

	$modes = array_values(array_intersect(['dmr', 'ysf', 'p25', 'nxdn', 'm17'], array_map('strtolower', array_map('strval', $modes))));
	$record = asrSettingsUrfAggregateRecord($config);
	$existingUrf = asrSettingsExistingUrfConfig($config);
	if(!$enabled) return ['enabled' => false, 'node' => '', 'modes' => [], 'record' => $record];

	if(!preg_match('/^[0-9]{3,10}$/D', $node)) {
		$error = 'URF Reflector needs a 3-10 digit shared AllStar node number.';
		return [];
	}
	if(!$modes) {
		$error = 'URF Reflector needs at least one enabled digital mode.';
		return [];
	}
	if(!$record && empty($existingUrf['enabled'])) $record = [
		'id'=>'urf', 'mode'=>'urf', 'node'=>$node, 'title'=>'URFWIL Reflector',
		'detailTitle'=>'URF Clients & Activity', 'friendlyName'=>'URFWIL Multi-Mode Bridge',
		'clientSource'=>'auto', 'clientUrl'=>'', 'clientUsername'=>'', 'cardType'=>'standard',
		'backendMode'=>'display_only', 'allowTune'=>false, 'fixedBridgeRecovery'=>false,
		'urfReflector'=>true, 'urfGroupId'=>'urf',
	];
	if($record) {
		$record = asrSettingsUrfApplyModePost($record, $error);
		if($error !== '') return [];
	}
	return ['enabled' => true, 'node' => $node, 'modes' => $modes, 'record' => $record];
}


function asrSettingsUrfModeBridges($urf) {
	if(empty($urf['enabled'])) return [];
	if(is_array($urf['record'] ?? null) && !empty($urf['record'])) {
		$record = $urf['record'];
		$record['node'] = (string)$urf['node'];
		$allUrfModes = ['dmr','ysf','p25','nxdn','m17'];
		if(array_key_exists('modes', $record) || array_values($urf['modes']) !== $allUrfModes) $record['modes'] = array_values($urf['modes']);
		return [$record];
	}

	$result = [];
	foreach($modes as $mode) {
		if(isset($managedByMode[$mode])) {
			$result[] = $managedByMode[$mode];
			continue;
		}
		$label = $mode === 'm17' ? 'M17' : strtoupper($mode);
		$result[] = [
			'id' => 'urf_' . $mode,
			'mode' => $mode,
			'node' => (string)($urf['node'] ?? ''),
			'title' => $label . ' Bridge',
			'detailTitle' => 'Connected Clients',
			'friendlyName' => $label . ' Bridge',
			'clientSource' => 'auto',
			'clientUrl' => '',
			'clientUsername' => '',
			'cardType' => 'standard',
			'backendMode' => 'display_only',
			'allowTune' => false,
			'fixedBridgeRecovery' => false,
			'urfReflector' => true,
			'urfGroupId' => 'urf',
		];
	}
	return $result;
}

function asrSettingsExistingUrfConfig($config) {
	$node = '';
	$modes = [];
	$aggregate = false;
	foreach((array)($config['bridges'] ?? []) as $bridge) {
		if(!is_array($bridge) || empty($bridge['urfReflector'])) continue;
		if($node === '') $node = (string)($bridge['node'] ?? '');
		$mode = strtolower((string)($bridge['mode'] ?? ''));

		if($mode === 'urf') {
			$aggregate = true;
			$configuredModes = is_array($bridge['modes'] ?? null) ? $bridge['modes'] : ['dmr','ysf','p25','nxdn','m17'];
			foreach($configuredModes as $configuredMode) {
				$configuredMode = strtolower((string)$configuredMode);
				if(in_array($configuredMode, ['dmr','ysf','p25','nxdn','m17'], true)) $modes[] = $configuredMode;
			}
		} elseif(in_array($mode, ['dmr','ysf','p25','nxdn','m17'], true)) $modes[] = $mode;

	}
	$record = $aggregate ? asrSettingsUrfAggregateRecord($config) : [];
	$modeConfig = [];
	foreach(array_keys(asrSettingsUrfModeFields()) as $mode) $modeConfig[$mode] = asrSettingsUrfModeConfig($record, $mode);
	return ['enabled' => !empty($modes), 'node' => $node, 'modes' => array_values(array_unique($modes)), 'aggregate' => $aggregate, 'record' => $record, 'modeConfig' => $modeConfig];
}

function asrSettingsUrfBridgesForSave($config, $urf) {
	$generated = asrSettingsUrfModeBridges($urf);
	if(empty($urf['enabled'])) return $generated;
	$existing = asrSettingsExistingUrfConfig($config);
	if(empty($existing['aggregate'])) return $generated;
	foreach((array)($config['bridges'] ?? []) as $candidate) {
		if(!is_array($candidate) || empty($candidate['urfReflector']) || strtolower((string)($candidate['mode'] ?? '')) !== 'urf') continue;
		$candidate = is_array($urf['record'] ?? null) && !empty($urf['record']) ? $urf['record'] : $candidate;
		$candidate['node'] = $urf['node'];
		$allUrfModes = ['dmr','ysf','p25','nxdn','m17'];
		if(array_key_exists('modes', $candidate) || array_values($urf['modes']) !== $allUrfModes)
			$candidate['modes'] = array_values($urf['modes']);
		return [$candidate];
	}
	return $generated;
}

$submit = $_POST['Submit'] ?? null;
$settingsSaveRequested = (string)($_POST['settingsAction'] ?? '') === 'save-settings' || $submit === SAVE_REIMAGINED_SETTINGS;
$asrAction = $_POST['asrAction'] ?? null;
$ysfImportBridgeId = trim((string)($_POST['ysfImportBridgeId'] ?? ''));
$urfAdminAction = trim((string)($_POST['urfAdminAction'] ?? ''));

$bridgeSetupActions = ['m17-plan', 'm17-install', 'p25-plan', 'p25-install', 'nxdn-plan', 'nxdn-install', 'ysf-plan', 'ysf-install', 'dmr-plan', 'dmr-install', 'zello-plan', 'zello-install', 'dstar-plan', 'dstar-install'];
if(in_array($asrAction, $bridgeSetupActions, true)) {
	header('Content-Type: application/json; charset=utf-8');
	$postedToken = (string)($_POST['settingsSaveCsrf'] ?? '');
	$error = '';
	if(($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST' || !asrSettingsRollbackPostIsSameOrigin(true) || $saveCsrfToken === '' || !hash_equals($saveCsrfToken, $postedToken)) {
		$error = 'Bridge setup request was not authorized.';
		$result = null;
	} else {
		$mode = explode('-', (string)$asrAction, 2)[0];
		if($mode === 'm17') $payload = asrSettingsM17SetupPayload($error);
		elseif($mode === 'dmr') $payload = asrSettingsDmrSetupPayload(str_ends_with((string)$asrAction, '-install'), $error);
		elseif($mode === 'zello') $payload = asrSettingsZelloSetupPayload(str_ends_with((string)$asrAction, '-install'), $error);
		elseif($mode === 'dstar') $payload = asrSettingsDstarSetupPayload($error);
		else $payload = asrSettingsDigitalSetupPayload($mode, $error);
		$result = $error === '' ? asrSettingsRunBridgeSetup($asrAction, $payload, $error) : null;
		if($result !== null && $mode === 'dmr' && str_ends_with((string)$asrAction, '-install')) {
			$persistConfig = asrSettingsReadConfig();
			$persistConfig['dmrId'] = (int)$payload['digitalId'];
			$persistError = '';
			if(!asrSettingsWriteConfig($persistConfig, $persistError)) { $error = 'Bridge installed, but ASR could not remember the DMR ID: ' . $persistError; $result = null; }
		}

	}
	if($result === null) {
		http_response_code(400);
		echo json_encode(['ok' => false, 'error' => $error], JSON_UNESCAPED_SLASHES);
	} else {
		echo json_encode(['ok' => true, 'result' => $result], JSON_UNESCAPED_SLASHES);
	}
	exit;
}

$urfAdminListError = '';
$urfAdminList = asrSettingsRunUrfAdmin('list', '', $urfAdminListError);
$urfAdminRules = is_array($urfAdminList['rules'] ?? null) ? $urfAdminList['rules'] : [];

if($urfAdminAction !== '') {
	$postedToken = (string)($_POST['settingsSaveCsrf'] ?? '');
	if(($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST' || !asrSettingsRollbackPostIsSameOrigin(true) || $saveCsrfToken === '' || !hash_equals($saveCsrfToken, $postedToken)) {
		$urfAdminError = 'URF administration request was not authorized.';
	} else {
		$rule = asrSettingsCleanText($_POST['urfAdminRule'] ?? '', 16); $helperError = '';
		$result = asrSettingsRunUrfAdmin($urfAdminAction, $rule, $helperError);
		if(!$result) {
			$urfAdminError = $helperError;
		} else {
			$urfAdminRules = is_array($result['rules'] ?? null) ? $result['rules'] : [];
			$urfAdminOk = strtoupper($urfAdminAction) . ($rule !== '' ? ' ' . strtoupper($rule) : '') . ' completed. URFD reloads the list automatically.';
		}
	}
} elseif($ysfImportBridgeId !== '') {
	$postedToken = (string)($_POST['ysfImportCsrf'] ?? '');
	if(($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
		$ysfImportError = 'YSF reflector-list import requires a POST request.';
	} elseif(!asrSettingsRollbackPostIsSameOrigin()) {
		$ysfImportError = 'YSF reflector-list import was blocked because the request did not come from this node.';
	} elseif($rollbackCsrfToken === '' || $postedToken === '' || !hash_equals($rollbackCsrfToken, $postedToken)) {
		$ysfImportError = 'The YSF reflector-list import confirmation was invalid. Reload this page and try again.';
	} else {
		$uploadError = '';
		$content = asrSettingsReadYsfHostsUpload($ysfImportBridgeId, $uploadError);
		if($content === null) {
			$ysfImportError = $uploadError;
		} else {
			$helperError = '';
			$result = asrSettingsRunYsfCatalogHelper('import', $ysfImportBridgeId, $content, $helperError);
			if(!$result) {
				$ysfImportError = $helperError;
			} else {
				$count = (int)($result['count'] ?? 0);
				$ysfImportOk = 'YSFHosts.txt imported successfully with ' . $count . ' reflectors.';
				if(isset($result['gatewayReloaded']) && !$result['gatewayReloaded'])
					$ysfImportOk .= ' The list is safe, but the dedicated YSF Gateway could not reload it; check Bridge Diagnostics before connecting.';
			}
		}
	}
} elseif($asrAction === 'queue-rollback') {
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
				$rollbackOk = 'Rollback to ' . $target['version'] . ' has started. Keep this page open without reloading or navigating away. Wait for the Rollback Completed confirmation, then select OK to return to the main dashboard.';
			}
		}
	}
} elseif($settingsSaveRequested) {
	$next = $config;
	$uploadError = '';
	$uploadedLogo = '';
	$postedSaveToken = (string)($_POST['settingsSaveCsrf'] ?? '');
	if(($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST')
		$saveError = 'Saving Settings requires a POST request.';
	elseif(!asrSettingsRollbackPostIsSameOrigin(true))
		$saveError = 'Saving Settings was blocked because the request origin could not be verified.';
	elseif($saveCsrfToken === '' || $postedSaveToken === '' || !hash_equals($saveCsrfToken, $postedSaveToken))
		$saveError = 'The Settings save confirmation was invalid. Reload this page and try again.';
	else
		$uploadedLogo = asrSettingsHandleLogoUpload($uploadError);
	$headerTitle = asrSettingsCleanText($_POST['headerTitle'] ?? '', 100);
	if($headerTitle === '')
		$headerTitle = '{CALLSIGN} | Node {NODE}';
	$logo = $uploadedLogo ? $uploadedLogo : asrSettingsCleanLogo($_POST['headerLogo'] ?? '');
	$requireLogin = !empty($_POST['requireLogin']);
	$requireAllScanLogin = !empty($_POST['requireAllScanLogin']);
	$maintainFriendlyNames = !empty($_POST['maintainFriendlyNames']);
	$announceStartupBridgeSummary = !empty($_POST['announceStartupBridgeSummary']);
	$announceNoConnectedBridges = $announceStartupBridgeSummary && !empty($_POST['announceNoConnectedBridges']);
	$lowPowerMode = !empty($_POST['lowPowerMode']);
	$filterServiceStations = !empty($_POST['filterServiceStations']);
	$filteredStationsError = '';
	$filteredStations = asrSettingsCleanFilteredStations($_POST['filteredStations'] ?? '', $filteredStationsError);

	if(!empty($saveError)) {
		// Authorization failed before any upload or settings mutation.
	} elseif($uploadError) {
		$saveError = $uploadError;
	} elseif($filteredStationsError !== '') {
		$saveError = $filteredStationsError;
	} elseif($logo === null) {
		$saveError = 'Header logo must be a local ASR path or an http/https URL.';
	} else {
		$bridgeError = '';
		$existingNonUrf = array_values(array_filter((array)($config['bridges'] ?? []), static fn($bridge) => !is_array($bridge) || empty($bridge['urfReflector'])));
		$bridges = asrSettingsBridgeRowsFromPost(
			$bridgeError,
			$existingNonUrf,
			$config['node'] ?? ''
		);

		$urf = $bridgeError === '' ? asrSettingsUrfConfigFromPost($bridgeError, $config) : [];
		$urfBridges = $bridgeError === '' ? asrSettingsUrfBridgesForSave($config, $urf) : [];

		if($bridgeError) {
			$saveError = $bridgeError;
		} else {
			$postedBridgeIds = is_array($_POST['bridgeId'] ?? null) ? $_POST['bridgeId'] : [];
			asrSettingsValidateOwnedBridgeMutations(
				$existingNonUrf, $bridges, $postedBridgeIds,
				$bridgeLifecyclePreviews, $saveError
			);
			$deletionPlan = $saveError === '' ? asrSettingsValidateDeletionPlan(
				$existingNonUrf, $bridges,
				(string)($_POST['bridgeDeletionConfirmations'] ?? ''),
				$bridgeLifecyclePreviews, $saveError
			) : null;
			if($saveError === '' && is_array($deletionPlan)) {
				foreach($deletionPlan['queue'] as $request) {
					$queueError = '';
					if(!asrSettingsQueueBridgeDeletion($request, $queueError)) {
						$saveError = $queueError;
						break;
					}
				}
			}
			if($saveError !== '') {
				$config = asrSettingsReadConfig();
			} else {
			$next['headerTitle'] = $headerTitle;
				$next['headerLogo'] = $logo;
				$next['brandByline'] = 'by KE7WIL';
				$next['footerLogo'] = asrSettingsWebPath('asr-logo-bright-r-tight.png');
				$next['requireLogin'] = $requireLogin;
				$next['maintainFriendlyNames'] = $maintainFriendlyNames;
			$next['announceStartupBridgeSummary'] = $announceStartupBridgeSummary;
			$next['announceNoConnectedBridges'] = $announceNoConnectedBridges;
			$next['lowPowerMode'] = $lowPowerMode;
			$next['filterServiceStations'] = $filterServiceStations;
			$next['filteredStations'] = $filteredStations;
			if((int)($config['dmrId'] ?? 0) > 0) $next['dmrId'] = (int)$config['dmrId'];
			$bridges = array_merge($bridges, $urfBridges);
			$next['bridges'] = $bridges;
			$saveError = '';
			$nextSecrets = $secrets;
			$nextSecrets['bridgeClientPasswords'] = is_array($nextSecrets['bridgeClientPasswords'] ?? null) ? $nextSecrets['bridgeClientPasswords'] : [];
			$postedPasswords = $_POST['bridgeClientPassword'] ?? [];
			$allowedSecretIds = [];
			foreach($bridges as $bridge) {
				if(($bridge['cardType'] ?? 'standard') === 'standard')
					$allowedSecretIds[$bridge['id']] = true;
			}
			// Unsaved/external rows may intentionally receive a new internal ID.
			// ASR-owned managed rows are locked by the mutation validator above.
			foreach($bridges as $index => $bridge) {
				if(($bridge['cardType'] ?? 'standard') !== 'standard') continue;
				$newSecretId = asrSettingsCleanBridgeId($bridge['id'] ?? '');
				$oldSecretId = asrSettingsCleanBridgeId($postedBridgeIds[$index] ?? '');
				$password = (string) ($postedPasswords[$index] ?? '');
				if($password === '' && $newSecretId !== '' && $oldSecretId !== ''
					&& $newSecretId !== $oldSecretId
					&& isset($nextSecrets['bridgeClientPasswords'][$oldSecretId]))
					$nextSecrets['bridgeClientPasswords'][$newSecretId] = $nextSecrets['bridgeClientPasswords'][$oldSecretId];
			}
			foreach(array_keys($nextSecrets['bridgeClientPasswords']) as $secretId) {
				if(!isset($allowedSecretIds[$secretId]))
					unset($nextSecrets['bridgeClientPasswords'][$secretId]);
			}
			$passwordCount = max(count($postedBridgeIds), count($postedPasswords));
			for($i = 0; $i < $passwordCount; $i++) {
				$secretId = asrSettingsCleanBridgeId($bridges[$i]['id'] ?? ($postedBridgeIds[$i] ?? ''));
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
				if(!$cfgModel->setStockRequireLogin($requireAllScanLogin))
					$saveError = 'The AllScan login policy could not be saved.';
				if($saveError === '' && asrSettingsWriteConfig($next, $saveError) && asrSettingsWriteSecrets($nextSecrets, $saveError)) {
					if($saveError === '') {
						if(is_executable('/usr/local/sbin/allscan-reimagined-friendly-names'))
							@shell_exec('sudo -n /usr/local/sbin/allscan-reimagined-friendly-names --once 2>/dev/null || /usr/local/sbin/allscan-reimagined-friendly-names --once 2>/dev/null');
						if(is_executable('/usr/local/sbin/allscan-reimagined-bridge-clients'))
							@shell_exec('sudo -n /usr/local/sbin/allscan-reimagined-bridge-clients --once 2>/dev/null || /usr/local/sbin/allscan-reimagined-bridge-clients --once 2>/dev/null');
						$reapplyOutput = [];
						$reapplyStatus = 1;
						$dockerReapply = '/usr/local/sbin/allscan-reimagined-docker-reapply';
						$dockerReapplyAvailable = is_executable($dockerReapply);
						$reapplyCommand = $dockerReapplyAvailable
							? 'sudo -n ' . escapeshellarg($dockerReapply) . ' 2>&1'
							: 'sudo -n /usr/bin/systemctl start allscan-reimagined-reapply.service 2>&1';
						exec($reapplyCommand, $reapplyOutput, $reapplyStatus);
						$config = $next;
						$secrets = $nextSecrets;
						if(function_exists('asrApplyAccessPolicy'))
							asrApplyAccessPolicy();
						if($reapplyStatus === 0) {
							$saveOk = true;
						} else {
							$reapplyHint = $dockerReapplyAvailable
								? ' Check the AllScan container logs before using bridge controls.'
								: ' Check allscan-reimagined-reapply.service before using bridge controls.';
							$saveError = 'Settings were saved, but ASR could not apply them.' . asrSettingsBridgeLifecycleFailureSummary() . $reapplyHint;
						}
				}
			}
			}
		}
	}
}

$modernCanSave = !$modernSettings || in_array($modernSettingsSection, ['appearance', 'bridges', 'lookup', 'access'], true);
$modernSaveLabels = [
	'appearance' => 'Save Appearance Settings',
	'bridges' => 'Save All Bridge Changes',
	'lookup' => 'Save Lookup & Map Settings',
	'access' => 'Save Access Settings',
];
$settingsSaveLabel = $modernSettings ? ($modernSaveLabels[$modernSettingsSection] ?? 'Save Settings') : SAVE_REIMAGINED_SETTINGS;
pageInit();
h1($modernSettings ? 'Settings' : 'Reimagined Settings');
if(!$modernSettings)
	echo '<div class="asr-settings-legacy-banner"><strong>Legacy Settings</strong><span>This is the previous Settings interface retained for evaluation and recovery.</span><a href="?view=modern">Return to redesigned Settings</a></div>';

if(!empty($saveOk)) {
	$savedSection = $modernSettings ? $modernSettingsSection : '';
	$saveMessages = [
		'appearance' => 'Appearance settings saved.',
		'bridges' => 'Bridge order and options saved.',
		'lookup' => 'Lookup and map settings saved.',
		'access' => 'Access settings saved.',
	];
	okMsg($saveMessages[$savedSection] ?? 'Settings saved.');
}
if(!empty($saveError))
	errMsg($saveError);
if(!empty($ysfImportOk))
	okMsg($ysfImportOk);
if(!empty($ysfImportError))
	errMsg($ysfImportError);
if(!empty($rollbackError))
	errMsg($rollbackError);
if(!empty($urfAdminOk)) okMsg($urfAdminOk);
if(!empty($urfAdminError)) errMsg($urfAdminError);

$requireLogin = !array_key_exists('requireLogin', $config) || !empty($config['requireLogin']);
$requireAllScanLogin = $cfgModel->stockRequireLogin();
$maintainFriendlyNames = !empty($config['maintainFriendlyNames']);
$announceStartupBridgeSummary = !empty($config['announceStartupBridgeSummary']);
$announceNoConnectedBridges = $announceStartupBridgeSummary && !empty($config['announceNoConnectedBridges']);
$lowPowerMode = !empty($config['lowPowerMode']);
$filterServiceStations = !array_key_exists('filterServiceStations', $config) || !empty($config['filterServiceStations']);
$filteredStations = asrSettingsCleanFilteredStations(implode("\n", (array)($config['filteredStations'] ?? [])));
$urfConfig = asrSettingsExistingUrfConfig($config);
$bridgeRows = array_values(array_filter(is_array($config['bridges'] ?? null) ? $config['bridges'] : [], static fn($bridge) => !is_array($bridge) || empty($bridge['urfReflector'])));
$bridgePasswords = is_array($secrets['bridgeClientPasswords'] ?? null) ? $secrets['bridgeClientPasswords'] : [];
$ysfCatalogStatuses = [];
foreach($bridgeRows as $bridge) {
	if(!is_array($bridge) || ($bridge['cardType'] ?? '') !== 'ysf_net')
		continue;
	$bridgeId = asrSettingsCleanBridgeId($bridge['id'] ?? '');
	if($bridgeId === '')
		continue;
	$statusError = '';
	$status = asrSettingsRunYsfCatalogHelper('status', $bridgeId, '', $statusError);
	$ysfCatalogStatuses[$bridgeId] = $status ?: [
		'ok' => false,
		'state' => 'unavailable',
		'count' => 0,
		'importedAt' => '',
		'error' => $statusError,
	];
}
$qrzSecrets = is_array($secrets['qrz'] ?? null) ? $secrets['qrz'] : [];
?>
<?php if($modernSettings): ?>
<div class="asr-settings-modern-shell" data-settings-category="<?php echo asrSettingsH($modernSettingsSection); ?>">
	<?php asrSettingsModernNav($modernSettingsSection); ?>
	<main class="asr-settings-modern-content">
		<header class="asr-settings-page-intro">
			<p class="asr-settings-eyebrow">Node configuration</p>
			<h2><?php echo asrSettingsH([
				'home'=>'Settings Home', 'appearance'=>'Appearance & Display', 'bridges'=>'Bridges',
				'lookup'=>'Lookup & Map', 'access'=>'Access & Administration',
				'system'=>'System & Recovery'
			][$modernSettingsSection]); ?></h2>
			<p><?php echo asrSettingsH([
				'home'=>'Review node configuration and go directly to the task you need.',
				'appearance'=>'Control how ASR looks and which station activity appears.',
				'bridges'=>'Configure bridge cards without changing active radio behavior until you save.',
				'lookup'=>'Manage QRZ lookup and map enrichment.',
				'access'=>'Control dashboard access and open administrator tools.',
				'system'=>'Check versions, update safely, or recover a previous installation.'
			][$modernSettingsSection]); ?></p>
		</header>
		<?php if($modernSettingsSection === 'home'): ?>
		<section class="asr-settings-overview" aria-label="Settings overview">
			<a class="asr-settings-overview-card" href="?section=bridges"><span>Digital bridges</span><strong><?php echo count($bridgeRows) + (!empty($urfConfig['enabled']) ? 1 : 0); ?> configured</strong><small>Cards, permissions, clients, and diagnostics</small></a>
			<a class="asr-settings-overview-card" href="?section=access"><span>Dashboard access</span><strong><?php echo $requireLogin ? 'Login required' : 'Public viewing'; ?></strong><small>Review access policy and administrator tools</small></a>
			<a class="asr-settings-overview-card" href="?section=system"><span>Installed version</span><strong><?php echo asrSettingsH($currentAsrVersion); ?></strong><small>Updates, backups, rollback, and recovery</small></a>
			<a class="asr-settings-overview-card" href="?section=appearance"><span>Node display</span><strong><?php echo $lowPowerMode ? 'Low-power mode' : 'Standard mode'; ?></strong><small>Branding, station filters, and display behavior</small></a>
		</section>
		<section class="asr-settings-common-tasks">
			<h3>Common tasks</h3>
			<div><a href="?section=bridges">Add or edit a bridge</a><a href="?section=appearance">Change the header</a><a href="?section=lookup">Configure QRZ lookup</a><a href="?section=system">Update or roll back ASR</a></div>
		</section>
		<?php endif; ?>
<?php endif; ?>
<div id="asrRollbackProgress" class="asr-rollback-progress" data-state="queued" role="status" aria-live="polite" aria-atomic="true"<?php echo empty($rollbackQueuedJobId) ? ' hidden' : ''; ?>>
	<strong id="asrRollbackProgressTitle">ROLLBACK IN PROGRESS — DO NOT LEAVE THIS PAGE</strong>
	<span id="asrRollbackProgressMessage">Keep this page open. Do not close it, reload it, use the browser Back button, or navigate elsewhere while the safety backup begins.</span>
</div>
<form id="asrReimaginedSettingsForm" class="asr-reimagined-settings-form" method="post" action="" enctype="multipart/form-data" data-max-bridges="<?php echo ASR_MAX_BRIDGES; ?>"<?php echo $modernSettings && $modernSettingsSection === 'home' ? ' hidden' : ''; ?>>
	<input type="hidden" name="ysfImportCsrf" value="<?php echo asrSettingsH($rollbackCsrfToken); ?>">
	<input type="hidden" name="settingsSaveCsrf" value="<?php echo asrSettingsH($saveCsrfToken); ?>">
	<input type="hidden" name="settingsAction" value="save-settings">
	<input id="asrBridgeDeletionConfirmations" type="hidden" name="bridgeDeletionConfirmations" value="[]">
	<p class="asr-reimagined-submit asr-reimagined-submit-top"<?php echo $modernCanSave ? '' : ' hidden'; ?>>
		<input type="submit" name="Submit" value="<?php echo asrSettingsH($settingsSaveLabel); ?>">
		<span>This action saves changes in the current Settings category.</span>
	</p>

	<section class="asr-settings-section" data-settings-section="header"<?php echo asrSettingsModernHidden('appearance', $modernSettingsSection); ?>>
		<div class="asr-settings-section-heading"><button class="asr-settings-section-toggle" type="button" aria-expanded="true">Header <span class="asr-settings-toggle-icon" aria-hidden="true">−</span></button></div>
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
	</section>

	<section class="asr-settings-section is-collapsed" data-settings-section="bridges"<?php echo asrSettingsModernHidden('bridges', $modernSettingsSection); ?>>
		<div class="asr-settings-section-heading"><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Bridge Cards <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></div>
		<div class="asr-settings-section-lead"><div><h3>Configured Bridges</h3><p>Select a bridge row to configure it. Reorder controls affect dashboard order.</p></div><button class="asr-settings-help-button" type="button" data-open-modal="asrBridgeHelpDialog">Bridge Setup Help</button></div>
		<div class="asr-urf-inline-panel" data-urf-panel<?php echo !empty($urfConfig['enabled']) ? '' : ' hidden'; ?>>
			<div class="asr-bridge-section-copy"><strong>URF Reflector</strong><span>One shared AllStar transport with mode cards for DMR, YSF, P25, NXDN, M17, and D-Star.</span></div>
			<input name="urfEnabled" type="hidden" value="<?php echo !empty($urfConfig['enabled']) ? '1' : '0'; ?>" data-urf-enabled>
			<div class="asr-settings-row"><label for="urfNode">Shared AllStar Transport Node</label><input id="urfNode" name="urfNode" type="text" inputmode="numeric" placeholder="1001" value="<?php echo asrSettingsH($urfConfig['node'] ?? ''); ?>"></div>
			<p class="asr-settings-inline-note">The shared node is transport health only; it never determines which URF mode is transmitting.</p>

			<div class="asr-urf-mode-list">
			<?php foreach(['dmr'=>'DMR','ysf'=>'YSF','p25'=>'P25','nxdn'=>'NXDN','m17'=>'M17'] as $urfMode=>$urfLabel): ?>
			<?php asrSettingsRenderUrfMode($urfMode, $urfLabel, (array)($urfConfig['modeConfig'][$urfMode] ?? []), in_array($urfMode,(array)($urfConfig['modes'] ?? []),true)); ?>

			<?php endforeach; ?>
			</div>
			<div class="asr-urf-destructive-actions"><span><strong>Remove reflector configuration</strong><small>This removes the aggregate URFWIL card when bridge changes are saved.</small></span><button type="button" class="asr-remove-urf-button">Remove URF Reflector</button></div>
			<?php if(!$modernSettings): ?>
			<div class="asr-settings-row"><label for="urfAdminRule">URF Access Control</label><input id="urfAdminRule" name="urfAdminRule" type="text" maxlength="16" placeholder="CALLSIGN or PREFIX*"></div>
			<div class="asr-settings-actions">
				<button type="submit" name="urfAdminAction" value="ban">Ban Callsign / Prefix</button>
				<button type="submit" name="urfAdminAction" value="unban">Unban Callsign / Prefix</button>
			</div>
			<?php if($urfAdminListError !== ''): ?>
			<p class="asr-settings-inline-note asr-settings-warning"><?php echo asrSettingsH($urfAdminListError); ?></p>
			<?php elseif(empty($urfAdminRules)): ?>
			<p class="asr-settings-inline-note">Current blacklist: none.</p>
			<?php else: ?>
			<div class="asr-settings-row"><label>Current Blacklist</label><div><?php foreach($urfAdminRules as $urfRule): ?><code><?php echo asrSettingsH($urfRule); ?></code> <?php endforeach; ?></div></div>
			<?php endif; ?>
			<p class="asr-settings-inline-note">URFD reloads blacklist changes automatically, normally within about 30 seconds. No URF credentials are required.</p>
			<?php endif; ?>
		</div>
		<div class="asr-bridge-behavior-options">
		<label class="asr-settings-check asr-option-row">
			<input name="maintainFriendlyNames" type="checkbox" value="1"<?php echo $maintainFriendlyNames ? ' checked' : ''; ?>>
			<span><strong>Maintain bridge names</strong><small>Keep configured bridge-node labels across updates and restarts.</small></span>
		</label>
		<label class="asr-settings-check asr-option-row">
			<input name="announceStartupBridgeSummary" type="checkbox" value="1"<?php echo $announceStartupBridgeSummary ? ' checked' : ''; ?>>
			<span><strong>Announce connected bridges after startup</strong><small>Reports configured Standard bridges that are actually linked.</small></span>
		</label>
		<label class="asr-settings-check asr-option-row">
			<input name="announceNoConnectedBridges" type="checkbox" value="1"<?php echo $announceNoConnectedBridges ? ' checked' : ''; ?>>
			<span><strong>Announce when none are connected</strong><small>Adds a short “No digital bridges connected” message to the startup summary.</small></span>
		</label>

		</div>
		<?php if($bridgeLifecycleError !== ''): ?><p class="asr-settings-inline-note asr-settings-warning"><?php echo asrSettingsH($bridgeLifecycleError); ?></p><?php endif; ?>

		<div class="asr-bridge-settings-table">
			<?php foreach($bridgeRows as $bridge): ?>
				<?php asrSettingsBridgePanel($bridge, $bridgePasswords, $ysfCatalogStatuses, $bridgeLifecyclePreviews); ?>
			<?php endforeach; ?>
		</div>
		<p class="asr-settings-inline-note">Saving applies every unsaved change on this Bridges page, including bridge settings, dashboard order, startup options, and confirmed removals. TGIF account sessions and YSF reflector-list imports are saved separately by their own buttons.</p>
		<p id="asr-bridge-order-status" class="asr-visually-hidden" aria-live="polite"></p>

		<button class="asr-setup-bridge-button" type="button"<?php echo $bridgeSetupAvailable ? '' : ' hidden'; ?>>+ Add Bridge</button>
		<button class="asr-add-urf-button" type="button"<?php echo !empty($urfConfig['enabled']) ? ' hidden' : ''; ?>>+ Add URF Reflector</button>
		<button class="asr-add-bridge-button" type="button">Add Manually Configured Bridge</button>
		<div class="asr-bridge-setup-panel" data-bridge-setup-panel hidden>
			<strong>Bridge Setup Wizard</strong>
			<p class="asr-settings-inline-note">Guided ASL3 provisioning uses Detect → Plan → Validate → Backup → Apply → Verify → Commit. Existing Asterisk configuration is protected and failed changes are rolled back.</p>
			<div class="asr-bridge-fields-grid">
				<label class="asr-setup-field asr-setup-field-short"><span>Mode</span><select data-bridge-setup-mode><option value="m17">M17</option><option value="p25">P25</option><option value="nxdn">NXDN</option><option value="ysf">YSF</option><option value="dmr">DMR</option><option value="dstar">D-Star</option><option value="zello">Zello</option></select></label>
				<label class="asr-setup-field asr-setup-field-medium"><span>Bridge Type</span><select data-bridge-setup-path><option value="standard">Standard Bridge</option><option value="net">Net Bridge</option><option value="urf">URF Reflector</option></select></label>
				<label class="asr-setup-field asr-setup-field-medium" data-bridge-setup-dmr hidden><span>DMR Network</span><select data-bridge-setup-dmr-network><option value="tgif">TGIF</option><option value="systemx">System X (FreeSTAR)</option><option value="amcomm">AmComm</option><option value="vkdmr">VKDMR</option><option value="freedmr">FreeDMR</option><option value="dmrplus">DMR+ / IPSC2</option><option value="custom">Custom DMR Network</option></select></label>
				<label class="asr-setup-field asr-setup-field-wide"><span>Card Title <small>(optional)</small></span><input data-bridge-setup-title type="text" maxlength="80" placeholder="Generated automatically"></label>
				<label class="asr-setup-field asr-setup-field-short" data-bridge-setup-node-field hidden><span>Private Bridge Node <small>(advanced override)</small></span><input data-bridge-setup-node type="text" inputmode="numeric" pattern="[0-9]+" maxlength="7" placeholder="Automatic"><small>Leave blank and ASR allocates an unused private node automatically.</small></label>
				<input data-bridge-setup-id type="hidden" value="m17">
				<?php if($setupCallsignDefault === 'SCRATCH'): ?>
				<label class="asr-setup-field asr-setup-field-medium"><span>Station Callsign</span><input data-bridge-setup-callsign type="text" maxlength="10" placeholder="KE7WIL" autocomplete="off"></label>
				<?php else: ?>
				<input data-bridge-setup-callsign type="hidden" value="<?php echo asrSettingsH($setupCallsignDefault); ?>">
				<?php endif; ?>
				<label class="asr-setup-field asr-setup-field-medium" data-bridge-setup-reflector-field><span data-bridge-setup-reflector-label>Fixed Reflector</span><input data-bridge-setup-reflector type="text" maxlength="9" placeholder="M17-WIL"></label>
				<label class="asr-setup-field asr-setup-field-short" data-bridge-setup-digital hidden><span data-bridge-setup-digital-label>DMR ID</span><input data-bridge-setup-digital-id type="number" min="1" max="9999999" placeholder="1234567" value="<?php echo $setupDmrIdDefault > 0 ? (int)$setupDmrIdDefault : ''; ?>"></label>
				<label class="asr-setup-field asr-setup-field-short" data-bridge-setup-digital hidden><span data-bridge-setup-destination-label>Talkgroup</span><input data-bridge-setup-destination type="text" inputmode="numeric" pattern="[0-9]+" maxlength="8" placeholder="64189"></label>
				<label class="asr-setup-field asr-setup-field-medium" data-bridge-setup-tgif-auth hidden><span>TGIF DMR Connection</span><select data-bridge-setup-tgif-auth-mode><option value="legacy">Legacy (no personal key)</option><option value="secured">Secured (hotspot key)</option></select><small>Your TGIF website callsign/password are separate from the DMR connection. Legacy uses your DMR ID without a personal key.</small></label>
				<label class="asr-setup-field asr-setup-field-credential" data-bridge-setup-dmr-username-field hidden><span>Network Username / Callsign</span><input data-bridge-setup-dmr-username type="text" maxlength="80" autocomplete="username" placeholder="Callsign or network username"></label>
				<label class="asr-setup-field asr-setup-field-credential" data-bridge-setup-tgif-key hidden><span>TGIF Hotspot Key <small>(not the website password)</small></span><input data-bridge-setup-tgif-password type="password" maxlength="128" autocomplete="new-password"></label>
				<label data-bridge-setup-zello hidden><span>Zello Username</span><input data-bridge-setup-zello-username type="text" maxlength="80" autocomplete="username"></label>
				<label data-bridge-setup-zello hidden><span>Zello Password</span><input data-bridge-setup-zello-password type="password" maxlength="256" autocomplete="new-password"></label>
				<label data-bridge-setup-zello hidden><span>Zello Channel</span><input data-bridge-setup-zello-channel type="text" maxlength="120"></label>
				<label data-bridge-setup-zello hidden><span>Developer Issuer</span><input data-bridge-setup-zello-issuer type="text" maxlength="160"></label>
				<label data-bridge-setup-zello hidden><span>WebSocket Endpoint</span><input data-bridge-setup-zello-ws-endpoint type="url" maxlength="253" value="wss://zello.io/ws"></label>
				<label data-bridge-setup-zello hidden><span>Developer Private Key (PEM)</span><textarea data-bridge-setup-zello-private-key rows="5" maxlength="16384" autocomplete="off"></textarea></label>
				<label class="asr-setup-field asr-setup-field-wide" data-bridge-setup-network><span>Server / Host (Advanced)</span><input data-bridge-setup-host type="text" maxlength="253" placeholder="m17.example.net"></label>
				<label class="asr-setup-field asr-setup-field-short" data-bridge-setup-network><span>Port (Advanced)</span><input data-bridge-setup-port type="number" min="1" max="65535" value="17000"></label>
				<label class="asr-setup-field asr-setup-field-short" data-bridge-setup-module-field><span>Module</span><input data-bridge-setup-module type="text" maxlength="1" value="A"></label>
			</div>
			<p class="asr-settings-inline-note" data-bridge-setup-note>Choose Standard for one fixed digital destination, or Net Bridge for an operator-selectable digital destination. ASR automatically allocates the private AllStar bridge node, ports, and service details. Create another Net Bridge only when you need simultaneous digital connections. Zello and D-Star are Standard only.</p>
			<p class="asr-settings-inline-note">Installing a new bridge briefly interrupts active AllStar connections while the node is registered.</p>
			<div class="asr-settings-actions"><button type="button" data-bridge-setup-preview>Check Setup</button><button type="button" data-bridge-setup-install disabled hidden>Install Bridge</button><button type="button" data-bridge-setup-cancel>Cancel</button></div>
			<div data-bridge-setup-output aria-live="polite" hidden></div>
		</div>
		<p class="asr-settings-inline-note">After saving bridge changes, refresh the main ASR page. If an old name remains, perform a hard refresh: Ctrl+Shift+R on Windows/Linux or Command+Shift+R on Mac. On a phone, close the ASR tab and reopen it.</p>
		<p class="asr-settings-help-action"><a class="asr-settings-help-button" href="<?php echo asrSettingsH(asrSettingsWebPath('asr-instructions/#bridge-cards')); ?>">Open Full Reimagined Help</a></p>

	</section>

	<section class="asr-settings-section is-collapsed" data-settings-section="station-filters"<?php echo asrSettingsModernHidden('appearance', $modernSettingsSection); ?>>
		<div class="asr-settings-section-heading"><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Station Display Filters <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></div>
		<label class="asr-settings-check asr-option-row">
			<input name="filterServiceStations" type="checkbox" value="1"<?php echo $filterServiceStations ? ' checked' : ''; ?>>
			<span>Hide service, probe, and filtered stations from bridge client and activity lists</span>
		</label>
		<p class="asr-settings-inline-note">ASR’s built-in list currently includes RFCKRD0, YSF-LIVE, KF0WSS, SRVPROB, and M7TEST. This changes display only; it does not kick or ban stations.</p>
		<div class="asr-settings-row">
			<label for="filteredStations">Custom Filtered Stations</label>
			<textarea id="filteredStations" name="filteredStations" rows="5" maxlength="1024" placeholder="One station per line"><?php echo asrSettingsH(implode("\n", $filteredStations)); ?></textarea>
		</div>
		<p class="asr-settings-inline-note">Add one exact station identity per line. Remove a station by deleting its line, then save Appearance Settings. Up to 64 custom entries are supported.</p>
	</section>

	<?php if($modernSettings): ?>
	<section class="asr-settings-section is-collapsed" data-settings-section="display-performance"<?php echo asrSettingsModernHidden('appearance', $modernSettingsSection); ?>>
		<div class="asr-settings-section-heading"><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Display Performance <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></div>
		<label class="asr-settings-check asr-option-row">
			<input name="lowPowerMode" type="checkbox" value="1"<?php echo $lowPowerMode ? ' checked' : ''; ?>>
			<span>Low-Power Node Mode</span>
		</label>
		<p class="asr-settings-inline-note">Reduces background work and disables animated themes for smaller nodes.</p>
	</section>
	<?php endif; ?>

	<section class="asr-settings-section is-collapsed" data-settings-section="bridge-help"<?php echo $modernSettings ? ' hidden' : ''; ?>>
		<div class="asr-settings-section-heading"><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Bridge Setup Help <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></div>
		<div class="asr-setup-help-grid">
			<section>
				<h2>Before Adding a Card</h2>
				<p>Use Add Bridge for guided installation of M17, P25, NXDN, YSF, DMR/TGIF, or Zello on a supported ASL3 host. The wizard detects the host, allocates a private bridge node and ports, previews the exact changes, installs the required runtime, verifies startup, and rolls back a failed apply. Add Existing Bridge Card is only for a bridge that is already installed and working.</p>
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
	</section>

	<section class="asr-settings-section is-collapsed" data-settings-section="bridge-diagnostics"<?php echo asrSettingsModernHidden('bridges', $modernSettingsSection); ?>>
		<div class="asr-settings-section-heading"><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Bridge Diagnostics <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></div>
		<p class="asr-settings-help">Read-only checks for bridge display, client collection, and common bridge service hints. The optional feed is an additional per-bridge JSON or API input; automatic bridge tracking can work without one.</p>
		<div id="asr-bridge-diagnostics" class="asr-bridge-diagnostics" data-loading="Loading bridge diagnostics...">Loading bridge diagnostics...</div>
	</section>

	<section class="asr-settings-section is-collapsed" data-settings-section="lookup-map"<?php echo asrSettingsModernHidden('lookup', $modernSettingsSection); ?>>
		<div class="asr-settings-section-heading"><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Lookup / Map <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></div>
		<p class="asr-settings-help">Optional QRZ credentials for lookup and map enrichment. These are stored only on the node and are not sent to the browser bundle.</p>
		<div class="asr-settings-secret-grid">
			<label><span>QRZ Username</span><input name="qrzUsername" type="text" value="<?php echo asrSettingsH($qrzSecrets['username'] ?? ''); ?>"></label>
			<label><span>QRZ Password</span><input name="qrzPassword" type="password" placeholder="<?php echo !empty($qrzSecrets['password']) ? 'Saved - leave blank to keep existing' : ''; ?>"></label>
		</div>
		<p class="asr-settings-inline-note">The password field stays blank after saving. Enter a new value only when changing it.</p>
	</section>

	<section class="asr-settings-section is-collapsed" data-settings-section="access"<?php echo asrSettingsModernHidden('access', $modernSettingsSection); ?>>
		<div class="asr-settings-section-heading"><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Access <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></div>
		<label class="asr-settings-check">
			<input name="requireLogin" type="checkbox" value="1"<?php echo $requireLogin ? ' checked' : ''; ?>>
			<span><strong>Require login to view ASR</strong><small>Protects the redesigned dashboard and ASR API.</small></span>
		</label>
		<label class="asr-settings-check">
			<input name="requireAllScanLogin" type="checkbox" value="1"<?php echo $requireAllScanLogin ? ' checked' : ''; ?>>
			<span><strong>Require login to view AllScan</strong><small>Controls the stock AllScan public-access permission.</small></span>
		</label>
		<?php if(!$modernSettings): ?>
		<label class="asr-settings-check">
			<input name="lowPowerMode" type="checkbox" value="1"<?php echo $lowPowerMode ? ' checked' : ''; ?>>
			<span>Low-Power Node Mode</span>
		</label>
		<p class="asr-settings-inline-note">Reduces background work and disables animated themes for smaller nodes.</p>
		<?php endif; ?>
	</section>

	<?php if($modernSettings): ?>
	<section class="asr-settings-section is-collapsed" data-settings-section="administrator-tools"<?php echo asrSettingsModernHidden('access', $modernSettingsSection); ?>>
		<div class="asr-settings-section-heading"><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Administrator Tools <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></div>
		<p class="asr-settings-help">User accounts and original AllScan configuration remain permission-controlled administrator tools.</p>
		<div class="asr-settings-link-grid">
			<button type="button" data-open-admin-modal="users"><strong>Users</strong><span>Add or edit permitted user accounts</span></button>
			<button type="button" data-open-admin-modal="config"><strong>Advanced Configuration</strong><span>Node identity, connection, and expert AllScan values</span></button>
			<button type="button" data-open-admin-modal="performance"><strong>Performance Stats</strong><span>Live, read-only node performance</span></button>
		</div>
	</section>

	<section class="asr-settings-section" data-settings-section="software-update"<?php echo asrSettingsModernHidden('system', $modernSettingsSection); ?>>
		<div class="asr-settings-section-heading"><button class="asr-settings-section-toggle" type="button" aria-expanded="true">Software Update <span class="asr-settings-toggle-icon" aria-hidden="true">−</span></button></div>
		<div class="asr-system-version-card"><span>Currently installed</span><strong><?php echo asrSettingsH($currentAsrVersion); ?></strong><small data-release-status>Checking update status…</small></div>
		<p class="asr-settings-help">Check for a verified ASR release and create a rollback backup automatically before installation.</p>
		<p class="asr-settings-help-action"><a class="asr-settings-help-button" href="<?php echo asrSettingsH(asrSettingsWebPath('?updateAsr=1')); ?>">Check for ASR Updates</a></p>
	</section>
	<?php endif; ?>

	<section class="asr-settings-section asr-rollback-section is-collapsed" data-settings-section="rollback"<?php echo asrSettingsModernHidden('system', $modernSettingsSection); ?>>
		<div class="asr-settings-section-heading"><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Roll Back ASR Version <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></div>
		<p class="asr-settings-help">Restore one of the five newest valid previous versions. Users, Favorites, the database, Settings, bridge configuration, map cache, and protected secrets are preserved.</p>
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

		<div class="asr-update-recovery-tools">
			<div><strong>Recovery Tools</strong><p>Use this only after a reboot, power loss, or crash interrupts an update.</p></div>
			<button id="asrUpdateRecoveryCheck" class="asr-update-recovery-button" type="button">Check for Interrupted Update</button>
			<span id="asrUpdateRecoveryStatus" class="asr-rollback-status" role="status" aria-live="polite"></span>
		</div>
		<p class="asr-settings-inline-note">Normal update failures are handled automatically. Detailed recovery instructions appear only when needed.</p>
		<p class="asr-rollback-warning"><strong>Important:</strong> After confirming a rollback, keep this page open. Do not reload it, close it, use the browser Back button, or navigate elsewhere. Wait for the <strong>Rollback Completed</strong> confirmation, then select <strong>OK</strong> to return to the main dashboard. Rollback has its own action, and unsaved Settings changes will not be saved.</p>

	</section>

	<p class="asr-reimagined-submit"<?php echo $modernCanSave ? '' : ' hidden'; ?>>
		<input type="submit" name="Submit" value="<?php echo asrSettingsH($settingsSaveLabel); ?>">
		<span>This action saves changes in the current Settings category.</span>
	</p>
</form>
<?php if($modernSettings): ?>
<div id="asrBridgeEditorDialog" class="asr-settings-modal" role="dialog" aria-modal="true" aria-labelledby="asrBridgeEditorTitle" hidden>
	<div class="asr-settings-modal-card asr-bridge-editor-modal-card"><header><div><span class="asr-settings-eyebrow">Bridge configuration</span><h2 id="asrBridgeEditorTitle">Configure Bridge</h2></div><button type="button" class="asr-modal-close" aria-label="Close bridge editor">×</button></header><div class="asr-settings-modal-body" data-bridge-editor-host></div><footer><span class="asr-modal-save-scope">Saves every unsaved change on the Bridges page.</span><button type="button" data-bridge-cancel>Cancel</button><button type="submit" form="asrReimaginedSettingsForm" name="Submit" value="<?php echo SAVE_REIMAGINED_SETTINGS; ?>" class="asr-primary-action">Save All Bridge Changes</button></footer></div>
</div>
<div id="asrBridgeHelpDialog" class="asr-settings-modal" role="dialog" aria-modal="true" aria-labelledby="asrBridgeHelpTitle" hidden>
	<div class="asr-settings-modal-card"><header><h2 id="asrBridgeHelpTitle">Bridge Setup Help</h2><button type="button" class="asr-modal-close" aria-label="Close bridge setup help">×</button></header><div class="asr-settings-modal-body asr-help-modal-content"><section><h3>Before adding a bridge</h3><p>The bridge software and its private AllStar node must already be installed. Start with the mode, role, and node number; leave service paths and ports alone unless the installer gave you different values.</p></section><div class="asr-mode-help-grid"><section><h3>URFWIL</h3><p>One reflector with separate DMR, YSF, P25, NXDN, and M17 destinations. You need only the modes you intend to connect.</p></section><section><h3>DMR / TGIF</h3><p>You need a DMR talkgroup. TGIF also needs your callsign, website password, and optional session talkgroup for authenticated session visibility.</p></section><section><h3>YSF</h3><p>You need the exact published reflector name or five-digit ID, such as US-KE7WIL-YSF. Import a host list only for a tunable YSF Net Bridge.</p></section><section><h3>P25 and NXDN</h3><p>You need the numeric destination or talkgroup assigned by that network. ASR manages controls only when the gateway backend is installed and verified.</p></section><section><h3>M17</h3><p>You need a reflector such as M17-WIL and its one-letter module. Host and port normally come from the reflector operator or installer.</p></section><section><h3>D-Star</h3><p>ASR shows evidence-backed reflector, gateway, and recent activity data. Reflector configuration remains in the installed D-Star gateway.</p></section><section><h3>Zello</h3><p>ASR shows the active or recent Zello talker when the external bridge exposes it. Zello sign-in and account management remain outside ASR.</p></section></div><section><h3>Standard, Net, and display-only</h3><p>A Standard bridge normally stays on one destination. A Net Bridge gives authorized operators supported destination controls. Display-only means ASR monitors an externally installed service but does not operate it.</p></section><p><a href="<?php echo asrSettingsH(asrSettingsWebPath('asr-instructions/#bridge-setup')); ?>" target="_blank" rel="noopener noreferrer">Open detailed setup reference in a new tab</a></p></div></div>
</div>
<div id="asrAdminDialog" class="asr-settings-modal" role="dialog" aria-modal="true" aria-labelledby="asrAdminDialogTitle" hidden>
	<div class="asr-settings-modal-card asr-admin-modal-card"><header><h2 id="asrAdminDialogTitle">Administrator Tool</h2><button type="button" class="asr-modal-close" aria-label="Close administrator tool">×</button></header><div class="asr-settings-modal-body" data-admin-modal-body></div></div>
</div>
<?php endif; ?>
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
		<p class="asr-rollback-dialog-warning"><strong>Keep this page open after starting the rollback.</strong> Do not reload it, close it, use the browser Back button, or navigate elsewhere. Wait for the <strong>Rollback Completed</strong> confirmation, then select <strong>OK</strong> to return to the main dashboard. Unsaved settings edits on this page will not be saved.</p>
		<div class="asr-rollback-dialog-actions">
			<button id="asrRollbackCancel" type="button">Cancel</button>
			<button id="asrRollbackConfirm" class="asr-rollback-button" type="button">Confirm Rollback</button>
		</div>
	</div>
</div>
<div id="asrRollbackCompleteDialog" class="asr-rollback-dialog" role="alertdialog" aria-modal="true" aria-labelledby="asrRollbackCompleteTitle" aria-describedby="asrRollbackCompleteMessage" hidden>
	<div class="asr-rollback-dialog-card asr-rollback-complete-card">
		<h2 id="asrRollbackCompleteTitle">Rollback Completed</h2>
		<p id="asrRollbackCompleteMessage"><strong id="asrRollbackCompletedVersion">The selected ASR version</strong> was restored successfully.</p>
		<p>The rollback is finished. Select OK to return to the main ASR dashboard.</p>
		<div class="asr-rollback-dialog-actions">
			<button id="asrRollbackCompleteOk" class="asr-rollback-complete-button" type="button">OK</button>
		</div>
	</div>
</div>
<?php if($modernSettings): ?>
	</main>
</div>
<?php endif; ?>
<template id="asr-bridge-row-template-progressive"><?php asrSettingsBridgePanel([], [], [], []); ?></template>
<script>
(function () {
	var asrBase = <?php echo json_encode(rtrim($urlbase, '/'), JSON_UNESCAPED_SLASHES); ?>;
	var form = document.querySelector('.asr-reimagined-settings-form');
	var table = document.querySelector('.asr-bridge-settings-table');
	var template = document.getElementById('asr-bridge-row-template-progressive');
	var addButton = document.querySelector('.asr-add-bridge-button');
	var setupButton = document.querySelector('.asr-setup-bridge-button');
	var setupPanel = document.querySelector('[data-bridge-setup-panel]');
	var setupCancel = document.querySelector('[data-bridge-setup-cancel]');
	var setupPreview = document.querySelector('[data-bridge-setup-preview]');
	var setupInstall = document.querySelector('[data-bridge-setup-install]');
	var setupOutput = document.querySelector('[data-bridge-setup-output]');
	var setupMode = document.querySelector('[data-bridge-setup-mode]');
	var setupPath = document.querySelector('[data-bridge-setup-path]');
	var setupFields = {
		setupBridgeId: document.querySelector('[data-bridge-setup-id]'),
		setupTitle: document.querySelector('[data-bridge-setup-title]'),
		setupBridgeNode: document.querySelector('[data-bridge-setup-node]'),
		setupCallsign: document.querySelector('[data-bridge-setup-callsign]'),
		setupReflector: document.querySelector('[data-bridge-setup-reflector]'),
		setupDigitalId: document.querySelector('[data-bridge-setup-digital-id]'),
		setupDestination: document.querySelector('[data-bridge-setup-destination]'),
		setupTgifAuthMode: document.querySelector('[data-bridge-setup-tgif-auth-mode]'),
		setupDmrUsername: document.querySelector('[data-bridge-setup-dmr-username]'),
		setupTgifPassword: document.querySelector('[data-bridge-setup-tgif-password]'),
		setupZelloUsername: document.querySelector('[data-bridge-setup-zello-username]'),
		setupZelloPassword: document.querySelector('[data-bridge-setup-zello-password]'),
		setupZelloChannel: document.querySelector('[data-bridge-setup-zello-channel]'),
		setupZelloIssuer: document.querySelector('[data-bridge-setup-zello-issuer]'),
		setupZelloWsEndpoint: document.querySelector('[data-bridge-setup-zello-ws-endpoint]'),
		setupZelloPrivateKey: document.querySelector('[data-bridge-setup-zello-private-key]'),
		setupHost: document.querySelector('[data-bridge-setup-host]'),
		setupPort: document.querySelector('[data-bridge-setup-port]'),
		setupModule: document.querySelector('[data-bridge-setup-module]')
	};
	var setupCallsign = setupFields.setupCallsign;
	var setupId = setupFields.setupBridgeId;
	var setupTitle = setupFields.setupTitle;
	var setupDmrNetwork = document.querySelector('[data-bridge-setup-dmr-network]');
	var setupTgifAuthMode = setupFields.setupTgifAuthMode;
	var setupPlanDigest = '';
	var addUrfButton = document.querySelector('.asr-add-urf-button');
	var urfPanel = document.querySelector('[data-urf-panel]');
	var urfEnabled = document.querySelector('[data-urf-enabled]');
	var deletionConfirmations = document.getElementById('asrBridgeDeletionConfirmations');
	var orderStatus = document.getElementById('asr-bridge-order-status');
	var draggedBridgeRow = null;
	var max = form ? parseInt(form.getAttribute('data-max-bridges') || '16', 10) : 16;
	var diagnosticsLoaded = false;
	var rollbackSelect = document.getElementById('asrRollbackSelect');
	var updateRecoveryCheck = document.getElementById('asrUpdateRecoveryCheck');
	var updateRecoveryStatus = document.getElementById('asrUpdateRecoveryStatus');
	var rollbackReview = document.getElementById('asrRollbackReview');
	var rollbackForm = document.getElementById('asrRollbackForm');
	var rollbackId = document.getElementById('asrRollbackId');
	var rollbackConfirmation = document.getElementById('asrRollbackConfirmation');
	var rollbackDialog = document.getElementById('asrRollbackDialog');
	var rollbackTargetVersion = document.getElementById('asrRollbackTargetVersion');
	var rollbackCancel = document.getElementById('asrRollbackCancel');
	var rollbackConfirm = document.getElementById('asrRollbackConfirm');
	var rollbackProgress = document.getElementById('asrRollbackProgress');
	var rollbackProgressTitle = document.getElementById('asrRollbackProgressTitle');
	var rollbackProgressMessage = document.getElementById('asrRollbackProgressMessage');
	var rollbackCompleteDialog = document.getElementById('asrRollbackCompleteDialog');
	var rollbackCompleteOk = document.getElementById('asrRollbackCompleteOk');
	var rollbackCompletedVersion = document.getElementById('asrRollbackCompletedVersion');
	var rollbackFocusReturn = null;
	var pendingRollbackId = '';
	var rollbackJobId = <?php echo json_encode((string) ($rollbackQueuedJobId ?? ''), JSON_UNESCAPED_SLASHES); ?>;
	var rollbackQueuedVersion = <?php echo json_encode((string) ($rollbackQueuedVersion ?? ''), JSON_UNESCAPED_SLASHES); ?>;
	var rollbackInProgress = !!rollbackJobId && /^\d{8}-\d{6}-[a-f0-9]{8}$/.test(rollbackJobId);
	var tgifState = null;

	if(updateRecoveryCheck) updateRecoveryCheck.addEventListener('click', function() {
		updateRecoveryCheck.disabled = true;
		if(updateRecoveryStatus) updateRecoveryStatus.textContent = 'Checking for an interrupted update…';
		fetch(asrBase + '/asr-api.php?action=update-recover', {method:'POST', credentials:'same-origin', cache:'no-store', headers:{'X-ASR-Requested-With':'asr-update-control'}})
			.then(function(response) { return response.json().then(function(data) { if(!response.ok || data.ok === false) throw new Error(data.error || 'Recovery check failed.'); return data; }); })
			.then(function(data) { if(updateRecoveryStatus) updateRecoveryStatus.textContent = data.status === 'nothing_to_recover' ? 'No interrupted update needs recovery.' : 'Interrupted update recovery check finished.'; })
			.catch(function(error) { if(updateRecoveryStatus) updateRecoveryStatus.textContent = error && error.message ? error.message : 'Recovery check failed.'; })
			.finally(function() { updateRecoveryCheck.disabled = false; });
	});

	function tgifRequest(action, options) {
		return fetch(asrBase + '/asr-api.php?action=' + encodeURIComponent(action), options || {credentials:'same-origin', cache:'no-store'})
			.then(function(response) { return response.json().then(function(data) {
				if(!response.ok || data.ok === false) throw new Error(data.error || 'TGIF request failed.');
				return data;
			}); });
	}
	function renderTgifState(data) {
		tgifState = data || {};
		var configured = !!tgifState.configured;
		var count = Array.isArray(tgifState.clients) ? tgifState.clients.length : 0;
		var sessionState = configured ? (tgifState.stale ? 'Session status: Unable to verify' : 'Session: Current') : '';
		var label = configured ? ('TGIF Account: Configured · ' + (tgifState.callsign || 'TGIF user') + ' · ' + sessionState + ' · ' + count + ' connected session' + (count === 1 ? '' : 's')) : 'TGIF Account: Not configured';
		if(tgifState.error) label += ' · ' + tgifState.error;
		document.querySelectorAll('.asr-dmr-tgif-settings').forEach(function(section) {
			var status = section.querySelector('[data-tgif-status]');
			var callsign = section.querySelector('[data-tgif-callsign]');
			var talkgroup = section.querySelector('[data-tgif-talkgroup]');
			var logout = section.querySelector('[data-tgif-logout]');
			var captchaWrap = section.querySelector('[data-tgif-captcha-wrap]');
			var captchaImage = section.querySelector('[data-tgif-captcha-image]');
			if(status) { status.textContent = label; status.classList.toggle('is-error', !!tgifState.error); }
			if(captchaWrap) captchaWrap.hidden = !tgifState.captchaRequired;
			if(captchaImage && tgifState.captchaRequired && tgifState.captchaUrl) captchaImage.src = tgifState.captchaUrl;
			if(callsign && !callsign.value && tgifState.callsign) callsign.value = tgifState.callsign;
			if(talkgroup && !talkgroup.value && tgifState.talkgroup) talkgroup.value = tgifState.talkgroup;
			if(logout) logout.disabled = !configured;
		});
	}
	function refreshUrfModeSummary(row) {
		if(!row) return;
		var mode = row.getAttribute('data-urf-mode') || '';
		var value = function(key) { var input = row.querySelector('input[name$="[' + key + ']"]'); return input ? input.value.trim() : ''; };
		var parts = [];
		if(mode === 'dmr') { if(value('network') || value('talkgroup')) parts.push(value('network') || 'TGIF'); if(value('talkgroup')) parts.push('TG ' + value('talkgroup')); }
		else if(mode === 'ysf') { if(value('reflector')) parts.push(value('reflector')); if(value('reflectorId')) parts.push('ID ' + value('reflectorId')); }
		else if(mode === 'p25' || mode === 'nxdn') { if(value('destination')) parts.push('Destination ' + value('destination')); if(value('module')) parts.push(value('module')); }
		else { if(value('reflector')) parts.push(value('reflector')); if(value('module')) parts.push('Module ' + value('module')); }
		var summary = row.querySelector('.asr-settings-check small');
		if(summary) summary.textContent = parts.length ? parts.join(' · ') : 'Destination not configured';
	}
	function refreshTgifState() {
		if(!document.querySelector('.asr-dmr-tgif-settings')) return;
		tgifRequest('tgif-user-status').then(renderTgifState).catch(function(error) {
			renderTgifState({configured:false, error:error.message || 'TGIF status unavailable.'});
		});
	}

	function setSectionExpanded(section, expanded) {
		if(!section) return;
		var button = section.querySelector('.asr-settings-section-toggle');
		var icon = button ? button.querySelector('.asr-settings-toggle-icon') : null;
		section.classList.toggle('is-collapsed', !expanded);
		if(button) button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
		if(icon) icon.hidden = true;
		if(expanded && section.getAttribute('data-settings-section') === 'bridge-diagnostics') loadBridgeDiagnostics();
	}
	function setBridgeExpanded(row, expanded) {
		if(!row) return;
		var button = row.querySelector('.asr-bridge-toggle');
		var icon = button ? button.querySelector('.asr-settings-toggle-icon') : null;
		row.classList.toggle('is-collapsed', !expanded);
		if(button) button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
		if(icon) icon.textContent = expanded ? 'v' : '>';
	}
	function rows() {
		return Array.prototype.slice.call(document.querySelectorAll('.asr-bridge-settings-row'));
	}
	function bridgeRowName(row) {
		var name = row ? row.querySelector('.asr-bridge-panel-name') : null;
		return name && name.textContent.trim() ? name.textContent.trim() : 'Bridge';
	}
	function confirmBridgeDeletion(row) {
		var savedId = row ? (row.getAttribute('data-saved-bridge-id') || '').trim() : '';
		if(!savedId) return true;
		var ownershipState = (row.getAttribute('data-ownership-state') || 'unknown').trim();
		if(ownershipState === 'unknown') {
			window.alert('ASR cannot verify who manages this bridge. Reload Settings after the lifecycle service is available; removal remains disabled.');
			return false;
		}
		var preview = {};
		try { preview = JSON.parse(row.getAttribute('data-delete-preview') || '{}'); } catch(error) { return false; }
		var message = 'Remove “' + bridgeRowName(row) + '” from ASR?\n\n';
		if(ownershipState === 'owned' && preview.owned) {
			message += 'ASR WILL REMOVE after Save:\n';
			(preview.resources || []).forEach(function(resource) { message += '• ' + resource + '\n'; });
			message += '\nASR WILL NOT TOUCH:\n';
			(preview.willNotTouch || []).forEach(function(item) { message += '• ' + item + '\n'; });
			message += '\nIf any cleanup step fails, ASR will preserve the ownership record, report what remains, and retry on the next reapply.';
		} else {
			message += 'ASR WILL REMOVE:\n• This ASR card and ASR-generated card status/cache data\n\nASR WILL NOT TOUCH:\n• External/manual bridge services\n• Asterisk configuration\n• Files and directories\n• Firewall rules and ports\n• Shared software and system packages\n\nASR has no ownership manifest, so it will not assume ownership of any matching resource.';
		}
		if(!window.confirm(message)) return false;
		if(deletionConfirmations) {
			var values = [];
			try { values = JSON.parse(deletionConfirmations.value || '[]'); } catch(error) { values = []; }
			values = values.filter(function(item) { return item && item.bridgeId !== savedId; });
			if(ownershipState === 'owned') {
				values.push({
					bridgeId: savedId,
					creationId: preview.creationId,
					manifestDigest: preview.manifestDigest,
					deletionToken: preview.deletionToken,
					owned: true
				});
			} else {
				values.push({bridgeId: savedId, owned: false});
			}
			deletionConfirmations.value = JSON.stringify(values);
		}
		return true;
	}
	function updateBridgeOrderControls() {
		var bridgeRows = rows();
		bridgeRows.forEach(function (row, index) {
			var up = row.querySelector('.asr-bridge-move-up');
			var down = row.querySelector('.asr-bridge-move-down');
			if(up) up.disabled = index === 0;
			if(down) down.disabled = index === bridgeRows.length - 1;
			row.setAttribute('data-bridge-position', String(index + 1));
		});
	}
	function announceBridgeOrder(row) {
		if(!orderStatus || !row) return;
		var bridgeRows = rows();
		var position = bridgeRows.indexOf(row) + 1;
		orderStatus.textContent = bridgeRowName(row) + ' moved to position ' + position + ' of ' + bridgeRows.length + '. Use Save All Bridge Changes to keep this order.';
	}
	function moveBridgeRow(row, direction) {
		if(!table || !row) return;
		var sibling = direction < 0 ? row.previousElementSibling : row.nextElementSibling;
		if(!sibling || !sibling.classList.contains('asr-bridge-settings-row')) return;
		if(direction < 0) table.insertBefore(row, sibling);
		else table.insertBefore(sibling, row);
		updateBridgeOrderControls();
		announceBridgeOrder(row);
		var focusTarget = row.querySelector(direction < 0 ? '.asr-bridge-move-up' : '.asr-bridge-move-down');
		if(focusTarget) focusTarget.focus();
	}
	function refreshBridgeTitle(row) {
		var title = row.querySelector('input[name="bridgeTitle[]"]');
		var id = row.querySelector('input[name="bridgeId[]"]');
		var mode = row.querySelector('select[name="bridgeMode[]"]');
		var node = row.querySelector('input[name="bridgeNode[]"]');
		var cardType = row.querySelector('select[name="bridgeCardType[]"]');
		var name = row.querySelector('.asr-bridge-panel-name');
		var summary = row.querySelector('.asr-bridge-panel-summary');
		if(!name) return;
		var text = title && title.value.trim() ? title.value.trim() : '';
		var isUnsaved = !id || !id.value.trim();
		var modeLabel = mode && mode.value === 'zello'
			? 'Zello'
			: mode && mode.value === 'dstar'
				? 'D-Star'
				: (mode ? mode.value.toUpperCase() : '');
		if(!text && !isUnsaved && modeLabel) text = modeLabel + (cardType && cardType.value === 'net' ? ' Net Bridge' : ' Bridge');
		name.textContent = text || 'New Digital Bridge';
		if(summary) summary.textContent = 'Node ' + (node && node.value.trim() ? node.value.trim() : 'not set') + ' · Bridge card, Connection Status name, and optional connected-client source.';
	}
		function refreshBridgeTitles() {
			rows().forEach(refreshBridgeTitle);
		}
		function refreshBridgeType(row) {
			var select = row.querySelector('select[name="bridgeCardType[]"]');
			var mode = row.querySelector('select[name="bridgeMode[]"]');
			var backend = row.querySelector('select[name="bridgeBackendMode[]"]');
			var standardSettings = row.querySelector('.asr-standard-bridge-settings');
			var tgifSettings = row.querySelector('.asr-standard-dmr-tgif');
			var backendChoice = row.querySelector('.asr-backend-choice-section');
			var destinationSettings = row.querySelector('.asr-destination-permission-section');
			var dmrSettings = row.querySelector('.asr-dmr-net-settings');
			var ysfSettings = row.querySelector('.asr-ysf-net-settings');
			var nextDigitalSettings = row.querySelector('.asr-next-digital-settings');
			var dstarStatusSettings = row.querySelector('.asr-dstar-status-settings');
			var clientSettings = row.querySelector('.asr-connected-client-settings');
			var fixedRecovery = row.querySelector('[data-fixed-recovery-checkbox]');
			var fixedRecoveryValue = row.querySelector('input[name="bridgeFixedRecovery[]"]');
			var netOption = select ? select.querySelector('option[value="net"]') : null;
			var standardOnlyMode = !!mode && (mode.value === 'dstar' || mode.value === 'zello');
			if(netOption) netOption.disabled = standardOnlyMode;
			if(standardOnlyMode && select && select.value !== 'standard') {
				select.value = 'standard';
				select.setAttribute('aria-description', (mode.value === 'dstar' ? 'D-Star' : 'Zello') + ' is Standard-only; Net Bridge is unavailable.');
			}
			var isStandard = !select || select.value === 'standard';
			var currentMode = mode ? mode.value : 'dmr';
			var isNextDigitalMode = currentMode === 'p25' || currentMode === 'nxdn' || currentMode === 'm17';
			var isManaged = !backend || backend.value === 'managed' || !isStandard;
			if(standardSettings) standardSettings.hidden = !isStandard;
			if(tgifSettings) tgifSettings.hidden = !isStandard || currentMode !== 'dmr';
			if(backendChoice) backendChoice.hidden = !isStandard || !isNextDigitalMode;
			if(destinationSettings) destinationSettings.hidden = !(!isStandard || (isNextDigitalMode && isManaged));
			if(!isStandard) {
				if(fixedRecovery) fixedRecovery.checked = false;
				if(fixedRecoveryValue) fixedRecoveryValue.value = '0';
			}
			if(dmrSettings) dmrSettings.hidden = isStandard || currentMode !== 'dmr';
			if(ysfSettings) ysfSettings.hidden = isStandard || currentMode !== 'ysf';
			if(nextDigitalSettings) nextDigitalSettings.hidden = !isNextDigitalMode || !isManaged;
			if(dstarStatusSettings) dstarStatusSettings.hidden = !isStandard || currentMode !== 'dstar';
			if(clientSettings) clientSettings.dataset.bridgeTabLabel = currentMode === 'zello' ? 'Recent Talkers' : 'Client Data';
			row.querySelectorAll('.asr-digital-instance-field').forEach(function(field) {
				field.hidden = !isNextDigitalMode || currentMode === 'm17';
			});
			row.querySelectorAll('.asr-m17-field').forEach(function(field) {
				field.hidden = currentMode !== 'm17' || (field.classList.contains('asr-digital-fixed-field') && !isStandard);
			});
			row.querySelectorAll('.asr-digital-fixed-field:not(.asr-m17-field)').forEach(function(field) {
				field.hidden = !isNextDigitalMode || currentMode === 'm17' || !isStandard;
			});
			row.querySelectorAll('.asr-nxdn-emulator-field').forEach(function(field) {
				field.hidden = currentMode !== 'nxdn';
			});
			row.querySelectorAll('.asr-approved-destinations-field').forEach(function(field) {
				field.hidden = isStandard || currentMode === 'dmr' || currentMode === 'ysf';
			});
			row.querySelectorAll('.asr-ysf-allow-tune-field').forEach(function(field) {
				field.hidden = isStandard || currentMode !== 'ysf';
			});
			row.querySelectorAll('.asr-detail-title-field').forEach(function(field) {
				field.hidden = !isStandard;
			});
			if(clientSettings) clientSettings.hidden = !isStandard || currentMode === 'dstar' || currentMode === 'dmr';
			refreshClientSource(row);
			refreshBridgeTitle(row);
		}
		function refreshClientSource(row) {
			var source = row.querySelector('select[name="bridgeClientSource[]"]');
			var custom = source && (source.value === 'local_json' || source.value === 'http_api');
			row.querySelectorAll('.asr-custom-client-source-field').forEach(function(field) {
				field.hidden = !custom;
			});
			row.querySelectorAll('.asr-http-client-source-field').forEach(function(field) {
				field.hidden = !source || source.value !== 'http_api';
			});
		}
		function refreshBridgeTypes() {
			rows().forEach(refreshBridgeType);
		}
	function updateAddButton() {
		if(addButton) addButton.disabled = rows().length >= max;
	}
	function finishBridgeDrag() {
		if(draggedBridgeRow) {
			draggedBridgeRow.classList.remove('is-dragging');
			announceBridgeOrder(draggedBridgeRow);
		}
		draggedBridgeRow = null;
		if(table) table.classList.remove('is-reordering');
		updateBridgeOrderControls();
	}
	if(table) {
		table.addEventListener('dragstart', function (event) {
			var handle = event.target && event.target.closest ? event.target.closest('.asr-bridge-drag-handle') : null;
			if(!handle) return;
			draggedBridgeRow = handle.closest('.asr-bridge-settings-row');
			if(!draggedBridgeRow) return;
			draggedBridgeRow.classList.add('is-dragging');
			table.classList.add('is-reordering');
			if(event.dataTransfer) {
				event.dataTransfer.effectAllowed = 'move';
				event.dataTransfer.setData('text/plain', bridgeRowName(draggedBridgeRow));
			}
		});
		table.addEventListener('dragover', function (event) {
			if(!draggedBridgeRow) return;
			var target = event.target && event.target.closest ? event.target.closest('.asr-bridge-settings-row') : null;
			if(!target || target === draggedBridgeRow) return;
			event.preventDefault();
			if(event.dataTransfer) event.dataTransfer.dropEffect = 'move';
			var bounds = target.getBoundingClientRect();
			var insertAfter = event.clientY > bounds.top + bounds.height / 2;
			table.insertBefore(draggedBridgeRow, insertAfter ? target.nextElementSibling : target);
			updateBridgeOrderControls();
		});
		table.addEventListener('drop', function (event) {
			if(!draggedBridgeRow) return;
			event.preventDefault();
			finishBridgeDrag();
		});
		table.addEventListener('dragend', finishBridgeDrag);
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
			var readinessTarget = document.querySelector('[data-bridge-readiness-id="' + String(bridge.id || '').replace(/[^a-z0-9_-]/g, '') + '"]');
			if(readinessTarget && bridge.readiness) {
				readinessTarget.setAttribute('data-readiness-state', String(bridge.readiness.state || 'unknown'));
				var readinessTitle = readinessTarget.querySelector('strong');
				var readinessCopy = readinessTarget.querySelector('span');
				if(readinessTitle) readinessTitle.textContent = bridge.readiness.ready ? 'Working' : (bridge.readiness.state === 'display_only' ? 'Monitoring only' : 'Needs attention');
				if(readinessCopy) readinessCopy.textContent = String(bridge.readiness.summary || 'Readiness unavailable.');
			}
			var serviceList = Array.isArray(bridge.services) ? bridge.services : [];
			var activeServices = serviceList.filter(function (service) {
				return String(service.state || '') === 'active' || String(service.state || '').indexOf('active running') !== -1;
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
					: 'Automatic detection';
			var sourceStatusLabel = configuredSource === 'disabled'
				? 'Automatic detection'
				: String(source.status || 'unknown');
			var warnings = Array.isArray(bridge.warnings) && bridge.warnings.length
				? '<div class="asr-diagnostics-warning">' + bridge.warnings.map(escapeHtml).join('<br>') + '</div>'
				: '';
			var dmr = bridge.dmrUdp ? '<div class="asr-diagnostics-block"><h3>DMR Network</h3><div class="asr-diagnostics-mini"><span>Local UDP: <strong>' + escapeHtml(bridge.dmrUdp.localPort || 'unknown') + '</strong></span><span>Master: <strong>' + escapeHtml((bridge.dmrUdp.master || 'unknown') + (bridge.dmrUdp.masterPort ? ':' + bridge.dmrUdp.masterPort : '')) + '</strong></span><span>Listener: <strong>' + escapeHtml(bridge.dmrUdp.listener || 'unknown') + '</strong></span></div></div>' : '';
			var tgif = bridge.tgif ? '<div class="asr-diagnostics-block"><h3>TGIF Client Tracking</h3><div class="asr-diagnostics-mini">'
				+ '<span>Mode: <strong>Per-user authentication</strong></span>'
				+ '<span>Refresh timer: <strong>' + escapeHtml((bridge.tgif.refreshTimer || {}).state || 'unknown') + '</strong></span>'
				+ '<span>This account: <strong>' + escapeHtml(bridge.tgif.currentUserConfigured ? ('signed in' + (bridge.tgif.currentUserCallsign ? ' as ' + bridge.tgif.currentUserCallsign : '')) : 'not signed in') + '</strong></span>'
				+ '<span>Clients visible to this account: <strong>' + escapeHtml(bridge.tgif.currentUserClientCount || 0) + '</strong></span>'
				+ '</div></div>' : '';
			var readiness = bridge.readiness || {};
			var missing = Array.isArray(readiness.missing) && readiness.missing.length
				? '<ul>' + readiness.missing.map(function(item) { return '<li>' + escapeHtml(item) + '</li>'; }).join('') + '</ul>'
				: '';
			html += '<section class="asr-diagnostics-bridge">'
				+ '<h2>' + escapeHtml(bridge.title || bridge.id || 'Bridge') + '</h2>'
				+ '<div class="asr-diagnostics-block"><h3>Can ASR operate this bridge?</h3><strong>' + escapeHtml(readiness.summary || 'ASR could not determine the bridge state. Check the service and AllStar link.') + '</strong>' + missing + '</div>'
				+ '<div class="asr-diagnostics-block"><h3>Bridge Link</h3>'
				+ '<div class="asr-diagnostics-mini">'
				+ '<span>ID: <strong>' + escapeHtml(bridge.id || '') + '</strong></span>'
				+ '<span>Node: <strong>' + escapeHtml(bridge.node || '') + '</strong></span>'
				+ '<span>AllStar link: <strong>' + escapeHtml(bridge.linked || 'unknown') + '</strong></span>'
				+ '</div></div>'
				+ '<div class="asr-diagnostics-block"><h3>Client or talker data</h3>'
				+ '<div class="asr-diagnostics-mini">'
				+ '<span>Optional feed: <strong>' + escapeHtml(sourceLabel) + '</strong></span>'
				+ '<span>Feed status: <strong>' + escapeHtml(sourceStatusLabel) + '</strong></span>'
				+ '<span>Current records: <strong>' + escapeHtml(bridge.clientCount || 0) + '</strong></span>'
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
	function setRollbackProgress(state, title, message) {
		if(!rollbackProgress) return;
		rollbackProgress.hidden = false;
		rollbackProgress.setAttribute('data-state', state);
		if(rollbackProgressTitle) rollbackProgressTitle.textContent = title;
		if(rollbackProgressMessage) rollbackProgressMessage.textContent = message;
	}
	function showRollbackCompleteDialog() {
		rollbackInProgress = false;
		setRollbackProgress(
			'succeeded',
			'ROLLBACK COMPLETED',
			'The selected ASR version was restored. Select OK in the confirmation box to return to the main dashboard.'
		);
		if(rollbackCompletedVersion)
			rollbackCompletedVersion.textContent = rollbackQueuedVersion || 'The selected ASR version';
		if(!rollbackCompleteDialog) {
			window.location.assign(asrBase + '/');
			return;
		}
		rollbackCompleteDialog.hidden = false;
		document.body.classList.add('asr-rollback-dialog-open');
		if(rollbackCompleteOk) rollbackCompleteOk.focus();
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
					if(state === 'queued')
						setRollbackProgress('queued', 'ROLLBACK IN PROGRESS — DO NOT LEAVE THIS PAGE', 'Keep this page open. Do not close it, reload it, use the browser Back button, or navigate elsewhere while the safety backup begins.');
					else if(state === 'running')
						setRollbackProgress('running', 'ROLLBACK IN PROGRESS — DO NOT LEAVE THIS PAGE', 'Keep this page open while ASR restores ' + (rollbackQueuedVersion || 'the selected version') + '. Do not close, reload, go back, or navigate away.');
					else if(state === 'succeeded')
						setRollbackProgress('succeeded', 'ROLLBACK COMPLETED', 'The selected ASR version was restored. Preparing the completion confirmation…');
					else if(state === 'failed')
						setRollbackProgress('failed', 'ROLLBACK FAILED', 'The previous installation was restored. Reopen ASR and verify the installed version before trying again.');
					else
						setRollbackProgress('running', 'ROLLBACK IN PROGRESS — DO NOT LEAVE THIS PAGE', 'Checking rollback status. Keep this page open and do not reload or navigate away.');
					if(state === 'succeeded') {
						showRollbackCompleteDialog();
						return;
					}
					if(state === 'failed') {
						rollbackInProgress = false;
						return;
					}
					window.setTimeout(poll, 2000);
				})
				.catch(function () {
					if(attempts < 450)
						window.setTimeout(poll, 2000);
					else
						setRollbackProgress('failed', 'ROLLBACK STATUS COULD NOT BE CONFIRMED', 'Reopen ASR and verify the installed version before trying another rollback.');
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
	if(rollbackCompleteOk) {
		rollbackCompleteOk.addEventListener('click', function () {
			rollbackCompleteOk.disabled = true;
			rollbackCompleteOk.textContent = 'Returning to Dashboard…';
			document.body.classList.remove('asr-rollback-dialog-open');
			window.location.assign(asrBase + '/');
		});
	}
	window.addEventListener('beforeunload', function (event) {
		if(!rollbackInProgress) return;
		event.preventDefault();
		event.returnValue = '';
	});
	document.addEventListener('input', function (event) {
		if(event.target && /^urfConfig\[/.test(event.target.name || '')) refreshUrfModeSummary(event.target.closest('.asr-urf-mode-row'));
		if(event.target && (event.target.name === 'bridgeTitle[]' || event.target.name === 'bridgeNode[]')) {
			var row = event.target.closest('.asr-bridge-settings-row');
			if(row) refreshBridgeTitle(row);
		}
	});
	document.addEventListener('change', function (event) {
		if(event.target && event.target.name === 'urfModes[]') {
			var modeRow = event.target.closest('.asr-urf-mode-row');
			if(modeRow) modeRow.classList.toggle('is-disabled', !event.target.checked);
		}
		if(event.target && event.target.name === 'urfModes[]' && event.target.value === 'dmr') {
			var urfPanel = event.target.closest('[data-urf-panel]');
			var urfTgif = urfPanel ? urfPanel.querySelector('[data-urf-tgif-account]') : null;
			if(urfTgif) urfTgif.hidden = !event.target.checked;
		}
		if(event.target && event.target.hasAttribute('data-fixed-recovery-checkbox')) {
			var recoveryRow = event.target.closest('.asr-bridge-settings-row');
			var recoveryValue = recoveryRow ? recoveryRow.querySelector('input[name="bridgeFixedRecovery[]"]') : null;
			if(recoveryValue) recoveryValue.value = event.target.checked ? '1' : '0';
		}
		if(event.target && (event.target.name === 'bridgeCardType[]' || event.target.name === 'bridgeMode[]' || event.target.name === 'bridgeBackendMode[]')) {
			var row = event.target.closest('.asr-bridge-settings-row');
			if(row) refreshBridgeType(row);
		}
		if(event.target && event.target.name === 'bridgeClientSource[]') {
			var sourceRow = event.target.closest('.asr-bridge-settings-row');
			if(sourceRow) refreshClientSource(sourceRow);
		}
	});

	document.addEventListener('input', function(event) {
		if(event.target.matches('[data-tgif-callsign]')) event.target.value = event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 10);
		if(event.target.matches('[data-tgif-talkgroup]')) event.target.value = event.target.value.replace(/\D/g, '').slice(0, 8);
	});
	document.addEventListener('click', function(event) {
		var login = event.target.closest('[data-tgif-login]');
		if(login) {
			event.preventDefault();
			var section = login.closest('.asr-dmr-tgif-settings');
			var callsign = section.querySelector('[data-tgif-callsign]');
			var password = section.querySelector('[data-tgif-password]');
			var talkgroup = section.querySelector('[data-tgif-talkgroup]');
			var captcha = section.querySelector('[data-tgif-captcha]');
			var status = section.querySelector('[data-tgif-status]');
			if(status) status.textContent = 'Signing in to TGIF...';
			var body = new URLSearchParams({callsign:callsign.value.trim().toUpperCase(), password:password.value, talkgroup:talkgroup.value.trim(), captcha:captcha ? captcha.value.trim().toLowerCase() : ''});
			tgifRequest('tgif-user-login', {method:'POST', credentials:'same-origin', cache:'no-store', headers:{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8','X-ASR-Requested-With':'tgif-user-session'}, body:body.toString()})
				.then(renderTgifState).catch(function(error) { renderTgifState({configured:false, error:error.message || 'TGIF sign-in failed.'}); })
				.finally(function() { password.value = ''; });
			return;
		}
		var logout = event.target.closest('[data-tgif-logout]');
		if(logout) {
			event.preventDefault();
			var status = logout.closest('.asr-dmr-tgif-settings').querySelector('[data-tgif-status]');
			if(status) status.textContent = 'Signing out of TGIF...';
			tgifRequest('tgif-user-logout', {method:'POST', credentials:'same-origin', cache:'no-store', headers:{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8','X-ASR-Requested-With':'tgif-user-session'}, body:''})
				.then(renderTgifState).catch(function(error) { renderTgifState({configured:false, error:error.message || 'TGIF sign-out failed.'}); });
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
		if(event.target && event.target.closest) {
			var expertButton = event.target.closest('.asr-expert-edit-button');
			if(expertButton) {
				var advanced = expertButton.closest('.asr-advanced-details');
				var enabled = expertButton.getAttribute('aria-pressed') !== 'true';
				if(advanced) advanced.querySelectorAll('[data-expert-field]').forEach(function(field) { field.readOnly = !enabled; });
				expertButton.setAttribute('aria-pressed', enabled ? 'true' : 'false');
				expertButton.textContent = enabled ? 'Stop Expert Edit' : 'Expert Edit';
				return;
			}
			var moveUp = event.target.closest('.asr-bridge-move-up');
			if(moveUp) {
				moveBridgeRow(moveUp.closest('.asr-bridge-settings-row'), -1);
				return;
			}
			var moveDown = event.target.closest('.asr-bridge-move-down');
			if(moveDown) {
				moveBridgeRow(moveDown.closest('.asr-bridge-settings-row'), 1);
				return;
			}
		}
		if(event.target && event.target.classList.contains('asr-bridge-delete')) {
			var row = event.target.closest('.asr-bridge-settings-row');
			if(row && confirmBridgeDeletion(row)) row.remove();
			updateAddButton();
			updateBridgeOrderControls();
		}
	});
	if(addUrfButton && urfPanel && urfEnabled) {
		addUrfButton.addEventListener('click', function () {
			urfEnabled.value = '1';
			urfPanel.hidden = false;
			addUrfButton.hidden = true;
			urfPanel.querySelectorAll('input[name="urfModes[]"]').forEach(function(input) { input.checked = true; });
			var node = urfPanel.querySelector('input[name="urfNode"]');
			if(node) node.focus();
		});
	}
	if(urfPanel && urfEnabled) {
		var removeUrfButton = urfPanel.querySelector('.asr-remove-urf-button');
		if(removeUrfButton) removeUrfButton.addEventListener('click', function () {
			urfEnabled.value = '0';
			urfPanel.querySelectorAll('input[name="urfModes[]"]').forEach(function(input) { input.checked = false; });
			urfPanel.hidden = true;
			if(addUrfButton) addUrfButton.hidden = false;
		});
	}
	function currentSetupMode() {
		return setupMode ? setupMode.value : 'm17';
	}
	function currentSetupPath() {
		return setupPath ? setupPath.value : 'standard';
	}
	function updateSetupMode() {
		var mode = currentSetupMode();
		var path = currentSetupPath();
		var dmrNetwork = setupDmrNetwork ? setupDmrNetwork.value : '';
		if(setupId) setupId.value = mode === 'dmr' && dmrNetwork === 'tgif' && path === 'urf' ? 'tgif-dmr' : (mode + (mode === 'dmr' && dmrNetwork ? '_' + dmrNetwork : '') + (path === 'net' ? '_net' : '')).toLowerCase();
		if(setupTitle && !setupTitle.dataset.userEdited) setupTitle.value = (mode === 'dmr' && setupDmrNetwork ? setupDmrNetwork.options[setupDmrNetwork.selectedIndex].text + ' DMR' : mode.toUpperCase()) + (path === 'net' ? ' Net Bridge' : ' Bridge');
		if(setupPath) {
			var urfOption = setupPath.querySelector('option[value="urf"]');
			var netOption = setupPath.querySelector('option[value="net"]');
			if(urfOption) urfOption.disabled = mode === 'zello';
			if(netOption) netOption.disabled = ['dmr','ysf','p25','nxdn','m17'].indexOf(mode) === -1;
			if(mode === 'zello' && path === 'urf') { setupPath.value = 'standard'; path = 'standard'; }
			if(netOption && netOption.disabled && path === 'net') { setupPath.value = 'standard'; path = 'standard'; }
		}
		var isM17 = mode === 'm17';
		var isDmr = mode === 'dmr';
		var isZello = mode === 'zello';
		var isDstar = mode === 'dstar';
		var isDigital = ['p25','nxdn','ysf','dmr'].indexOf(mode) !== -1;
		var defaults = {
			m17: {port: '17000', destination: '', host: 'm17.example.net'},
			p25: {port: '41000', destination: '64189', host: 'p25-reflector.example.net'},
			nxdn: {port: '41400', destination: '64189', host: 'nxdn-reflector.example.net'},
			ysf: {port: '42000', destination: '64189', host: 'ysf-reflector.example.net'},
			dmr: {port: '', destination: '', host: ''},
			zello: {port: '', destination: '', host: ''},
			dstar: {port: '', destination: '', host: ''}
		};
		var selected = defaults[mode] || defaults.m17;
		document.querySelectorAll('[data-bridge-setup-reflector-field],[data-bridge-setup-module-field]').forEach(function(field) { field.hidden = !(isM17 || isDstar); });
		document.querySelectorAll('[data-bridge-setup-digital]').forEach(function(field) { field.hidden = !(isDigital || isDstar); });
		if(setupFields.setupDestination && setupFields.setupDestination.closest('label')) setupFields.setupDestination.closest('label').hidden = !(isDigital && !isDstar);
		document.querySelectorAll('[data-bridge-setup-dmr]').forEach(function(field) { field.hidden = !isDmr; });
		document.querySelectorAll('[data-bridge-setup-tgif-auth]').forEach(function(field) { field.hidden = !(isDmr && dmrNetwork === 'tgif'); });
		document.querySelectorAll('[data-bridge-setup-tgif-key]').forEach(function(field) { field.hidden = !(isDmr && dmrNetwork === 'tgif' && setupTgifAuthMode && setupTgifAuthMode.value === 'secured'); });
		document.querySelectorAll('[data-bridge-setup-dmr-username-field]').forEach(function(field) { field.hidden = !isDmr || dmrNetwork === 'tgif'; });
		document.querySelectorAll('[data-bridge-setup-zello]').forEach(function(field) { field.hidden = !isZello; });
		document.querySelectorAll('[data-bridge-setup-network]').forEach(function(field) { field.hidden = isDmr || isZello || isDstar; });
		var label = document.querySelector('[data-bridge-setup-callsign-label]');
		var digitalLabel = document.querySelector('[data-bridge-setup-digital-label]');
		var destinationLabel = document.querySelector('[data-bridge-setup-destination-label]');
		if(label) label.textContent = isM17 ? 'M17 Callsign' : 'Station Callsign';
		if(setupFields.setupCallsign && setupFields.setupCallsign.closest('label')) setupFields.setupCallsign.closest('label').hidden = isZello;
		if(digitalLabel) digitalLabel.textContent = isDmr ? 'DMR ID' : 'Digital ID';
		var reflectorLabel = document.querySelector('[data-bridge-setup-reflector-label]');
		if(reflectorLabel) reflectorLabel.textContent = path === 'urf' ? 'URF Reflector Name' : (isDstar ? 'D-Star Reflector Name' : (mode.toUpperCase() + ' Reflector Name'));
		if(destinationLabel) destinationLabel.textContent = isDmr ? 'TGIF Talkgroup' : 'Destination / Reflector';
		if(setupPreview) setupPreview.textContent = 'Check Setup'; if(setupInstall) { setupInstall.textContent = 'Install Bridge'; setupInstall.hidden = true; }
		if(isDstar && setupFields.setupReflector) setupFields.setupReflector.placeholder = 'XRF641';
		if(setupFields.setupHost) setupFields.setupHost.placeholder = selected.host;
		if(setupFields.setupPort) setupFields.setupPort.value = selected.port;
		if(setupFields.setupDestination) setupFields.setupDestination.value = selected.destination;
		if(isDmr && setupFields.setupDmrUsername && !setupFields.setupDmrUsername.value) setupFields.setupDmrUsername.value = <?php echo json_encode($setupCallsignDefault, JSON_UNESCAPED_SLASHES); ?>;
		if(!isDmr && setupFields.setupTgifPassword) setupFields.setupTgifPassword.value = '';
		if(!isZello) { if(setupFields.setupZelloPassword) setupFields.setupZelloPassword.value=''; if(setupFields.setupZelloPrivateKey) setupFields.setupZelloPrivateKey.value=''; }
		setupPlanDigest = '';
		if(setupInstall) setupInstall.disabled = true;
		if(setupOutput) setupOutput.textContent = '';
	}
	if(setupPanel) setupPanel.addEventListener('input', function(event) { if(event.target === setupTitle) setupTitle.dataset.userEdited = '1'; setupPlanDigest = ''; if(setupPreview) setupPreview.hidden = false; if(setupInstall) { setupInstall.hidden = true; setupInstall.disabled = true; } });
	if(setupDmrNetwork) setupDmrNetwork.addEventListener('change', function(){ if(setupTitle) delete setupTitle.dataset.userEdited; updateSetupMode(); });
	if(setupTgifAuthMode) setupTgifAuthMode.addEventListener('change', function(){ if(setupFields.setupTgifPassword) setupFields.setupTgifPassword.value = ''; updateSetupMode(); });
	if(setupMode) setupMode.addEventListener('change', function(){ if(setupTitle) delete setupTitle.dataset.userEdited; updateSetupMode(); });
	if(setupPath) setupPath.addEventListener('change', updateSetupMode);
	if(setupButton && setupPanel) {
		setupButton.addEventListener('click', function () {
			setupPanel.hidden = false;
			if(setupMode) setupMode.focus();
		});
	}
	if(setupCancel && setupPanel) setupCancel.addEventListener('click', function () { setupPanel.hidden = true; });
	function bridgeSetupRequest(action) {
		var values = new URLSearchParams();
		values.set('asrAction', action);
		var token = form ? form.querySelector('input[name="settingsSaveCsrf"]') : null;
		values.set('settingsSaveCsrf', token ? token.value : '');
		values.set('setupBridgeRole', currentSetupPath() === 'net' ? 'net' : 'standard');
		// Callsign belongs to the node, not the bridge form. Always send the server-derived node callsign.
		if(setupCallsign && <?php echo $setupCallsignDefault === 'SCRATCH' ? 'false' : 'true'; ?>) setupCallsign.value = <?php echo json_encode($setupCallsignDefault, JSON_UNESCAPED_SLASHES); ?>;
		Object.keys(setupFields).forEach(function(key) {
			if(['setupTgifPassword','setupZelloPassword','setupZelloPrivateKey'].indexOf(key) !== -1 && !action.endsWith('-install')) return;
			var rawValue = setupFields[key] ? setupFields[key].value : '';
			values.set(key, ['setupZelloPassword','setupZelloPrivateKey'].indexOf(key) !== -1 ? rawValue : rawValue.trim());
		});
		if(action.endsWith('-install')) values.set('setupPlanDigest', setupPlanDigest);
		setupPreview.disabled = true;
		if(setupInstall) setupInstall.disabled = true;
		if(setupOutput) setupOutput.hidden = false;
		var modeLabel = currentSetupMode().toUpperCase();
		if(action.endsWith('-plan')) setupOutput.textContent = 'Detecting this ASL3 system and validating the ' + modeLabel + ' installation plan…'; else setupOutput.innerHTML = '<span>Installing the ' + modeLabel + ' bridge. This may compile pinned software; </span><strong class="asr-install-warning">DO NOT CLOSE THIS PAGE</strong><span>…</span>';
		return fetch(window.location.href.split('#')[0], {
			method: 'POST', credentials: 'same-origin', cache: 'no-store',
			headers: {'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'},
			body: values.toString()
		}).then(function(response) {
			return response.json().catch(function() { throw new Error('Bridge setup returned an invalid response.'); })
				.then(function(data) {
					if(!response.ok || !data.ok) throw new Error(data.error || 'Bridge setup failed.');
					return data.result;
				});
		}).finally(function() { setupPreview.disabled = false; });
	}
	function renderBridgeSetupPlan(result) {
		if(setupOutput) setupOutput.hidden = false;
		var mode = currentSetupMode();
		var label = mode.toUpperCase();
		var ports = result.ports || {};
		setupPlanDigest = typeof result.digest === 'string' ? result.digest : '';
		var details;
		if(mode === 'm17') {
			var changed = Array.isArray(result.changed) && result.changed.length ? result.changed.join('\n  ') : 'None (installation is already current)';
			details = 'M17 UDP port: ' + ports.m17 + '\n' +
				'USRP receive/transmit: ' + ports.usrpRx + ' / ' + ports.usrpTx + '\n' +
				'Files to change:\n  ' + changed;
		} else if(mode === 'dstar') {
			details = 'Managed D-Star runtime: yes\n' +
				'USRP receive/transmit: ' + ports.usrp_rx + ' / ' + ports.usrp_tx + '\n' +
				'Gateway / MMDVM ports: ' + ports.gateway + ' / ' + ports.mmdvm + '\n' +
				'Software AMBE port: ' + ports.vocoder;
		} else if(mode === 'zello') {
			details = 'Managed Zello runtime: yes\n' +
				'USRP receive/transmit: ' + ports.usrp_rx + ' / ' + ports.usrp_tx;
		} else if(mode === 'dmr') {
			details = 'Managed URF/TGIF runtime: yes\n' +
				'USRP receive/transmit: ' + ports.usrp_rx + ' / ' + ports.usrp_tx + '\n' +
				'URF DMR port: ' + ports.dmr + '\n' +
				'TGIF local ports: ' + ports.tgif_local + ' / ' + ports.tgif_mmdvm;
		} else {
			var services = Array.isArray(result.services) ? result.services.join('\n  ') : '';
			details = 'USRP receive/transmit: ' + ports.usrp_rx + ' / ' + ports.usrp_tx + '\n' +
				'Gateway network port: ' + ports.network + '\n' +
				'Software vocoder port: ' + ports.emulator + '\n' +
				'Managed services:\n  ' + services;
		}
		setupOutput.textContent = 'Validated ' + label + ' installation plan\n' +
			'Bridge node: ' + result.bridgeNode + '\n' + details +
			'\n\nNo changes have been applied. Live audio qualification remains a separate status.';
		if(setupInstall) { setupInstall.hidden = false; setupInstall.disabled = !/^[a-f0-9]{64}$/.test(setupPlanDigest); }
	}
	if(setupPreview && setupOutput) setupPreview.addEventListener('click', function () {
		var mode = currentSetupMode();
		if(currentSetupPath() === 'urf' && mode !== 'dmr') {
			if(mode === 'zello') { setupOutput.textContent = 'Zello is Standard only.'; return; }
			if(!urfPanel || !urfEnabled) { setupOutput.textContent = 'URF reflector settings are unavailable on this installation.'; return; }
			urfEnabled.value = '1';
			urfPanel.hidden = false;
			if(addUrfButton) addUrfButton.hidden = true;
			var wanted = urfPanel.querySelector('input[name="urfModes[]"][value="' + mode + '"]');
			if(wanted) wanted.checked = true;
			setupPlanDigest = '';
			if(setupInstall) setupInstall.disabled = true;
			setupOutput.textContent = mode.toUpperCase() + ' is selected for the shared URF reflector. Enter the shared URF AllStar node above and save Reimagined Settings. The Standard installer will not run.';
			urfPanel.scrollIntoView({behavior:'smooth', block:'start'});
			return;
		}
		bridgeSetupRequest(mode + '-plan').then(renderBridgeSetupPlan)
			.catch(function(error) { setupOutput.hidden = false; setupOutput.textContent = error.message || mode.toUpperCase() + ' plan failed.'; });
	});
	if(setupInstall && setupOutput) setupInstall.addEventListener('click', function () {
		var mode = currentSetupMode();
		if(currentSetupPath() === 'urf' && mode !== 'dmr') { setupOutput.textContent = 'URF modes are applied with Save Reimagined Settings above.'; return; }
		var label = mode.toUpperCase();
		if(!window.confirm('Install this ' + label + ' bridge using the validated plan?')) return;
		bridgeSetupRequest(mode + '-install').then(function(result) {
			var qualification = 'Provisioning and managed-service startup passed. Live RF/audio qualification is still pending.';
			setupOutput.textContent = label + ' bridge installation committed successfully.\nBridge node: ' + result.bridgeNode +
				'\n\n' + qualification + ' Reload Settings to view the new bridge card.';
			if(mode === 'dmr' && setupFields.setupTgifPassword) setupFields.setupTgifPassword.value = '';
			if(mode === 'zello') { if(setupFields.setupZelloPassword) setupFields.setupZelloPassword.value=''; if(setupFields.setupZelloPrivateKey) setupFields.setupZelloPrivateKey.value=''; }
		}).catch(function(error) { setupOutput.hidden = false; setupOutput.textContent = error.message || label + ' installation failed and was rolled back.'; });
	});
	Object.keys(setupFields).forEach(function(key) {
		if(setupFields[key]) setupFields[key].addEventListener('input', function() {
			if(['setupTgifPassword','setupZelloPassword','setupZelloPrivateKey'].indexOf(key) !== -1) return;
			setupPlanDigest = '';
			if(setupInstall) setupInstall.disabled = true;
		});
	});
	if(addButton && table && template) {
		addButton.addEventListener('click', function () {
			if(rows().length >= max) return;
				var fragment = template.content.cloneNode(true);
				table.appendChild(fragment);
				refreshBridgeTitles();
				refreshBridgeTypes();
				updateAddButton();
				updateBridgeOrderControls();
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
	refreshTgifState();
	refreshBridgeTypes();
	updateAddButton();
	updateBridgeOrderControls();
	updateRollbackButton();
	loadBridgeDiagnostics();
	pollRollbackStatus();
})();
</script>
<?php if($modernSettings): ?>
<script src="<?php echo asrSettingsH(asrSettingsWebPath('js/asr-settings-modern.js')); ?>"></script>
<?php endif; ?>
<?php
asExit();
