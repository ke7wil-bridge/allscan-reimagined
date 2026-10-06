#!/usr/bin/env php
<?php
declare(strict_types=1);

$root = dirname(__DIR__);
$settingsDir = $root . '/compat/allscan-v1.01/asr-settings';
chdir($settingsDir);
define('ASR_SETTINGS_FUNCTIONS_ONLY', true);
// The isolated controller test omits common.php; its URL migration helper
// is irrelevant to bridge setup but is called when a real config is present.
function asrRebaseLegacyWebPath($path, $defaultPath = '') {
    return (string) ($path ?: $defaultPath);
}
require $settingsDir . '/index.php';

$settingsSource = file_get_contents($settingsDir . '/index.php');
if(!is_string($settingsSource)
	|| !str_contains($settingsSource, 'str_ends_with((string)$asrAction, \'-install\')')
	|| !str_contains($settingsSource, "allscan-reimagined-friendly-names --once"))
	throw new RuntimeException('Guided provisioning does not reconcile private-node friendly names.');

function check(bool $condition, string $message): void {
	if(!$condition) throw new RuntimeException($message);
}

check(substr_count($settingsSource, '<option value="net_bridge">Net Bridge</option>') === 1,
	'Add Bridge does not expose exactly one unified Net Bridge choice.');
check(!str_contains($settingsSource, '<option value="net">Net Bridge</option>'),
	'Add Bridge still exposes per-mode Net Bridge installation.');
foreach(['dmr','ysf','p25','nxdn','m17'] as $mode)
	check(str_contains($settingsSource, "'{$mode}' =>") || str_contains($settingsSource, "'{$mode}'=>"), "Unified setup is missing $mode.");
check(!str_contains($settingsSource, 'm17.example.net'), 'Fake M17 host default remains in Settings.');
$helperSource = file_get_contents($root . '/scripts/asr-bridge-setup-helper.py');
check(is_string($helperSource) && str_contains($helperSource, '"net-bridge-plan"')
	&& str_contains($helperSource, 'allscan-reimagined-net-bridge-mode-control'),
	'Unified setup can bypass the constrained lifecycle controller.');

$unified = [];
foreach(['dmr','ysf','p25','nxdn','m17'] as $mode)
	$unified[] = ['id'=>$mode . '_net', 'mode'=>$mode, 'node'=>'1999', 'cardType'=>$mode . '_net', 'title'=>strtoupper($mode) . ' Net Bridge'];
ob_start();
asrSettingsUnifiedNetBridgePanel($unified, 'dmr');
$unifiedHtml = (string)ob_get_clean();
check(substr_count($unifiedHtml, 'asr-unified-net-bridge-settings') === 1
	&& substr_count($unifiedHtml, 'asr-net-mode-settings-row') === 5,
	'Existing unified configuration did not render as one Settings bridge with five modes.');

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
$filterError = '';
check(asrSettingsCleanFilteredStations("n0call\nSRV_custom,n0call", $filterError) === ['N0CALL', 'SRV_CUSTOM'], 'Custom station filters were not normalized and deduplicated.');
check($filterError === '', 'Valid custom station filters reported an error.');
$filterError = '';
check(asrSettingsCleanFilteredStations('BAD*', $filterError) === [] && $filterError !== '', 'Invalid custom station filter was accepted.');
check(asrSettingsDefaultConfig()['filterServiceStations'] === true, 'Built-in station filtering is not enabled by default.');

