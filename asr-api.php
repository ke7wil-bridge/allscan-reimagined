<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');

const ASR_LOCAL_NODE = '641890';
const ASR_DEFAULT_FAVORITES = '/var/www/html/allscan/favorites.ini';
const ASR_MESSAGE_CACHE = '/tmp/allscan-reimagined-node-message.json';

require_once __DIR__ . '/include/common.php';

$msg = [];
asInit($msg);
$db = dbInit();
$userCnt = checkTables($db, $msg);
$cfgModel = new CfgModel($db);
$userModel = new UserModel($db);
$user = $userModel->validate();

function asr_json(array $payload, int $status = 200) {
    http_response_code($status);
    echo json_encode($payload, JSON_UNESCAPED_SLASHES);
    exit;
}

function asr_error(string $message, int $status = 400) {
    asr_json(['ok' => false, 'error' => $message], $status);
}

function asr_logged_in(): bool {
    global $user;
    return isset($user->user_id) && validDbID($user->user_id);
}

function asr_auth_payload(): array {
    global $user, $gCfg;
    $loggedIn = asr_logged_in();
    return [
        'ok' => true,
        'loggedIn' => $loggedIn,
        'username' => $loggedIn ? (string) ($user->name ?? '') : '',
        'permission' => $loggedIn ? userPermission() : PERMISSION_NONE,
        'publicPermission' => (int) ($gCfg[publicPermission] ?? PERMISSION_READ_ONLY),
        'canRead' => readOk(),
        'canModify' => $loggedIn && modifyOk(),
        'canWrite' => $loggedIn && writeOk(),
        'isAdmin' => $loggedIn && adminUser(),
    ];
}

function asr_require_read(): void {
    if (!readOk()) asr_error('Login required.', 403);
}

function asr_require_modify(): void {
    if (!asr_logged_in() || !modifyOk()) asr_error('Login with node-control permission required.', 403);
}

function asr_origin_host(string $value): string {
    $host = parse_url($value, PHP_URL_HOST);
    if (!is_string($host) || $host === '') return '';

    $port = parse_url($value, PHP_URL_PORT);
    return strtolower($host . (is_int($port) ? ':' . $port : ''));
}

function asr_request_host(): string {
    return strtolower((string) ($_SERVER['HTTP_HOST'] ?? ''));
}

function asr_require_same_origin(): void {
    $requestHost = asr_request_host();
    $origin = (string) ($_SERVER['HTTP_ORIGIN'] ?? '');
    $referer = (string) ($_SERVER['HTTP_REFERER'] ?? '');

    if ($origin !== '' && asr_origin_host($origin) !== $requestHost) {
        asr_error('Cross-site control request rejected.', 403);
    }

    if ($origin === '' && $referer !== '' && asr_origin_host($referer) !== $requestHost) {
        asr_error('Cross-site control request rejected.', 403);
    }
}

function asr_require_post(): void {
    if (strtoupper((string) ($_SERVER['REQUEST_METHOD'] ?? 'GET')) !== 'POST') {
        asr_error('POST required for this action.', 405);
    }
}

function asr_allscan_dir(): string {
    return realpath(__DIR__) ?: __DIR__;
}

function asr_favorites_files(): array {
    $dir = asr_allscan_dir();
    $files = glob($dir . '/favorites*.ini') ?: [];
    sort($files, SORT_NATURAL | SORT_FLAG_CASE);
    return $files;
}

function asr_safe_favorites_file(string $requested = ''): string {
    $files = asr_favorites_files();
    $default = file_exists(ASR_DEFAULT_FAVORITES) ? ASR_DEFAULT_FAVORITES : ($files[0] ?? ASR_DEFAULT_FAVORITES);

    if ($requested === '') return $default;

    $real = realpath($requested);
    $dir = asr_allscan_dir();
    $allowedPrefix = $dir . DIRECTORY_SEPARATOR;
    if (!$real || strncmp($real, $allowedPrefix, strlen($allowedPrefix)) !== 0 || !preg_match('/\/favorites[^\/]*\.ini$/', $real)) {
        return $default;
    }

    return $real;
}

