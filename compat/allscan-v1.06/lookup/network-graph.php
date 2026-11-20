<?php
// Authenticated, bounded AllStarLink topology proxy for standard ASR.
require_once('../include/common.php');
$msg = [];
asInit($msg);
asrInitAuthenticatedUser($msg);
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: private, no-store, no-cache, must-revalidate, max-age=0');
$node = (string)($_GET['node'] ?? '');
if (!preg_match('/^[0-9]{3,7}$/D', $node)) {
    http_response_code(400);
    echo json_encode(['error' => 'Invalid node']);
    exit;
}
$dir = sys_get_temp_dir() . '/asr-standard-network-graph';
if (!is_dir($dir) && !@mkdir($dir, 0700, true) && !is_dir($dir)) {
    http_response_code(503);
    echo json_encode(['error' => 'Cache unavailable']);
    exit;
}
$cache = $dir . '/' . $node . '.json';
$cached = static function () use ($cache): ?string {
    if (!is_file($cache)) return null;
    $age = time() - (int)@filemtime($cache);
    if ($age > 900) return null; // Never serve topology more than 15 minutes stale.
    $raw = @file_get_contents($cache);
    if ($raw === false) return null;
    if ($age > 300) {
        $data = json_decode($raw, true);
        if (!is_array($data)) return null;
        $data['reported'] = false;
        $data['status'] = 'stale';
        $data['stale_seconds'] = $age;
        return json_encode($data);
    }
    return $raw;
};
if (is_file($cache) && time() - filemtime($cache) < 300 && ($raw = $cached()) !== null) {
    echo $raw;
    exit;
}
$lock = @fopen($dir . '/upstream.lock', 'c+');
if (!$lock || !flock($lock, LOCK_EX | LOCK_NB)) {
    echo $cached() ?? json_encode(['node' => $node, 'links' => [], 'reported' => false, 'status' => 'busy']);
    exit;
}
$budget = $dir . '/budget.json';
$now = time();
$times = json_decode(@file_get_contents($budget) ?: '[]', true);
$times = is_array($times) ? array_values(array_filter($times, static fn($t) => is_numeric($t) && $t > $now - 60)) : [];
if (count($times) >= 10) {
    flock($lock, LOCK_UN);
    fclose($lock);
    echo $cached() ?? json_encode(['node' => $node, 'links' => [], 'reported' => false, 'status' => 'rate-limited']);
    exit;
}
$times[] = $now;
@file_put_contents($budget, json_encode($times), LOCK_EX);
flock($lock, LOCK_UN);
fclose($lock);
$ctx = stream_context_create(['http' => [
    'timeout' => 4,
    'ignore_errors' => false,
    'header' => "User-Agent: ASR-Standard-Graph/1.0\r\n"
]]);
$raw = @file_get_contents('https://stats.allstarlink.org/api/stats/' . $node, false, $ctx);
$data = $raw === false ? null : json_decode($raw, true);
$links = $data['stats']['data']['links'] ?? [];
$links = is_array($links) ? $links : [];
$links = array_values(array_unique(array_filter(array_map('strval', $links),
    static fn($n) => (bool)preg_match('/^[0-9]{3,7}$/D', $n))));
$out = json_encode(['node' => $node, 'links' => array_slice($links, 0, 250),
    'reported' => is_array($data['stats'] ?? null), 'status' => is_array($data['stats'] ?? null) ? 'ok' : 'not-reporting', 'source' => 'AllStarLink stats API']);
if (is_array($data)) @file_put_contents($cache, $out, LOCK_EX);
echo is_array($data) ? $out : ($cached() ?? $out);
