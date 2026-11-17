#!/usr/bin/env php
<?php
declare(strict_types=1);

$root = dirname(__DIR__);
$settingsDir = $root . '/compat/allscan-v1.02/asr-settings';
chdir($settingsDir);
define('ASR_SETTINGS_FUNCTIONS_ONLY', true);
$wwwroot = '/var/www/html';
$asdir = 'allscan';
$urlbase = '/allscan';
// The isolated controller test omits common.php; its URL migration helper
// is irrelevant to bridge setup but is called when a real config is present.
function asrRebaseLegacyWebPath($path, $defaultPath = '') {
    return (string) ($path ?: $defaultPath);
}
require $settingsDir . '/index.php';

$settingsSource = file_get_contents($settingsDir . '/settings-controller.php');
if(!is_string($settingsSource)
	|| !str_contains($settingsSource, 'str_ends_with((string)$asrAction, \'-install\')')
	|| !str_contains($settingsSource, "allscan-reimagined-friendly-names --once"))
	throw new RuntimeException('Guided provisioning does not reconcile private-node friendly names.');

function check(bool $condition, string $message): void {
	if(!$condition) throw new RuntimeException($message);
}

foreach(['v1.02', 'v1.03', 'v1.04', 'v1.05'] as $compatVersion) {
	$controllerSource = file_get_contents($root . '/compat/allscan-' . $compatVersion . '/asr-settings/settings-controller.php');
	check(is_string($controllerSource), "Unable to read $compatVersion Settings controller.");
	$adminCss = file_get_contents($root . '/compat/allscan-' . $compatVersion . '/css/asr-admin.css');
	check(is_string($adminCss)
		&& str_contains($adminCss, '.asr-net-bridge-setup-fields fieldset select { padding-right: 2.25rem; }')
		&& str_contains($adminCss, 'grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));')
		&& str_contains($adminCss, '.asr-net-bridge-setup-fields fieldset input,'),
		"$compatVersion Net Bridge fields do not reserve enough width for complete select labels.");
	check(substr_count($adminCss, '{') === substr_count($adminCss, '}'),
		"$compatVersion admin stylesheet has unbalanced rule braces.");
	foreach(['updateRecoveryCheck' => 'asrUpdateRecoveryCheck', 'updateRecoveryStatus' => 'asrUpdateRecoveryStatus'] as $variable => $elementId) {
		$declaration = "var $variable = document.getElementById('$elementId');";
		check(substr_count($controllerSource, $declaration) === 1, "$compatVersion Settings does not declare $variable exactly once.");
		check(strpos($controllerSource, $declaration) < strpos($controllerSource, "if(updateRecoveryCheck)"), "$compatVersion Settings uses $variable before declaration.");
	}
	check(str_contains($controllerSource, "setupButton.addEventListener('click', function () {")
		&& str_contains($controllerSource, 'delete setupPanel.dataset.editingManagedDmr;')
		&& str_contains($controllerSource, 'updateSetupMode();'),
		"$compatVersion Settings does not initialize the selected bridge mode when the setup wizard opens.");

	check(str_contains($controllerSource, "'bridgePath' => strtolower(asrSettingsCleanText(\$_POST['setupBridgePath']")
		&& str_contains($controllerSource, 'data-bridge-setup-direct-dmr')
		&& str_contains($controllerSource, 'data-edit-direct-dmr')
		&& (str_contains($controllerSource, "setupPanel.dataset.editingManagedDmr='1'") || str_contains($controllerSource, "setupPanel.dataset.editingManagedDmr = '1'")),
		"$compatVersion Settings does not expose managed direct-DMR install/edit controls.");
}

