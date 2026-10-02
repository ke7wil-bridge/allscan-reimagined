#!/usr/bin/env php
<?php
require_once __DIR__ . '/../compat/allscan-v1.01/include/asrLinkState.php';

$dir = sys_get_temp_dir() . '/asr-link-state-' . getmypid();
if (!mkdir($dir, 0700) && !is_dir($dir)) throw new RuntimeException('Could not create test directory.');
$path = $dir . '/astapi-641890.json';
$payload = ['updated'=>1000.0,'current'=>['641890'=>['remote_nodes'=>[
    ['node'=>'1001','link'=>'Established','lnodes'=>[]],
    ['node'=>1,'link'=>'','lnodes'=>['1002','1003']],
]]]];
file_put_contents($path, json_encode($payload));
$state = asrLinkStateSnapshot('641890', $dir, null, 1005.0);
assert($state['available'] === true && $state['source'] === 'live-status-cache');
assert(isset($state['nodes']['1001'], $state['nodes']['1002'], $state['nodes']['1003']));
$unknown = asrLinkStateSnapshot('641890', $dir, null, 1040.0);
assert($unknown['available'] === false);
$fallback = asrLinkStateSnapshot('641890', $dir, static fn() => [[
    '1001 URFWIL 0 OUT 0 ESTABLISHED', '1003 Zello 0 OUT 0 ESTABLISHED',
], 0], 1040.0);
assert($fallback['source'] === 'asterisk' && isset($fallback['nodes']['1001'], $fallback['nodes']['1003']));
unlink($path); rmdir($dir);
echo "ASR link-state self-test passed\n";
