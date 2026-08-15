#!/usr/bin/env php
<?php
declare(strict_types=1);

$root = dirname(__DIR__);
$settingsDir = $root . '/compat/allscan-v1.01/asr-settings';
chdir($settingsDir);
define('ASR_SETTINGS_FUNCTIONS_ONLY', true);
require $settingsDir . '/index.php';

function check(bool $condition, string $message): void {
	if(!$condition) throw new RuntimeException($message);
}

function expectFailure(callable $operation, string $needle): void {
	try {
		$operation();
		throw new RuntimeException('Expected Settings validation to fail.');
	} catch(RuntimeException $error) {
		check(strpos($error->getMessage(), $needle) !== false, 'Unexpected validation error: ' . $error->getMessage());
	}
}

function postedBridge(array $values, array $existing = [], string $mainNode = '123456'): array {
	$defaults = [
		'bridgeId' => [''], 'bridgeMode' => ['dmr'], 'bridgeNode' => ['4321'],
		'bridgeTitle' => [''], 'bridgeDetailTitle' => [''], 'bridgeFriendlyName' => [''],
		'bridgeClientSource' => ['auto'], 'bridgeClientUrl' => [''], 'bridgeClientUsername' => [''],
		'bridgeClientPassword' => [''], 'bridgeCardType' => ['standard'], 'bridgeFixedRecovery' => ['0'],
		'bridgeBackendMode' => ['managed'], 'bridgePermission' => [''], 'bridgeApprovedDestinations' => [''],
		'bridgeAbinfoPath' => [''], 'bridgeDvswitchScript' => [''], 'bridgeAnalogConfig' => [''],
		'bridgeYsfGatewayConfig' => [''], 'bridgeMmdvmConfig' => [''], 'bridgeYsfGatewayService' => [''],
		'bridgeMmdvmService' => [''], 'bridgeAnalogBridgeService' => [''], 'bridgeEmulatorService' => [''],
		'bridgeYsfHostsPath' => [''], 'bridgeYsfCustomReflectors' => [''], 'bridgeAllowTune' => ['0'],
		'bridgeInstance' => [''], 'bridgeGatewayConfig' => [''], 'bridgeGatewayService' => [''],
		'bridgeDigitalMmdvmService' => [''], 'bridgeDigitalAnalogService' => [''], 'bridgeDigitalEmulatorService' => [''],
		'bridgeMqttName' => [''], 'bridgeMmdvmMqttName' => [''], 'bridgeFixedDestination' => [''],
		'bridgeM17Callsign' => [''], 'bridgeM17BindPort' => [''], 'bridgeM17UsrpRxPort' => [''],
		'bridgeM17UsrpTxPort' => [''], 'bridgeM17Reflector' => [''], 'bridgeM17Host' => [''],
		'bridgeM17Port' => [''], 'bridgeM17Module' => [''],
	];
	$_POST = array_replace($defaults, $values);
	$error = '';
	$rows = asrSettingsBridgeRowsFromPost($error, $existing, $mainNode);
	if($error !== '') throw new RuntimeException($error);
	check(count($rows) === 1, 'Expected exactly one bridge row.');
	return $rows[0];
}

foreach(['dmr', 'ysf', 'zello'] as $mode) {
	$row = postedBridge(['bridgeMode' => [$mode], 'bridgeBackendMode' => ['managed']]);
	check($row['cardType'] === 'standard' && $row['clientSource'] === 'auto', "$mode Standard card did not preserve simple Auto behavior.");
}
check(asrSettingsDefaultDetailTitle('zello') === 'Recent Talkers', 'Zello detail default is not mode-aware.');
check(asrSettingsDefaultDetailTitle('p25') === 'Linked Clients', 'P25 detail default is not mode-aware.');
check(asrSettingsClientPayloadHasSupportedShape([]), 'An authoritative empty client list was rejected.');
check(asrSettingsClientPayloadHasSupportedShape(['clients' => []]), 'A grouped client list was rejected.');
check(!asrSettingsClientPayloadHasSupportedShape(['status' => 'ok']), 'An unrelated JSON object was accepted as a client feed.');

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
check($p25Net['cardType'] === 'p25_net' && $p25Net['approvedDestinations'] === ['10200', '10201'], 'P25 Net allowlist was not enforced.');

$nxdn = postedBridge([
	'bridgeMode' => ['nxdn'], 'bridgeCardType' => ['net'], 'bridgePermission' => ['self_owned'],
	'bridgeApprovedDestinations' => ['65000'],
]);
check($nxdn['cardType'] === 'nxdn_net' && $nxdn['approvedDestinations'] === ['65000'], 'NXDN Net allowlist was not enforced.');

$dmr = postedBridge([
	'bridgeMode' => ['dmr'], 'bridgeCardType' => ['net'], 'bridgePermission' => ['approved'],
	'bridgeApprovedDestinations' => [''], 'bridgeAbinfoPath' => ['/tmp/ABInfo_12345.json'],
	'bridgeDvswitchScript' => ['/opt/MMDVM_Bridge_Test/dvswitch.sh'],
	'bridgeAnalogConfig' => ['/opt/Analog_Bridge_Test/Analog_Bridge.ini'],
]);
check($dmr['linkAlias'] === '999123456' && $dmr['approvedDestinations'] === [], 'DMR Net manual-entry card required an approved TG list.');

$ysf = postedBridge([
	'bridgeMode' => ['ysf'], 'bridgeCardType' => ['net'], 'bridgePermission' => ['approved'],
	'bridgeApprovedDestinations' => [''], 'bridgeAllowTune' => ['1'],
	'bridgeYsfGatewayConfig' => ['/opt/YSFGateway_test/YSFGateway.ini'],
	'bridgeMmdvmConfig' => ['/opt/MMDVM_Bridge_test/MMDVM_Bridge.ini'],
	'bridgeYsfGatewayService' => ['ysfgateway_test.service'], 'bridgeMmdvmService' => ['mmdvm_test.service'],
]);
check($ysf['approvedDestinations'] === [], 'YSF Net manual-entry card required an approved reflector list.');

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

$m17 = postedBridge([
	'bridgeMode' => ['m17'], 'bridgeBackendMode' => ['managed'], 'bridgePermission' => ['self_owned'],
	'bridgeM17Callsign' => ['N0CALL'], 'bridgeM17Reflector' => ['M17-TST'],
	'bridgeM17Host' => ['m17.example.net'], 'bridgeM17Port' => ['17000'], 'bridgeM17Module' => ['A'],
]);
check($m17['m17AudioQualified'] === false && $m17['m17BindPort'] > 0, 'M17 qualification remained editable or ports were not assigned.');

$m17Net = postedBridge([
	'bridgeMode' => ['m17'], 'bridgeCardType' => ['net'], 'bridgePermission' => ['approved'],
	'bridgeM17Callsign' => ['N0CALL'],
	'bridgeApprovedDestinations' => ['M17-TST | m17.example.net | 17000 | A'],
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

expectFailure(static function (): void {
	postedBridge(['bridgeMode' => ['p25'], 'bridgeCardType' => ['net'], 'bridgePermission' => ['approved']]);
}, 'at least one approved destination');

expectFailure(static function (): void {
	postedBridge([
		'bridgeMode' => ['m17'], 'bridgeCardType' => ['net'], 'bridgePermission' => ['approved'],
		'bridgeM17Callsign' => ['N0CALL'], 'bridgeApprovedDestinations' => ['not a target'],
	]);
}, 'REFLECTOR | HOST | PORT | MODULE');

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

echo "ASR bridge Settings self-test passed\n";
