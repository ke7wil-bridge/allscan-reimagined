<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');

const ASR_ETC_FAVORITES = '/etc/allscan/favorites.ini';
const ASR_DEFAULT_FAVORITES = '/var/www/html/allscan/favorites.ini';
const ASR_RUNTIME_CONFIG = '/etc/allscan-reimagined/config.json';
const ASR_RUNTIME_SECRETS = '/etc/allscan-reimagined/secrets.json';
const ASR_STATION_MAP_CACHE = '/etc/allscan-reimagined/station-map-cache.json';
const ASR_VERSION_LABEL = 'v1.0.0 Beta 5.10';

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

function asr_require_admin(): void {
    if (!asr_logged_in() || !adminUser()) asr_error('Login with admin permission required.', 403);
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

function asr_lookup_node_label(string $node): string {
    $record = asr_lookup_node_record($node);
    return $record ? implode(' ', array_values(array_filter($record, static fn (string $piece): bool => $piece !== ''))) : '';
}

function asr_lookup_node_record(string $node): array {
    if ($node === '') return [];

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
            if (($parts[0] ?? '') !== $node) continue;
            fclose($handle);
            return array_map('trim', [
                'name' => (string) ($parts[1] ?? ''),
                'desc' => (string) ($parts[2] ?? ''),
                'location' => (string) ($parts[3] ?? ''),
            ]);
        }
        fclose($handle);
    }

    return [];
}

function asr_lookup_node_location(string $node): string {
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
            if (($parts[0] ?? '') !== $node) continue;
            fclose($handle);
            return substr(trim((string) ($parts[3] ?? '')), 0, 120);
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
        'brandByline' => 'by KE7WIL',
        'footerByline' => $replace((string) ($stored['footerByline'] ?? 'customized by KE7WIL')),
        'headerLogo' => (string) ($stored['headerLogo'] ?? '/allscan/asr-logo-bright-r-tight.png'),
        'footerLogo' => '/allscan/asr-logo-bright-r-tight.png',
        'versionLabel' => ASR_VERSION_LABEL,
        'lowPowerMode' => !empty($stored['lowPowerMode']),
        'bridges' => $bridges,
    ];
}

function asr_bridge_status_payload(): array {
    $bridge = [];
    $path = __DIR__ . '/bridge-live.json';
    if (is_readable($path)) {
        $decoded = json_decode((string) file_get_contents($path), true);
        if (is_array($decoded)) $bridge = $decoded;
    }
    return [
        'ok' => true,
        'bridge' => $bridge,
        'clients' => asr_bridge_clients_payload(),
    ];
}

function asr_cpu_temp_payload(): array {
    $cache = '/run/allscan-reimagined/cpu-temp.json';
    if (is_readable($cache) && (int) @filemtime($cache) >= time() - 15) {
        $decoded = json_decode((string) file_get_contents($cache), true);
        if (is_array($decoded)) return $decoded;
    }
    $raw = (string) cpuTemp();
    $text = trim(html_entity_decode(strip_tags($raw), ENT_QUOTES | ENT_HTML5));
    preg_match('/background-color\s*:\s*([^;"\']+)/i', $raw, $background);
    preg_match('/CPU Temp:\s*(.+?)\s*@/i', $text, $temperature);
    $payload = [
        'ok' => true,
        'value' => trim((string) ($temperature[1] ?? preg_replace('/^CPU Temp:\s*/i', '', $text))),
        'bgColor' => trim((string) ($background[1] ?? '#59461c')),
        'updated' => gmdate('c'),
    ];
    if (is_dir(dirname($cache))) @file_put_contents($cache, json_encode($payload), LOCK_EX);
    return $payload;
}

function asr_raw_runtime_config(): array {
    $stored = is_readable(ASR_RUNTIME_CONFIG)
        ? json_decode((string) file_get_contents(ASR_RUNTIME_CONFIG), true)
        : null;
    return is_array($stored) ? $stored : [];
}

function asr_runtime_secrets(): array {
    $stored = is_readable(ASR_RUNTIME_SECRETS)
        ? json_decode((string) file_get_contents(ASR_RUNTIME_SECRETS), true)
        : null;
    return is_array($stored) ? $stored : [];
}

function asr_bridge_client_secret(string $id): string {
    $secrets = asr_runtime_secrets();
    $passwords = $secrets['bridgeClientPasswords'] ?? [];
    return is_array($passwords) ? (string) ($passwords[$id] ?? '') : '';
}

function asr_decode_json_payload(string $payload): array {
    $decoded = json_decode($payload, true);
    return is_array($decoded) ? $decoded : [];
}

function asr_client_epoch_value(mixed $value): int {
    if (is_int($value) || is_float($value)) return (int) $value;
    $text = trim((string) $value);
    if ($text === '') return 0;
    if (preg_match('/^\d+(?:\.\d+)?$/', $text)) return (int) floor((float) $text);
    $epoch = strtotime($text);
    return $epoch === false ? 0 : $epoch;
}

function asr_client_row_is_fresh(array $row, string $bridgeId): bool {
    $now = time();
    $lastSeen = asr_client_epoch_value($row['last_seen_epoch'] ?? $row['last_seen'] ?? $row['timestamp'] ?? 0);
    $lastTalk = asr_client_epoch_value($row['last_tx_epoch'] ?? $row['tx_epoch'] ?? $row['last_talk_epoch'] ?? 0);
    $isCurrent = filter_var($row['active'] ?? $row['current'] ?? $row['connected'] ?? false, FILTER_VALIDATE_BOOLEAN);

    if ($bridgeId === 'zello') {
        return $isCurrent && ($lastSeen === 0 || $now - $lastSeen <= 180);
    }
    if ($bridgeId === 'ysf') {
        return $lastSeen > 0 && $now - $lastSeen <= 180;
    }
    if ($lastSeen > 0) return $now - $lastSeen <= 180;
    if ($lastTalk > 0) return $now - $lastTalk <= 300;
    return $bridgeId !== 'zello' && $bridgeId !== 'ysf';
}

function asr_sanitize_client_rows(array $rows, string $bridgeId = ''): array {
    $clean = [];
    foreach ($rows as $row) {
        if (is_string($row)) {
            $value = trim($row);
            if ($value !== '' && $bridgeId !== 'zello' && $bridgeId !== 'ysf') $clean[] = ['name' => substr($value, 0, 120)];
            continue;
        }
        if (!is_array($row)) continue;

        $item = [];
        foreach (['callsign', 'call', 'station', 'username', 'name', 'display_name', 'displayName', 'user', 'current_user', 'dmrid', 'dmr_id', 'id', 'last_tx_epoch', 'tx_epoch', 'last_talk_epoch', 'last_seen_epoch', 'last_seen', 'timestamp', 'active', 'current', 'connected'] as $key) {
            if (!array_key_exists($key, $row)) continue;
            $value = $row[$key];
            if (is_scalar($value)) $item[$key] = is_string($value) ? substr(trim($value), 0, 160) : $value;
        }
        if ($item !== [] && asr_client_row_is_fresh($item, $bridgeId)) $clean[] = $item;
    }
    return $clean;
}