// Issue #136: verify the v1.05 bridge editor persists validated directional gains.
$gainController = file_get_contents($root . '/compat/allscan-v1.05/asr-settings/settings-controller.php');
check(is_string($gainController)
    && str_contains($gainController, 'name="bridgeGainInDb[]"')
    && str_contains($gainController, 'name="bridgeGainOutDb[]"')
    && str_contains($gainController, "'gainInDb' => (float)\$gainIn")
    && str_contains($gainController, "'gainOutDb' => (float)\$gainOut")
    && str_contains($gainController, '(float)$gainValue < -20')
    && str_contains($gainController, '(float)$gainValue > 12'),
    'Bridge gain fields are not saved with bounded validation.');
check(str_contains($gainController, "'dmrToAslGainDb', 'digitalToAslGainDb'")
    && str_contains($gainController, "'aslToDmrGainDb', 'aslToDigitalGainDb'")
    && str_contains($gainController, "'dmr' => ['dmrToAslGainDb', 'aslToDmrGainDb']")
    && str_contains($gainController, "'m17' => ['digitalToAslGainDb', 'aslToDigitalGainDb']")
    && str_contains($gainController, "'zello' => ['zelloToAslGainDb', 'aslToZelloGainDb']")
    && str_contains($gainController, "asrSettingsBridgeGainValue(\$existingGain, 'in')"),
    'Legacy bridge gain fallback or installer synchronization is missing.');



// Exercise the gain lookup itself, including installer changes made after
// an older generic gain value was saved by the dashboard.
$gainStart = strpos($gainController, 'function asrSettingsBridgeGainValue(');
$gainEnd = strpos($gainController, 'function asrSettingsBridgeRowsFromPost(', $gainStart);
check($gainStart !== false && $gainEnd !== false, 'Bridge gain helper missing.');
if (!function_exists('asrSettingsBridgeGainValue'))
    eval(substr($gainController, $gainStart, $gainEnd - $gainStart));

$filterError = '';
check(asrSettingsCleanFilteredStations("n0call\nSRV_custom,n0call", $filterError) === ['N0CALL', 'SRV_CUSTOM'], 'Custom station filters were not normalized and deduplicated.');
check($filterError === '', 'Valid custom station filters reported an error.');
$filterError = '';
check(asrSettingsCleanFilteredStations('BAD*', $filterError) === [] && $filterError !== '', 'Invalid custom station filter was accepted.');
check(asrSettingsDefaultConfig()['filterServiceStations'] === true, 'Built-in station filtering is not enabled by default.');

check(str_contains($settingsSource, '$next[\'bridgeOrderVersion\'] = 2;'), 'Bridge saves do not mark the explicit unified order schema.');
check(str_contains($settingsSource, 'if($bridgeOrderVersion < 2)'), 'Legacy bridge ordering does not migrate standard cards ahead of the unified group.');

$display = postedBridge(['bridgeMode' => ['p25'], 'bridgeBackendMode' => ['display_only']]);
check($display['backendMode'] === 'display_only' && !isset($display['gatewayConfig']), 'P25 display-only card gained managed resources.');
$displayM17 = postedBridge(['bridgeMode' => ['m17'], 'bridgeBackendMode' => ['display_only']]);
check($displayM17['backendMode'] === 'display_only' && !isset($displayM17['m17BindPort']), 'M17 display-only card gained managed resources.');

$p25 = postedBridge([
	'bridgeMode' => ['p25'], 'bridgeBackendMode' => ['managed'], 'bridgePermission' => ['approved'],
	'bridgeFixedDestination' => ['10200'],
]);
check($p25['gatewayConfig'] === '/opt/P25Gateway_p25/P25Gateway.ini', 'P25 resources were not derived.');
check(($p25['emulatorService'] ?? '') === '', 'P25 retained an irrelevant NXDN emulator.');

$p25Net = postedBridge([
	'bridgeMode' => ['p25'], 'bridgeCardType' => ['net'], 'bridgePermission' => ['approved'],
	'bridgeApprovedDestinations' => ['10200 10201'],
]);
check($p25Net['cardType'] === 'p25_net' && $p25Net['approvedDestinations'] === ['10200', '10201'], 'P25 Net defaults were not preserved.');