$aggregateUrf = [
	'bridges' => [[
		'id' => 'urf', 'mode' => 'urf', 'node' => '1001', 'urfReflector' => true,
		'title' => 'URFWIL', 'installerMetadata' => 'preserve-me',
	]],
];
$urfConfig = asrSettingsExistingUrfConfig($aggregateUrf);
check($urfConfig['enabled'] && $urfConfig['aggregate'] && $urfConfig['node'] === '1001', 'Aggregate URF bridge was not discovered.');
check($urfConfig['modes'] === ['dmr','ysf','p25','nxdn','m17'], 'Aggregate URF defaults were not represented.');
$savedAggregateUrf = asrSettingsUrfBridgesForSave($aggregateUrf, [
	'enabled' => true, 'node' => '1002', 'modes' => ['dmr','ysf','p25','nxdn','m17'],
]);
check(count($savedAggregateUrf) === 1 && $savedAggregateUrf[0]['mode'] === 'urf', 'Aggregate URF bridge was converted into per-mode rows.');
check($savedAggregateUrf[0]['node'] === '1002' && ($savedAggregateUrf[0]['installerMetadata'] ?? '') === 'preserve-me', 'Aggregate URF metadata did not survive a save.');
check(!array_key_exists('modes', $savedAggregateUrf[0]), 'Default aggregate URF save introduced a format-only modes field.');
$savedSubsetUrf = asrSettingsUrfBridgesForSave($aggregateUrf, [
	'enabled' => true, 'node' => '1001', 'modes' => ['dmr','m17'],
]);
check(($savedSubsetUrf[0]['modes'] ?? []) === ['dmr','m17'], 'Aggregate URF mode selection did not round trip.');

$configuredAggregate = ['bridges' => [[
	'id'=>'urf','mode'=>'urf','node'=>'1001','title'=>'URFWIL','urfReflector'=>true,'urfGroupId'=>'urf',
	'modeConfig'=>[
		'dmr'=>['network'=>'TGIF','talkgroup'=>'86753','dmrId'=>'3224939','futureKey'=>'keep-dmr'],
		'ysf'=>['reflector'=>'US-KE7WIL-YSF','reflectorId'=>'64189'],
		'p25'=>['destination'=>'64189'], 'nxdn'=>['destination'=>'15846'],
		'm17'=>['reflector'=>'M17-WIL','host'=>'m17.example.net','port'=>17000,'module'=>'A','callsign'=>'KE7WIL-M'],
	],
	'futureAggregateKey'=>['preserve'=>true],
	]]];
$originalConfiguredAggregate = $configuredAggregate['bridges'][0];
$_POST = [
	'urfEnabled'=>'1','urfNode'=>'1001','urfModes'=>['dmr','ysf','p25','nxdn','m17'],
	'urfConfig'=>[
		'dmr'=>['network'=>'TGIF','talkgroup'=>'86753','dmrId'=>'3224939'],
		'ysf'=>['reflector'=>'US-KE7WIL-YSF','reflectorId'=>'64189'],
		'p25'=>['destination'=>'64189'], 'nxdn'=>['destination'=>'15846'],
		'm17'=>['reflector'=>'M17-WIL','host'=>'m17.example.net','port'=>'17000','module'=>'A','callsign'=>'KE7WIL-M'],
	],
];
$urfError = '';
$postedUrf = asrSettingsUrfConfigFromPost($urfError, $configuredAggregate);
check($urfError === '', 'Valid aggregate URF settings were rejected: ' . $urfError);
$roundTrippedUrf = asrSettingsUrfBridgesForSave($configuredAggregate, $postedUrf);
check($roundTrippedUrf === [$originalConfiguredAggregate], 'Unchanged aggregate URF save altered its structure or value types.');
$loadedUrf = asrSettingsExistingUrfConfig($configuredAggregate);
check($loadedUrf['modeConfig']['dmr']['talkgroup'] === '86753' && $loadedUrf['modeConfig']['ysf']['reflector'] === 'US-KE7WIL-YSF', 'URF mode-specific values did not load.');
check($loadedUrf['modeConfig']['p25']['destination'] === '64189' && $loadedUrf['modeConfig']['nxdn']['destination'] === '15846', 'P25/NXDN destinations did not load.');
check($loadedUrf['modeConfig']['m17']['reflector'] === 'M17-WIL' && $loadedUrf['modeConfig']['m17']['module'] === 'A', 'M17 configuration did not load.');