function asr_ini_values(string $contents, string $key): array {
    preg_match_all('/^\s*' . preg_quote($key, '/') . '\s*\[\]\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(.+?))\s*$/mi', $contents, $matches, PREG_SET_ORDER);
    return array_map(static function (array $match): string {
        return trim($match[1] !== '' ? $match[1] : ($match[2] !== '' ? $match[2] : $match[3]));
    }, $matches);
}

function asr_node_from_command(string $cmd): string {
    if (preg_match('/\bilink\s+\d+\s+([A-Za-z0-9*#]+)\b/i', $cmd, $match)) return $match[1];
    if (preg_match('/\b([0-9]{3,7})\b(?!.*\b[0-9]{3,7}\b)/', $cmd, $match)) return $match[1];
    return '';
}

function asr_parse_label(string $label, string $node): array {
    $text = trim(preg_replace('/\s+/', ' ', $label) ?: '');
    if ($node !== '') $text = trim(preg_replace('/\s+' . preg_quote($node, '/') . '$/', '', $text) ?: $text);

    $location = '';
    $beforeLocation = $text;
    if (strpos($text, ',') !== false) {
        [$beforeLocation, $location] = array_map('trim', explode(',', $text, 2));
    }

    $words = preg_split('/\s+/', $beforeLocation) ?: [];
    $descTokens = [];
    while (count($words) > 1) {
        $last = (string) end($words);
        if (preg_match('/^[0-9.\-+]+$|^(HUB|HUBNet|BACON|\-|enhanced|parrot|MHz)$/i', $last)) {
            array_unshift($descTokens, array_pop($words));
            continue;
        }
        break;
    }

    return [
        'name' => trim(implode(' ', $words)) ?: $text,
        'desc' => trim(implode(' ', $descTokens)),
        'location' => $location,
    ];
}

function asr_favorites_payload(string $requested = ''): array {
    $selected = asr_safe_favorites_file($requested);
    $contents = is_readable($selected) ? (string) file_get_contents($selected) : '';
    $labels = asr_ini_values($contents, 'label');
    $cmds = asr_ini_values($contents, 'cmd');
    $rows = [];

    foreach ($labels as $index => $label) {
        $cmd = $cmds[$index] ?? '';
        $node = asr_node_from_command($cmd);
        if ($node === '' && preg_match('/\b([0-9]{3,7})\b\s*$/', $label, $match)) $node = $match[1];
        if ($node === '') continue;

        $parts = asr_parse_label($label, $node);
        $rows[] = [
            'index' => (string) $index,
            'node' => $node,
            'label' => $label,
            'name' => $parts['name'],
            'desc' => $parts['desc'],
            'location' => $parts['location'],
            'rx' => '',
            'lcnt' => '',
            'href' => 'http://stats.allstarlink.org/stats/' . rawurlencode($node),
        ];
    }

    $files = array_map(static fn (string $file): array => [
        'value' => $file,
        'label' => $file,
        'selected' => $file === $selected,
    ], asr_favorites_files());

    return ['ok' => true, 'rows' => $rows, 'files' => $files, 'selectedFile' => $selected];
}

function asr_write_node_message(string $line): void {
    @file_put_contents(ASR_MESSAGE_CACHE, json_encode([
        'latestLine' => trim($line),
        'rawText' => trim($line),
        'time' => time(),
    ], JSON_UNESCAPED_SLASHES));
}

function asr_node_messages(): array {
    $payload = is_readable(ASR_MESSAGE_CACHE) ? json_decode((string) file_get_contents(ASR_MESSAGE_CACHE), true) : null;
    if (!is_array($payload)) return ['ok' => true, 'latestLine' => '', 'rawText' => ''];
    if (time() - (int) ($payload['time'] ?? 0) > 300) return ['ok' => true, 'latestLine' => '', 'rawText' => ''];
    return [
        'ok' => true,
        'latestLine' => (string) ($payload['latestLine'] ?? ''),
        'rawText' => (string) ($payload['rawText'] ?? ''),
    ];
}