function asr_bridge_clients_payload(): array {
    $clients = [];

    $readCurrentFile = static function (string $path): array {
        if (!is_readable($path)) return [];
        $mtime = (int) @filemtime($path);
        if ($mtime <= 0 || time() - $mtime > 45) return [];
        $payload = @file_get_contents($path);
        return is_string($payload) ? asr_decode_json_payload($payload) : [];
    };

    $externalPayload = $readCurrentFile(__DIR__ . '/connected-clients.json');
    $managedPayload = $readCurrentFile(__DIR__ . '/asr-connected-clients.json');
    $bridgeIds = array_unique(array_merge(array_keys($externalPayload), array_keys($managedPayload)));

    foreach ($bridgeIds as $id) {
        $id = (string) $id;
        if (!preg_match('/^[a-z][a-z0-9_-]{1,31}$/', $id)) continue;

        // A current external file is authoritative for each bridge key it
        // publishes, including an intentionally empty client list. ASR's own
        // collector fills only bridge keys that the external writer omits.
        $rows = array_key_exists($id, $externalPayload)
            ? $externalPayload[$id]
            : ($managedPayload[$id] ?? []);
        if (!is_array($rows)) continue;
        $clients[$id] = asr_sanitize_client_rows($rows, $id);
    }

    return $clients;
}

function asr_extract_callsign(string $value): string {
    if (preg_match('/\b([A-Z]{1,2}[0-9][A-Z0-9]{1,4})\b/i', $value, $match)) {
        return strtoupper($match[1]);
    }
    if (preg_match('/\b([A-Z]{1,2}[0-9][A-Z0-9]{1,4})(?=(?:IAX|DMR|YSF|ZELLO|ECHOLINK|EL)(?:\b|$))/i', $value, $match)) {
        return strtoupper($match[1]);
    }
    return '';
}

function asr_lookup_item(string $source, string $label, string $node = '', string $callsign = '', string $detail = ''): array {
    $label = trim($label);
    $node = trim($node);
    $detail = trim($detail);
    $echolinkLookup = asr_echolink_lookup_value($node);
    if ($echolinkLookup !== '' && ($label === '' || $label === $node || preg_match('/not\s+in\s+db/i', $label))) {
        $label = 'EchoLink ' . $echolinkLookup;
    }
    $callsign = $callsign !== '' ? strtoupper($callsign) : asr_extract_callsign($label . ' ' . $detail);
    if ($callsign === '' && $node !== '') {
        $callsign = asr_detect_callsign($node);
    }
    if (($label === '' || $label === $node || preg_match('/not\s+in\s+db/i', $label)) && $callsign !== '') {
        $label = $callsign;
    }
    return [
        'source' => $source,
        'label' => $label,
        'node' => $node,
        'callsign' => $callsign,
        'detail' => $detail,
        'qrzUrl' => $callsign !== '' ? 'https://www.qrz.com/db/' . rawurlencode($callsign) : '',
        'allstarUrl' => $echolinkLookup === '' && preg_match('/^\d{3,10}$/', $node) ? 'http://stats.allstarlink.org/stats/' . rawurlencode($node) : '',
        'echolinkLookup' => $echolinkLookup,
    ];
}

function asr_echolink_lookup_value(string $node): string {
    $node = trim($node);
    return preg_match('/^3(\d{6})$/', $node, $match) ? $match[1] : '';
}

function asr_lookup_is_private_client_id(string $value, array $bridgeNodes): bool {
    $value = trim($value);
    if ($value === '' || !preg_match('/^\d+$/', $value)) {
        return false;
    }
    if (isset($bridgeNodes[$value])) {
        return true;
    }
    $number = (int) $value;
    return $number > 0 && $number < 2000;
}

function asr_lookup_is_private_node(string $value, array $bridgeNodes): bool {
    $value = trim($value);
    if ($value === '' || !preg_match('/^\d+$/', $value)) {
        return false;
    }
    if (isset($bridgeNodes[$value])) {
        return true;
    }
    return preg_match('/^\d{4}$/', $value) === 1;
}

function asr_lookup_is_iax_client(string $node, string $label, string $detail): bool {
    $node = trim($node);
    $text = $node . ' ' . $label . ' ' . $detail;
    return ($node !== '' && !preg_match('/^\d+$/', $node)) || preg_match('/\b(IAX|IaxRpt|Web Transceiver|WebTransceiver)\b/i', $text);
}

function asr_lookup_payload(): array {
    $runtime = asr_runtime_config();
    $node = (string) ($runtime['node'] ?? '');
    $bridgeNodes = [];
    foreach (($runtime['bridges'] ?? []) as $bridge) {
        if (is_array($bridge) && !empty($bridge['node'])) {
            $bridgeNodes[(string) $bridge['node']] = (string) ($bridge['title'] ?? 'Bridge');
        }
    }

    $items = [];
    $seen = [];
    $add = function(array $item) use (&$items, &$seen): void {
        $key = strtolower(($item['source'] ?? '') . '|' . ($item['node'] ?? '') . '|' . ($item['callsign'] ?? '') . '|' . ($item['label'] ?? ''));
        if (isset($seen[$key])) return;
        $seen[$key] = true;
        $items[] = $item;
    };

    if ($node !== '') {
        $asteriskRead = '/usr/local/sbin/allscan-reimagined-asterisk-read';
        $lstats = [];
        if (is_executable($asteriskRead)) {
            $lstats = asr_command_lines('sudo -n ' . escapeshellarg($asteriskRead) . ' lstats ' . escapeshellarg($node), 10000);
        }

        if (is_executable($asteriskRead)) {
            foreach ($lstats as $line) {
                if (!preg_match('/^(\S+)\s+(?:(\S+)\s+)?\d+\s+(IN|OUT)\s+/i', $line, $match)) continue;
                $rowNode = trim($match[1]);
                if ($rowNode === '' || $rowNode === 'NODE' || $rowNode === '----' || $rowNode === '1') continue;
                if (asr_lookup_is_private_node($rowNode, $bridgeNodes)) continue;
                $source = asr_echolink_lookup_value($rowNode) !== '' ? 'EchoLink Connection' : (isset($bridgeNodes[$rowNode]) ? 'Bridge Link' : 'Connection Status');
                $label = asr_lookup_node_label($rowNode);
                if ($label === '') $label = $rowNode;
                $detail = trim((string) ($match[2] ?? ''));
                if (asr_lookup_is_iax_client($rowNode, $label, $detail)) {
                    $callsign = asr_extract_callsign($rowNode . ' ' . $label . ' ' . $detail);
                    $iaxDetail = trim($rowNode . (($label !== '' && $label !== $rowNode) ? ' · ' . $label : '') . ($detail !== '' ? ' · ' . $detail : ''));
                    $add(asr_lookup_item('IAX Client', $callsign !== '' ? $callsign : $rowNode, '', $callsign, $iaxDetail));
                    continue;
                }
                $item = asr_lookup_item($source, $label, $rowNode, '', $detail);
                $item['locationHint'] = asr_lookup_node_location($rowNode);
                $add($item);
            }
        }
    }

    foreach (asr_bridge_clients_payload() as $bridgeId => $clients) {
        if (!is_array($clients)) continue;
        foreach ($clients as $client) {
            if (!is_array($client)) continue;
            $label = (string) ($client['callsign'] ?? $client['call'] ?? $client['station'] ?? $client['username'] ?? $client['name'] ?? $client['display_name'] ?? $client['displayName'] ?? $client['user'] ?? '');
            if ($label === '') continue;
            $detail = (string) ($client['dmrid'] ?? $client['dmr_id'] ?? $client['id'] ?? '');
            if (asr_lookup_is_private_client_id($detail, $bridgeNodes)) continue;
            $add(asr_lookup_item(strtoupper((string) $bridgeId) . ' Client', $label, '', '', $detail));
        }
    }

    return [
        'ok' => true,
        'node' => $node,
        'bridgeNodes' => array_keys($bridgeNodes),
        'generatedAt' => gmdate('c'),
        'items' => $items,
    ];
}

