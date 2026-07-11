<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');

const ASR_ETC_FAVORITES = '/etc/allscan/favorites.ini';
const ASR_DEFAULT_FAVORITES = '/var/www/html/allscan/favorites.ini';
const ASR_RUNTIME_CONFIG = '/etc/allscan-reimagined/config.json';
const ASR_VERSION_LABEL = 'v1.0.0 Beta 5 Test';

require_once __DIR__ . '/include/common.php';

$msg = [];
asInit($msg);
$db = dbInit();
checkTables($db, $msg);
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

function asr_detect_callsign(string $node): string {
    if ($node === '') return '';

    $files = [
        __DIR__ . '/astdb.txt',
        '/etc/allscan/asdb.txt',
        '/var/log/asterisk/astdb.txt',
    ];
    foreach ($files as $file) {
        if (!is_readable($file)) continue;
        $handle = fopen($file, 'r');
        if (!$handle) continue;
        while (($line = fgets($handle)) !== false) {
            $parts = explode('|', trim($line));
            if (($parts[0] ?? '') === $node && !empty($parts[1])) {
                fclose($handle);
                return strtoupper(trim((string) $parts[1]));
            }
        }
        fclose($handle);
    }
    return '';
}

function asr_detect_bridges(): array {
    $file = __DIR__ . '/bridge-live.json';
    $payload = is_readable($file) ? json_decode((string) file_get_contents($file), true) : null;
    if (!is_array($payload)) return [];

    $definitions = [
        'dmr' => ['DMR Bridge', 'Connected Clients'],
        'ysf' => ['YSF Bridge', 'Linked Gateways'],
        'zello' => ['Zello Bridge', 'Recent Talkers'],
        'dstar' => ['D-Star Bridge', 'Linked Gateways'],
        'p25' => ['P25 Bridge', 'Linked Clients'],
        'm17' => ['M17 Bridge', 'Linked Clients'],
        'nxdn' => ['NXDN Bridge', 'Linked Clients'],
    ];
    $bridges = [];
    foreach ($payload as $id => $value) {
        if (in_array($id, ['updated', 'updated_epoch'], true)) continue;
        if (!preg_match('/^[a-z][a-z0-9_-]{1,31}$/', (string) $id)) continue;
        if (!isset($payload[$id]) || !is_array($payload[$id]) || $payload[$id] === []) continue;
        [$title, $detailTitle] = $definitions[$id] ?? [ucfirst((string) $id) . ' Bridge', 'Linked Clients'];
        $bridges[] = ['id' => $id, 'node' => '', 'title' => $title, 'detailTitle' => $detailTitle];
    }
    return $bridges;
}

function asr_runtime_config(): array {
    global $amicfg;

    $stored = is_readable(ASR_RUNTIME_CONFIG)
        ? json_decode((string) file_get_contents(ASR_RUNTIME_CONFIG), true)
        : null;
    if (!is_array($stored)) $stored = [];

    $messages = [];
    if (!isset($amicfg->node)) getAmiCfg($messages);
    $node = preg_match('/^\d{3,10}$/', (string) ($stored['node'] ?? ''))
        ? (string) $stored['node']
        : (preg_match('/^\d{3,10}$/', (string) ($amicfg->node ?? '')) ? (string) $amicfg->node : '');
    $callsign = strtoupper(trim((string) ($stored['callsign'] ?? '')));
    if ($callsign === '') $callsign = asr_detect_callsign($node);

    $replace = static fn (string $value): string => str_replace(
        ['{CALLSIGN}', '{NODE}'],
        [$callsign ?: 'AllScan', $node],
        $value,
    );

    $storedBridges = is_array($stored['bridges'] ?? null) ? $stored['bridges'] : asr_detect_bridges();
    $bridges = [];
    foreach ($storedBridges as $bridge) {
        if (!is_array($bridge) || !preg_match('/^[a-z][a-z0-9_-]{1,31}$/', (string) ($bridge['id'] ?? ''))) continue;
        $bridgeNode = preg_match('/^\d{3,10}$/', (string) ($bridge['node'] ?? '')) ? (string) $bridge['node'] : '';
        $bridges[] = [
            'id' => (string) $bridge['id'],
            'node' => $bridgeNode,
            'title' => substr(trim((string) ($bridge['title'] ?? 'Bridge')), 0, 80),
            'detailTitle' => substr(trim((string) ($bridge['detailTitle'] ?? 'Connections')), 0, 80),
        ];
    }

    $headerTitle = $replace((string) ($stored['headerTitle'] ?? '{CALLSIGN} | Node {NODE}'));
    $browserTitle = $replace((string) ($stored['browserTitle'] ?? ($headerTitle . ' | ASR')));

    return [
        'ok' => true,
        'node' => $node,
        'callsign' => $callsign,
        'headerTitle' => $headerTitle,
        'browserTitle' => $browserTitle,
        'brandByline' => $replace((string) ($stored['brandByline'] ?? 'by KE7WIL')),
        'footerByline' => $replace((string) ($stored['footerByline'] ?? 'customized by KE7WIL')),
        'headerLogo' => (string) ($stored['headerLogo'] ?? '/allscan/asr-logo-bright-r-tight.png'),
        'footerLogo' => (string) ($stored['footerLogo'] ?? '/allscan/asr-logo-bright-r-tight.png'),
        'versionLabel' => ASR_VERSION_LABEL,
        'bridges' => $bridges,
    ];
}