$_POST['urfConfig']['dmr']['talkgroup'] = '86754';
$_POST['urfConfig']['ysf']['reflector'] = 'US-KE7WIL-YSF2';
$_POST['urfConfig']['p25']['destination'] = '64190';
$_POST['urfConfig']['nxdn']['destination'] = '15847';
$_POST['urfConfig']['m17']['module'] = 'B';
$urfError = '';
$editedUrf = asrSettingsUrfConfigFromPost($urfError, $configuredAggregate);
check($urfError === '', 'Edited aggregate URF settings were rejected: ' . $urfError);
$editedRecord = asrSettingsUrfBridgesForSave($configuredAggregate, $editedUrf)[0];
check($editedRecord['modeConfig']['dmr']['talkgroup'] === '86754', 'DMR destination edit was not saved.');
check($editedRecord['modeConfig']['ysf']['reflector'] === 'US-KE7WIL-YSF2', 'YSF reflector edit was not saved.');
check($editedRecord['modeConfig']['p25']['destination'] === '64190' && $editedRecord['modeConfig']['nxdn']['destination'] === '15847', 'P25/NXDN edits were not saved.');
check($editedRecord['modeConfig']['m17']['module'] === 'B' && $editedRecord['modeConfig']['dmr']['futureKey'] === 'keep-dmr', 'M17 edit or unknown mode metadata preservation failed.');

$_POST['urfModes'] = ['dmr','ysf','p25','nxdn'];
$urfError = '';
$disabledM17 = asrSettingsUrfConfigFromPost($urfError, $configuredAggregate);
$disabledRecord = asrSettingsUrfBridgesForSave($configuredAggregate, $disabledM17)[0];
check($disabledRecord['modes'] === ['dmr','ysf','p25','nxdn'] && $disabledRecord['modeConfig']['m17']['reflector'] === 'M17-WIL', 'Disabling a URF mode erased its configuration.');
$_POST['urfModes'][] = 'm17';
$urfError = '';
$reenabledM17 = asrSettingsUrfConfigFromPost($urfError, ['bridges'=>[$disabledRecord]]);
$reenabledRecord = asrSettingsUrfBridgesForSave(['bridges'=>[$disabledRecord]], $reenabledM17)[0];
check($reenabledRecord['modeConfig']['m17']['reflector'] === 'M17-WIL', 'Re-enabling a URF mode did not restore its configuration.');

ob_start();
asrSettingsRenderUrfMode('dmr', 'DMR', ['network'=>'TGIF','talkgroup'=>'86753'], true);
asrSettingsRenderUrfMode('ysf', 'YSF', ['reflector'=>'US-KE7WIL-YSF'], true);
asrSettingsRenderUrfMode('p25', 'P25', ['destination'=>'64189'], true);
asrSettingsRenderUrfMode('nxdn', 'NXDN', ['destination'=>'15846'], true);
asrSettingsRenderUrfMode('m17', 'M17', ['reflector'=>'M17-WIL','host'=>'m17.example.net','port'=>'17000','module'=>'A'], true);
$urfMarkup = (string)ob_get_clean();
foreach(['TGIF · TG 86753','US-KE7WIL-YSF','Destination 64189','Destination 15846','M17-WIL · Module A'] as $summary) check(strpos($urfMarkup, $summary) !== false, "URF summary missing: $summary");
foreach(['urfConfig[dmr][talkgroup]','urfConfig[ysf][reflector]','urfConfig[p25][destination]','urfConfig[nxdn][destination]','urfConfig[m17][reflector]','urfConfig[m17][usrpTxPort]'] as $name) check(strpos($urfMarkup, 'name="' . $name . '"') !== false, "URF field missing: $name");
check(strpos($urfMarkup, 'TGIF Account / Session Authentication') !== false && strpos($urfMarkup, 'Destination Talkgroup') !== false, 'TGIF account and DMR destination were conflated or omitted.');
foreach([
	'DMR carries reflector audio through a DMR network',
	'exact reflector name published in your YSF host list',
	'P25 routes reflector traffic to one numeric destination',
	'NXDN routes reflector traffic to one numeric talkgroup',
	'name such as M17-WIL identifies the reflector',
] as $guidance) check(stripos($urfMarkup, $guidance) !== false, "URF beginner guidance missing: $guidance");
ob_start();
asrSettingsRenderUrfMode('p25', 'P25', ['destination'=>'64189'], true);
asrSettingsRenderUrfMode('nxdn', 'NXDN', ['destination'=>'15846'], true);
$simpleUrfMarkup = (string)ob_get_clean();
check(strpos($simpleUrfMarkup, 'Advanced runtime fields') === false && strpos($simpleUrfMarkup, 'Advanced Runtime Fields') === false, 'P25/NXDN still expose a dead advanced-runtime disclosure.');