$nxdn = postedBridge([
	'bridgeMode' => ['nxdn'], 'bridgeCardType' => ['net'], 'bridgePermission' => ['self_owned'],
	'bridgeApprovedDestinations' => ['65000'],
]);
check($nxdn['cardType'] === 'nxdn_net' && $nxdn['approvedDestinations'] === ['65000'], 'NXDN Net defaults were not preserved.');

$dmr = postedBridge([
	'bridgeMode' => ['dmr'], 'bridgeCardType' => ['net'], 'bridgePermission' => ['approved'],
	'bridgeApprovedDestinations' => [''], 'bridgeAbinfoPath' => ['/tmp/ABInfo_12345.json'],
	'bridgeDvswitchScript' => ['/opt/MMDVM_Bridge_Test/dvswitch.sh'],
	'bridgeAnalogConfig' => ['/opt/Analog_Bridge_Test/Analog_Bridge.ini'],
]);
check(!isset($dmr['linkAlias']) && $dmr['approvedDestinations'] === [], 'DMR Net manual-entry card retained a non-routable link alias or required an approved TG list.');

$managedDmrExisting = [
	'id'=>'dmr_tgif_net', 'mode'=>'dmr', 'node'=>'1999', 'cardType'=>'dmr_net',
	'backendMode'=>'managed', 'managedNetControl'=>true,
	'managedTargetFile'=>'/opt/allscan-reimagined-bridges/dmr_tgif_net/tgif-run/net-target',
	'bridgePermission'=>'self_owned', 'approvedDestinations'=>['86753'], 'fixedDestination'=>'86753',
];
$managedDmr = postedBridge([
	'bridgeId'=>['dmr_tgif_net'], 'bridgeMode'=>['dmr'], 'bridgeNode'=>['1999'],
	'bridgeCardType'=>['dmr_net'], 'bridgePermission'=>['self_owned'],
	'bridgeApprovedDestinations'=>['86753'], 'bridgeFixedDestination'=>['86753'],
], [$managedDmrExisting]);
check(!empty($managedDmr['managedNetControl']) && ($managedDmr['abinfoPath'] ?? '') === '',
	'Native managed DMR runtime could not round-trip Settings without legacy DVSwitch paths.');

expectFailure(static function (): void {
	postedBridge([
		'bridgeMode' => ['dmr'], 'bridgeCardType' => ['net'], 'bridgePermission' => [''],
		'bridgeApprovedDestinations' => [''], 'bridgeAbinfoPath' => ['/tmp/ABInfo_12345.json'],
		'bridgeDvswitchScript' => ['/opt/MMDVM_Bridge_Test/dvswitch.sh'],
		'bridgeAnalogConfig' => ['/opt/Analog_Bridge_Test/Analog_Bridge.ini'],
	]);
}, 'requires confirmed permission');

expectFailure(static function (): void {
	postedBridge([
		'bridgeMode' => ['ysf'], 'bridgeCardType' => ['net'], 'bridgePermission' => [''],
		'bridgeApprovedDestinations' => [''], 'bridgeAllowTune' => ['1'],
		'bridgeYsfGatewayConfig' => ['/opt/YSFGateway_test/YSFGateway.ini'],
		'bridgeMmdvmConfig' => ['/opt/MMDVM_Bridge_test/MMDVM_Bridge.ini'],
		'bridgeYsfGatewayService' => ['ysfgateway_test.service'],
		'bridgeMmdvmService' => ['mmdvm_test.service'],
	]);
}, 'requires confirmed permission');

$ysf = postedBridge([
	'bridgeMode' => ['ysf'], 'bridgeCardType' => ['net'], 'bridgePermission' => ['approved'],
	'bridgeApprovedDestinations' => [''], 'bridgeAllowTune' => ['1'],
	'bridgeYsfGatewayConfig' => ['/opt/YSFGateway_test/YSFGateway.ini'],
	'bridgeMmdvmConfig' => ['/opt/MMDVM_Bridge_test/MMDVM_Bridge.ini'],
	'bridgeYsfGatewayService' => ['ysfgateway_test.service'], 'bridgeMmdvmService' => ['mmdvm_test.service'],
]);
check($ysf['approvedDestinations'] === [], 'YSF Net manual-entry card required an approved reflector list.');

