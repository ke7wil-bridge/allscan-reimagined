#!/usr/bin/env php
<?php
require_once ($argv[1] ?? __DIR__ . '/../compat/allscan-v1.02/include/asrLinkState.php');

$dir = sys_get_temp_dir() . '/asr-link-state-' . getmypid();
if (!mkdir($dir, 0700) && !is_dir($dir)) throw new RuntimeException('Could not create test directory.');
$path = $dir . '/astapi-641890.json';
$payload = ['updated'=>1000.0,'current'=>['641890'=>['remote_nodes'=>[
    ['node'=>'1001','link'=>'Established','direction'=>'OUT','lnodes'=>[]],
    ['node'=>'1004','link'=>'Established','direction'=>'IN','lnodes'=>[]],
    ['node'=>1,'link'=>'','lnodes'=>['1002','1003','1005','1006']],
]]]];
file_put_contents($path, json_encode($payload));
$state = asrLinkStateSnapshot('641890', $dir, null, 1005.0);
assert($state['available'] === true && $state['source'] === 'live-status-cache');
foreach (['1001','1002','1003','1004','1005','1006'] as $bridgeNode) {
    assert(isset($state['nodes'][$bridgeNode]), "Missing established bridge transport $bridgeNode");
}
assert(isset($state['directNodes']['1001'], $state['directNodes']['1004']));
assert(!isset($state['directNodes']['1002'], $state['directNodes']['1003']));
$managed = ['cardType'=>'standard','backendMode'=>'managed'];
$monitor = ['cardType'=>'standard','backendMode'=>'display_only'];
// Reproduce a disconnected local DMR doorway that is still advertised by
// the propagated node list. Preserve topology for display-only monitoring.
$payload['current']['641890']['remote_nodes'][0] = ['node'=>1,'lnodes'=>['1001']];
file_put_contents($path, json_encode($payload));
$state = asrLinkStateSnapshot('641890', $dir, null, 1005.0);
assert(isset($state['nodes']['1001']) && !isset($state['directNodes']['1001']));
assert(!isset(asrBridgeLinkNodes($state, $managed)['1001']));
assert(isset(asrBridgeLinkNodes($state, $monitor)['1001']));
assert(!isset(asrBridgeLinkNodes($state, $monitor + ['manualDisconnect'=>true])['1001']));
assert(isset(asrBridgeLinkNodes($state, $managed)['1004']));
$unknown = asrLinkStateSnapshot('641890', $dir, null, 1040.0);
assert($unknown['available'] === false);
$fallback = asrLinkStateSnapshot('641890', $dir, static fn() => [[
    '1001 URFWIL 0 OUT 0 ESTABLISHED', '1003 Zello 0 OUT 0 ESTABLISHED',
], 0], 1040.0);
assert($fallback['source'] === 'asterisk' && isset($fallback['nodes']['1001'], $fallback['nodes']['1003']));
assert($fallback['directNodes'] === $fallback['nodes']);
unlink($path); rmdir($dir);
echo "ASR link-state self-test passed\n";