ob_start();
asrSettingsBridgePanel(['id'=>'zello','mode'=>'zello','cardType'=>'standard','node'=>'1003','title'=>'Zello'], [], [], ['available'=>true,'bridges'=>[]]);
$zelloMarkup = (string)ob_get_clean();
check(strpos($zelloMarkup, 'data-bridge-tab-label="Recent Talkers"') !== false, 'Zello talker tab was not named for its real data.');
check(strpos($zelloMarkup, 'not a list of signed-in Zello users') !== false && strpos($zelloMarkup, 'does not kick or disconnect Zello accounts') !== false, 'Zello talker capability is misleading.');

ob_start();
asrSettingsBridgePanel(['id'=>'dmr','mode'=>'dmr','cardType'=>'standard','node'=>'1002','title'=>'DMR'], [], [], ['available'=>true,'bridges'=>[]]);
$dmrMarkup = (string)ob_get_clean();
check(strpos($dmrMarkup, 'data-bridge-tab-label="TGIF Sessions"') !== false, 'DMR does not expose the authoritative TGIF session workflow.');
check((bool)preg_match('/asr-connected-client-settings"[^>]* hidden/', $dmrMarkup), 'DMR still exposes the duplicate generic client-source path.');
check(strpos($dmrMarkup, 'name="bridgeDetailTitle[]"') !== false, 'Advanced client/talker heading lost its round-trip field name.');

$_POST = [
	'urfEnabled'=>'1','urfNode'=>'1001','urfModes'=>['dmr','ysf','p25','nxdn','m17'],
	'urfConfig'=>['dmr'=>['network'=>'TGIF','talkgroup'=>'86753'],'m17'=>['reflector'=>'M17-WIL','host'=>'m17.example.net','port'=>'17000','module'=>'A']],
];
$urfError = '';
$newUrf = asrSettingsUrfConfigFromPost($urfError, ['bridges'=>[]]);
$newUrfRows = asrSettingsUrfBridgesForSave(['bridges'=>[]], $newUrf);
check($urfError === '' && count($newUrfRows) === 1 && $newUrfRows[0]['mode'] === 'urf', 'New URF configuration was split into independent bridge records.');
check($newUrfRows[0]['tgifTalkgroup'] === '86753' && $newUrfRows[0]['m17Reflector'] === 'M17-WIL', 'New aggregate URF mode settings were not stored on the authoritative aggregate record.');

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
	'setupBridgeId' => 'urf_dmr', 'setupTitle' => 'DMR Bridge',
	'setupCallsign' => 'ke7wil', 'setupDigitalId' => '3224939',
	'setupDestination' => '86753',
];
$dmrSetup = asrSettingsDmrSetupPayload(false, $setupError);
check($setupError === '' && $dmrSetup['callsign'] === 'KE7WIL' && $dmrSetup['destination'] === 86753 && $dmrSetup['authMode'] === 'legacy' && !isset($dmrSetup['tgifPassword']), 'Valid DMR preview request was not normalized or leaked a password field.');
$dmrInstall = asrSettingsDmrSetupPayload(true, $setupError);
check($setupError === '' && $dmrInstall['authMode'] === 'legacy' && !isset($dmrInstall['tgifPassword']), 'Legacy TGIF install unexpectedly required a key.');
$_POST['setupTgifAuthMode'] = 'secured';
$_POST['setupTgifPassword'] = 'secret-123';
$dmrInstall = asrSettingsDmrSetupPayload(true, $setupError);
check($setupError === '' && $dmrInstall['tgifPassword'] === 'secret-123', 'Secured TGIF hotspot key was not accepted.');
$_POST['setupTgifPassword'] = "bad\npassword";
asrSettingsDmrSetupPayload(true, $setupError);
check(strpos($setupError, 'hotspot key') !== false, 'Unsafe TGIF hotspot key was accepted.');

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