$m17 = postedBridge([
	'bridgeMode' => ['m17'], 'bridgeBackendMode' => ['managed'], 'bridgePermission' => ['self_owned'],
	'bridgeM17Callsign' => ['N0CALL'], 'bridgeM17Reflector' => ['M17-TST'],
	'bridgeM17Host' => ['127.0.0.1'], 'bridgeM17Port' => ['17000'], 'bridgeM17Module' => ['A'],
]);
check($m17['m17AudioQualified'] === false && $m17['m17BindPort'] > 0, 'M17 qualification remained editable or ports were not assigned.');
check(($m17['m17BindAddress'] ?? '') === '0.0.0.0', 'M17 network socket cannot reach external reflectors.');

$m17Net = postedBridge([
	'bridgeMode' => ['m17'], 'bridgeCardType' => ['net'], 'bridgePermission' => ['approved'],
	'bridgeM17Callsign' => ['N0CALL'],
	'bridgeApprovedDestinations' => ['M17-TST | 127.0.0.1 | 17000 | A'],
]);
check($m17Net['cardType'] === 'm17_net' && count($m17Net['approvedDestinations']) === 1, 'M17 Net approved target was not preserved.');

$preserved = postedBridge([
	'bridgeId' => ['p25_primary'], 'bridgeMode' => ['p25'], 'bridgeBackendMode' => ['display_only'],
	'bridgeTitle' => ['My P25 Card'], 'bridgeDetailTitle' => ['My Linked Clients'],
], [['id' => 'p25_primary', 'mode' => 'p25', 'node' => '4321', 'title' => 'My P25 Card']]);
check($preserved['id'] === 'p25_primary' && $preserved['title'] === 'My P25 Card' && $preserved['detailTitle'] === 'My Linked Clients', 'Existing card identity or labels were not preserved.');

expectFailure(static function (): void {
	postedBridge([
		'bridgeMode' => ['dmr'], 'bridgeCardType' => ['net'], 'bridgePermission' => ['approved'],
		'bridgeApprovedDestinations' => ['4000'], 'bridgeAbinfoPath' => ['/tmp/ABInfo_12345.json'],
		'bridgeDvswitchScript' => ['/opt/MMDVM_Bridge_Test/dvswitch.sh'],
		'bridgeAnalogConfig' => ['/opt/Analog_Bridge_Test/Analog_Bridge.ini'],
	]);
}, 'cannot use disconnect TG 4000');

expectFailure(static function (): void {
	postedBridge([
		'bridgeMode' => ['ysf'], 'bridgeCardType' => ['net'], 'bridgePermission' => ['approved'],
		'bridgeApprovedDestinations' => ['00000'],
	]);
}, 'exact reflector names or five-digit IDs');

$p25NetWithoutDefaults = postedBridge([
	'bridgeMode' => ['p25'], 'bridgeCardType' => ['net'], 'bridgePermission' => ['approved'],
]);
check($p25NetWithoutDefaults['approvedDestinations'] === [], 'P25 Net Bridge unexpectedly required a destination allowlist.');

expectFailure(static function (): void {
	postedBridge([
		'bridgeMode' => ['m17'], 'bridgeCardType' => ['net'], 'bridgePermission' => ['approved'],
		'bridgeM17Callsign' => ['N0CALL'], 'bridgeApprovedDestinations' => ['not a target'],
	]);
}, 'REFLECTOR | HOST | PORT | MODULE');

expectFailure(static function (): void {
	postedBridge(['bridgeMode' => ['dstar'], 'bridgeCardType' => ['net']]);
}, 'Choose a supported Digital Mode');