function asr_http_get(string $url, int $timeout = 5): string {
    $context = stream_context_create([
        'http' => [
            'method' => 'GET',
            'header' => "Accept: application/xml, text/xml;q=0.9, application/json;q=0.8\r\nUser-Agent: AllScan-Reimagined-Beta5/1.0 (station origin map)",
            'timeout' => $timeout,
            'ignore_errors' => true,
        ],
        'ssl' => [
            'verify_peer' => true,
            'verify_peer_name' => true,
        ],
    ]);
    $payload = @file_get_contents($url, false, $context);
    return is_string($payload) ? $payload : '';
}

function asr_xml_value(string $xml, string $tag): string {
    if (!preg_match('/<' . preg_quote($tag, '/') . '(?:\s[^>]*)?>(.*?)<\/' . preg_quote($tag, '/') . '>/is', $xml, $match)) {
        return '';
    }
    return trim(html_entity_decode(strip_tags((string) $match[1]), ENT_QUOTES | ENT_XML1, 'UTF-8'));
}

function asr_qrz_session(array $secrets): string {
    $qrz = is_array($secrets['qrz'] ?? null) ? $secrets['qrz'] : [];
    $username = trim((string) ($qrz['username'] ?? ''));
    $password = (string) ($qrz['password'] ?? '');
    if ($username === '' || $password === '') return '';

    $url = 'https://xmldata.qrz.com/xml/current/?' . http_build_query([
        'username' => $username,
        'password' => $password,
        'agent' => 'AllScan-Reimagined-Beta5',
    ], '', '&', PHP_QUERY_RFC3986);
    return asr_xml_value(asr_http_get($url), 'Key');
}

function asr_qrz_station(string $callsign, string &$session): array {
    if ($session === '') return [];
    $url = 'https://xmldata.qrz.com/xml/current/?' . http_build_query([
        's' => $session,
        'callsign' => $callsign,
    ], '', '&', PHP_QUERY_RFC3986);
    $xml = asr_http_get($url);
    if ($xml === '') return [];

    $nextSession = asr_xml_value($xml, 'Key');
    if ($nextSession !== '') $session = $nextSession;
    if (!preg_match('/<Callsign(?:\s[^>]*)?>(.*?)<\/Callsign>/is', $xml, $match)) {
        return ['resolved' => false];
    }

    $record = (string) $match[1];
    $lat = filter_var(asr_xml_value($record, 'lat'), FILTER_VALIDATE_FLOAT);
    $lng = filter_var(asr_xml_value($record, 'lon'), FILTER_VALIDATE_FLOAT);
    if ($lat === false || $lng === false || $lat < -90 || $lat > 90 || $lng < -180 || $lng > 180) {
        return ['resolved' => false];
    }

    $name = asr_xml_value($record, 'name_fmt');
    if ($name === '') {
        $name = trim(asr_xml_value($record, 'fname') . ' ' . asr_xml_value($record, 'name'));
    }
    $locationParts = array_values(array_filter(array_map('trim', [
        asr_xml_value($record, 'addr2'),
        asr_xml_value($record, 'state'),
        asr_xml_value($record, 'country'),
    ]), static fn (string $value): bool => $value !== ''));

    return [
        'resolved' => true,
        'callsign' => strtoupper($callsign),
        'name' => $name,
        'location' => implode(', ', array_values(array_unique($locationParts))),
        // Keep browser-visible points approximate rather than exposing a precise address.
        'lat' => round((float) $lat, 2),
        'lng' => round((float) $lng, 2),
        'source' => 'qrz',
    ];
}

function asr_station_map_cache_read(): array {
    $cache = is_readable(ASR_STATION_MAP_CACHE)
        ? json_decode((string) file_get_contents(ASR_STATION_MAP_CACHE), true)
        : null;
    if (!is_array($cache)) return ['callsigns' => []];
    if (!is_array($cache['callsigns'] ?? null)) $cache['callsigns'] = [];
    if (!is_array($cache['geocodes'] ?? null)) $cache['geocodes'] = [];
    return $cache;
}