$managedUrf = [[
	'id' => 'urf_dmr', 'mode' => 'dmr', 'node' => '1001', 'title' => 'DMR Bridge',
	'backendMode' => 'managed', 'instance' => 'urf_dmr', 'urfReflector' => true,
	'urfName' => 'URFWIL', 'tgifTalkgroup' => '86753', 'dmrId' => '3224939',
]];
$preservedUrf = asrSettingsUrfModeBridges(['enabled' => false, 'node' => '', 'modes' => []], $managedUrf);
check(count($preservedUrf) === 1 && $preservedUrf[0] === $managedUrf[0], 'Managed URF/TGIF bridge was lost or rewritten by a generic Settings save.');

$settingsSource = file_get_contents($settingsDir . '/index.php');
check(strpos($settingsSource, "mode === 'net_bridge' ? 'net-bridge-plan' : mode + '-plan'") !== false, 'Bridge setup preview is not wired to the selected helper endpoint.');
check(strpos($settingsSource, "mode === 'net_bridge' ? 'net-bridge-install' : mode + '-install'") !== false, 'Bridge setup install is not wired to the selected helper endpoint.');
check(strpos($settingsSource, "'p25-plan', 'p25-install'") !== false, 'P25 helper actions are unavailable.');
check(strpos($settingsSource, "'nxdn-plan', 'nxdn-install'") !== false, 'NXDN helper actions are unavailable.');
check(strpos($settingsSource, "'ysf-plan', 'ysf-install'") !== false, 'YSF helper actions are unavailable.');
check(strpos($settingsSource, "'dmr-plan', 'dmr-install'") !== false, 'DMR helper actions are unavailable.');
check(strpos($settingsSource, "'zello-plan', 'zello-install'") !== false, 'Zello helper actions are unavailable.');
check(stripos($settingsSource, 'dstar') === false, 'D-Star remains exposed by Settings.');
check(strpos($settingsSource, 'data-bridge-setup-path') !== false, 'Bridge type selector is missing.');
check(strpos($settingsSource, '<option value="standard">Standard Bridge</option><option value="urf">URF Reflector</option>') !== false, 'Standard Bridge/URF choices are incomplete.');
check(strpos($settingsSource, '<option value="net_bridge">Net Bridge</option>') !== false, 'Unified Net Bridge choice is missing.');
check(strpos($settingsSource, "urfOption.disabled = mode === 'zello'") !== false, 'Zello URF selection is not blocked.');
check(strpos($settingsSource, "var isUnifiedNet = mode === 'net_bridge'") !== false, 'Unified Net Bridge mode handling is missing.');
check(strpos($settingsSource, 'DMR / TGIF via URF') === false, 'DMR is still mislabeled as URF-only.');
check(strpos($settingsSource, 'data-bridge-setup-zello-private-key') !== false, 'Zello private-key input is missing.');
check(strpos($settingsSource, 'data-bridge-setup-tgif-password') !== false, 'TGIF password input is missing from the guided wizard.');
check(strpos($settingsSource, "p25: {port: '41000'") !== false, 'P25 setup defaults are missing.');
check(strpos($settingsSource, "nxdn: {port: '41400'") !== false, 'NXDN setup defaults are missing.');
check(strpos($settingsSource, "ysf: {port: '42000'") !== false, 'YSF setup defaults are missing.');
check(strpos($settingsSource, "setupPlanDigest") !== false, 'Bridge setup plan digest binding is missing.');
check(strpos($settingsSource, 'data-bridge-setup-install disabled') !== false, 'Install control is not fail-closed before preview.');

echo "ASR bridge Settings self-test passed\n";
