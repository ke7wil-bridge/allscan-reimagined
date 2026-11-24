<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
header('X-Robots-Tag: noindex, nofollow');

const ASRPLUS_TOKEN_HELPER = '/usr/local/sbin/asrplus-token';
const ASRPLUS_CONTROL_HELPER = '/usr/local/sbin/asrplus-control';
const ASRPLUS_STATUS_PATH = '/run/allscan-reimagined/asrplus-status.json';

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

function asrplus_json(array $payload, int $status = 200): never {
    http_response_code($status);
    echo json_encode($payload, JSON_UNESCAPED_SLASHES);
    exit;
}

function asrplus_bearer_token(): string {
    $authorization = trim((string) ($_SERVER['HTTP_AUTHORIZATION'] ?? ''));
    if ($authorization === '') return '';
    if (!preg_match('/^Bearer\s+(\S+)$/iD', $authorization, $match)) {
        asrplus_json(['ok' => false, 'error' => 'Invalid Authorization header.'], 401);
    }
    return $match[1];
}

function asrplus_secure_request(): bool {
    return strtolower((string) ($_SERVER['HTTPS'] ?? '')) === 'on';
}

function asrplus_run_helper(array $command, array $input): array {
    $process = proc_open($command, [
        0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w'],
    ], $pipes);
    if (!is_resource($process)) return [1, [], 'Security helper is unavailable.'];
    fwrite($pipes[0], json_encode($input, JSON_UNESCAPED_SLASHES));
    fclose($pipes[0]);
    $stdout = stream_get_contents($pipes[1]); fclose($pipes[1]);
    $stderr = stream_get_contents($pipes[2]); fclose($pipes[2]);
    $status = proc_close($process);
    $payload = json_decode((string) $stdout, true);
    return [$status, is_array($payload) ? $payload : [], trim((string) $stderr)];
}

function asrplus_canonical_hub(string $hub): string {
    $aliases = [
        'masterhub' => 'kentucky', 'kentucky' => 'kentucky',
        'arizona-hub' => 'phoenix', 'phoenix' => 'phoenix',
        'kentucky-hub' => 'ky2', 'ky2' => 'ky2',
        'texas-hub' => 'texas', 'texas' => 'texas',
    ];
    return $aliases[strtolower($hub)] ?? '';
}

function asrplus_permission(string $method, string $action): string {
    if ($method === 'GET') return 'status:read';
    return match ($action) {
        'connect' => 'control:connect',
        'disconnect' => 'control:disconnect',
        'ban' => 'control:ban',
        'unban' => 'control:unban',
        default => '',
    };
}

function asrplus_token_auth(string $token, string $hub, string $permission, string $method): array {
    [$status, $payload] = asrplus_run_helper(
        ['sudo', '-n', ASRPLUS_TOKEN_HELPER, 'authenticate'],
        [
            'token' => $token, 'node' => $hub, 'permission' => $permission,
            'method' => $method, 'secure' => asrplus_secure_request(),
            'source' => substr((string) ($_SERVER['REMOTE_ADDR'] ?? ''), 0, 80),
        ]
    );
    if ($status !== 0 || empty($payload['ok'])) {
        asrplus_json(['ok' => false, 'error' => (string) ($payload['error'] ?? 'Token denied.')], 403);
    }
    return $payload;
}

function asrplus_filter_status(array $payload, string $hub): array {
    $hubs = $payload['hubs'] ?? null;
    if (!is_array($hubs)) return ['ok' => false, 'error' => 'Status payload format is invalid.'];
    $payload['hubs'] = array_values(array_filter($hubs, static function ($candidate) use ($hub): bool {
        if (!is_array($candidate)) return false;
        return asrplus_canonical_hub((string) ($candidate['id'] ?? '')) === $hub;
    }));
    if (count($payload['hubs']) !== 1) return ['ok' => false, 'error' => 'Authorized hub status is unavailable.'];
    return [
        'ok' => !empty($payload['ok']),
        'generatedEpoch' => $payload['generatedEpoch'] ?? null,
        'hubs' => $payload['hubs'],
    ];
}

$method = strtoupper((string) ($_SERVER['REQUEST_METHOD'] ?? 'GET'));
if (!in_array($method, ['GET', 'POST'], true)) {
    header('Allow: GET, POST');
    asrplus_json(['ok' => false, 'error' => 'Method not allowed.'], 405);
}

$token = asrplus_bearer_token();
$loggedIn = isset($user->user_id) && validDbID($user->user_id);
$request = [];
if ($method === 'POST') {
    $request = json_decode((string) file_get_contents('php://input'), true);
    if (!is_array($request)) asrplus_json(['ok' => false, 'error' => 'Invalid JSON request.'], 400);
}
$requestedHub = (string) ($method === 'GET' ? ($_GET['hub'] ?? '') : ($request['hub'] ?? ''));
$hub = asrplus_canonical_hub($requestedHub);
$action = strtolower((string) ($request['action'] ?? ''));
$permission = asrplus_permission($method, $action);
$tokenClaims = [];

if ($token !== '') {
    if ($hub === '' || $permission === '') {
        asrplus_json(['ok' => false, 'error' => 'A valid hub and permitted operation are required.'], 400);
    }
    $tokenClaims = asrplus_token_auth($token, $hub, $permission, $method);
} elseif (!$loggedIn) {
    asrplus_json(['ok' => false, 'error' => 'Login or bearer token required.'], 403);
}

if ($method === 'GET') {
    if (!is_readable(ASRPLUS_STATUS_PATH)) {
        asrplus_json(['ok' => false, 'error' => 'Network status is not ready.'], 503);
    }
    $payload = json_decode((string) file_get_contents(ASRPLUS_STATUS_PATH), true);
    if (!is_array($payload)) asrplus_json(['ok' => false, 'error' => 'Network status is invalid.'], 503);
    if ($token !== '') $payload = asrplus_filter_status($payload, $hub);
    header('X-ASRPlus-Response-Epoch-Ms: ' . (string) floor(microtime(true) * 1000));
    asrplus_json($payload, empty($payload['ok']) ? 503 : 200);
}

if ($token === '') {
    if (!modifyOk()) asrplus_json(['ok' => false, 'error' => 'Node-control permission required.'], 403);
    if ((string) ($_SERVER['HTTP_X_ASRPLUS_REQUESTED_WITH'] ?? '') !== 'control') {
        asrplus_json(['ok' => false, 'error' => 'Invalid control request.'], 403);
    }
    $request['actor'] = substr((string) ($user->name ?? ''), 0, 80);
} else {
    $request['actor'] = 'token:' . (string) ($tokenClaims['tokenId'] ?? 'unknown');
    $request['hub'] = $hub;
}

[$exitCode, $result, $stderr] = asrplus_run_helper(
    ['sudo', '-n', ASRPLUS_CONTROL_HELPER], $request
);
if (!$result) $result = ['ok' => false, 'error' => $stderr !== '' ? $stderr : 'Control helper returned invalid output.'];
asrplus_json($result, $exitCode === 0 && !empty($result['ok']) ? 200 : 400);