expectFailure(static function (): void {
	postedBridge(['bridgeMode' => ['zello'], 'bridgeCardType' => ['net']]);
}, 'Standard Bridge card only');

$ownedExisting = [[
	'id' => 'p25_primary', 'mode' => 'p25', 'cardType' => 'standard',
	'backendMode' => 'managed', 'node' => '4321',
]];
$preview = [
	'bridgeId' => 'p25_primary',
	'creationId' => str_repeat('1', 32),
	'manifestDigest' => str_repeat('2', 64),
	'deletionToken' => str_repeat('3', 64),
	'owned' => true,
	'resources' => ['Service p25gateway-p25_primary.service'],
	'willNotTouch' => ['Manual services'],
];
$lifecycle = ['available' => true, 'bridges' => ['p25_primary' => $preview]];
$error = '';
$confirmation = json_encode([[
	'bridgeId' => 'p25_primary', 'creationId' => $preview['creationId'],
	'manifestDigest' => $preview['manifestDigest'], 'deletionToken' => $preview['deletionToken'],
	'owned' => true,
]]);
$plan = asrSettingsValidateDeletionPlan($ownedExisting, [], $confirmation, $lifecycle, $error);
check($error === '' && count($plan['queue']) === 1, 'Exact managed deletion was not queued.');

foreach([
	['raw' => '[]', 'lifecycle' => $lifecycle, 'needle' => 'one exact deletion confirmation'],
	['raw' => json_encode([['bridgeId' => 'p25_primary', 'creationId' => str_repeat('1', 32), 'manifestDigest' => str_repeat('2', 64), 'deletionToken' => str_repeat('0', 64), 'owned' => true]]), 'lifecycle' => $lifecycle, 'needle' => 'forged'],
	['raw' => $confirmation, 'lifecycle' => ['available' => false, 'bridges' => []], 'needle' => 'ownership is unknown'],
	['raw' => json_encode([['bridgeId' => 'p25_primary'], ['bridgeId' => 'p25_primary']]), 'lifecycle' => $lifecycle, 'needle' => 'invalid'],
] as $case) {
	$error = '';
	$result = asrSettingsValidateDeletionPlan($ownedExisting, [], $case['raw'], $case['lifecycle'], $error);
	check($result === null && stripos($error, $case['needle']) !== false, 'Deletion authorization regression was not rejected: ' . $case['needle']);
}

$error = '';
$external = asrSettingsValidateDeletionPlan(
	$ownedExisting, [], json_encode([['bridgeId' => 'p25_primary', 'owned' => false]]),
	['available' => true, 'bridges' => []], $error
);
check($error === '' && $external['queue'] === [], 'External/display-only deletion created managed cleanup intent.');

$error = '';
$mutated = [[
	'id' => 'nxdn', 'mode' => 'nxdn', 'cardType' => 'standard',
	'backendMode' => 'managed', 'node' => '4321',
]];
check(!asrSettingsValidateOwnedBridgeMutations(
	$ownedExisting, $mutated, ['p25_primary'], $lifecycle, $error
) && strpos($error, 'cannot change Digital Mode') !== false, 'Owned Digital Mode mutation was accepted.');

$error = '';
$roleChanged = [[
	'id' => 'p25_primary', 'mode' => 'p25', 'cardType' => 'p25_net',
	'backendMode' => 'managed', 'node' => '4321',
]];
check(!asrSettingsValidateOwnedBridgeMutations(
	$ownedExisting, $roleChanged, ['p25_primary'], $lifecycle, $error
), 'Owned role mutation was accepted.');