function asr_favorite_action(string $action, string $node, string $requested): array {
    if (!preg_match('/^[A-Za-z0-9*#]{3,8}$/', $node)) asr_error('Invalid node.');
    $file = asr_safe_favorites_file($requested);
    if (!is_writable($file)) asr_error('Favorites file is not writable.', 500);

    $contents = (string) file_get_contents($file);
    $lines = preg_split('/\R/', $contents) ?: [];

    if ($action === 'delfav') {
        $next = [];
        for ($i = 0; $i < count($lines); $i += 1) {
            $line = $lines[$i];
            $nextLine = $lines[$i + 1] ?? '';
            if (preg_match('/^\s*label\s*\[\]\s*=/', $line) && asr_node_from_command($nextLine) === $node) {
                $i += 1;
                continue;
            }
            $next[] = $line;
        }
        file_put_contents($file, rtrim(implode(PHP_EOL, $next)) . PHP_EOL);
        asr_write_node_message("Deleted {$node} from Favorites.");
        return ['ok' => true, 'message' => "Deleted {$node} from Favorites."];
    }

    if (!preg_match('/\bilink\s+\d+\s+' . preg_quote($node, '/') . '\b/i', $contents)) {
        $entry = PHP_EOL . 'label[] = "' . addcslashes($node . ' ' . $node, '"\\') . '"' . PHP_EOL .
            'cmd[] = "rpt cmd %node% ilink 3 ' . $node . '"' . PHP_EOL;
        file_put_contents($file, rtrim($contents) . PHP_EOL . $entry);
    }
    asr_write_node_message("Added {$node} to Favorites.");
    return ['ok' => true, 'message' => "Added {$node} to Favorites."];
}

function asr_drop_clients(): array {
    $raw = shell_exec('sudo /usr/local/bin/allscan_wt_clients.sh 2>/dev/null');
    $clients = [];
    foreach (preg_split('/\R/', (string) $raw) ?: [] as $line) {
        $parts = explode('|', trim($line), 3);
        if (count($parts) !== 3) continue;

        [$label, $ip, $channel] = array_map('trim', $parts);
        $call = preg_replace('/[^A-Za-z0-9]/', '', $label) ?: '';
        if ($label === '' || $call === '') continue;
        if (!preg_match('/^IAX2\/[A-Za-z0-9_.@:+-]+-[0-9]+$/', $channel)) continue;

        $clients[] = [
            'label' => $label,
            'raw_label' => 'allstar-public',
            'ip' => $ip,
            'call' => $call,
            'channel' => $channel,
            'state' => 'Up',
            'app' => 'Rpt',
        ];
    }
    return ['ok' => true, 'clients' => $clients];
}

function asr_drop_client(string $channel): array {
    if (!preg_match('/^IAX2\/[A-Za-z0-9_.@:+-]+-[0-9]+$/', $channel)) {
        asr_error('Unsafe channel name rejected.');
    }

    $command = 'sudo /usr/local/bin/allscan_wt_clients.sh drop ' . escapeshellarg($channel) . ' 2>&1';
    $output = trim((string) shell_exec($command));
    if (stripos($output, 'Drop requested:') !== 0) {
        asr_error($output ?: 'Asterisk did not accept the hangup request.', 500);
    }

    $message = "Dropped {$channel}.";
    asr_write_node_message($message);
    return ['ok' => true, 'message' => $message, 'channel' => $channel];
}

$action = (string) ($_GET['action'] ?? $_POST['action'] ?? '');

if ($action === 'auth-status') asr_json(asr_auth_payload());
if ($action === 'favorites') {
    asr_require_read();
    asr_json(asr_favorites_payload((string) ($_GET['favsfile'] ?? '')));
}
if ($action === 'node-messages') {
    asr_require_read();
    asr_json(asr_node_messages());
}
if ($action === 'favorite-command') {
    asr_require_post();
    asr_require_same_origin();
    asr_require_modify();
    asr_json(asr_favorite_action((string) ($_POST['favoriteAction'] ?? ''), (string) ($_POST['node'] ?? ''), (string) ($_POST['favsfile'] ?? '')));
}
if ($action === 'drop-clients') {
    asr_require_same_origin();
    asr_require_modify();
    asr_json(asr_drop_clients());
}
if ($action === 'drop-client') {
    asr_require_post();
    asr_require_same_origin();
    asr_require_modify();
    asr_json(asr_drop_client((string) ($_POST['channel'] ?? '')));
}

asr_error('Unknown action.', 404);
