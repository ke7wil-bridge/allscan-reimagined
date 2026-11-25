<?php
declare(strict_types=1);
// Exercise the actual API functions with temporary runtime files and no node IO.
$source = $argv[1] ?? dirname(__DIR__) . '/asr-api.php';
$api = file_get_contents($source);
function load_api_range(string $api, string $start, string $end): void {
    $a = strpos($api, 'function ' . $start . '(');
    $b = strpos($api, 'function ' . $end . '(', $a ?: 0);
    if ($a === false || $b === false) throw new RuntimeException('API test boundary missing');
    eval(substr($api, $a, $b - $a));
}
function check(bool $ok, string $message): void { if (!$ok) throw new RuntimeException($message); }
$dir = sys_get_temp_dir() . '/asr-zello-count-' . bin2hex(random_bytes(8));
mkdir($dir, 0700);
function asrRuntimeFilePath($name) { return $GLOBALS['dir'] . '/' . $name; }
function asr_standard_bridge_live_statuses() { return []; }
function asr_dmr_net_live_statuses() { return []; }
function asr_ysf_net_live_statuses() { return []; }
function asr_next_mode_statuses() { return ['live'=>[], 'controls'=>[]]; }
function asr_runtime_config() { return ['bridges'=>[['id'=>'zello','mode'=>'zello','cardType'=>'standard'],['id'=>'ysf','mode'=>'ysf','cardType'=>'standard']]]; }
function asr_display_only_bridge_link_statuses($config) { return []; }
function asrPublicBridgeLiveStatuses($bridge, $config) { return $bridge; }
function asrPublicBridgeControls($controls) { return $controls; }
function asr_dmr_net_control_statuses() { return []; }
function asr_ysf_net_control_statuses() { return []; }
function asr_secure_service_runtime_json($path, $directory) { return null; }
function asr_zello_connection_evidence($runtime, $now) { return true; }
function asr_bridge_mode($row) { return $row['mode'] ?? $row['id']; }
function asr_tgif_node_status() { return []; }
function asrClientIdentityKeys($row) { return isset($row['name']) ? ['name:' . strtolower($row['name'])] : []; }
load_api_range($api, 'asr_decode_json_payload', 'asr_bridge_clients_payload');
load_api_range($api, 'asr_bridge_status_payload', 'asr_fast_standard_activity_payload');
try {
    $now = time();
    $old = ['name'=>'N0OLD','last_seen_epoch'=>$now-115200];
    $old2 = ['name'=>'N0OLD2','last_seen_epoch'=>$now-129600];
    $fresh = ['name'=>'N0NEW','last_tx_epoch'=>$now-10];
    $recent = ['name'=>'N0RECENT','last_seen_epoch'=>$now-7200];
    $future = ['name'=>'N0FUTURE','last_tx_epoch'=>$now+1000];
    $event = ['name'=>'N0OLD','epoch'=>$now-115200,'duration_seconds'=>4];
    file_put_contents($dir.'/connected-clients.json', json_encode(['zello'=>[$fresh], 'ysf'=>[['name'=>'N0YSF','last_seen_epoch'=>$now-10]], '_asr_meta'=>['zello'=>['kind'=>'recent','mode'=>'zello'],'ysf'=>['kind'=>'current','mode'=>'ysf']]]));
    $payload = asr_bridge_status_payload();
    check($payload['clientCounts']['zello'] === 0 && $payload['clientCounts']['ysf'] === 1, 'fallback talker rows counted as connected, or YSF count changed');
    $write = function(array $rows) use ($dir,$event) { file_put_contents($dir.'/zello-talkers.json',json_encode(['recent_users'=>$rows,'tx_events'=>[$event],'last_user'=>'N0OLD','active'=>false])); };
    $write([$old,$old2]); $payload=asr_bridge_status_payload();
    check($payload['clientCounts']['zello'] === 0 && $payload['clients']['zello'] === [] && $payload['bridge']['zello']['recent_users'] === [], 'stale Zello records survived or counted');
    check($payload['bridge']['zello']['last_source_epoch'] === $event['epoch'] && count($payload['bridge']['zello']['tx_events']) === 1, 'history/activity evidence altered');
    $write([$fresh,$recent,$old,$future,['name'=>'N0UNKNOWN'],['last_tx_epoch'=>$now]]); $payload=asr_bridge_status_payload();
    check($payload['clientCounts']['zello'] === 0 && count($payload['clients']['zello']) === 2, 'fresh talkers must remain history, not connected users');
    check($payload['clients']['zello'] === $payload['bridge']['zello']['recent_users'] && $payload['clientMeta']['zello']['kind'] === 'recent', 'history provenance or card rows differ');
    check($payload['bridge']['zello']['online'] === true && $payload['clientCounts']['ysf'] === 1, 'online state or other bridge count changed');
    echo "Zello connected-count API regression: PASS\n";
} finally { foreach (glob($dir.'/*') as $file) unlink($file); rmdir($dir); }