$_SERVER = ['HTTP_SEC_FETCH_SITE' => 'cross-site'];
check(!asrSettingsRollbackPostIsSameOrigin(true), 'Cross-site Settings Save was accepted.');
$_SERVER = ['HTTP_SEC_FETCH_SITE' => 'same-origin'];
check(asrSettingsRollbackPostIsSameOrigin(true), 'Positive same-origin browser evidence was rejected.');
$_SERVER = [
	'HTTP_SEC_FETCH_SITE' => 'same-origin', 'HTTP_ORIGIN' => 'https://node.example',
	'HTTPS' => 'on', 'HTTP_HOST' => 'node.example',
];
check(asrSettingsRollbackPostIsSameOrigin(true), 'Matching Settings origin was rejected.');
$_SERVER['HTTP_ORIGIN'] = 'https://attacker.example';
check(!asrSettingsRollbackPostIsSameOrigin(true), 'Forged Settings origin was accepted.');

$_POST = [
	'setupBridgeId' => 'm17_test', 'setupTitle' => 'Test M17',
	'setupCallsign' => 'n0call', 'setupReflector' => 'm17-tst',
	'setupHost' => '127.0.0.1', 'setupPort' => '17000', 'setupModule' => 'a',
];
$setupError = '';
$setup = asrSettingsM17SetupPayload($setupError);
check($setupError === '' && $setup['callsign'] === 'N0CALL' && $setup['module'] === 'A', 'Valid M17 setup request was not normalized.');
$_POST['setupHost'] = 'bad host';
asrSettingsM17SetupPayload($setupError);
check(strpos($setupError, 'hostname') !== false, 'Invalid M17 setup host was accepted.');

$_POST = [
	'setupBridgeId' => 'p25_test', 'setupTitle' => 'Test P25',
	'setupCallsign' => 'n0call', 'setupDigitalId' => '1234567',
	'setupDestination' => '64189', 'setupHost' => '127.0.0.1', 'setupPort' => '41000',
];
$setup = asrSettingsDigitalSetupPayload('p25', $setupError);
check($setupError === '' && $setup['callsign'] === 'N0CALL' && $setup['destination'] === 64189, 'Valid P25 setup request was not normalized.');

$_POST = [
	'setupBridgeId' => 'dmr_tgif', 'setupTitle' => 'DMR Bridge',
	'setupCallsign' => 'ke7wil', 'setupDigitalId' => '3224939',
	'setupDestination' => '86753', 'setupTgifEssid' => '50',
	'dmrEnabled' => 'true', 'm17Enabled' => 'false',
];
$dmrSetup = asrSettingsDmrSetupPayload(false, $setupError);

$dmrInstall = asrSettingsDmrSetupPayload(true, $setupError);
check($setupError === '' && $dmrInstall['authMode'] === 'legacy' && !isset($dmrInstall['tgifPassword']), 'Legacy TGIF install unexpectedly required a key.');
$_POST['setupTgifAuthMode'] = 'secured';
unset($_POST['setupTgifPassword']);
$dmrPreview = asrSettingsDmrSetupPayload(false, $setupError);
check($setupError === '' && !isset($dmrPreview['tgifPassword']), 'Secured DMR preview unexpectedly required or exposed the TGIF hotspot key.');
asrSettingsDmrSetupPayload(true, $setupError);
check(strpos($setupError, 'hotspot key') !== false, 'Secured DMR install without a hotspot key reached the helper boundary.');
$_POST['setupTgifPassword'] = 'secret-123';
$dmrInstall = asrSettingsDmrSetupPayload(true, $setupError);
check($setupError === '' && $dmrInstall['tgifPassword'] === 'secret-123', 'Secured TGIF hotspot key was not accepted.');
$_POST['setupTgifPassword'] = "bad\npassword";
asrSettingsDmrSetupPayload(true, $setupError);
check(strpos($setupError, 'hotspot key') !== false, 'Unsafe TGIF hotspot key was accepted.');
$_POST['setupTgifPassword'] = 'secret-123';
$_POST['setupTgifEssid'] = '100';
asrSettingsDmrSetupPayload(true, $setupError);
check(strpos($setupError, 'ESSID') !== false, 'Unsafe TGIF hotspot ESSID was accepted.');
$_POST['setupDigitalId'] = '314133';
$_POST['setupTgifEssid'] = '50';
asrSettingsDmrSetupPayload(true, $setupError);
check(strpos($setupError, 'repeater DMR IDs') !== false, 'Six-digit repeater DMR ID accepted a hotspot ESSID.');

