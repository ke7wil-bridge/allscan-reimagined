<?php
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
header('X-Robots-Tag: noindex, nofollow');
require_once '/var/www/html/asr/include/common.php';
$msg = [];
$asrplusScriptName = $_SERVER['SCRIPT_NAME'] ?? '';
$_SERVER['SCRIPT_NAME'] = '/asr/index.php';
asInit($msg);
$_SERVER['SCRIPT_NAME'] = $asrplusScriptName;
$db = dbInit();
checkTables($db, $msg);
$userModel = new UserModel($db);
$user = $userModel->validate();
if (!isset($user->user_id) || !validDbID($user->user_id)) {
    http_response_code(403);
    echo json_encode(['ok' => false, 'error' => 'Login required.']);
    exit;
}
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!modifyOk()) {
        http_response_code(403);
        echo json_encode(['ok' => false, 'error' => 'Node-control permission required.']);
        exit;
    }
    if ((string) ($_SERVER['HTTP_X_ASRPLUS_REQUESTED_WITH'] ?? '') !== 'control') {
        http_response_code(403);
        echo json_encode(['ok' => false, 'error' => 'Invalid control request.']);
        exit;
    }
    $request = json_decode((string) file_get_contents('php://input'), true);
    if (!is_array($request)) {
        http_response_code(400);
        echo json_encode(['ok' => false, 'error' => 'Invalid JSON request.']);
        exit;
    }
    $request['actor'] = substr((string) ($user->name ?? ''), 0, 80);
    $process = proc_open(
        ['sudo', '-n', '/usr/local/sbin/asrplus-control'],
        [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']],
        $pipes
    );
    if (!is_resource($process)) {
        http_response_code(503);
        echo json_encode(['ok' => false, 'error' => 'Control helper is unavailable.']);
        exit;
    }
    fwrite($pipes[0], json_encode($request)); fclose($pipes[0]);
    $stdout = stream_get_contents($pipes[1]); fclose($pipes[1]);
    $stderr = stream_get_contents($pipes[2]); fclose($pipes[2]);
    $exitCode = proc_close($process);
    $result = json_decode((string) $stdout, true);
    if (!is_array($result)) $result = ['ok' => false, 'error' => trim($stderr) ?: 'Control helper returned invalid output.'];
    if ($exitCode !== 0 || empty($result['ok'])) http_response_code(400);
    echo json_encode($result, JSON_UNESCAPED_SLASHES);
    exit;
}
$path = '/run/allscan-reimagined/asrplus-status.json';
if (!is_readable($path)) {
    http_response_code(503);
    echo json_encode(['ok' => false, 'error' => 'Network status is not ready.']);
    exit;
}
header('X-ASRPlus-Response-Epoch-Ms: ' . (string) floor(microtime(true) * 1000));
readfile($path);