function asr_station_map_cache_write(array $cache): void {
    $directory = dirname(ASR_STATION_MAP_CACHE);
    if (!is_dir($directory) || !is_writable($directory)) return;
    $json = json_encode($cache, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
    if (!is_string($json)) return;
    $temporary = ASR_STATION_MAP_CACHE . '.tmp.' . getmypid();
    if (@file_put_contents($temporary, $json . "\n", LOCK_EX) === false) return;
    @chmod($temporary, 0660);
    if (!@rename($temporary, ASR_STATION_MAP_CACHE)) @unlink($temporary);
}

function asr_clean_location_hint(string $value): string {
    $value = trim((string) preg_replace('/\s+/', ' ', strip_tags($value)));
    if (strlen($value) < 3 || !preg_match('/[A-Z]/i', $value) || preg_match('#https?://#i', $value)) return '';
    return substr($value, 0, 120);
}

function asr_nominatim_location(string $location): array {
    $url = 'https://nominatim.openstreetmap.org/search?' . http_build_query([
        'format' => 'jsonv2',
        'limit' => 1,
        'addressdetails' => 0,
        'q' => $location,
    ], '', '&', PHP_QUERY_RFC3986);
    $payload = asr_http_get($url, 4);
    if ($payload === '') return [];
    $rows = json_decode($payload, true);
    if (!is_array($rows)) return [];
    $row = is_array($rows[0] ?? null) ? $rows[0] : null;
    if (!$row) return ['resolved' => false];
    $lat = filter_var($row['lat'] ?? null, FILTER_VALIDATE_FLOAT);
    $lng = filter_var($row['lon'] ?? null, FILTER_VALIDATE_FLOAT);
    if ($lat === false || $lng === false || $lat < -90 || $lat > 90 || $lng < -180 || $lng > 180) {
        return ['resolved' => false];
    }
    return [
        'resolved' => true,
        'location' => $location,
        'lat' => round((float) $lat, 2),
        'lng' => round((float) $lng, 2),
        'source' => 'nominatim',
    ];
}

function asr_station_map_payload(?array $requestedStations = null): array {
    $stations = [];
    if (is_array($requestedStations)) {
        foreach (array_slice($requestedStations, 0, 30) as $requestedStation) {
            if (is_string($requestedStation)) $requestedStation = ['callsign' => $requestedStation];
            if (!is_array($requestedStation)) continue;
            $callsign = strtoupper(trim((string) ($requestedStation['callsign'] ?? '')));
            if (!preg_match('/^[A-Z0-9]{1,3}\/[A-Z0-9]{3,8}$|^[A-Z]{1,2}[0-9][A-Z0-9]{1,4}$/', $callsign)) continue;
            $stations[$callsign] = [
                'callsign' => $callsign,
                'node' => '',
                'label' => $callsign,
                'locationHint' => asr_clean_location_hint((string) ($requestedStation['locationHint'] ?? '')),
            ];
        }
    } else {
        $lookup = asr_lookup_payload();
        foreach (($lookup['items'] ?? []) as $item) {
            if (!is_array($item)) continue;
            $callsign = strtoupper(trim((string) ($item['callsign'] ?? '')));
            if (!preg_match('/^[A-Z0-9]{1,3}\/[A-Z0-9]{3,8}$|^[A-Z]{1,2}[0-9][A-Z0-9]{1,4}$/', $callsign)) continue;
            if (!isset($stations[$callsign])) $stations[$callsign] = $item;
        }
    }

    $lock = @fopen(ASR_STATION_MAP_CACHE . '.lock', 'c');
    if ($lock) @flock($lock, LOCK_EX);
    $cache = asr_station_map_cache_read();
    $now = time();
    $positiveTtl = 30 * 86400;
    $needsLookup = [];
    foreach ($stations as $callsign => $_item) {
        $entry = is_array($cache['callsigns'][$callsign] ?? null) ? $cache['callsigns'][$callsign] : [];
        $fetchedAt = (int) ($entry['fetchedAt'] ?? 0);
        $qrzAttemptAt = (int) ($entry['qrzAttemptAt'] ?? 0);
        if (($entry['source'] ?? '') === 'qrz' && $fetchedAt > 0 && $now - $fetchedAt < $positiveTtl) continue;
        if ($qrzAttemptAt > 0 && $now - $qrzAttemptAt < 86400) continue;
        $needsLookup[] = $callsign;
    }

    if ($needsLookup !== [] && $now - (int) ($cache['lastQrzLoginAttemptAt'] ?? 0) >= 60) {
        $cache['lastQrzLoginAttemptAt'] = $now;
        $session = asr_qrz_session(asr_runtime_secrets());
        if ($session !== '') {
            foreach (array_slice($needsLookup, 0, 30) as $callsign) {
                $station = asr_qrz_station($callsign, $session);
                if ($station === []) continue;
                $existing = is_array($cache['callsigns'][$callsign] ?? null) ? $cache['callsigns'][$callsign] : [];
                if (!empty($station['resolved'])) {
                    $station['fetchedAt'] = $now;
                    $station['qrzAttemptAt'] = $now;
                    $cache['callsigns'][$callsign] = $station;
                } else {
                    $existing['qrzAttemptAt'] = $now;
                    $cache['callsigns'][$callsign] = $existing;
                }
            }
        }
    }

    $geocodePositiveTtl = 90 * 86400;
    $geocodeNegativeTtl = 7 * 86400;
    $geocodeCandidate = null;
    foreach ($stations as $callsign => $item) {
        $entry = is_array($cache['callsigns'][$callsign] ?? null) ? $cache['callsigns'][$callsign] : [];
        if (!empty($entry['resolved'])) continue;
        $locationHint = asr_clean_location_hint((string) ($item['locationHint'] ?? ''));
        if ($locationHint === '') continue;
        $geocodeKey = strtolower($locationHint);
        $geocode = is_array($cache['geocodes'][$geocodeKey] ?? null) ? $cache['geocodes'][$geocodeKey] : [];
        $geocodeAge = $now - (int) ($geocode['fetchedAt'] ?? 0);
        $geocodeTtl = !empty($geocode['resolved']) ? $geocodePositiveTtl : $geocodeNegativeTtl;
        if (($geocode['fetchedAt'] ?? 0) && $geocodeAge < $geocodeTtl) {
            if (!empty($geocode['resolved'])) {
                $cache['callsigns'][$callsign] = array_merge($entry, $geocode, [
                    'callsign' => $callsign,
                    'source' => 'nominatim',
                ]);
            }
            continue;
        }
        if ($geocodeCandidate === null) $geocodeCandidate = [$callsign, $locationHint, $geocodeKey];
    }

    // At most one uncached public geocode every 15 seconds (four per minute), with durable caching.
    if ($geocodeCandidate !== null && $now - (int) ($cache['lastNominatimAt'] ?? 0) >= 15) {
        [$callsign, $locationHint, $geocodeKey] = $geocodeCandidate;
        $cache['lastNominatimAt'] = $now;
        $geocode = asr_nominatim_location($locationHint);
        if ($geocode !== []) {
            $geocode['fetchedAt'] = $now;
            $cache['geocodes'][$geocodeKey] = $geocode;
            if (!empty($geocode['resolved'])) {
                $existing = is_array($cache['callsigns'][$callsign] ?? null) ? $cache['callsigns'][$callsign] : [];
                $cache['callsigns'][$callsign] = array_merge($existing, $geocode, [
                    'callsign' => $callsign,
                    'source' => 'nominatim',
                ]);
            }
        }
    }

    $cache['updatedAt'] = gmdate('c');
    asr_station_map_cache_write($cache);
    if ($lock) {
        @flock($lock, LOCK_UN);
        @fclose($lock);
    }

    $points = [];
    $unmapped = [];
    foreach ($stations as $callsign => $item) {
        $entry = is_array($cache['callsigns'][$callsign] ?? null) ? $cache['callsigns'][$callsign] : [];
        if (!empty($entry['resolved']) && isset($entry['lat'], $entry['lng'])) {
            $points[] = [
                'callsign' => $callsign,
                'name' => (string) ($entry['name'] ?? ''),
                'node' => (string) ($item['node'] ?? ''),
                'label' => (string) ($item['label'] ?? ''),
                'location' => (string) ($entry['location'] ?? ''),
                'lat' => (float) $entry['lat'],
                'lng' => (float) $entry['lng'],
            ];
        } else {
            $unmapped[] = [
                'callsign' => $callsign,
                'node' => (string) ($item['node'] ?? ''),
                'label' => (string) ($item['label'] ?? ''),
            ];
        }
    }

    usort($points, static fn (array $a, array $b): int => strcasecmp((string) $a['callsign'], (string) $b['callsign']));
    usort($unmapped, static fn (array $a, array $b): int => strcasecmp((string) $a['callsign'], (string) $b['callsign']));
    return [
        'ok' => true,
        'generatedAt' => gmdate('c'),
        'points' => $points,
        'unmapped' => $unmapped,
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

function asr_format_favorite_label(array $record, string $node): string {
    $prefix = trim((string) ($record['name'] ?? '') . ' ' . (string) ($record['desc'] ?? ''));
    $location = trim((string) ($record['location'] ?? ''));
    return trim($prefix . ($location !== '' ? ', ' . $location : '') . ' ' . $node);
}

function asr_favorite_display_data(string $label, string $node): array {
    $normalized = trim(preg_replace('/\s+/', ' ', $label) ?: '');
    $placeholder = $normalized === $node || $normalized === $node . ' ' . $node;
    $record = asr_lookup_node_record($node);
    if ($record) {
        $resolvedLabel = asr_format_favorite_label($record, $node);
        $legacyLabel = implode(' ', array_values(array_filter($record, static fn (string $piece): bool => $piece !== ''))) . ' ' . $node;
        if ($placeholder || $normalized === $resolvedLabel || $normalized === $legacyLabel) {
            return ['label' => $resolvedLabel] + $record;
        }
    }

    return ['label' => $label] + asr_parse_label($label, $node);
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

        $display = asr_favorite_display_data($label, $node);
        $rows[] = [
            'index' => (string) $index,
            'node' => $node,
            'label' => (string) $display['label'],
            'name' => (string) $display['name'],
            'desc' => (string) $display['desc'],
            'location' => (string) $display['location'],
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
        $record = asr_lookup_node_record($node);
        $favoriteLabel = $record ? asr_format_favorite_label($record, $node) : $node . ' ' . $node;
        $entry = PHP_EOL . 'label[] = "' . addcslashes($favoriteLabel, '"\\') . '"' . PHP_EOL .
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

function asr_redact_diagnostics(string $text): string {
    $text = preg_replace('/(Authorization\s*:\s*(?:Bearer|Basic)\s+)[^\s"\'<>]+/i', '$1[REDACTED]', $text) ?? $text;
    $text = preg_replace('/(ami(pass|password)?|password|passwd|secret|token|cookie|session|hash)(["\'\s:=]+)[^\\s"\'&<>]+/i', '$1$3[REDACTED]', $text) ?? $text;
    $text = preg_replace('/(cpass|PHPSESSID)=([^;\\s]+)/i', '$1=[REDACTED]', $text) ?? $text;
    return $text;
}

function asr_diag_command(string $command, int $maxChars = 4000): string {
    $output = (string) shell_exec('timeout 4s ' . $command . ' 2>&1');
    $output = asr_redact_diagnostics(trim($output));
    if ($output === '') return '(no output)';
    if (strlen($output) <= $maxChars) return $output;

    $truncated = substr($output, -$maxChars);
    $firstCompleteLine = strpos($truncated, "\n");
    if ($firstCompleteLine !== false) {
        $truncated = substr($truncated, $firstCompleteLine + 1);
    }
    return "[older lines omitted]\n" . ltrim($truncated);
}

function asr_diag_journal(string $scope, int $maxChars = 5000): string {
    if (!in_array($scope, ['apache', 'asterisk', 'asr', 'bridge-clients'], true)) {
        return 'Invalid journal scope.';
    }
    $helper = '/usr/local/sbin/allscan-reimagined-asterisk-read';
    return asr_diag_command('sudo -n ' . escapeshellarg($helper) . ' journal ' . escapeshellarg($scope), $maxChars);
}

function asr_file_status(string $path): string {
    if (!file_exists($path)) return "{$path}: missing";
    $perms = substr(sprintf('%o', fileperms($path)), -4);
    $size = is_file($path) ? filesize($path) : 0;
    return sprintf('%s: exists, perms %s, size %s bytes', $path, $perms, (string) $size);
}

function asr_diagnostics_report(): array {
    global $AllScanVersion, $gCfg, $user;

    $runtime = asr_runtime_config();
    $auth = asr_auth_payload();
    $node = (string) ($runtime['node'] ?? '');
    $bridges = [];
    foreach ((array) ($runtime['bridges'] ?? []) as $bridge) {
        if (!is_array($bridge)) continue;
        $bridges[] = sprintf(
            '%s node=%s title=%s',
            (string) ($bridge['id'] ?? ''),
            (string) ($bridge['node'] ?? ''),
            (string) ($bridge['title'] ?? '')
        );
    }

    $sections = [];
    $sections[] = ['ASR Bug Report', [
        'Send to: ke7wil@gmail.com',
        'Generated: ' . date('c'),
        'Generated by: ' . (string) ($user->name ?? 'unknown'),
    ]];
    $sections[] = ['Versions', [
        'ASR: ' . ASR_VERSION_LABEL,
        'AllScan: ' . (string) ($AllScanVersion ?? 'unknown'),
        'PHP: ' . PHP_VERSION,
    ]];
    $sections[] = ['Node', [
        'Node: ' . $node,
        'Callsign: ' . (string) ($runtime['callsign'] ?? ''),
        'Header title: ' . (string) ($runtime['headerTitle'] ?? ''),
        'Public permission: ' . (string) ($auth['publicPermission'] ?? ''),
        'Logged in: ' . ($auth['loggedIn'] ? 'yes' : 'no'),
        'Admin: ' . ($auth['isAdmin'] ? 'yes' : 'no'),
    ]];
    $sections[] = ['Bridge Config', $bridges ?: ['No configured bridge cards.']];
    $sections[] = ['Files', [
        asr_file_status('/etc/allscan/allscan.db'),
        asr_file_status('/etc/allscan/favorites.ini'),
        asr_file_status(__DIR__ . '/favorites.ini'),
        asr_file_status(__DIR__ . '/bridge-live.json'),
        asr_file_status(__DIR__ . '/connected-clients.json'),
        asr_file_status(__DIR__ . '/asr-connected-clients.json'),
        asr_file_status(__DIR__ . '/astapi/server.php'),
        asr_file_status(__DIR__ . '/astapi/AMI.php'),
    ]];
    $sections[] = ['Syntax Checks', [
        asr_diag_command('php -l ' . escapeshellarg(__DIR__ . '/asr-api.php'), 1200),
        asr_diag_command('php -l ' . escapeshellarg(__DIR__ . '/astapi/server.php'), 1200),
        asr_diag_command('php -l ' . escapeshellarg(__DIR__ . '/astapi/AMI.php'), 1200),
    ]];
    $sections[] = ['System', [
        asr_diag_command('uptime', 1000),
        asr_diag_command('free -h', 1200),
        asr_diag_command('df -h / /tmp /var/log', 1600),
    ]];
    $sections[] = ['Services', [
        asr_diag_command('systemctl --no-pager --plain --lines=0 status apache2', 2500),
        asr_diag_command('systemctl --no-pager --plain --lines=0 status asterisk', 2500),
        asr_diag_command('systemctl --no-pager --plain --lines=0 status allscan-reimagined-reapply.timer allscan-reimagined-reapply.path', 2500),
    ]];
    $sections[] = ['Recent Logs', [
        'apache2 recent:',
        asr_diag_journal('apache'),
        'asterisk recent:',
        asr_diag_journal('asterisk'),
        'ASR services:',
        asr_diag_journal('asr'),
        'Bridge client services:',
        asr_diag_journal('bridge-clients'),
    ]];

    $lines = [];
    foreach ($sections as [$title, $items]) {
        $lines[] = "## {$title}";
        foreach ($items as $item) {
            $lines[] = asr_redact_diagnostics((string) $item);
        }
        $lines[] = '';
    }

    return [
        'ok' => true,
        'email' => 'ke7wil@gmail.com',
        'subject' => 'ASR Bug Report - Node ' . ($node ?: 'unknown'),
        'report' => trim(implode(PHP_EOL, $lines)) . PHP_EOL,
    ];
}

function asr_command_lines(string $command, int $maxChars = 12000): array {
    $output = asr_diag_command($command, $maxChars);
    if ($output === '(no output)') return [];
    return array_values(array_filter(array_map('trim', preg_split('/\R/', $output) ?: []), static fn (string $line): bool => $line !== ''));
}

function asr_unit_state(string $unit): array {
    if (!preg_match('/^[A-Za-z0-9_.@:-]+$/', $unit)) {
        return ['unit' => $unit, 'state' => 'unknown'];
    }
    $state = trim((string) shell_exec('systemctl is-active ' . escapeshellarg($unit) . ' 2>/dev/null'));
    $enabled = trim((string) shell_exec('systemctl is-enabled ' . escapeshellarg($unit) . ' 2>/dev/null'));
    return [
        'unit' => $unit,
        'state' => $state !== '' ? $state : 'unknown',
        'enabled' => $enabled !== '' ? $enabled : 'unknown',
    ];
}

function asr_service_hints(string $bridgeId): array {
    $patterns = [
        'dmr' => 'mmdvm|analog_bridge|md380|dmr|brandmeister|tgif',
        'ysf' => 'ysf|mmdvm_bridge_ysf|analog_bridge_ysf|md380-emu-ysf',
        'zello' => 'zello',
        'dstar' => 'dstar|ircddb|xlx|dplus',
        'p25' => 'p25',
        'm17' => 'm17',
        'nxdn' => 'nxdn',
    ];
    $pattern = $patterns[$bridgeId] ?? preg_replace('/[^A-Za-z0-9_-]/', '', $bridgeId);
    if (!$pattern) return [];

    $lines = asr_command_lines('systemctl --no-pager --plain --type=service --all | grep -Ei ' . escapeshellarg($pattern) . ' | head -12', 6000);
    return array_map(static function (string $line): array {
        $parts = preg_split('/\s+/', $line, 5) ?: [];
        return [
            'unit' => (string) ($parts[0] ?? $line),
            'state' => trim(implode(' ', array_slice($parts, 2))) ?: $line,
        ];
    }, $lines);
}

function asr_local_path_status(string $path): array {
    $path = trim($path);
    if ($path === '') return ['path' => '', 'status' => 'not configured'];
    $resolved = $path;
    if (str_starts_with($path, '/allscan/')) $resolved = __DIR__ . substr($path, strlen('/allscan'));
    $real = realpath($resolved);
    if (!$real) return ['path' => $path, 'status' => 'missing'];
    return [
        'path' => $path,
        'status' => is_readable($real) ? 'readable' : 'not readable',
        'resolved' => $real,
        'size' => is_file($real) ? filesize($real) : null,
    ];
}

function asr_dmr_udp_diagnostics(): array {
    $ini = '/opt/MMDVM_Bridge/MMDVM_Bridge.ini';
    $result = [
        'ini' => is_readable($ini) ? 'readable' : 'not readable',
        'localPort' => '',
        'master' => '',
        'masterPort' => '',
        'listener' => '',
    ];
    if (is_readable($ini)) {
        $lines = file($ini, FILE_IGNORE_NEW_LINES) ?: [];
        $inDmr = false;
        foreach ($lines as $line) {
            $trim = trim($line);
            if ($trim === '' || str_starts_with($trim, ';') || str_starts_with($trim, '#')) continue;
            if (str_starts_with($trim, '[') && str_ends_with($trim, ']')) {
                $inDmr = strtolower($trim) === '[dmr network]';
                continue;
            }
            if (!$inDmr || strpos($trim, '=') === false) continue;
            [$key, $value] = array_map('trim', explode('=', $trim, 2));
            $lower = strtolower($key);
            if ($lower === 'local') $result['localPort'] = $value;
            if ($lower === 'address') $result['master'] = $value;
            if ($lower === 'port') $result['masterPort'] = $value;
        }
    }
    if ($result['localPort'] !== '' && preg_match('/^\d+$/', $result['localPort'])) {
        $listeners = asr_command_lines('ss -lunp 2>/dev/null | grep ' . escapeshellarg(':' . $result['localPort']) . ' | head -4', 2000);
        $result['listener'] = implode(' | ', $listeners);
    }
    return $result;
}

function asr_file_brief(string $path): array {
    if (!file_exists($path)) return ['path' => $path, 'status' => 'missing'];
    return [
        'path' => $path,
        'status' => is_readable($path) ? 'present' : 'present, not readable',
        'mtime' => date('c', filemtime($path) ?: time()),
        'perms' => substr(sprintf('%o', fileperms($path)), -4),
    ];
}

function asr_root_file_brief(string $path): array {
    $helper = '/usr/local/sbin/allscan-reimagined-asterisk-read';
    $line = trim((string) shell_exec('sudo -n ' . escapeshellarg($helper) . ' file-status ' . escapeshellarg($path) . ' 2>/dev/null'));
    if ($line === '') return asr_file_brief($path);
    $parts = explode('|', $line);
    if (($parts[0] ?? '') === 'missing') return ['path' => $path, 'status' => 'missing'];
    if (($parts[0] ?? '') === 'present') {
        return [
            'path' => $path,
            'status' => is_readable($path) ? 'present' : 'present, protected',
            'perms' => (string) ($parts[2] ?? ''),
            'mtime' => isset($parts[3]) && ctype_digit((string) $parts[3]) ? date('c', (int) $parts[3]) : '',
            'size' => isset($parts[4]) && ctype_digit((string) $parts[4]) ? (int) $parts[4] : null,
        ];
    }
    return asr_file_brief($path);
}

function asr_tgif_tracking_diagnostics(): array {
    $dropin = '/etc/systemd/system/connected-clients-daemon.service.d/tgif-token.conf';
    $tokenEnvironment = '/etc/allscan-reimagined/connected-clients-daemon.env';
    $loginEnv = '/root/tgif-login.env';
    $refreshScript = '/usr/local/sbin/tgif-refresh-token.py';
    $daemonScript = '/usr/local/sbin/connected-clients-daemon.py';
    $dropinInfo = asr_root_file_brief($dropin);
    $tokenEnvironmentInfo = asr_root_file_brief($tokenEnvironment);
    $tokenConfigured = in_array(($tokenEnvironmentInfo['status'] ?? ''), ['present', 'present, protected'], true)
        && (int) ($tokenEnvironmentInfo['size'] ?? 0) > 0;
    if (!$tokenConfigured && is_readable($dropin)) {
        $contents = (string) file_get_contents($dropin);
        $tokenConfigured = preg_match('/^\s*Environment=TGIF_API_TOKEN=.+/m', $contents) === 1;
    } elseif (($dropinInfo['status'] ?? '') === 'present, protected' || ($dropinInfo['status'] ?? '') === 'present') {
        $tokenConfigured = true;
    }

    return [
        'refreshTimer' => asr_unit_state('tgif-refresh-token.timer'),
        'refreshService' => asr_unit_state('tgif-refresh-token.service'),
        'clientDaemon' => asr_unit_state('connected-clients-daemon.service'),
        'loginEnv' => asr_root_file_brief($loginEnv),
        'refreshScript' => asr_root_file_brief($refreshScript),
        'daemonScript' => asr_root_file_brief($daemonScript),
        'tokenDropin' => $dropinInfo,
        'tokenEnvironment' => $tokenEnvironmentInfo,
        'tokenConfigured' => $tokenConfigured,
    ];
}

function asr_bridge_collector_required(array $bridges): bool {
    foreach ($bridges as $bridge) {
        if (!is_array($bridge)) continue;
        $source = (string) ($bridge['clientSource'] ?? 'disabled');
        $url = trim((string) ($bridge['clientUrl'] ?? ''));
        if (in_array($source, ['local_json', 'http_api'], true) && $url !== '') return true;
    }
    return false;
}

function asr_bridge_diagnostics(): array {
    $config = asr_raw_runtime_config();
    $runtime = asr_runtime_config();
    $bridges = is_array($config['bridges'] ?? null) ? $config['bridges'] : [];
    $clients = asr_bridge_clients_payload();
    $collectorTimer = asr_unit_state('allscan-reimagined-bridge-clients.timer');
    $collectorService = asr_unit_state('allscan-reimagined-bridge-clients.service');
    $clientFile = asr_file_status(__DIR__ . '/connected-clients.json');
    $asrClientFile = asr_file_status(__DIR__ . '/asr-connected-clients.json');
    $node = (string) ($runtime['node'] ?? '');
    $asteriskRead = '/usr/local/sbin/allscan-reimagined-asterisk-read';
    $lstats = $node !== '' ? implode("\n", asr_command_lines('sudo -n ' . escapeshellarg($asteriskRead) . ' lstats ' . escapeshellarg($node), 10000)) : '';
    $nodesOutput = $node !== '' ? implode("\n", asr_command_lines('sudo -n ' . escapeshellarg($asteriskRead) . ' nodes ' . escapeshellarg($node), 6000)) : '';
    $rows = [];

    foreach ($bridges as $bridge) {
        if (!is_array($bridge)) continue;
        $id = (string) ($bridge['id'] ?? '');
        if (!preg_match('/^[a-z][a-z0-9_-]{1,31}$/', $id)) continue;
        $bridgeNode = (string) ($bridge['node'] ?? '');
        $linked = 'unknown';
        if ($bridgeNode !== '') {
            $linkedByLstats = $lstats !== '' && preg_match('/(^|\s)' . preg_quote($bridgeNode, '/') . '\s/m', $lstats);
            $linkedByNodes = $nodesOutput !== '' && preg_match('/(^|[,\s])T?' . preg_quote($bridgeNode, '/') . '([,\s]|$)/m', $nodesOutput);
            $linked = ($linkedByLstats || $linkedByNodes) ? 'yes' : 'no';
        }
        $source = (string) ($bridge['clientSource'] ?? 'disabled');
        $sourceStatus = ['status' => $source === 'disabled' ? 'disabled' : 'configured'];
        if ($source === 'local_json') {
            $sourceStatus = asr_local_path_status((string) ($bridge['clientUrl'] ?? ''));
        } elseif ($source === 'http_api') {
            $sourceStatus = [
                'status' => trim((string) ($bridge['clientUrl'] ?? '')) !== '' ? 'configured' : 'missing URL',
                'url' => trim((string) ($bridge['clientUrl'] ?? '')),
                'auth' => trim((string) ($bridge['clientUsername'] ?? '')) !== '' || asr_bridge_client_secret($id) !== '' ? 'configured' : 'none',
            ];
        }
        $clientCount = isset($clients[$id]) && is_array($clients[$id]) ? count($clients[$id]) : 0;
        $warnings = [];
        if ($id === 'zello' && $clientCount === 0) {
            $zelloTalkers = asr_file_status(__DIR__ . '/zello-talkers.json');
            $zelloStatus = asr_file_status(__DIR__ . '/zello-status-data.json');
            $staleTalkers = ($zelloTalkers['status'] ?? '') !== 'exists' || (int) ($zelloTalkers['mtime'] ?? 0) < time() - 3600;
            if ($staleTalkers) {
                $warnings[] = 'No current Zello talker source is updating. ASR can show Zello users only after the Zello bridge writes talker names to zello-talkers.json or zello-status-data.json.';
            } elseif (($zelloStatus['status'] ?? '') === 'exists') {
                $warnings[] = 'Zello status is updating, but it does not currently include Zello user names.';
            }
        }

        $rows[] = [
            'id' => $id,
            'title' => (string) ($bridge['title'] ?? $id),
            'node' => $bridgeNode,
            'linked' => $linked,
            'clientSource' => $source,
            'clientCount' => $clientCount,
            'sourceStatus' => $sourceStatus,
            'warnings' => $warnings,
            'services' => asr_service_hints($id),
            'dmrUdp' => $id === 'dmr' ? asr_dmr_udp_diagnostics() : null,
            'tgif' => $id === 'dmr' ? asr_tgif_tracking_diagnostics() : null,
        ];
    }

    return [
        'ok' => true,
        'node' => $node,
        'collectorRequired' => asr_bridge_collector_required($bridges),
        'collectorTimer' => $collectorTimer,
        'collectorService' => $collectorService,
        'connectedClientsFile' => $clientFile,
        'asrConnectedClientsFile' => $asrClientFile,
        'bridges' => $rows,
    ];
}

function asr_format_bytes(int|float $bytes): string {
    $bytes = max(0, (float) $bytes);
    foreach (['B', 'KB', 'MB', 'GB', 'TB'] as $unit) {
        if ($bytes < 1024 || $unit === 'TB') return number_format($bytes, $unit === 'B' ? 0 : 1) . ' ' . $unit;
        $bytes /= 1024;
    }
    return '0 B';
}

function asr_uptime_label(float $seconds): string {
    $days = (int) floor($seconds / 86400);
    $hours = (int) floor(($seconds % 86400) / 3600);
    $minutes = (int) floor(($seconds % 3600) / 60);
    return ($days > 0 ? $days . 'd ' : '') . $hours . 'h ' . $minutes . 'm';
}

function asr_process_count(string $name): int {
    if (!preg_match('/^[A-Za-z0-9_.-]+$/', $name)) return 0;
    return max(0, (int) trim((string) shell_exec('pgrep -c -x ' . escapeshellarg($name) . ' 2>/dev/null')));
}

function asr_active_viewers(): int {
    $helper = '/usr/local/sbin/allscan-reimagined-asterisk-read';
    if (!is_executable($helper)) return 0;
    return max(0, (int) trim((string) shell_exec('sudo -n ' . escapeshellarg($helper) . ' astapi-viewers 2>/dev/null')));
}

function asr_recent_request_count(): int {
    $helper = '/usr/local/sbin/allscan-reimagined-asterisk-read';
    $lines = is_executable($helper)
        ? asr_command_lines('sudo -n ' . escapeshellarg($helper) . ' apache-access', 100000)
        : [];
    $cutoff = time() - 60;
    $count = 0;
    foreach ($lines as $line) {
        if (strpos($line, ' /allscan/') === false) continue;
        if (!preg_match('/\[([^\]]+)\]/', $line, $match)) continue;
        $date = DateTimeImmutable::createFromFormat('d/M/Y:H:i:s O', $match[1]);
        if ($date && $date->getTimestamp() >= $cutoff) $count++;
    }
    return $count;
}

function asr_performance_stats(): array {
    $cachePath = '/run/allscan-reimagined/performance-stats.json';
    if (is_readable($cachePath) && (int) @filemtime($cachePath) >= time() - 3) {
        $cached = json_decode((string) file_get_contents($cachePath), true);
        if (is_array($cached)) return $cached;
    }
    $config = asr_raw_runtime_config();
    $bridges = is_array($config['bridges'] ?? null) ? $config['bridges'] : [];
    $load = sys_getloadavg();
    $mem = [];
    foreach (file('/proc/meminfo', FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: [] as $line) {
        if (preg_match('/^([A-Za-z_()]+):\s+(\d+)/', $line, $match)) $mem[$match[1]] = (int) $match[2] * 1024;
    }
    $memoryTotal = (int) ($mem['MemTotal'] ?? 0);
    $memoryAvailable = (int) ($mem['MemAvailable'] ?? 0);
    $uptime = is_readable('/proc/uptime') ? (float) explode(' ', trim((string) file_get_contents('/proc/uptime')))[0] : 0;
    $cacheFiles = glob('/run/allscan-reimagined/astapi-*.json') ?: [];
    $cacheAge = $cacheFiles ? max(0, time() - max(array_map('filemtime', $cacheFiles))) : null;
    $cpu = asr_cpu_temp_payload();
    $diskTotal = (float) @disk_total_space('/');
    $diskFree = (float) @disk_free_space('/');

    $payload = [
        'ok' => true,
        'updated' => gmdate('c'),
        'mode' => !empty($config['lowPowerMode']) ? 'Low-Power' : 'Standard',
        'cpuTemp' => (string) ($cpu['value'] ?? '--'),
        'load' => [
            'one' => round((float) ($load[0] ?? 0), 2),
            'five' => round((float) ($load[1] ?? 0), 2),
            'fifteen' => round((float) ($load[2] ?? 0), 2),
        ],
        'memory' => [
            'used' => asr_format_bytes(max(0, $memoryTotal - $memoryAvailable)),
            'total' => asr_format_bytes($memoryTotal),
            'percent' => $memoryTotal > 0 ? round((($memoryTotal - $memoryAvailable) / $memoryTotal) * 100, 1) : 0,
        ],
        'disk' => [
            'used' => asr_format_bytes(max(0, $diskTotal - $diskFree)),
            'total' => asr_format_bytes($diskTotal),
            'percent' => $diskTotal > 0 ? round((($diskTotal - $diskFree) / $diskTotal) * 100, 1) : 0,
        ],
        'uptime' => asr_uptime_label($uptime),
        'activeViewers' => asr_active_viewers(),
        'requestsLastMinute' => asr_recent_request_count(),
        'apacheWorkers' => asr_process_count('apache2'),
        'asteriskRunning' => asr_process_count('asterisk') > 0,
        'statusCacheAge' => $cacheAge,
        'bridgeCollector' => asr_bridge_collector_required($bridges)
            ? (asr_unit_state('allscan-reimagined-bridge-clients.timer')['state'] ?? 'unknown')
            : 'not needed',
        'integrityTimer' => asr_unit_state('allscan-reimagined-reapply.timer')['state'] ?? 'unknown',
    ];
    if (is_dir(dirname($cachePath))) @file_put_contents($cachePath, json_encode($payload), LOCK_EX);
    return $payload;
}

$action = (string) ($_GET['action'] ?? $_POST['action'] ?? '');

if ($action === 'auth-status') asr_json(asr_auth_payload());
if ($action === 'runtime-config') asr_json(asr_runtime_config());
if ($action === 'bridge-clients') {
    asr_require_read();
    asr_json(asr_bridge_clients_payload());
}
if ($action === 'bridge-status') {
    asr_require_read();
    asr_json(asr_bridge_status_payload());
}
if ($action === 'cpu-temp') {
    asr_require_read();
    asr_json(asr_cpu_temp_payload());
}
if ($action === 'lookup-data') {
    asr_require_read();
    asr_json(asr_lookup_payload());
}
if ($action === 'station-map') {
    asr_require_read();
    $requestedStations = null;
    if (array_key_exists('stations', $_GET)) {
        $encodedStations = substr((string) $_GET['stations'], 0, 12000);
        $decodedStations = json_decode($encodedStations, true);
        $requestedStations = is_array($decodedStations) ? $decodedStations : [];
    } elseif (array_key_exists('callsigns', $_GET)) {
        $requestedStations = preg_split('/\s*,\s*/', trim((string) $_GET['callsigns']), -1, PREG_SPLIT_NO_EMPTY);
    }
    asr_json(asr_station_map_payload(is_array($requestedStations) ? $requestedStations : null));
}
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
if ($action === 'diagnostics-report') {
    asr_require_same_origin();
    asr_require_admin();
    asr_json(asr_diagnostics_report());
}
if ($action === 'bridge-diagnostics') {
    asr_require_same_origin();
    asr_require_admin();
    asr_json(asr_bridge_diagnostics());
}
if ($action === 'performance-stats') {
    asr_require_same_origin();
    asr_require_admin();
    asr_json(asr_performance_stats());
}

asr_error('Unknown action.', 404);