$_POST = [
	'setupBridgeId' => 'zello', 'setupTitle' => 'Zello Bridge',
	'setupZelloUsername' => 'bridge-user', 'setupZelloChannel' => 'My Channel',
	'setupZelloIssuer' => 'issuer-1', 'setupZelloWsEndpoint' => 'wss://zello.io/ws',
];
$zelloSetup = asrSettingsZelloSetupPayload(false, $setupError);
check($setupError === '' && $zelloSetup['username'] === 'bridge-user' && !isset($zelloSetup['password']) && !isset($zelloSetup['privateKey']), 'Valid Zello preview was not normalized or leaked secrets.');
$_POST['setupZelloPassword'] = 'secret';
$_POST['setupZelloPrivateKey'] = "-----BEGIN PRIVATE KEY-----
TEST
-----END PRIVATE KEY-----";
$zelloInstall = asrSettingsZelloSetupPayload(true, $setupError);
check($setupError === '' && $zelloInstall['password'] === 'secret' && str_contains($zelloInstall['privateKey'], 'PRIVATE KEY-----'), 'Valid Zello install secrets were rejected.');
$_POST['setupZelloWsEndpoint'] = 'http://unsafe.example';
asrSettingsZelloSetupPayload(false, $setupError);
check(strpos($setupError, 'wss://') !== false, 'Unsafe Zello WebSocket endpoint was accepted.');

$settingsSource = file_get_contents($settingsDir . '/settings-controller.php');
check(strpos($settingsSource, "mode === 'net_bridge' ? 'net-bridge-plan' : mode + '-plan'") !== false, 'Bridge setup preview is not wired to the selected helper endpoint.');
check(strpos($settingsSource, "mode === 'net_bridge' ? 'net-bridge-install' : mode + '-install'") !== false, 'Bridge setup install is not wired to the selected helper endpoint.');
check(strpos($settingsSource, "Enter the TGIF hotspot security key before installing.") !== false
	&& strpos($settingsSource, "Check Setup does not send or validate this secret.") !== false,
	'Secured DMR install does not explain its install-only hotspot-key requirement before dispatch.');
check(strpos($settingsSource, "Promise.resolve().then(function() { return bridgeSetupRequest(action); })") !== false
	&& strpos($settingsSource, 'function renderBridgeSetupError(error, fallback)') !== false
	&& strpos($settingsSource, "setupOutput.setAttribute('role', 'alert')") !== false,
	'Bridge setup synchronous or request failures can still bypass visible error reporting.');

check(strpos($settingsSource, 'setupOutput.innerHTML') === false,
	'Bridge installation status still uses a failure-prone HTML sink before request dispatch.');
check(strpos($settingsSource, "[data-bridge-setup-tgif-password], [data-net-tgif-password]") !== false,
	'Entering an install-only bridge secret still invalidates the validated setup plan.');
check(strpos($settingsSource, "'p25-plan', 'p25-install'") !== false, 'P25 helper actions are unavailable.');
check(strpos($settingsSource, "'nxdn-plan', 'nxdn-install'") !== false, 'NXDN helper actions are unavailable.');
check(strpos($settingsSource, "'ysf-plan', 'ysf-install'") !== false, 'YSF helper actions are unavailable.');
check(strpos($settingsSource, "'dmr-plan', 'dmr-install'") !== false, 'DMR helper actions are unavailable.');
check(strpos($settingsSource, "'dmr-security-update'") !== false
	&& strpos($settingsSource, 'data-dmr-security-maintenance') !== false
	&& strpos($settingsSource, 'Save and verify DMR security') !== false
	&& strpos($settingsSource, 'data-dmr-hotspot-key') !== false,
	'Installed DMR Net Bridge credentials cannot be maintained through Settings.');