function asr_allscan_dir(): string {
    return realpath(__DIR__) ?: __DIR__;
}

function asr_favorites_files(): array {
    $dir = asr_allscan_dir();
    $files = [];
    if (file_exists(ASR_ETC_FAVORITES)) $files[] = ASR_ETC_FAVORITES;

    $webFiles = glob($dir . '/favorites*.ini') ?: [];
    sort($webFiles, SORT_NATURAL | SORT_FLAG_CASE);
    foreach ($webFiles as $file) {
        if (basename($file) === 'favorites.ini' && file_exists(ASR_ETC_FAVORITES)) continue;
        $files[] = $file;
    }

    return $files;
}

function asr_safe_favorites_file(string $requested = ''): string {
    $files = asr_favorites_files();
    $default = file_exists(ASR_DEFAULT_FAVORITES) ? ASR_DEFAULT_FAVORITES : ($files[0] ?? ASR_DEFAULT_FAVORITES);
    if (file_exists(ASR_ETC_FAVORITES)) $default = ASR_ETC_FAVORITES;

    if ($requested === '') return $default;

    $dir = asr_allscan_dir();
    $candidates = str_contains($requested, DIRECTORY_SEPARATOR)
        ? [$requested]
        : [dirname(ASR_ETC_FAVORITES) . DIRECTORY_SEPARATOR . basename($requested), $dir . DIRECTORY_SEPARATOR . basename($requested)];

    foreach ($candidates as $candidate) {
        $real = realpath($candidate);
        if (!$real || !preg_match('/\/favorites[^\/]*\.ini$/', $real)) continue;
        $inWebRoot = strncmp($real, $dir . DIRECTORY_SEPARATOR, strlen($dir) + 1) === 0;
        $inEtcAllScan = strncmp($real, '/etc/allscan/', strlen('/etc/allscan/')) === 0;
        if ($inWebRoot || $inEtcAllScan) return $real;
    }

    return $default;
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
        'value' => basename($file),
        'label' => basename($file),
        'selected' => $file === $selected,
    ], asr_favorites_files());

    return ['ok' => true, 'rows' => $rows, 'files' => $files, 'selectedFile' => basename($selected)];
}

function asr_favorite_action(string $action, string $node, string $requested): array {
    if (!in_array($action, ['addfav', 'delfav'], true)) asr_error('Invalid Favorites action.');
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
        return ['ok' => true, 'message' => "Deleted {$node} from Favorites."];
    }

    if (!preg_match('/\bilink\s+\d+\s+' . preg_quote($node, '/') . '\b/i', $contents)) {
        $entry = PHP_EOL . 'label[] = "' . addcslashes($node . ' ' . $node, '"\\') . '"' . PHP_EOL .
            'cmd[] = "rpt cmd %node% ilink 3 ' . $node . '"' . PHP_EOL;
        file_put_contents($file, rtrim($contents) . PHP_EOL . $entry);
    }
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
    return ['ok' => true, 'message' => $message, 'channel' => $channel];
}

$action = (string) ($_GET['action'] ?? $_POST['action'] ?? '');

if ($action === 'auth-status') asr_json(asr_auth_payload());
if ($action === 'runtime-config') asr_json(asr_runtime_config());
if ($action === 'favorites') {
    asr_require_read();
    asr_json(asr_favorites_payload((string) ($_GET['favsfile'] ?? '')));
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