check(strpos($settingsSource, "'zello-plan', 'zello-install'") !== false, 'Zello helper actions are unavailable.');

check(strpos($settingsSource, 'data-bridge-setup-path') !== false, 'Bridge type selector is missing.');

check(strpos($settingsSource, '<option value="net_bridge">Net Bridge</option>') !== false, 'Unified Net Bridge choice is missing.');

check(strpos($settingsSource, "var isUnifiedNet = mode === 'net_bridge'") !== false, 'Unified Net Bridge mode handling is missing.');
check(strpos($settingsSource, 'data-net-mode-choice="<?php echo $mode; ?>"') !== false
	&& strpos($settingsSource, "values.append('setupNetModes[]', mode)") !== false
	&& strpos($settingsSource, 'Select at least one Net Bridge mode.') !== false,
	'Unified Net Bridge mode selection is not carried through browser and server validation.');
check(strpos($settingsSource, 'Rerun Standard DMR/TGIF setup to change it; Session Talkgroup changes only the displayed TGIF roster.') !== false,
	'Standard TGIF talkgroup change guidance is missing from Settings help.');

check(strpos($settingsSource, '$isBridgeSave = !$modernSettings || $modernSettingsSection === \'bridges\'') !== false,
	'Modern Settings saves do not isolate bridge reconstruction to the Bridges category.');
check(strpos($settingsSource, "\t\$saveError = '';") !== false,
	'Modern non-bridge Settings saves do not initialize their validation result.');
check(strpos($settingsSource, '$next = is_array($storedConfig) ? $storedConfig : $config;') !== false,
	'Settings saves do not preserve the exact stored config before applying category changes.');
check(strpos($settingsSource, 'if(!$modernSettings || $modernSettingsSection === \'appearance\')') !== false,
	'Modern Settings saves do not isolate Appearance fields from unrelated categories.');
check(strpos($settingsSource, 'if(!$modernSettings || $modernSettingsSection === \'lookup\')') !== false,
	'Modern Settings saves do not isolate Lookup secrets from unrelated categories.');
check(strpos($settingsSource, 'data-bridge-setup-zello-private-key') !== false, 'Zello private-key input is missing.');
check(strpos($settingsSource, 'data-bridge-setup-tgif-password') !== false, 'TGIF password input is missing from the guided wizard.');
check(strpos($settingsSource, "p25: {port: '41000'") !== false, 'P25 setup defaults are missing.');
check(strpos($settingsSource, "nxdn: {port: '41400'") !== false, 'NXDN setup defaults are missing.');
check(strpos($settingsSource, "ysf: {port: '42000'") !== false, 'YSF setup defaults are missing.');
check(strpos($settingsSource, "setupPlanDigest") !== false, 'Bridge setup plan digest binding is missing.');
check(strpos($settingsSource, 'data-bridge-setup-install disabled') !== false, 'Install control is not fail-closed before preview.');
check(strpos($settingsSource, "bridge.readiness.state === 'inactive_mode' ? 'Installed, inactive'") !== false,
	'Bridge Diagnostics still reports an unselected Net Bridge mode as a fault.');
check(strpos($settingsSource, 'Installed services (inactive while another Net Bridge mode is selected)') !== false,
	'Bridge Diagnostics does not label intentionally inactive Net Bridge services.');
$apiPath = is_file($root . '/asr-api.php')
	? $root . '/asr-api.php'
	: $root . '/server/asr-api.php';
$apiSource = file_get_contents($apiPath);
check(is_string($apiSource) && strpos($apiSource, "'state' => 'inactive_mode'") !== false
	&& strpos($apiSource, 'is the selected Net Bridge mode.') !== false,
	'API diagnostics do not distinguish intentionally inactive Net Bridge modes.');

echo "ASR bridge Settings self-test passed\n";
