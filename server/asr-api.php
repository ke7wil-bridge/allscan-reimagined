<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');

const ASR_ETC_FAVORITES = '/etc/allscan/favorites.ini';
const ASR_DEFAULT_FAVORITES = __DIR__ . '/favorites.ini';
const ASR_RUNTIME_CONFIG = '/etc/allscan-reimagined/config.json';
const ASR_RUNTIME_SECRETS = '/etc/allscan-reimagined/secrets.json';
const ASR_STATION_MAP_CACHE = '/etc/allscan-reimagined/station-map-cache.json';
const ASR_LOOKUP_DATA_CACHE = '/run/allscan-reimagined/lookup-data.json';
const ASR_RELEASE_STATUS_CACHE = '/run/allscan-reimagined/release-check/release-status.json';
const ASR_UPDATER_HELPER = '/usr/local/sbin/allscan-reimagined-updater';
const ASR_RELEASE_STATUS_MAX_AGE = 259200;
const ASR_BRIDGE_CONTROL_HELPER = '/usr/local/sbin/allscan-reimagined-bridge-control';
const ASR_YSF_BRIDGE_CONTROL_HELPER = '/usr/local/sbin/allscan-reimagined-ysf-bridge-control';
const ASR_P25_BRIDGE_CONTROL_HELPER = '/usr/local/sbin/allscan-reimagined-p25-bridge-control';
const ASR_NXDN_BRIDGE_CONTROL_HELPER = '/usr/local/sbin/allscan-reimagined-nxdn-bridge-control';
const ASR_M17_BRIDGE_CONTROL_HELPER = '/usr/local/sbin/allscan-reimagined-m17-bridge-control';
const ASR_FAVORITES_UPDATE_HELPER = '/usr/local/sbin/allscan-reimagined-favorites-update';
const ASR_FAVORITES_MANAGER_HELPER = '/usr/local/sbin/allscan-reimagined-favorites-manager';
const ASR_FAVORITES_USER_STATE = '/etc/allscan/favorites-user.json';
const ASR_TGIF_USER_HELPER = '/usr/local/sbin/allscan-reimagined-tgif-user-session';
const ASR_URF_ADMIN_HELPER = '/usr/local/sbin/allscan-reimagined-urf-admin';
const ASR_ASL_BAN_HELPER = '/usr/local/sbin/allscan-reimagined-asl-ban';
const ASR_VERSION = '1.0.0-beta.7.6';
const ASR_VERSION_LABEL = 'v1.0.0 Beta 7.6';

require_once __DIR__ . '/include/common.php';
require_once __DIR__ . '/include/asrRuntime.php';
require_once __DIR__ . '/include/asrFavorites.php';
require_once __DIR__ . '/include/asrBridgeStatus.php';

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

function asr_web_base(): string {
    global $urlbase;
    return rtrim((string) $urlbase, '/');
}

function asr_web_path(string $path = ''): string {
    $suffix = ltrim($path, '/');
    return $suffix === '' ? asr_web_base() . '/' : asr_web_base() . '/' . $suffix;
}

function asr_rebase_legacy_web_path(string $path): string {
    return asrRebaseLegacyWebPath($path);
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

function asr_tgif_user_id(): string {
    global $user;
    if (!asr_logged_in()) asr_error('Login required for TGIF client tracking.', 403);
    return (string) $user->user_id;
}

function asr_tgif_user_command(string $verb, array $args = [], string $stdin = ''): array {
    $allowed = ['status', 'login', 'logout'];
    if (!in_array($verb, $allowed, true)) asr_error('Invalid TGIF user-session action.', 400);
    $command = 'sudo -n ' . escapeshellarg(ASR_TGIF_USER_HELPER) . ' ' . escapeshellarg($verb)
        . ' ' . escapeshellarg(asr_tgif_user_id());
    foreach ($args as $arg) $command .= ' ' . escapeshellarg((string) $arg);
    $spec = [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']];
    $process = @proc_open($command, $spec, $pipes);
    if (!is_resource($process)) asr_error('TGIF session helper is unavailable.', 503);
    fwrite($pipes[0], $stdin);
    fclose($pipes[0]);
    $stdout = stream_get_contents($pipes[1]); fclose($pipes[1]);
    $stderr = stream_get_contents($pipes[2]); fclose($pipes[2]);
    $status = proc_close($process);
    $payload = json_decode((string) $stdout, true);
    if ($status !== 0 || !is_array($payload)) {
        $message = trim((string) $stderr);
        if ($message === '') $message = 'TGIF authentication or session request failed.';
        $lines = preg_split('/\R+/', $message) ?: [];
        $useful = [];
        foreach ($lines as $line) {
            $line = trim($line);
            if ($line === '' || str_starts_with($line, 'Traceback ') || str_starts_with($line, 'File ') || str_contains($line, 'raise SystemExit(main())')) continue;
            $useful[] = $line;
        }
        if ($useful) $message = end($useful);
        asr_error(substr($message, 0, 300), 502);
    }
    return $payload;
}

function asr_tgif_user_status(): array {
    if (!asr_logged_in()) return ['ok' => true, 'configured' => false, 'clients' => [], 'updatedEpoch' => 0];
    return asr_tgif_user_command('status');
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
    $file = asrRuntimeFilePath('bridge-live.json');
    $payload = is_readable($file) ? json_decode((string) file_get_contents($file), true) : null;
    if (!is_array($payload)) return [];

    $definitions = [
        'dmr' => ['DMR Bridge', 'Connected Clients'],
        'ysf' => ['YSF Bridge', 'Linked Gateways'],
        'dstar' => ['D-Star Bridge', 'Recent D-Star Activity'],
        'zello' => ['Zello Bridge', 'Recent Talkers'],
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

function asr_bridge_mode(array $bridge): string {
    foreach ([$bridge['mode'] ?? '', $bridge['type'] ?? '', $bridge['id'] ?? ''] as $value) {
        $candidate = strtolower(trim((string) $value));
        if ($candidate === '') continue;
        $compact = preg_replace('/[^a-z0-9]/', '', $candidate);
        if (str_starts_with((string) $compact, 'dstar')) return 'dstar';
        foreach (['dmr', 'ysf', 'zello', 'p25', 'm17', 'nxdn'] as $knownMode) {
            if (str_starts_with((string) $compact, $knownMode)) return $knownMode;
        }
        if (preg_match('/^([a-z][a-z0-9]*)(?:[_-]|$)/D', $candidate, $match)) {
            return $match[1];
        }
    }
    return 'unknown';
}

const ASR_STANDALONE_ADMIN_DIR = '/run/asr-standalone-admin';
const ASR_STANDALONE_ADMIN_HELPER = '/usr/local/sbin/allscan-reimagined-standalone-admin';

function asr_global_ban_policy_digest(): string {
    $blacklist = @file_get_contents('/run/urf-wil-config/urfd.blacklist');
    $timedBans = @file_get_contents('/run/urf-wil-config/asr-timed-bans.json');
    if (!is_string($blacklist) || !is_string($timedBans)) return '';
    return hash('sha256', "asr-global-ban-v1\0" . $blacklist . "\0" . $timedBans);
}

function asr_standalone_client_admin(array $bridge): array {
    $id = (string) ($bridge['id'] ?? '');
    $mode = asr_bridge_mode($bridge);
    if (!preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', $id)
        || !in_array($mode, ['dmr', 'ysf', 'p25', 'nxdn', 'm17'], true)
        || !empty($bridge['urfReflector'])
        || !is_executable(ASR_STANDALONE_ADMIN_HELPER)) return [];

    $root = @realpath(ASR_STANDALONE_ADMIN_DIR);
    $path = ASR_STANDALONE_ADMIN_DIR . '/' . $id . '/capabilities.json';
    $resolved = @realpath($path);
    $stat = @lstat($path);
    if (!is_string($root) || !is_string($resolved)
        || !str_starts_with($resolved, $root . DIRECTORY_SEPARATOR)
        || is_link($path) || !is_array($stat) || !is_readable($resolved)
        || (($stat['mode'] ?? 0) & 0170000) !== 0100000
        || (($stat['mode'] ?? 0) & 0022) !== 0
        || (int) ($stat['uid'] ?? -1) !== 0
        || (int) ($stat['size'] ?? 0) < 2 || (int) ($stat['size'] ?? 0) > 65536) return [];

    $now = time();
    $mtime = (int) ($stat['mtime'] ?? 0);
    if ($mtime <= 0 || abs($now - $mtime) > 30) return [];
    $cap = json_decode((string) @file_get_contents($resolved), true);
    if (!is_array($cap)) return [];
    $heartbeat = is_int($cap['heartbeatEpoch'] ?? null) ? $cap['heartbeatEpoch'] : 0;
    if (($cap['schema'] ?? null) !== 1
        || ($cap['bridgeId'] ?? '') !== $id || ($cap['mode'] ?? '') !== $mode
        || ($cap['healthy'] ?? false) !== true
        || !preg_match('/^[a-z0-9][a-z0-9_.-]{0,63}$/D', (string) ($cap['adapter'] ?? ''))
        || $heartbeat <= 0 || abs($now - $heartbeat) > 30) return [];

    $result = [];
    if (($cap['listClients'] ?? false) === true) $result[] = 'listClients';
    if (($cap['kickClient'] ?? false) === true
        && ($cap['kickContract'] ?? '') === 'disconnect-until-reconnect'
        && ($cap['kickRequiresCurrentSession'] ?? false) === true
        && ($cap['kickAllowsImmediateReconnect'] ?? false) === true) {
        $result[] = 'kickClient';
    }

    $policyDigest = asr_global_ban_policy_digest();
    $appliedDigest = strtolower(trim((string) ($cap['appliedPolicyDigest'] ?? '')));
    if (($cap['banClient'] ?? false) === true
        && ($cap['unbanClient'] ?? false) === true
        && ($cap['listBans'] ?? false) === true
        && ($cap['banContract'] ?? '') === 'global-timed-v1'
        && $policyDigest !== '' && preg_match('/^[a-f0-9]{64}$/D', $appliedDigest)
        && hash_equals($policyDigest, $appliedDigest)) {
        array_push($result, 'banClient', 'unbanClient', 'listBans');
    }
    return array_values(array_unique($result));
}

function asr_bridge_admin_capabilities(array $bridge): array {
    $cardType = (string) ($bridge['cardType'] ?? 'standard');
    $urf = !empty($bridge['urfReflector']);
    $bridgeControl = in_array($cardType, ['dmr_net', 'ysf_net', 'p25_net', 'nxdn_net', 'm17_net'], true)
        ? ['connect', 'disconnect', 'changeDestination'] : [];
    $mode = strtolower((string) ($bridge['mode'] ?? ''));
    $clientAdmin = ($urf || $mode === 'dstar') ? ['listClients'] : [];
    if (!$urf) $clientAdmin = array_merge($clientAdmin, asr_standalone_client_admin($bridge));
    $globalAdmin = $urf || in_array($mode, ['dstar', 'zello'], true)
        || in_array('banClient', $clientAdmin, true);
    if ($urf && $mode === 'dmr' && is_dir('/run/dmr-bridge')) $clientAdmin = array_merge($clientAdmin, ['kickClient', 'banClient', 'unbanClient', 'listBans']);
    if ($urf && $mode !== 'dmr' && is_readable('/run/urf-wil/asr-admin-capabilities.json')) $clientAdmin = array_merge($clientAdmin, ['kickClient', 'banClient', 'unbanClient', 'listBans']);
    if ($mode === 'dstar' && is_dir('/run/dstar-reflector-admin') && is_dir('/run/dstar-reflector-control')) $clientAdmin = array_merge($clientAdmin, ['kickClient', 'banClient', 'unbanClient', 'listBans']);
    if ($mode === 'zello' && is_dir('/var/www/html/asr')) $clientAdmin = array_merge($clientAdmin, ['banClient', 'unbanClient', 'listBans']);
    $clientAdmin = array_values(array_unique($clientAdmin));
    return [
        'bridgeControl' => $bridgeControl,
        'clientAdmin' => $clientAdmin,
        'activity' => ['currentTalker', 'lastTalker', 'recentActivity'],
        'lifecycle' => ['health', 'readiness', 'runtimeAvailability'],
        'managementScope' => $globalAdmin ? 'asr-global' : 'bridge',
        'clientAuthority' => $globalAdmin ? 'asr-global-ban' : 'none',
        'authoritativeClientId' => $globalAdmin ? ($mode === 'zello' ? 'zello-username' : 'callsign') : '',
        'banScopeLabel' => $globalAdmin ? 'GLOBAL' : '',
    ];
}

function asr_protected_ban_identities(): array {
    $protected = [];
    foreach (glob('/etc/asterisk/extensions.d/*broadcast*.conf') ?: [] as $path) {
        if (!is_file($path) || !is_readable($path) || filesize($path) > 65536) continue;
        $raw = (string) file_get_contents($path);
        if (!preg_match_all('/\\bRpt\\(\\s*[0-9]{3,10}\\s*,\\s*P\\s*,\\s*([A-Z0-9_.\\/-]{1,32})/i', $raw, $matches)) continue;
        foreach ($matches[1] as $identity) $protected[strtoupper(trim((string) $identity))] = true;
    }
    return array_keys($protected);
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
    // Local URF compatibility: expand the original single URF card into one display card per protocol.
    $normalizedStoredBridges = [];
    foreach ($storedBridges as $bridge) {
        if (is_array($bridge) && !empty($bridge['urfReflector']) && asr_bridge_mode($bridge) === 'urf') {
            foreach (['dmr' => 'DMR', 'ysf' => 'YSF', 'p25' => 'P25', 'nxdn' => 'NXDN', 'm17' => 'M17'] as $urfMode => $urfLabel) {
                $expanded = $bridge;
                $expanded['id'] = 'urf_' . $urfMode;
                $expanded['mode'] = $urfMode;
                $expanded['title'] = $urfLabel . ' Bridge';
                $expanded['detailTitle'] = 'Connected Clients';
                $expanded['friendlyName'] = $urfLabel . ' Bridge';
                $expanded['urfGroupId'] = (string) ($bridge['urfGroupId'] ?? 'urf');
                $normalizedStoredBridges[] = $expanded;
            }
            continue;
        }
        $normalizedStoredBridges[] = $bridge;
    }
    $storedBridges = $normalizedStoredBridges;
    $bridges = [];
    foreach ($storedBridges as $bridge) {
        if (!is_array($bridge) || !preg_match('/^[a-z][a-z0-9_-]{1,31}$/', (string) ($bridge['id'] ?? ''))) continue;
        $mode = asr_bridge_mode($bridge);
        $bridgeNode = preg_match('/^\d{3,10}$/', (string) ($bridge['node'] ?? '')) ? (string) $bridge['node'] : '';
        $cardType = in_array((string) ($bridge['cardType'] ?? ''), ['standard', 'dmr_net', 'ysf_net', 'p25_net', 'nxdn_net', 'm17_net'], true)
            ? (string) $bridge['cardType']
            : 'standard';
        $linkAlias = '';
        if ($cardType === 'dmr_net'
            && $bridgeNode !== ''
            && preg_match('/^[0-9]{3,6}$/D', $node)
            && preg_match('/^999[0-9]{6}$/D', (string) ($bridge['linkAlias'] ?? ''))
            && hash_equals('999' . str_pad($node, 6, '0', STR_PAD_LEFT), (string) $bridge['linkAlias'])
            && (string) $bridge['linkAlias'] !== $bridgeNode) {
            $linkAlias = (string) $bridge['linkAlias'];
        }
        $bridges[] = [
            'id' => (string) $bridge['id'],
            'mode' => $mode,
            'node' => $bridgeNode,
            'linkAlias' => $linkAlias,
            'urfReflector' => !empty($bridge['urfReflector']),
            'urfGroupId' => !empty($bridge['urfReflector']) ? (string) ($bridge['urfGroupId'] ?? 'urf') : '',
            'title' => substr(trim((string) ($bridge['title'] ?? 'Bridge')), 0, 80),
            'detailTitle' => substr(trim((string) ($bridge['detailTitle'] ?? 'Connected Clients')), 0, 80),
            'friendlyName' => substr(trim((string) ($bridge['friendlyName'] ?? '')), 0, 80),
            'cardType' => $cardType,
            'backendMode' => in_array((string) ($bridge['backendMode'] ?? ''), ['display_only', 'managed'], true)
                ? (string) $bridge['backendMode']
                : ($cardType === 'standard' && in_array($mode, ['p25', 'nxdn', 'm17'], true)
                    ? (isset($bridge['bridgePermission']) ? 'managed' : 'display_only')
                    : 'managed'),
            'allowTune' => $cardType !== 'standard' && !empty($bridge['allowTune']),
            'adminCapabilities' => asr_bridge_admin_capabilities($bridge),
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
        'headerLogo' => asr_rebase_legacy_web_path((string) ($stored['headerLogo'] ?? asr_web_path('asr-logo-bright-r-tight.png'))),
        'footerLogo' => asr_web_path('asr-logo-bright-r-tight.png'),
        'versionLabel' => ASR_VERSION_LABEL,
        'lowPowerMode' => !empty($stored['lowPowerMode']),
        'protectedBanIdentities' => asr_protected_ban_identities(),
        'bridges' => $bridges,
    ];
}

function asr_release_status_payload(): array {
    $pending = [
        'ok' => true,
        'status' => 'pending',
        'updateAvailable' => false,
        'checkedAt' => '',
        'installedVersion' => ASR_VERSION,
        'installedLabel' => ASR_VERSION_LABEL,
        'availableVersion' => '',
        'availableLabel' => '',
        'releaseUrl' => '',
        'publishedAt' => '',
        'package' => ['name' => '', 'url' => '', 'size' => 0, 'sha256' => ''],
    ];
    if (!is_readable(ASR_RELEASE_STATUS_CACHE)) return $pending;

    $decoded = json_decode((string) file_get_contents(ASR_RELEASE_STATUS_CACHE), true);
    if (!is_array($decoded) || (string) ($decoded['installedVersion'] ?? '') !== ASR_VERSION) {
        return $pending;
    }

    $checkedAt = (string) ($decoded['checkedAt'] ?? '');
    $checkedEpoch = $checkedAt !== '' ? strtotime($checkedAt) : false;
    if ($checkedEpoch === false || time() - $checkedEpoch > ASR_RELEASE_STATUS_MAX_AGE) {
        $pending['checkedAt'] = $checkedAt;
        return $pending;
    }

    $trustedGithubUrl = static function (string $value): string {
        $parts = parse_url($value);
        if (!is_array($parts)
            || strtolower((string) ($parts['scheme'] ?? '')) !== 'https'
            || strtolower((string) ($parts['host'] ?? '')) !== 'github.com'
            || isset($parts['user'])
            || isset($parts['pass'])
            || isset($parts['port'])
            || !str_starts_with(
                (string) ($parts['path'] ?? ''),
                '/ke7wil-bridge/allscan-reimagined/releases/'
            )) {
            return '';
        }
        return $value;
    };

    $releaseUrl = $trustedGithubUrl((string) ($decoded['releaseUrl'] ?? ''));
    $package = is_array($decoded['package'] ?? null) ? $decoded['package'] : [];
    $packageUrl = $trustedGithubUrl((string) ($package['url'] ?? ''));
    $checksum = strtolower((string) ($package['sha256'] ?? ''));
    if (!preg_match('/^[a-f0-9]{64}$/', $checksum)) $checksum = '';

    return [
        'ok' => true,
        'status' => !empty($decoded['updateAvailable']) ? 'update_available' : 'up_to_date',
        'updateAvailable' => !empty($decoded['updateAvailable']),
        'checkedAt' => $checkedAt,
        'installedVersion' => ASR_VERSION,
        'installedLabel' => ASR_VERSION_LABEL,
        'availableVersion' => substr((string) ($decoded['availableVersion'] ?? ''), 0, 80),
        'availableLabel' => substr((string) ($decoded['availableLabel'] ?? ''), 0, 80),
        'releaseUrl' => $releaseUrl,
        'publishedAt' => (string) ($decoded['publishedAt'] ?? ''),
        'package' => [
            'name' => substr((string) ($package['name'] ?? ''), 0, 180),
            'url' => $packageUrl,
            'size' => max(0, (int) ($package['size'] ?? 0)),
            'sha256' => $checksum,
        ],
    ];
}


function asr_updater_command(string $operation, string $jobId = ''): array {
    $allowed = [
        'check' => '--check-json',
        'preflight' => '--preflight-json',
        'queue' => '--queue-update',
        'recover' => '--recover-json',
        'status' => '--status-json',
    ];
    if (!isset($allowed[$operation])) asr_error('Invalid update operation.', 400);
    if ($operation === 'status' && !preg_match('/^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$/D', $jobId)) {
        asr_error('Invalid update job ID.', 400);
    }
    if (!is_executable(ASR_UPDATER_HELPER)) asr_error('ASR updater is not installed.', 503);
    $argv = ['sudo', '-n', ASR_UPDATER_HELPER, $allowed[$operation]];
    if ($operation === 'status') $argv[] = $jobId;
    $process = @proc_open($argv, [
        0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w'],
    ], $pipes);
    if (!is_resource($process)) asr_error('ASR updater is unavailable.', 503);
    fclose($pipes[0]);
    $stdout = stream_get_contents($pipes[1], 16384);
    fclose($pipes[1]);
    // The helper's raw output is root-only; never pass stderr to the browser.
    stream_get_contents($pipes[2], 16384);
    fclose($pipes[2]);
    $exit = proc_close($process);
    $payload = json_decode((string) $stdout, true);
    if (!is_array($payload)) asr_error('ASR updater did not return a valid status.', 503);
    if ($exit !== 0 || empty($payload['ok'])) {
        $reason = (string) ($payload['error'] ?? 'Update operation could not complete.');
        asr_error(substr(preg_replace('/[^A-Za-z0-9 .,;:-]/', '', $reason), 0, 180), 409);
    }
    return $payload;
}

function asr_dmr_net_live_statuses(): array {
    $path = '/run/allscan-reimagined-bridge-control/bridge-live.json';
    $fileStatus = @stat($path);
    if (!is_array($fileStatus)
        || (int) ($fileStatus['uid'] ?? -1) !== 0
        || (((int) ($fileStatus['mode'] ?? 0)) & 0022) !== 0) {
        return [];
    }
    $liveMtime = (int) ($fileStatus['mtime'] ?? 0);
    $liveFresh = $liveMtime > 0 && $liveMtime <= time() + 300 && time() - $liveMtime <= 10;
    $decoded = json_decode((string) @file_get_contents($path), true);
    $entries = is_array($decoded['bridges'] ?? null) ? $decoded['bridges'] : [];
    if ($entries === []) return [];

    $config = is_readable(ASR_RUNTIME_CONFIG)
        ? json_decode((string) file_get_contents(ASR_RUNTIME_CONFIG), true)
        : null;
    $allowed = [];
    foreach ((array) ($config['bridges'] ?? []) as $bridge) {
        if (!is_array($bridge) || ($bridge['cardType'] ?? '') !== 'dmr_net') continue;
        $id = (string) ($bridge['id'] ?? '');
        if (preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', $id)) $allowed[$id] = true;
    }

    $clean = [];
    foreach ($entries as $id => $entry) {
        $id = (string) $id;
        if (!isset($allowed[$id]) || !is_array($entry)) continue;
        $role = $liveFresh ? strtolower((string) ($entry['role'] ?? 'idle')) : 'idle';
        if (!in_array($role, ['idle', 'source', 'relay'], true)) $role = 'idle';
        $clean[$id] = [
            'active' => $role !== 'idle',
            'role' => $role,
            'state' => $role === 'source' ? 'TX ACTIVE' : ($role === 'relay' ? 'RELAY' : 'Idle'),
            'node' => substr((string) ($entry['node'] ?? ''), 0, 10),
            'title' => substr((string) ($entry['title'] ?? 'DMR Net Bridge'), 0, 80),
            'channel' => substr((string) ($entry['channel'] ?? '-'), 0, 80),
            'active_start_epoch' => max(0, (int) ($entry['active_start_epoch'] ?? 0)),
            'activity_epoch' => max(0, (int) ($entry['activity_epoch'] ?? 0)),
            'last_time_epoch' => max(0, (int) ($entry['last_time_epoch'] ?? 0)),
            'warning' => substr((string) ($entry['warning'] ?? ''), 0, 160),
            'current_user' => substr((string) ($entry['current_user'] ?? ''), 0, 120),
            'last_user' => substr((string) ($entry['last_user'] ?? '-'), 0, 120),
            'caller' => substr((string) ($entry['caller'] ?? ''), 0, 120),
            'last_source_user' => substr((string) ($entry['last_source_user'] ?? ''), 0, 120),
            'last_source_epoch' => max(0, (int) ($entry['last_source_epoch'] ?? 0)),
            'recent_users' => [],
        ];
    }
    return $clean;
}

function asr_next_mode_bridge_config(string $bridgeId): ?array {
    if (!preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', $bridgeId) || !is_readable(ASR_RUNTIME_CONFIG)) return null;
    $config = json_decode((string) file_get_contents(ASR_RUNTIME_CONFIG), true);
    foreach ((array) ($config['bridges'] ?? []) as $bridge) {
        if (!is_array($bridge) || (string) ($bridge['id'] ?? '') !== $bridgeId) continue;
        $cardType = (string) ($bridge['cardType'] ?? '');
        if (!in_array($cardType, ['p25_net', 'nxdn_net', 'm17_net'], true)) return null;
        return $bridge;
    }
    return null;
}

function asr_next_mode_helper_path(string $mode): string {
    if ($mode === 'p25') return ASR_P25_BRIDGE_CONTROL_HELPER;
    if ($mode === 'nxdn') return ASR_NXDN_BRIDGE_CONTROL_HELPER;
    if ($mode === 'm17') return ASR_M17_BRIDGE_CONTROL_HELPER;
    return '';
}

function asr_next_mode_helper(string $mode, string $bridgeId, array $arguments, string $fallback, bool $fatal = true): array {
    global $user;
    $helper = asr_next_mode_helper_path($mode);
    if ($helper === '' || !is_executable($helper)) {
        if ($fatal) asr_error(strtoupper($mode) . ' Net Bridge control helper is not installed.', 503);
        return [];
    }
    $username = substr(preg_replace('/[^A-Za-z0-9_.@+-]/', '_', (string) ($user->name ?? 'unknown')), 0, 80);
    $command = 'sudo -n ' . escapeshellarg($helper);
    if ($mode === 'm17') {
        $command .= ' --bridge ' . escapeshellarg($bridgeId);
        if (in_array((string) ($arguments[0] ?? ''), ['connect', 'disconnect'], true)) {
            $command .= ' --user ' . escapeshellarg($username);
        }
        foreach ($arguments as $argument) $command .= ' ' . escapeshellarg((string) $argument);
    } else {
        foreach ($arguments as $argument) $command .= ' ' . escapeshellarg((string) $argument);
        if (in_array((string) ($arguments[0] ?? ''), ['connect', 'disconnect'], true)) {
            $command .= ' --user ' . escapeshellarg($username);
        }
    }
    $lines = [];
    $status = 1;
    exec($command . ' 2>&1', $lines, $status);
    $payload = null;
    foreach (array_reverse($lines) as $line) {
        $decoded = json_decode($line, true);
        if (is_array($decoded)) { $payload = $decoded; break; }
    }
    if (!is_array($payload)) {
        if ($fatal) asr_error(strtoupper($mode) . ' Net Bridge control returned an invalid response.', 500);
        return [];
    }
    if ($status !== 0 || empty($payload['ok'])) {
        if ($fatal) asr_error((string) ($payload['error'] ?? $fallback), 500);
        return [];
    }
    return $payload;
}

function asr_next_mode_statuses(): array {
    if (!is_readable(ASR_RUNTIME_CONFIG)) return ['live' => [], 'controls' => []];
    $config = json_decode((string) file_get_contents(ASR_RUNTIME_CONFIG), true);
    $live = [];
    $controls = [];
    $modeCaches = [];
    foreach (['p25', 'nxdn'] as $cacheMode) {
        $cache = asr_secure_root_json('/run/allscan-reimagined-' . $cacheMode . '-bridge-control/status.json');
        $updated = (int) ($cache['updatedEpoch'] ?? 0);
        if (is_array($cache)
            && (string) ($cache['mode'] ?? '') === $cacheMode
            && $updated > 0
            && $updated <= time() + 30
            && time() - $updated <= 10
            && is_array($cache['bridges'] ?? null)) {
            $modeCaches[$cacheMode] = $cache['bridges'];
        }
    }
    $linkedNodes = [];
    $mainNode = preg_match('/^[0-9]{3,10}$/D', (string) ($config['node'] ?? '')) ? (string) $config['node'] : '';
    $asteriskRead = '/usr/local/sbin/allscan-reimagined-asterisk-read';
    if ($mainNode !== '' && is_executable($asteriskRead)) {
        foreach (asr_command_lines('sudo -n ' . escapeshellarg($asteriskRead) . ' lstats ' . escapeshellarg($mainNode), 10000) as $line) {
            if (preg_match('/^([0-9]{3,10})\s+.*\sESTABLISHED\s*$/D', trim($line), $match)) $linkedNodes[$match[1]] = true;
        }
    }
    foreach ((array) ($config['bridges'] ?? []) as $bridge) {
        if (!is_array($bridge)) continue;
        $id = (string) ($bridge['id'] ?? '');
        $mode = asr_bridge_mode($bridge);
        $cardType = (string) ($bridge['cardType'] ?? 'standard');
        $modeCardTypes = ['standard', $mode . '_net'];
        if (!in_array($mode, ['p25', 'nxdn', 'm17'], true)
            || !in_array($cardType, $modeCardTypes, true)
            || !preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', $id)) continue;
        $backendMode = (string) ($bridge['backendMode'] ?? (isset($bridge['bridgePermission']) ? 'managed' : 'display_only'));
        if ($cardType === 'standard' && $backendMode === 'display_only') continue;
        if (!in_array((string) ($bridge['bridgePermission'] ?? ''), ['self_owned', 'approved'], true)
            || !is_executable(asr_next_mode_helper_path($mode))) {
            $reason = !in_array((string) ($bridge['bridgePermission'] ?? ''), ['self_owned', 'approved'], true)
                ? 'Bridge permission is not confirmed.'
                : 'The configured ' . strtoupper($mode) . ' control helper is not installed.';
            $controls[$id] = ['ready' => false, 'linked' => false, 'digitalLinked' => false, 'allstarLinked' => false, 'currentDestination' => '', 'currentDestinationLabel' => '', 'reason' => strtoupper($mode) . ' backend not ready: ' . $reason, 'missing' => [$reason]];
            continue;
        }
        $status = $mode === 'm17'
            ? asr_next_mode_helper($mode, $id, ['status'], 'Status unavailable.', false)
            : (is_array($modeCaches[$mode][$id] ?? null) ? $modeCaches[$mode][$id] : []);
        if ($status === [] || ($mode !== 'm17' && (empty($status['ok']) || !empty($status['stale'])))) {
            $reason = trim((string) ($status['message'] ?? $status['error'] ?? ''));
            if ($reason === '') $reason = trim((string) ($status['talkerEvidenceReason'] ?? ''));
            if ($reason === '') $reason = 'Fresh backend status is unavailable.';
            $missing = [];
            foreach ((array) (($status['serviceState']['services'] ?? [])) as $unit => $service) {
                if (!is_array($service) || empty($service['active'])) {
                    $missing[] = (string) $unit . ' is not active (' . substr((string) ($service['subState'] ?? 'unknown'), 0, 40) . ').';
                }
            }
            $missing[] = $reason;
            $controls[$id] = ['ready' => false, 'linked' => false, 'digitalLinked' => false, 'allstarLinked' => false, 'currentDestination' => '', 'currentDestinationLabel' => '', 'reason' => strtoupper($mode) . ' backend not ready: ' . $reason, 'missing' => array_values(array_unique($missing))];
            continue;
        }
        if ($mode === 'm17') {
            $confirmed = is_array($status['confirmedTarget'] ?? null) ? $status['confirmedTarget'] : [];
            $reflector = substr((string) ($confirmed['reflector'] ?? ''), 0, 16);
            $module = substr((string) ($confirmed['module'] ?? ''), 0, 1);
            $destination = trim($reflector . ' ' . $module);
            $linked = (string) ($status['linkState'] ?? '') === 'linked';
            $ready = !empty($status['audioReady']);
            $talker = $linked ? substr((string) ($status['talker'] ?? ''), 0, 9) : '';
            $warning = substr((string) ($status['error'] ?? ''), 0, 160);
        } else {
            $destination = substr((string) ($status['confirmedTarget'] ?? ''), 0, 12);
            $linked = !empty($status['reachabilityConfirmed']);
            $serviceState = is_array($status['serviceState'] ?? null) ? $status['serviceState'] : [];
            $ready = !empty($status['ok']) && !empty($serviceState['ready']);
            $talker = !empty($status['talkerEvidenceAvailable'])
                && ($status['inboundTalkerActive'] ?? null) === true
                ? substr((string) ($status['inboundTalker'] ?? ''), 0, 16)
                : '';
            $warningText = (string) ($status['talkerEvidenceReason'] ?? '');
            if ($warningText === '') $warningText = (string) ($status['message'] ?? '');
            $warning = substr($warningText, 0, 160);
        }
        $allstarLinked = isset($linkedNodes[(string) ($bridge['node'] ?? '')]);
        $live[$id] = [
            'active' => $talker !== '',
            'role' => $talker !== '' ? 'source' : 'idle',
            'state' => $talker !== '' ? 'TX ACTIVE' : ($linked ? 'Idle' : 'Disconnected'),
            'node' => substr((string) ($bridge['node'] ?? ''), 0, 10),
            'title' => substr((string) ($bridge['title'] ?? strtoupper($mode) . ' Net Bridge'), 0, 80),
            'channel' => $linked ? $destination : '-',
            'destination' => $destination,
            'destinationName' => $destination,
            'linked' => $linked,
            'digitalLinked' => $linked,
            'allstarLinked' => $allstarLinked,
            'ready' => $ready,
            'current_user' => $talker,
            'caller' => $talker,
            'last_user' => '-',
            'warning' => $warning,
            'recent_users' => [],
        ];
        $controls[$id] = [
            'ready' => $ready,
            'reason' => $ready
                ? strtoupper($mode) . ' backend ready.'
                : strtoupper($mode) . ' backend not ready: ' . ($warning !== '' ? $warning : 'Required service, status, audio, or security checks have not passed.'),
            'linked' => $linked && $allstarLinked,
            'digitalLinked' => $linked,
            'allstarLinked' => $allstarLinked,
            'currentDestination' => $destination,
            'currentDestinationLabel' => $destination,
            'missing' => $ready ? [] : [($warning !== '' ? $warning : 'Required service, status, audio, or security checks have not passed.')],
        ];
    }
    return ['live' => $live, 'controls' => $controls];
}

function asr_next_mode_connect(string $bridgeId, string $destination): array {
    $bridge = asr_next_mode_bridge_config($bridgeId);
    if ($bridge === null) asr_error('Configured P25, NXDN, or M17 Net Bridge was not found.', 404);
    $mode = asr_bridge_mode($bridge);
    $destination = strtoupper(trim($destination));
    if ($mode === 'm17') {
        if (!preg_match('/^(M17-[A-Z0-9]{3})[\s\/:]+([A-Z])$/D', $destination, $match)) {
            asr_error('Enter an approved M17 destination as REFLECTOR MODULE, for example M17-M17 C.');
        }
        $payload = asr_next_mode_helper($mode, $bridgeId, [
            'connect', '--reflector', $match[1], '--module', $match[2],
        ], 'M17 Net Bridge connection failed.');
        $payload['currentDestination'] = $match[1] . ' ' . $match[2];
        $payload['currentDestinationLabel'] = $payload['currentDestination'];
        return $payload;
    }
    if (!preg_match('/^[0-9]{1,6}$/D', $destination) || (int) $destination < 1) {
        asr_error('Enter an approved numeric ' . strtoupper($mode) . ' destination.');
    }
    $payload = asr_next_mode_helper($mode, $bridgeId, ['connect', $bridgeId, $destination], strtoupper($mode) . ' Net Bridge connection failed.');
    $payload['currentDestination'] = $destination;
    $payload['currentDestinationLabel'] = $destination;
    return $payload;
}

function asr_next_mode_disconnect(string $bridgeId): array {
    $bridge = asr_next_mode_bridge_config($bridgeId);
    if ($bridge === null) asr_error('Configured P25, NXDN, or M17 Net Bridge was not found.', 404);
    $mode = asr_bridge_mode($bridge);
    return $mode === 'm17'
        ? asr_next_mode_helper($mode, $bridgeId, ['disconnect'], 'M17 Net Bridge disconnect failed.')
        : asr_next_mode_helper($mode, $bridgeId, ['disconnect', $bridgeId], strtoupper($mode) . ' Net Bridge disconnect failed.');
}

function asr_urf_admin_command(string $verb, string $rule = '', string $detail = ''): array {
    global $user;
    if (!in_array($verb, ['list', 'ban', 'unban', 'dmr-list', 'dmr-ban', 'dmr-unban'], true)) asr_error('Invalid Global Ban action.');
    if (!is_executable(ASR_URF_ADMIN_HELPER)) asr_error('Global Ban helper is unavailable.', 503);
    $command = 'sudo -n ' . escapeshellarg(ASR_URF_ADMIN_HELPER) . ' ' . escapeshellarg($verb);
    if ($rule !== '') $command .= ' ' . escapeshellarg($rule);
    if ($detail !== '') $command .= ' ' . escapeshellarg($detail);
    if (!str_starts_with($verb, 'dmr-')) {
        $actor = substr(preg_replace('/[^A-Za-z0-9_.@+-]/', '_', (string) ($user->name ?? 'unknown')), 0, 80);
        $command .= ' --actor ' . escapeshellarg($actor ?: 'unknown');
    }
    $lines = []; $status = 1; exec($command . ' 2>&1', $lines, $status);
    $payload = null;
    foreach (array_reverse($lines) as $line) { $decoded = json_decode($line, true); if (is_array($decoded)) { $payload = $decoded; break; } }
    if ($status !== 0 || !is_array($payload) || empty($payload['ok'])) asr_error(substr((string)($payload['error'] ?? 'Global Ban command failed.'), 0, 180), 500);
    return $payload;
}

function asr_asl_ban_command(string $verb, string $kind = '', string $value = '', string $duration = '', string $reason = ''): array {
    global $user;
    if (!in_array($verb, ['list', 'ban', 'unban'], true)) asr_error('Invalid AllStar/EchoLink ban action.');
    if (!is_executable(ASR_ASL_BAN_HELPER)) asr_error('AllStar/EchoLink ban helper is unavailable.', 503);
    $command = 'sudo -n ' . escapeshellarg(ASR_ASL_BAN_HELPER) . ' ' . escapeshellarg($verb);
    if ($verb !== 'list') {
        if (!in_array($kind, ['node', 'allstar-call', 'echolink-call'], true)) asr_error('Invalid ban target.');
        $value = strtoupper(trim($value));
        if ($kind === 'node' ? !preg_match('/^[0-9]{3,10}$/D', $value) : !preg_match('/^[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}(?:-[LR])?$/D', $value)) {
            asr_error('Invalid node or callsign.');
        }
        $command .= ' --kind ' . escapeshellarg($kind) . ' --value ' . escapeshellarg($value);
        $actor = substr(preg_replace('/[^A-Za-z0-9_.@+-]/', '_', (string) ($user->name ?? 'unknown')), 0, 80);
        $command .= ' --actor ' . escapeshellarg($actor ?: 'unknown');
        if ($verb === 'ban') {
            if (!in_array($duration, ['15m', '1h', '1w', '30d', 'permanent'], true)) asr_error('Invalid ban duration.');
            $reason = trim(preg_replace('/[\x00-\x1f\x7f]/', ' ', $reason) ?? '');
            if (strlen($reason) > 160) asr_error('Ban reason is too long.');
            $command .= ' --duration ' . escapeshellarg($duration)
                . ' --reason ' . escapeshellarg($reason);
        }
    }
    $lines = []; $status = 1; exec($command . ' 2>&1', $lines, $status);
    $payload = null;
    foreach (array_reverse($lines) as $line) {
        $candidate = json_decode($line, true);
        if (is_array($candidate)) { $payload = $candidate; break; }
    }
    if ($status !== 0 || !is_array($payload) || empty($payload['ok'])) {
        asr_error(substr((string) ($payload['error'] ?? 'AllStar/EchoLink ban operation failed.'), 0, 180), 502);
    }
    return $payload;
}

function asr_urf_extract_elements(string $body, string $tag): array {
    $pattern = '#<' . preg_quote($tag, '#') . '>(.*?)</' . preg_quote($tag, '#') . '>#s';
    if (!preg_match_all($pattern, $body, $matches)) return [];
    return array_map(static fn($value) => trim(html_entity_decode(strip_tags((string) $value), ENT_QUOTES | ENT_XML1, 'UTF-8')), $matches[1]);
}

function asr_urf_first_element(string $body, string $tag): string {
    $values = asr_urf_extract_elements($body, $tag);
    return (string) ($values[0] ?? '');
}

function asr_urf_normalize_callsign(string $callsign): string {
    $normalized = strtoupper(trim($callsign));
    // URFD/YSF may append the linked module as a separate one-character suffix.
    // Strip that transport suffix before identity policy is applied.
    return trim((string) (preg_replace('/\s+[A-Z]$/D', '', $normalized) ?: $normalized));
}

function asr_urf_is_service_identity(string $callsign, string $mode): bool {
    static $policy = null;
    if ($policy === null) {
        $stored = is_readable(ASR_RUNTIME_CONFIG)
            ? json_decode((string) file_get_contents(ASR_RUNTIME_CONFIG), true)
            : [];
        if (!is_array($stored)) $stored = [];
        $enabled = !array_key_exists('filterServiceStations', $stored)
            || !empty($stored['filterServiceStations']);
        $custom = [];
        foreach ((array) ($stored['filteredStations'] ?? []) as $station) {
            $station = strtoupper(trim((string) $station));
            if (preg_match('/^[A-Z0-9][A-Z0-9_.\/-]{0,14}$/D', $station)) $custom[$station] = true;
            if (count($custom) >= 64) break;
        }
        $policy = ['enabled' => $enabled, 'custom' => $custom];
    }
    if (empty($policy['enabled'])) return false;
    $normalized = asr_urf_normalize_callsign($callsign);
    // These identities recur on fixed schedules across several protocols and
    // are reflector health/test traffic rather than normal station sessions.
    if (in_array($normalized, ['RFCKRD0', 'YSF-LIVE', 'KF0WSS', 'SRVPROB', 'M7TEST'], true)) return true;
    return isset($policy['custom'][$normalized]);
}

function asr_urf_kick_command(string $callsign, string $protocol): array {
    global $user;
    if (!is_executable(ASR_URF_ADMIN_HELPER)) asr_error('Bridge client administration helper is unavailable.', 503);
    $callsign = strtoupper(trim($callsign));
    $protocol = strtoupper(trim($protocol));
    if (!preg_match('/^[A-Z0-9][A-Z0-9_.\/-]{0,14}$/D', $callsign)) asr_error('Invalid bridge client identity.');
    if (!in_array($protocol, ['DMRMMDVM', 'YSF', 'P25', 'NXDN', 'M17', 'DSTAR'], true)) asr_error('Invalid bridge client protocol.');
    $actor = substr(preg_replace('/[^A-Za-z0-9_.@+-]/', '_', (string) ($user->name ?? 'unknown')), 0, 80);
    $command = 'sudo -n ' . escapeshellarg(ASR_URF_ADMIN_HELPER) . ' kick ' . escapeshellarg($callsign) . ' ' . escapeshellarg($protocol) . ' --actor ' . escapeshellarg($actor ?: 'unknown');
    $output = []; $code = 0; exec($command . ' 2>/dev/null', $output, $code);
    $payload = json_decode((string) end($output), true);
    if ($code !== 0 || !is_array($payload) || empty($payload['ok'])) asr_error((string) ($payload['error'] ?? 'Bridge client kick failed.'), 500);
    return $payload;
}

function asr_standalone_kick_command(string $bridgeId, string $callsign): array {
    global $user;
    $bridge = asr_bridge_config_by_id($bridgeId);
    if (!is_array($bridge) || !in_array('kickClient', asr_standalone_client_admin($bridge), true)) {
        asr_error('Standalone bridge Kick backend is unavailable.', 503);
    }
    $callsign = strtoupper(trim($callsign));
    if (!preg_match('/^[A-Z0-9][A-Z0-9_.\/-]{0,14}$/D', $callsign)) asr_error('Invalid bridge client identity.');
    $actor = substr(preg_replace('/[^A-Za-z0-9_.@+-]/', '_', (string) ($user->name ?? 'unknown')), 0, 80);
    $command = 'sudo -n ' . escapeshellarg(ASR_STANDALONE_ADMIN_HELPER)
        . ' kick ' . escapeshellarg($bridgeId) . ' ' . escapeshellarg($callsign)
        . ' --actor ' . escapeshellarg($actor ?: 'unknown');
    $output = []; $code = 0; exec($command . ' 2>/dev/null', $output, $code);
    $payload = json_decode((string) end($output), true);
    if ($code !== 0 || !is_array($payload) || empty($payload['ok'])) {
        asr_error((string) ($payload['error'] ?? 'Standalone bridge Kick failed.'), 500);
    }
    return $payload;
}

function asr_urf_status_payload(): array {
    $path = '/run/urf-wil/urfd.xml';
    $eventPath = '/run/urf-wil/asr-live-events.jsonl';
    $stat = @stat($path);
    if (!is_array($stat) || !is_readable($path)) {
        return ['ok' => false, 'online' => false, 'stale' => true, 'error' => 'URF status source unavailable.', 'modes' => [], 'clients' => [], 'recent' => [], 'events' => []];
    }
    $raw = (string) @file_get_contents($path);
    $age = max(0, time() - (int) ($stat['mtime'] ?? 0));
    $clients = [];
    if (preg_match_all('#<NODE>(.*?)</NODE>#s', $raw, $matches)) foreach ($matches[1] as $node) {
        $protocol = strtoupper(asr_urf_first_element((string)$node, 'Protocol'));
        $mode = str_starts_with($protocol, 'DMR') ? 'DMR' : $protocol;
        $callsign = asr_urf_normalize_callsign(asr_urf_first_element((string)$node, 'Callsign'));
        $ip = trim(asr_urf_first_element((string)$node, 'IP'));
        // Loopback entries are ASR/URFD transport legs, not remotely connected clients.
        if ($ip === '127.0.0.1' || $ip === '::1') continue;
        if (asr_urf_is_service_identity($callsign, $mode)) continue;
        $clients[] = ['callsign' => $callsign, 'mode' => $mode, 'module' => asr_urf_first_element((string)$node, 'LinkedModule'), 'connected' => asr_urf_first_element((string)$node, 'ConnectTime'), 'lastHeard' => asr_urf_first_element((string)$node, 'LastHeardTime')];
    }
    $recent = [];
    if (preg_match_all('#<STATION>(.*?)</STATION>#s', $raw, $matches)) foreach ($matches[1] as $station) {
        $stationCallsign = trim(asr_urf_first_element((string)$station, 'Callsign'));
        if (asr_urf_is_service_identity($stationCallsign, '')) continue;
        $recent[] = ['callsign' => $stationCallsign, 'viaNode' => trim(asr_urf_first_element((string)$station, 'Via node')), 'module' => asr_urf_first_element((string)$station, 'On module'), 'viaPeer' => trim(asr_urf_first_element((string)$station, 'Via peer')), 'lastHeard' => asr_urf_first_element((string)$station, 'LastHeardTime')];
    }
    $events = [];
    if (is_readable($eventPath)) {
        $lines = @file($eventPath, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: [];
        foreach (array_reverse($lines) as $line) {
            $event = json_decode($line, true);
            if (!is_array($event) || (int)($event['epoch'] ?? 0) <= 0) continue;
            $eventMode = strtoupper(trim((string)($event['mode'] ?? '')));
            if (str_starts_with($eventMode, 'DMR')) $event['mode'] = 'DMR';
            $eventCallsign = (string) ($event['callsign'] ?? $event['client'] ?? '');
            if (asr_urf_is_service_identity($eventCallsign, (string) ($event['mode'] ?? $eventMode))) continue;
            $events[] = $event;
            if (count($events) >= 100) break;
        }
        // Probe traffic can be much noisier than human activity. Limit only
        // after filtering so probes cannot evict meaningful lifecycle history.
        $events = array_reverse($events);
    }
    $modes = [];
    foreach (['DMR','YSF','P25','NXDN','M17','USRP'] as $mode) $modes[$mode] = ['clients'=>0,'active'=>false];
    foreach ($clients as $client) { $mode=(string)$client['mode']; if (!isset($modes[$mode])) $modes[$mode]=['clients'=>0,'active'=>false]; $modes[$mode]['clients']++; }
    $currentTalker = null;
    $open = [];
    $eventCutoff = time() - 300;
    foreach ($events as $event) {
        if ((int)($event['epoch'] ?? 0) < $eventCutoff) continue;
        $mode = strtoupper((string)($event['mode'] ?? ''));
        $module = (string)($event['module'] ?? '');
        $key = $mode . '|' . $module;
        if (($event['event'] ?? '') === 'tx_start') $open[$key] = $event;
        elseif (($event['event'] ?? '') === 'tx_stop') unset($open[$key]);
    }
    if ($open) { usort($open, static fn($a,$b)=>(int)($b['epoch']??0)<=>(int)($a['epoch']??0)); $currentTalker=$open[0]; $mode=strtoupper((string)($currentTalker['mode']??'')); if(isset($modes[$mode])) $modes[$mode]['active']=true; }
    return ['ok'=>true,'online'=>$age<=30,'stale'=>$age>20,'ageSeconds'=>$age,'version'=>asr_urf_first_element($raw,'Version'),'clients'=>$clients,'recent'=>$recent,'events'=>array_reverse($events),'currentTalker'=>$currentTalker,'modes'=>$modes,'updatedEpoch'=>(int)($stat['mtime']??0),'eventUpdatedEpoch'=>$events ? max(array_map(static fn($e)=>(int)($e['epoch']??0),$events)) : 0];
}

function asr_bridge_status_payload(): array {
    $bridge = [];
    $path = asrRuntimeFilePath('bridge-live.json');
    if (is_readable($path)) {
        $decoded = json_decode((string) file_get_contents($path), true);
        if (is_array($decoded)) $bridge = $decoded;
    }
    foreach (asr_standard_bridge_live_statuses() as $id => $entry) {
        $bridge[$id] = array_merge(is_array($bridge[$id] ?? null) ? $bridge[$id] : [], $entry);
    }
    foreach (asr_dmr_net_live_statuses() as $id => $entry) $bridge[$id] = $entry;
    foreach (asr_ysf_net_live_statuses() as $id => $entry) $bridge[$id] = $entry;
    $nextModes = asr_next_mode_statuses();
    foreach ($nextModes['live'] as $id => $entry) $bridge[$id] = $entry;

    $configuredBridges = (array) (asr_runtime_config()['bridges'] ?? []);
    $urf = asr_urf_status_payload();
    $urfClients = [];
    $urfCounts = [];
    $sourceMode = strtoupper(trim((string) ($urf['currentTalker']['mode'] ?? '')));
    $sourceCallsign = asr_urf_normalize_callsign((string) ($urf['currentTalker']['callsign'] ?? ''));
    $urfActive = !empty($urf['online']) && $sourceMode !== '';
    foreach ($configuredBridges as $configuredBridge) {
        if (!is_array($configuredBridge) || empty($configuredBridge['urfReflector'])) continue;
        $id = (string) ($configuredBridge['id'] ?? '');
        $mode = strtoupper(asr_bridge_mode($configuredBridge));
        if ($id === '' || !in_array($mode, ['DMR','YSF','P25','NXDN','M17'], true)) continue;
        $modeClients = array_values(array_filter((array) ($urf['clients'] ?? []), static function($client) use ($mode): bool {
            if (!is_array($client) || strtoupper(trim((string) ($client['mode'] ?? ''))) !== $mode) return false;
            $clientCallsign = asr_urf_normalize_callsign((string) ($client['callsign'] ?? ''));
            if (asr_urf_is_service_identity($clientCallsign, $mode)) return false;
            return true;
        }));
        if ($mode === 'DMR') {
            // URFD sees the TGIF transport only as loopback. The sidecar publishes
            // current inbound DMR source identity separately for the URF DMR card.
            $dmrRosterPath = '/run/dmr-bridge/dmr-clients.json';
            $dmrRoster = [];
            if (is_readable($dmrRosterPath)) {
                $dmrRoster = json_decode((string) @file_get_contents($dmrRosterPath), true);
                if (!is_array($dmrRoster)) $dmrRoster = [];
                $dmrRows = is_array($dmrRoster['urf_dmr'] ?? null) ? $dmrRoster['urf_dmr'] : [];
                // DMR connection membership persists until explicit Kick/Ban/disconnect.
                // Silence is not a disconnect and must not age a client out of the roster.
                $modeClients = asr_dedupe_client_rows(array_merge($modeClients, asr_sanitize_client_rows($dmrRows, 'dmr', true)));
            }
        }
        if ($mode === 'DMR' && is_array($dmrRoster ?? null)) {
            $dmrEvents = (array) ($dmrRoster['events'] ?? []);
            foreach ($dmrEvents as $dmrEvent) {
                if (!is_array($dmrEvent)) continue;
                $eventName = strtolower(trim((string) ($dmrEvent['event'] ?? '')));
                $epoch = (int) ($dmrEvent['epoch'] ?? 0);
                $callsign = trim((string) ($dmrEvent['callsign'] ?? ''));
                if ($callsign !== '' && in_array($eventName, ['connect','disconnect','kick','ban'], true) && $epoch > 0) {
                    $urf['events'][] = ['mode' => 'DMR', 'callsign' => $callsign, 'event' => $eventName, 'epoch' => $epoch];
                }
            }
        }
        $lastTxByCallsign = [];
        foreach ((array) ($urf['events'] ?? []) as $urfEvent) {
            if (!is_array($urfEvent) || strtolower(trim((string) ($urfEvent['event'] ?? ''))) !== 'tx_stop') continue;
            if (strtoupper(trim((string) ($urfEvent['mode'] ?? ''))) !== $mode) continue;
            $txCallsign = asr_urf_normalize_callsign((string) ($urfEvent['callsign'] ?? $urfEvent['client'] ?? ''));
            $txEpoch = (int) ($urfEvent['epoch'] ?? 0);
            if ($txCallsign !== '' && $txEpoch > (int) ($lastTxByCallsign[strtoupper($txCallsign)] ?? 0)) $lastTxByCallsign[strtoupper($txCallsign)] = $txEpoch;
        }
        foreach ($modeClients as &$modeClient) {
            $clientCallsign = strtoupper(trim((string) ($modeClient['callsign'] ?? '')));
            if (isset($lastTxByCallsign[$clientCallsign])) $modeClient['last_tx_epoch'] = $lastTxByCallsign[$clientCallsign];
        }
        unset($modeClient);
        $urfClients[$id] = $modeClients;
        $urfCounts[$id] = count($modeClients);
        $isSource = $urfActive && $sourceMode === $mode;
        $modeEvents = array_values(array_filter((array) ($urf['events'] ?? []), static fn($event): bool =>
            is_array($event) && strtoupper(trim((string) ($event['mode'] ?? ''))) === $mode
        ));
        $openTx = [];
        $txEvents = [];
        $clientEvents = [];
        $lastSourceUser = '';
        $lastSourceEpoch = 0;
        foreach (array_reverse($modeEvents) as $event) {
            $eventName = strtolower(trim((string) ($event['event'] ?? '')));
            $module = strtoupper(trim((string) ($event['module'] ?? '')));
            $key = $mode . '|' . $module;
            $epoch = (int) ($event['epoch'] ?? 0);
            $callsign = trim((string) ($event['callsign'] ?? $event['client'] ?? ''));
            if ($eventName === 'tx_start') {
                $openTx[$key] = $event;
            } elseif ($eventName === 'tx_stop') {
                $start = is_array($openTx[$key] ?? null) ? $openTx[$key] : [];
                $startEpoch = (int) ($start['epoch'] ?? $epoch);
                $talker = asr_urf_normalize_callsign((string) ($event['callsign'] ?? $event['client'] ?? $start['callsign'] ?? $start['client'] ?? ''));
                if ($talker !== '' && $epoch > 0 && !asr_urf_is_service_identity($talker, $mode)) {
                    $txEvents[] = [
                        'callsign' => $talker,
                        'event' => 'transmit',
                        'epoch' => $epoch,
                        'start_epoch' => $startEpoch > 0 ? $startEpoch : $epoch,
                        'duration_seconds' => max(0, $epoch - ($startEpoch > 0 ? $startEpoch : $epoch)),
                    ];
                    if ($epoch >= $lastSourceEpoch) {
                        $lastSourceUser = $talker;
                        $lastSourceEpoch = $epoch;
                    }
                }
                unset($openTx[$key]);
            } elseif (in_array($eventName, ['connect','disconnect','kick','ban'], true) && $callsign !== '' && $epoch > 0) {
                $normalizedCallsign = asr_urf_normalize_callsign($callsign);
                if (!asr_urf_is_service_identity($normalizedCallsign, $mode)) {
                    $clientEvents[] = ['callsign' => trim($normalizedCallsign), 'event' => $eventName, 'epoch' => $epoch];
                }
            }
        }
        usort($txEvents, static fn($a, $b): int => (int) ($b['epoch'] ?? 0) <=> (int) ($a['epoch'] ?? 0));
        usort($clientEvents, static fn($a, $b): int => (int) ($b['epoch'] ?? 0) <=> (int) ($a['epoch'] ?? 0));
        $warning = empty($urf['ok']) ? (string) ($urf['error'] ?? 'URF status unavailable.') : (!empty($urf['stale']) ? 'URF status data is stale.' : '-');
        $healthSeverity = empty($urf['online']) ? 'offline' : (!empty($urf['stale']) ? 'warning' : null);
        $bridge[$id] = [
            'active' => $urfActive,
            'role' => $isSource ? 'source' : ($urfActive ? 'relay' : 'idle'),
            'state' => $isSource ? 'Source/TX' : ($urfActive ? 'Relay' : 'Idle'),
            'current_user' => $isSource ? $sourceCallsign : '',
            'caller' => $isSource ? $sourceCallsign : '',
            'last_caller' => $isSource ? $sourceCallsign : '',
            'lastCaller' => $isSource ? $sourceCallsign : '',
            'last_source_user' => $lastSourceUser,
            'last_source_epoch' => $lastSourceEpoch,
            'tx_events' => array_slice($txEvents, 0, 100),
            'client_events' => array_slice($clientEvents, 0, 100),
            'online' => !empty($urf['online']),
            'warning' => $warning,
            'health_severity' => $healthSeverity,
            'health_issues' => $warning !== '-' ? [$warning] : [],
            'updated' => !empty($urf['updatedEpoch']) ? date('Y-m-d H:i:s T', (int) $urf['updatedEpoch']) : '',
        ];
    }

    $bridge = asrPublicBridgeLiveStatuses($bridge, $configuredBridges);
    $controls = asrPublicBridgeControls(array_merge(
        asr_dmr_net_control_statuses(),
        asr_ysf_net_control_statuses(),
        $nextModes['controls'],
    ));
    $clientState = asr_bridge_clients_state();
    $clients = array_merge((array) ($clientState['clients'] ?? []), $urfClients);
    $counts = array_merge((array) ($clientState['counts'] ?? []), $urfCounts);

    // Zello publishes its own authoritative talker/activity feed.
    $zelloPath = asrRuntimeFilePath('zello-talkers.json');
    if (is_readable($zelloPath)) {
        $zelloPayload = json_decode((string) @file_get_contents($zelloPath), true);
        if (is_array($zelloPayload)) {
            $zelloRows = is_array($zelloPayload['recent_users'] ?? null) ? $zelloPayload['recent_users'] : [];
            foreach ($zelloRows as &$zelloRow) {
                if (!is_array($zelloRow)) continue;
                $rowEpoch = asr_client_epoch_value($zelloRow['last_tx_epoch'] ?? $zelloRow['last_seen_epoch'] ?? 0);
                if ($rowEpoch > 0) $zelloRow['last_tx_epoch'] = $rowEpoch;
            }
            unset($zelloRow);
            $clients['zello'] = $zelloRows;
            $counts['zello'] = count($zelloRows);
            $zelloEvents = [];
            foreach ((array) ($zelloPayload['tx_events'] ?? []) as $row) {
                if (!is_array($row)) continue;
                $name = substr(trim((string) ($row['name'] ?? $row['callsign'] ?? '')), 0, 120);
                $epoch = asr_client_epoch_value($row['epoch'] ?? 0);
                $duration = max(0.0, (float) ($row['duration_seconds'] ?? 0));
                if ($name !== '' && $epoch > 0) $zelloEvents[] = ['callsign'=>$name,'event'=>'transmit','epoch'=>$epoch,'start_epoch'=>$epoch - $duration,'duration_seconds'=>$duration];
            }
            $lastUser = substr(trim((string) ($zelloPayload['last_user'] ?? '')), 0, 120);
            $lastEpoch = $zelloEvents ? (int) ($zelloEvents[0]['epoch'] ?? 0) : 0;
            if ($lastEpoch <= 0 && isset($zelloRows[0]) && is_array($zelloRows[0])) $lastEpoch = asr_client_epoch_value($zelloRows[0]['last_seen_epoch'] ?? 0);
            $active = !empty($zelloPayload['active']);
            $current = $active ? substr(trim((string) ($zelloPayload['current_user'] ?? '')), 0, 120) : '';
            $bridge['zello'] = array_merge(is_array($bridge['zello'] ?? null) ? $bridge['zello'] : [], [
                'active'=>$active, 'role'=>$active ? 'source' : 'idle', 'state'=>$active ? 'TX ACTIVE' : 'Idle',
                'current_user'=>$current, 'caller'=>$current, 'last_user'=>$lastUser ?: '-',
                'last_source_user'=>$lastUser, 'last_source_epoch'=>$lastEpoch,
                'recent_users'=>$zelloRows, 'tx_events'=>$zelloEvents, 'client_events'=>[],
            ]);
        }
    }
    foreach ($configuredBridges as $configuredBridge) {
        if (!is_array($configuredBridge) || asr_bridge_mode($configuredBridge) !== 'dstar') continue;
        $id = (string) ($configuredBridge['id'] ?? '');
        if (!preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', $id)) continue;
        $entry = is_array($bridge[$id] ?? null) ? $bridge[$id] : [];
        $dstarClients = is_array($entry['linked_clients'] ?? null) ? $entry['linked_clients'] : [];
        $clients[$id] = $dstarClients;
        $counts[$id] = count($dstarClients);
        $clientState['meta'][$id] = ['kind' => 'current', 'mode' => 'dstar'];
    }
    return [
        'ok' => true,
        'bridge' => $bridge,
        'clients' => $clients,
        'clientCounts' => $counts,
        'clientMeta' => (array) ($clientState['meta'] ?? []),
        'controls' => $controls,
        'urf' => $urf,
    ];
}

function asr_valid_ysf_net_config(array $bridge): bool {
    $gateway = (string) ($bridge['ysfGatewayConfig'] ?? '');
    $mmdvm = (string) ($bridge['mmdvmConfig'] ?? '');
    if (!preg_match('#^/opt/YSFGateway_([A-Za-z0-9_-]+)/YSFGateway\.ini$#D', $gateway, $gatewayMatch)
        || !preg_match('#^/opt/MMDVM_Bridge_([A-Za-z0-9_-]+)/MMDVM_Bridge\.ini$#D', $mmdvm, $mmdvmMatch)
        || strcasecmp((string) $gatewayMatch[1], (string) $mmdvmMatch[1]) !== 0
        || (string) ($bridge['commandTransport'] ?? '') !== 'remote_command'
        || (isset($bridge['allowTune']) && !is_bool($bridge['allowTune']))
        || !preg_match('/^[0-9]{3,10}$/D', (string) ($bridge['node'] ?? ''))
        || !asr_bridge_permission_is_confirmed($bridge)) {
        return false;
    }
    foreach (['ysfGatewayService', 'mmdvmService'] as $key) {
        if (!preg_match('/^[a-z0-9][a-z0-9@_.-]{0,79}\.service$/D', (string) ($bridge[$key] ?? ''))) return false;
    }
    foreach (['analogBridgeService', 'emulatorService'] as $key) {
        if (isset($bridge[$key]) && (string) $bridge[$key] !== ''
            && !preg_match('/^[a-z0-9][a-z0-9@_.-]{0,79}\.service$/D', (string) $bridge[$key])) {
            return false;
        }
    }
    if (isset($bridge['ysfHostsPath']) && (string) $bridge['ysfHostsPath'] !== ''
        && !preg_match('#^/var/lib/mmdvm/[A-Za-z0-9_.-]*YSF[A-Za-z0-9_.-]*Hosts[A-Za-z0-9_.-]*$#D', (string) $bridge['ysfHostsPath'])) {
        return false;
    }
    $customReflectors = $bridge['ysfCustomReflectors'] ?? [];
    if (!is_array($customReflectors) || count($customReflectors) > 32) return false;
    if (count($customReflectors) > 0
        && !preg_match('#^/var/lib/mmdvm/[A-Za-z0-9_.-]*YSF[A-Za-z0-9_.-]*Hosts[A-Za-z0-9_.-]*$#D', (string) ($bridge['ysfHostsPath'] ?? ''))) {
        return false;
    }
    $seenIds = [];
    $seenNames = [];
    foreach ($customReflectors as $reflector) {
        if (!is_array($reflector)) return false;
        $id = (string) ($reflector['id'] ?? '');
        $name = (string) ($reflector['name'] ?? '');
        $host = (string) ($reflector['host'] ?? '');
        $port = filter_var($reflector['port'] ?? null, FILTER_VALIDATE_INT);
        $description = (string) ($reflector['description'] ?? '');
        $validIp = filter_var($host, FILTER_VALIDATE_IP) !== false;
        $validHost = preg_match('/(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/D', $host) === 1;
        $nameKey = strtolower($name);
        if (!preg_match('/^[0-9]{5}$/D', $id) || $id === '00000'
            || !preg_match('/^[A-Z0-9][A-Z0-9 _.-]{0,15}$/D', $name)
            || preg_match('/^[0-9]{5}$/D', $name)
            || (!$validIp && !$validHost)
            || preg_match('/[;\x00-\x20\/\\\\\[\]@]/', $host)
            || $port === false || $port < 1 || $port > 65535
            || $description === '' || strlen($description) > 120
            || preg_match('/[;\x00-\x1F\x7F]/', $description)
            || isset($seenIds[$id]) || isset($seenNames[$nameKey])) {
            return false;
        }
        $seenIds[$id] = true;
        $seenNames[$nameKey] = true;
    }
    return true;
}

function asr_ysf_net_bridge_config(string $bridgeId): ?array {
    if (!preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', $bridgeId)) return null;
    $config = is_readable(ASR_RUNTIME_CONFIG)
        ? json_decode((string) file_get_contents(ASR_RUNTIME_CONFIG), true)
        : null;
    $bridges = (array) ($config['bridges'] ?? []);
    foreach ($bridges as $index => $bridge) {
        if (!is_array($bridge)
            || (string) ($bridge['id'] ?? '') !== $bridgeId
            || (string) ($bridge['cardType'] ?? '') !== 'ysf_net'
            || !asr_valid_ysf_net_config($bridge)) {
            continue;
        }
        foreach ($bridges as $otherIndex => $other) {
            if ($otherIndex === $index || !is_array($other)) continue;
            if ((string) ($other['node'] ?? '') === (string) ($bridge['node'] ?? '')
                || ((string) ($other['ysfGatewayConfig'] ?? '') !== ''
                    && (string) $other['ysfGatewayConfig'] === (string) $bridge['ysfGatewayConfig'])
                || ((string) ($other['mmdvmConfig'] ?? '') !== ''
                    && (string) $other['mmdvmConfig'] === (string) $bridge['mmdvmConfig'])) {
                return null;
            }
        }
        return $bridge;
    }
    return null;
}

function asr_secure_root_json(string $path): ?array {
    $fileStatus = @stat($path);
    if (is_link($path)
        || !is_array($fileStatus)
        || (((int) ($fileStatus['mode'] ?? 0)) & 0170000) !== 0100000
        || (int) ($fileStatus['nlink'] ?? 0) !== 1
        || (int) ($fileStatus['uid'] ?? -1) !== 0
        || (((int) ($fileStatus['mode'] ?? 0)) & 0022) !== 0
        || (int) ($fileStatus['size'] ?? -1) < 0
        || (int) ($fileStatus['size'] ?? 0) > 2 * 1024 * 1024) {
        return null;
    }
    $decoded = json_decode((string) @file_get_contents($path), true);
    return is_array($decoded) ? $decoded : null;
}

function asr_standard_bridge_live_statuses(): array {
    $payload = asr_secure_root_json('/run/allscan-reimagined-standard-bridge-status/bridge-live.json');
    if (!is_array($payload) || !is_array($payload['bridges'] ?? null)) return [];
    $updatedEpoch = (int) ($payload['updated_epoch'] ?? 0);
    if ($updatedEpoch <= 0 || $updatedEpoch > time() + 300 || time() - $updatedEpoch > 10) return [];

    $allowed = [];
    foreach ((array) (asr_raw_runtime_config()['bridges'] ?? []) as $bridge) {
        if (!is_array($bridge) || (string) ($bridge['cardType'] ?? 'standard') !== 'standard') continue;
        $id = (string) ($bridge['id'] ?? '');
        $mode = asr_bridge_mode($bridge);
        $node = (string) ($bridge['node'] ?? '');
        if (in_array($mode, ['dmr', 'ysf', 'dstar'], true)
            && preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', $id)
            && preg_match('/^[0-9]{3,10}$/D', $node)) {
            $allowed[$id] = ['node' => $node, 'mode' => $mode];
        }
    }

    $clean = [];
    foreach ($payload['bridges'] as $id => $entry) {
        $id = (string) $id;
        if (!isset($allowed[$id]) || !is_array($entry)
            || !hash_equals((string) $allowed[$id]['node'], (string) ($entry['node'] ?? ''))) continue;
        $mode = (string) $allowed[$id]['mode'];
        $online = $mode === 'dstar' ? !empty($entry['online']) : null;
        $role = strtolower((string) ($entry['role'] ?? 'idle'));
        if (!in_array($role, ['idle', 'source', 'relay'], true) || ($mode === 'dstar' && !$online)) $role = 'idle';
        $caller = $role === 'source' ? substr(trim((string) ($entry['current_user'] ?? '')), 0, 120) : '';
        $linked = is_bool($entry['linked'] ?? null) ? (bool) $entry['linked'] : null;
        $recent = [];
        foreach ((array) ($entry['recent_users'] ?? []) as $row) {
            if (!is_array($row)) continue;
            $callsign = strtoupper(substr(trim((string) ($row['callsign'] ?? '')), 0, 8));
            $epoch = max(0, (int) ($row['last_tx_epoch'] ?? 0));
            if (!preg_match('/^[A-Z0-9]{3,8}$/D', $callsign)
                || $epoch <= 0 || $epoch > time() + 300 || time() - $epoch > 86400) continue;
            $recent[] = [
                'callsign' => $callsign,
                'last_tx_epoch' => $epoch,
                'reflector' => substr(strtoupper(trim((string) ($row['reflector'] ?? ''))), 0, 8),
                'module' => substr(strtoupper(trim((string) ($row['module'] ?? ''))), 0, 1),
            ];
            if (count($recent) >= 50) break;
        }
        $txEvents = [];
        foreach ((array) ($entry['tx_events'] ?? []) as $row) {
            if (!is_array($row)) continue;
            $callsign = strtoupper(substr(trim((string) ($row['callsign'] ?? '')), 0, 20));
            $epoch = max(0, (int) ($row['epoch'] ?? 0));
            if (!preg_match('/^[A-Z0-9][A-Z0-9 -]{2,19}$/D', $callsign) || $epoch <= 0 || time() - $epoch > 86400) continue;
            $txEvents[] = ['callsign'=>$callsign, 'event'=>'transmit', 'epoch'=>$epoch, 'start_epoch'=>max(0,(int)($row['start_epoch']??0)), 'duration_seconds'=>max(0,(float)($row['duration_seconds']??0))];
            if (count($txEvents) >= 100) break;
        }
        $clientEvents = [];
        foreach ((array) ($entry['client_events'] ?? []) as $row) {
            if (!is_array($row)) continue;
            $callsign = strtoupper(substr(trim((string) ($row['callsign'] ?? '')), 0, 20));
            $event = strtolower(trim((string) ($row['event'] ?? '')));
            $epoch = max(0, (int) ($row['epoch'] ?? 0));
            if (!preg_match('/^[A-Z0-9][A-Z0-9 -]{2,19}$/D', $callsign) || !in_array($event,['connect','disconnect'],true) || $epoch <= 0 || time() - $epoch > 86400) continue;
            $clientEvents[] = ['callsign'=>$callsign, 'event'=>$event, 'epoch'=>$epoch];
            if (count($clientEvents) >= 100) break;
        }
        $linkedClients = [];
        foreach ((array) ($entry['linked_clients'] ?? []) as $row) {
            if (!is_array($row)) continue;
            $callsign = strtoupper(substr(trim((string) ($row['callsign'] ?? '')), 0, 20));
            if (!preg_match('/^[A-Z0-9][A-Z0-9 -]{2,19}$/D', $callsign)) continue;
            $linkedClients[] = [
                'callsign' => $callsign,
                'module' => substr(strtoupper(trim((string) ($row['module'] ?? ''))), 0, 1),
                'protocol' => substr(trim((string) ($row['protocol'] ?? '')), 0, 20),
                'connected' => true,
            ];
        }
        $clean[$id] = [
            'active' => $role !== 'idle',
            'role' => $role,
            'state' => $role === 'source' ? 'TX ACTIVE' : ($role === 'relay' ? 'RELAY' : 'Idle'),
            'active_start_epoch' => $role === 'idle' ? 0 : max(0, (int) ($entry['active_start_epoch'] ?? 0)),
            'activity_epoch' => max(0, (int) ($entry['activity_epoch'] ?? 0)),
            'last_time_epoch' => max(0, (int) ($entry['last_time_epoch'] ?? 0)),
            'current_user' => $caller,
            'caller' => $caller,
            'last_user' => substr(trim((string) ($entry['last_user'] ?? '-')), 0, 120),
            'last_source_user' => substr(trim((string) ($entry['last_source_user'] ?? '')), 0, 120),
            'last_source_epoch' => max(0, (int) ($entry['last_source_epoch'] ?? 0)),
            'warning' => substr(trim((string) ($entry['warning'] ?? '')), 0, 160),
            'health_severity' => in_array((string) ($entry['health_severity'] ?? ''), ['warning', 'unhealthy', 'offline'], true) ? (string) $entry['health_severity'] : null,
            'health_issues' => array_values(array_slice(array_filter(array_map(static fn($issue) => substr(trim((string) $issue), 0, 160), is_array($entry['health_issues'] ?? null) ? $entry['health_issues'] : [])), 0, 8)),
            'online' => $online,
            'linked' => $linked,
            'reflector' => substr(strtoupper(trim((string) ($entry['reflector'] ?? ''))), 0, 8),
            'module' => substr(strtoupper(trim((string) ($entry['module'] ?? ''))), 0, 1),
            'link_protocol' => substr(trim((string) ($entry['link_protocol'] ?? '')), 0, 20),
            'recent_users' => $recent,
            'tx_events' => $txEvents,
            'client_events' => $clientEvents,
            'linked_clients' => $linkedClients,
        ];
    }
    return $clean;
}

function asr_ysf_net_live_statuses(): array {
    $path = '/run/allscan-reimagined-ysf-bridge-control/ysf-live.json';
    $payload = asr_secure_root_json($path);
    if (!is_array($payload) || !is_array($payload['bridges'] ?? null)) {
        return [];
    }
    $updatedEpoch = (int) ($payload['updated_epoch'] ?? 0);
    $liveFresh = $updatedEpoch > 0
        && $updatedEpoch <= time() + 300
        && time() - $updatedEpoch <= 10;
    $clean = [];
    foreach ($payload['bridges'] as $id => $entry) {
        $id = (string) $id;
        if (!is_array($entry) || asr_ysf_net_bridge_config($id) === null) continue;
        $role = $liveFresh ? strtolower((string) ($entry['role'] ?? 'idle')) : 'idle';
        if (!in_array($role, ['idle', 'source', 'relay'], true)) $role = 'idle';
        $linked = $liveFresh && !empty($entry['linked']);
        if (!$linked) $role = 'idle';
        $caller = $role === 'source' ? substr((string) ($entry['current_user'] ?? ''), 0, 20) : '';
        $clean[$id] = [
            'active' => $role !== 'idle',
            'role' => $role,
            'state' => $role === 'source' ? 'TX ACTIVE' : ($role === 'relay' ? 'RELAY' : ($linked ? 'Idle' : 'Disconnected')),
            'node' => substr((string) ($entry['node'] ?? ''), 0, 10),
            'title' => 'YSF Net Bridge',
            'channel' => $linked ? substr((string) ($entry['name'] ?? ''), 0, 80) : '-',
            'destination' => preg_match('/^[0-9]{5}$/D', (string) ($entry['destination'] ?? '')) ? (string) $entry['destination'] : '',
            'destinationName' => substr((string) ($entry['name'] ?? ''), 0, 80),
            'linked' => $linked,
            'digitalLinked' => $linked,
            'allstarLinked' => $liveFresh && !empty($entry['allstarLinked']),
            'ready' => $liveFresh && !empty($entry['ready']),
            'active_start_epoch' => max(0, (int) ($entry['active_start_epoch'] ?? 0)),
            'activity_epoch' => max(0, (int) ($entry['activity_epoch'] ?? 0)),
            'last_time_epoch' => max(0, (int) ($entry['activity_epoch'] ?? 0)),
            'warning' => substr((string) ($entry['warning'] ?? ''), 0, 160),
            'current_user' => $caller,
            'last_user' => substr((string) ($entry['last_user'] ?? '-'), 0, 20),
            'caller' => $caller,
            'last_source_user' => substr((string) ($entry['last_source_user'] ?? ''), 0, 20),
            'last_source_epoch' => max(0, (int) ($entry['last_source_epoch'] ?? 0)),
            'recent_users' => [],
        ];
    }
    return $clean;
}

function asr_ysf_net_control_statuses(): array {
    $live = asr_ysf_net_live_statuses();
    $statuses = [];
    $config = is_readable(ASR_RUNTIME_CONFIG)
        ? json_decode((string) file_get_contents(ASR_RUNTIME_CONFIG), true)
        : null;
    foreach ((array) ($config['bridges'] ?? []) as $bridge) {
        if (!is_array($bridge) || ($bridge['cardType'] ?? '') !== 'ysf_net') continue;
        $id = (string) ($bridge['id'] ?? '');
        if (!preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', $id)) continue;
        $configValid = asr_valid_ysf_net_config($bridge);
        $entry = is_array($live[$id] ?? null) ? $live[$id] : [];
        $reasons = [];
        if (!$configValid) $reasons[] = 'Configured YSF paths, services, or permission are invalid.';
        if (empty($bridge['allowTune'])) $reasons[] = 'Dashboard controls are disabled in Settings.';
        if (!is_executable(ASR_YSF_BRIDGE_CONTROL_HELPER)) $reasons[] = 'The YSF control helper is not installed.';
        if (empty($entry['ready'])) $reasons[] = trim((string) ($entry['warning'] ?? 'The configured YSF services, Remote Commands, catalog, or isolated resources are not ready.'));
        $statuses[$id] = [
            'ready' => count($reasons) === 0,
            'reason' => count($reasons) === 0 ? 'YSF backend ready.' : 'YSF backend not ready: ' . implode(' ', array_filter($reasons)),
            'missing' => array_values(array_filter($reasons)),
            'linked' => !empty($entry['linked']) && !empty($entry['allstarLinked']),
            'digitalLinked' => !empty($entry['linked']),
            'allstarLinked' => !empty($entry['allstarLinked']),
            'currentDestination' => (string) ($entry['destination'] ?? ''),
            'currentDestinationLabel' => (string) ($entry['destinationName'] ?? ''),
        ];
    }
    return $statuses;
}

function asr_ysf_net_destination_rows(string $bridgeId): array {
    if (asr_ysf_net_bridge_config($bridgeId) === null) asr_error('Configured YSF Net Bridge was not found.', 404);
    $path = '/run/allscan-reimagined-ysf-bridge-control/destinations-' . $bridgeId . '.json';
    $payload = asr_secure_root_json($path);
    if (!is_array($payload)
        || (string) ($payload['bridgeId'] ?? '') !== $bridgeId
        || !is_array($payload['destinations'] ?? null)) {
        $catalogCommand = 'sudo -n ' . escapeshellarg(ASR_YSF_BRIDGE_CONTROL_HELPER)
            . ' --catalog-status ' . escapeshellarg($bridgeId) . ' 2>/dev/null';
        $catalogLines = asr_command_lines($catalogCommand, 65536);
        $catalog = json_decode(implode("\n", $catalogLines), true);
        if (is_array($catalog) && ($catalog['state'] ?? '') === 'no_valid_list') {
            asr_error('No valid YSF reflector list is installed. Import YSFHosts.txt in Reimagined Settings.', 503);
        }
        asr_error('YSF reflector cache is still initializing. Try again shortly.', 503);
    }
    $destinations = [];
    foreach ($payload['destinations'] as $item) {
        if (!is_array($item)
            || !preg_match('/^[0-9]{5}$/D', (string) ($item['id'] ?? ''))
            || trim((string) ($item['name'] ?? '')) === '') {
            continue;
        }
        $destinations[] = [
            'id' => (string) $item['id'],
            'name' => substr(trim((string) $item['name']), 0, 80),
        ];
    }
    return $destinations;
}

function asr_ysf_net_destinations(string $bridgeId): array {
    $destinations = [];
    foreach (asr_ysf_net_destination_rows($bridgeId) as $item) {
        $destinations[] = [
            'id' => $item['id'],
            'name' => $item['name'],
            'value' => $item['name'],
            'label' => $item['name'] . ' (' . $item['id'] . ')',
        ];
    }
    return ['ok' => true, 'bridgeId' => $bridgeId, 'destinations' => $destinations];
}

function asr_bridge_destinations(string $bridgeId): array {
    $bridge = asr_bridge_config_by_id($bridgeId);
    if (!is_array($bridge) || (string) ($bridge['cardType'] ?? 'standard') === 'standard') {
        asr_error('Configured Net Bridge was not found.', 404);
    }
    if (!asr_bridge_permission_is_confirmed($bridge)) asr_error('Bridge permission is not confirmed.', 403);
    $mode = asr_bridge_mode($bridge);
    if ($mode === 'ysf') return asr_ysf_net_destinations($bridgeId);
    $destinations = [];
    foreach (asr_bridge_approved_values($bridge) as $value) {
        if ($mode === 'dmr') $label = 'TG ' . $value;
        elseif ($mode === 'm17') $label = $value;
        else $label = strtoupper($mode) . ' ' . $value;
        $destinations[] = ['id' => $value, 'name' => $label, 'value' => $value, 'label' => $label];
    }
    return ['ok' => true, 'bridgeId' => $bridgeId, 'destinations' => $destinations];
}

function asr_ysf_net_resolve_destination(string $bridgeId, string $query): array {
    $query = trim($query);
    if ($query === '' || strlen($query) > 80 || preg_match('/[\x00-\x1F\x7F]/', $query)) {
        asr_error('Enter an exact YSF reflector name or five-digit ID.');
    }
    $matches = [];
    $isId = preg_match('/^[0-9]{5}$/D', $query) === 1;
    foreach (asr_ysf_net_destination_rows($bridgeId) as $item) {
        if (($isId && hash_equals($item['id'], $query))
            || (!$isId && strcasecmp($item['name'], $query) === 0)) {
            $matches[$item['id']] = $item;
        }
    }
    if(count($matches) === 0) asr_error('YSF reflector name or ID was not found. Import a current YSFHosts.txt list or add an unlisted reflector in Reimagined Settings.');
    if(count($matches) > 1) asr_error('More than one reflector uses that name. Enter its five-digit ID.');
    return array_values($matches)[0];
}

function asr_ysf_helper(array $arguments, string $fallback): array {
    global $user;
    if (!is_executable(ASR_YSF_BRIDGE_CONTROL_HELPER)) asr_error('YSF Net Bridge control helper is not installed.', 503);
    $username = substr(preg_replace('/[^A-Za-z0-9_.@+-]/', '_', (string) ($user->name ?? 'unknown')), 0, 80);
    $command = 'sudo -n ' . escapeshellarg(ASR_YSF_BRIDGE_CONTROL_HELPER);
    foreach ($arguments as $argument) $command .= ' ' . escapeshellarg((string) $argument);
    $command .= ' --user ' . escapeshellarg($username) . ' 2>&1';
    $lines = [];
    $status = 1;
    exec($command, $lines, $status);
    $payload = null;
    foreach (array_reverse($lines) as $line) {
        $decoded = json_decode($line, true);
        if (is_array($decoded)) {
            $payload = $decoded;
            break;
        }
    }
    if (!is_array($payload)) asr_error('YSF Net Bridge control returned an invalid response.', 500);
    if ($status !== 0 || empty($payload['ok'])) asr_error((string) ($payload['error'] ?? $fallback), 500);
    return $payload;
}

function asr_ysf_net_connect(string $bridgeId, string $destination): array {
    $bridge = asr_ysf_net_bridge_config($bridgeId);
    if ($bridge === null) asr_error('Configured YSF Net Bridge was not found.', 404);
    if (empty($bridge['allowTune'])) asr_error('YSF Net Bridge tuning is disabled.', 403);
    $resolved = asr_ysf_net_resolve_destination($bridgeId, $destination);
    $payload = asr_ysf_helper(['--connect', $bridgeId, $resolved['id']], 'YSF Net Bridge connection failed.');
    $payload['currentDestination'] = $resolved['id'];
    $payload['currentDestinationLabel'] = $resolved['name'];
    return $payload;
}

function asr_ysf_net_disconnect(string $bridgeId): array {
    $bridge = asr_ysf_net_bridge_config($bridgeId);
    if ($bridge === null) asr_error('Configured YSF Net Bridge was not found.', 404);
    if (empty($bridge['allowTune'])) asr_error('YSF Net Bridge tuning is disabled.', 403);
    return asr_ysf_helper(['--disconnect', $bridgeId], 'YSF Net Bridge disconnect failed.');
}

function asr_valid_dmr_net_paths(array $bridge): bool {
    return preg_match('#^/tmp/ABInfo_[0-9]{2,5}\.json$#D', (string) ($bridge['abinfoPath'] ?? '')) === 1
        && preg_match('#^/opt/MMDVM_Bridge[A-Za-z0-9_-]+/dvswitch\.sh$#D', (string) ($bridge['dvswitchScript'] ?? '')) === 1
        && preg_match('#^/opt/Analog_Bridge[A-Za-z0-9_-]+/Analog_Bridge\.ini$#D', (string) ($bridge['analogConfig'] ?? '')) === 1;
}

function asr_dmr_net_current_tg(string $path, string $bridgeId = '', string $abinfoPath = ''): string {
    if (preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', $bridgeId)) {
        $statePath = '/run/allscan-reimagined-bridge-control/bridge-control-' . $bridgeId . '.json';
        if (is_readable($statePath)) {
            $state = json_decode((string) @file_get_contents($statePath), true);
            $stateTg = (int) ($state['currentTg'] ?? 0);
            if ($stateTg >= 1 && $stateTg <= 16777215) return (string) $stateTg;
        }
    }
    if ($abinfoPath !== '' && is_readable($abinfoPath)) {
        $abinfo = json_decode((string) @file_get_contents($abinfoPath), true);
        if (is_array($abinfo)) {
            $values = [
                $abinfo['last_tune'] ?? null,
                is_array($abinfo['digital'] ?? null) ? ($abinfo['digital']['tg'] ?? null) : null,
            ];
            foreach ($values as $value) {
                $liveTg = (int) $value;
                if ($liveTg === 4000) return '';
                if ($liveTg >= 1 && $liveTg <= 16777215) return (string) $liveTg;
            }
        }
    }
    if (!is_readable($path)) return '';
    $contents = (string) @file_get_contents($path);
    if (!preg_match('/^\s*txTg\s*=\s*(\d+)\b/mi', $contents, $match)) return '';
    $tg = (int) $match[1];
    return $tg >= 1 && $tg <= 16777215 ? (string) $tg : '';
}

function asr_dmr_net_control_statuses(): array {
    $config = is_readable(ASR_RUNTIME_CONFIG)
        ? json_decode((string) file_get_contents(ASR_RUNTIME_CONFIG), true)
        : null;
    $localNode = preg_match('/^[0-9]{3,6}$/D', (string) ($config['node'] ?? ''))
        ? (string) $config['node']
        : '';
    $expectedLinkAlias = $localNode !== ''
        ? '999' . str_pad($localNode, 6, '0', STR_PAD_LEFT)
        : '';
    $statuses = [];
    foreach ((array) ($config['bridges'] ?? []) as $bridge) {
        if (!is_array($bridge) || ($bridge['cardType'] ?? '') !== 'dmr_net') continue;
        $id = (string) ($bridge['id'] ?? '');
        if (!preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', $id)) continue;
        $pathsValid = asr_valid_dmr_net_paths($bridge);
        $script = (string) $bridge['dvswitchScript'];
        $analogConfig = (string) $bridge['analogConfig'];
        $bridgeNode = (string) ($bridge['node'] ?? '');
        $linkAlias = (string) ($bridge['linkAlias'] ?? '');
        $linkAliasValid = $expectedLinkAlias !== ''
            && preg_match('/^999[0-9]{6}$/D', $linkAlias)
            && hash_equals($expectedLinkAlias, $linkAlias)
            && $linkAlias !== $bridgeNode;
        $statePath = '/run/allscan-reimagined-bridge-control/bridge-control-' . $id . '.json';
        $state = is_readable($statePath)
            ? json_decode((string) @file_get_contents($statePath), true)
            : null;
        $stateTg = is_array($state) ? (int) ($state['currentTg'] ?? 0) : 0;
        $reasons = [];
        if (!$pathsValid) $reasons[] = 'One or more configured DMR backend paths are invalid.';
        if (!asr_bridge_permission_is_confirmed($bridge)) $reasons[] = 'Bridge permission is not confirmed.';
        if (!$linkAliasValid) $reasons[] = 'The generated internal link alias is missing or invalid.';
        if (!is_executable(ASR_BRIDGE_CONTROL_HELPER)) $reasons[] = 'The DMR control helper is not installed.';
        if (!is_file($script) || !is_executable($script)) $reasons[] = 'The configured DVSwitch script is missing or not executable.';
        if (!is_file($analogConfig)) $reasons[] = 'The configured Analog Bridge file is missing.';
        $statuses[$id] = [
            'currentTg' => $pathsValid ? asr_dmr_net_current_tg($analogConfig, $id, (string) $bridge['abinfoPath']) : '',
            'linked' => $stateTg >= 1 && $stateTg <= 16777215,
            'ready' => count($reasons) === 0,
            'reason' => count($reasons) === 0 ? 'DMR backend ready.' : 'DMR backend not ready: ' . implode(' ', $reasons),
            'missing' => $reasons,
            'abinfoAvailable' => is_file((string) $bridge['abinfoPath']),
        ];
    }
    return $statuses;
}

function asr_dmr_net_connect(string $bridgeId, string $talkgroup): array {
    global $user;
    if (!preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', $bridgeId)) asr_error('Invalid bridge ID.');
    if (!preg_match('/^\d{1,8}$/D', $talkgroup) || (int) $talkgroup < 1 || (int) $talkgroup > 16777215) {
        asr_error('Enter a valid DMR talkgroup.');
    }
    if ((int) $talkgroup === 4000) asr_error('Use Disconnect instead of entering TG 4000.');
    $bridge = asr_bridge_config_by_id($bridgeId);
    if (!is_array($bridge) || (string) ($bridge['cardType'] ?? '') !== 'dmr_net') asr_error('Configured DMR Net Bridge was not found.', 404);
    if (!asr_bridge_permission_is_confirmed($bridge)) asr_error('DMR Net Bridge permission is not confirmed.', 403);
    if (!is_executable(ASR_BRIDGE_CONTROL_HELPER)) asr_error('DMR Net Bridge control helper is not installed.', 503);

    $username = substr(preg_replace('/[^A-Za-z0-9_.@+-]/', '_', (string) ($user->name ?? 'unknown')), 0, 80);
    $command = 'sudo -n ' . escapeshellarg(ASR_BRIDGE_CONTROL_HELPER)
        . ' --connect ' . escapeshellarg($bridgeId)
        . ' ' . escapeshellarg($talkgroup)
        . ' --user ' . escapeshellarg($username)
        . ' 2>&1';
    $lines = [];
    $status = 1;
    exec($command, $lines, $status);
    $payload = null;
    foreach (array_reverse($lines) as $line) {
        $decoded = json_decode($line, true);
        if (is_array($decoded)) {
            $payload = $decoded;
            break;
        }
    }
    if (!is_array($payload)) asr_error('DMR Net Bridge control returned an invalid response.', 500);
    if ($status !== 0 || empty($payload['ok'])) asr_error((string) ($payload['error'] ?? 'DMR Net Bridge connection failed.'), 500);
    return $payload;
}

function asr_dmr_net_disconnect(string $bridgeId): array {
    global $user;
    if (!preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', $bridgeId)) asr_error('Invalid bridge ID.');
    if (!is_executable(ASR_BRIDGE_CONTROL_HELPER)) asr_error('DMR Net Bridge control helper is not installed.', 503);

    $username = substr(preg_replace('/[^A-Za-z0-9_.@+-]/', '_', (string) ($user->name ?? 'unknown')), 0, 80);
    $command = 'sudo -n ' . escapeshellarg(ASR_BRIDGE_CONTROL_HELPER)
        . ' --disconnect ' . escapeshellarg($bridgeId)
        . ' --user ' . escapeshellarg($username)
        . ' 2>&1';
    $lines = [];
    $status = 1;
    exec($command, $lines, $status);
    $payload = null;
    foreach (array_reverse($lines) as $line) {
        $decoded = json_decode($line, true);
        if (is_array($decoded)) {
            $payload = $decoded;
            break;
        }
    }
    if (!is_array($payload)) asr_error('DMR Net Bridge disconnect returned an invalid response.', 500);
    if ($status !== 0 || empty($payload['ok'])) asr_error((string) ($payload['error'] ?? 'DMR Net Bridge disconnect failed.'), 500);
    return $payload;
}

function asr_cpu_temperature_reading(): ?array {
    $candidates = [];
    foreach ((array) glob('/sys/class/hwmon/hwmon*') as $hwmon) {
        $name = strtolower(trim((string) @file_get_contents($hwmon . '/name')));
        foreach ((array) glob($hwmon . '/temp*_input') as $input) {
            $base = substr($input, 0, -6);
            $label = strtolower(trim((string) @file_get_contents($base . '_label')));
            $priority = null;
            if ($name === 'coretemp' && str_starts_with($label, 'package id')) $priority = 100;
            elseif ($name === 'k10temp' && in_array($label, ['tctl', 'tdie'], true)) $priority = $label === 'tdie' ? 100 : 95;
            elseif ($name === 'zenpower' && in_array($label, ['tdie', 'tctl'], true)) $priority = $label === 'tdie' ? 100 : 95;
            if ($priority === null) continue;
            $raw = (float) trim((string) @file_get_contents($input));
            if ($raw > 0) $candidates[] = [$priority, $raw / 1000.0, 'x86'];
        }
    }
    foreach ((array) glob('/sys/class/thermal/thermal_zone*') as $zone) {
        $type = strtolower(trim((string) @file_get_contents($zone . '/type')));
        $priority = match ($type) {
            'x86_pkg_temp' => 90,
            'tcpu' => 85,
            'cpu-thermal', 'cpu_thermal' => 80,
            default => null,
        };
        if ($priority === null) continue;
        $raw = (float) trim((string) @file_get_contents($zone . '/temp'));
        if ($raw > 0) $candidates[] = [$priority, $raw / 1000.0, in_array($type, ['x86_pkg_temp', 'tcpu'], true) ? 'x86' : 'embedded'];
    }
    if (!$candidates) return null;
    usort($candidates, static fn($a, $b) => $b[0] <=> $a[0]);
    return ['celsius' => (float) $candidates[0][1], 'class' => (string) $candidates[0][2]];
}

function asr_cpu_temp_thresholds(string $class): array {
    // x86 laptop/desktop CPUs routinely operate well above SBC/node-device temperatures.
    // Keep legacy AllScan limits for embedded/fallback hardware; warn x86 at 80C and alarm at 90C.
    return $class === 'x86'
        ? ['warnC' => 80.0, 'alarmC' => 90.0]
        : ['warnC' => (130 - 32) / 1.8, 'alarmC' => (150 - 32) / 1.8];
}

function asr_cpu_temp_payload(): array {
    $cache = '/run/allscan-reimagined/cpu-temp.json';
    if (is_readable($cache) && (int) @filemtime($cache) >= time() - 15) {
        $decoded = json_decode((string) file_get_contents($cache), true);
        if (is_array($decoded)) return $decoded;
    }
    $reading = asr_cpu_temperature_reading();
    if ($reading !== null) {
        $celsius = (float) $reading['celsius'];
        $thresholds = asr_cpu_temp_thresholds((string) $reading['class']);
        $ct = (int) round($celsius);
        $ft = (int) round($celsius * 1.8 + 32);
        $background = $celsius < $thresholds['warnC'] ? 'darkgreen' : ($celsius < $thresholds['alarmC'] ? '#660' : 'red');
        $payload = [
            'ok' => true,
            'value' => $ft . '°F / ' . $ct . '°C',
            'bgColor' => $background,
            'updated' => gmdate('c'),
        ];
    } else {
        // Preserve compatibility with hardware supported by upstream AllScan's helper (for example older Raspberry Pi installs).
        $raw = (string) cpuTemp();
        $text = trim(html_entity_decode(strip_tags($raw), ENT_QUOTES | ENT_HTML5));
        preg_match('/background-color\s*:\s*([^;"\']+)/i', $raw, $backgroundMatch);
        preg_match('/CPU Temp:\s*(.+?)\s*@/i', $text, $temperature);
        $payload = [
            'ok' => true,
            'value' => trim((string) ($temperature[1] ?? preg_replace('/^CPU Temp:\s*/i', '', $text))),
            'bgColor' => trim((string) ($backgroundMatch[1] ?? '#59461c')),
            'updated' => gmdate('c'),
        ];
    }
    if (is_dir(dirname($cache))) @file_put_contents($cache, json_encode($payload), LOCK_EX);
    return $payload;
}

function asr_raw_runtime_config(): array {
    $stored = is_readable(ASR_RUNTIME_CONFIG)
        ? json_decode((string) file_get_contents(ASR_RUNTIME_CONFIG), true)
        : null;
    return is_array($stored) ? $stored : [];
}

function asr_bridge_config_by_id(string $bridgeId): ?array {
    if (!preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', $bridgeId)) return null;
    foreach ((array) (asr_raw_runtime_config()['bridges'] ?? []) as $bridge) {
        if (is_array($bridge) && (string) ($bridge['id'] ?? '') === $bridgeId) return $bridge;
    }
    return null;
}

function asr_bridge_permission_is_confirmed(array $bridge): bool {
    return in_array((string) ($bridge['bridgePermission'] ?? ''), ['self_owned', 'approved'], true);
}

function asr_bridge_approved_values(array $bridge): array {
    $values = [];
    foreach ((array) ($bridge['approvedDestinations'] ?? []) as $item) {
        if (is_array($item)) {
            $reflector = strtoupper(trim((string) ($item['reflector'] ?? '')));
            $module = strtoupper(trim((string) ($item['module'] ?? '')));
            if ($reflector !== '' && $module !== '') $values[] = $reflector . ' ' . $module;
            continue;
        }
        $value = trim((string) $item);
        if ($value !== '') $values[] = $value;
    }
    return array_values(array_unique($values));
}

function asr_bridge_destination_is_approved(array $bridge, string $destination): bool {
    foreach (asr_bridge_approved_values($bridge) as $approved) {
        if (strcasecmp($approved, trim($destination)) === 0) return true;
    }
    return false;
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

function asr_client_explicit_state(array $row): ?bool {
    foreach (['active', 'current', 'connected'] as $key) {
        if (!array_key_exists($key, $row)) continue;
        $value = $row[$key];
        if (is_bool($value)) return $value;
        if (is_int($value) || is_float($value)) {
            if ((float) $value === 1.0) return true;
            if ((float) $value === 0.0) return false;
            continue;
        }
        $text = strtolower(trim((string) $value));
        if (in_array($text, ['1', 'true', 'yes', 'on', 'active', 'current', 'connected'], true)) return true;
        if (in_array($text, ['0', '0.0', 'false', 'no', 'off', 'none', 'null', 'inactive', 'disconnected'], true)) return false;
    }
    return null;
}

function asr_client_epoch_is_fresh(int $epoch, int $maxAge, int $now): bool {
    return $epoch > 0 && $epoch <= $now + 300 && $now - $epoch <= $maxAge;
}

function asr_client_row_is_fresh(array $row, string $bridgeId, bool $currentConnectedFeed = false): bool {
    $now = time();
    $lastSeen = asr_client_epoch_value($row['last_seen_epoch'] ?? $row['last_seen'] ?? $row['timestamp'] ?? 0);
    $lastTalk = asr_client_epoch_value($row['last_tx_epoch'] ?? $row['tx_epoch'] ?? $row['last_talk_epoch'] ?? 0);
    $isCurrent = asr_client_explicit_state($row);
    if ($isCurrent === false) return false;

    if ($bridgeId === 'zello') {
        if ($lastSeen > 0 || $lastTalk > 0) {
            return asr_client_epoch_is_fresh($lastSeen, 180, $now)
                || asr_client_epoch_is_fresh($lastTalk, 180, $now);
        }
        return $currentConnectedFeed || $isCurrent === true;
    }
    if ($bridgeId === 'ysf') {
        if ($lastSeen > 0 || $lastTalk > 0) {
            return asr_client_epoch_is_fresh($lastSeen, 180, $now)
                || asr_client_epoch_is_fresh($lastTalk, 300, $now);
        }
        return $currentConnectedFeed || $isCurrent === true;
    }
    if ($lastSeen > 0) return asr_client_epoch_is_fresh($lastSeen, 180, $now);
    if ($lastTalk > 0) return asr_client_epoch_is_fresh($lastTalk, 300, $now);
    return $currentConnectedFeed || $isCurrent === true || ($bridgeId !== 'zello' && $bridgeId !== 'ysf');
}

function asr_client_has_identity(array $row): bool {
    foreach (['callsign', 'call', 'station', 'username', 'name', 'display_name', 'displayName', 'user', 'current_user', 'dmrid', 'dmr_id', 'id'] as $key) {
        if (trim((string) ($row[$key] ?? '')) !== '') return true;
    }
    return false;
}

function asr_sanitize_client_rows(array $rows, string $bridgeId = '', bool $currentConnectedFeed = false): array {
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
        if ($item !== []
            && asr_client_has_identity($item)
            && asr_client_row_is_fresh($item, $bridgeId, $currentConnectedFeed)) {
            $clean[] = $item;
        }
    }
    return $clean;
}

function asr_client_identity_keys(array $row): array {
    return asrClientIdentityKeys($row);
}

function asr_dedupe_client_rows(array $rows): array {
    $unique = [];
    $aliases = [];
    foreach ($rows as $row) {
        if (!is_array($row)) continue;
        $identityKeys = asr_client_identity_keys($row);
        if ($identityKeys === []) continue;
        $key = '';
        foreach ($identityKeys as $identityKey) {
            if (isset($aliases[$identityKey])) {
                $key = $aliases[$identityKey];
                break;
            }
        }
        if ($key === '') $key = $identityKeys[0];
        foreach ($identityKeys as $identityKey) $aliases[$identityKey] = $key;
        $epoch = max(
            asr_client_epoch_value($row['last_tx_epoch'] ?? $row['tx_epoch'] ?? $row['last_talk_epoch'] ?? 0),
            asr_client_epoch_value($row['last_seen_epoch'] ?? $row['last_seen'] ?? $row['timestamp'] ?? 0),
        );
        $oldEpoch = isset($unique[$key]) ? max(
            asr_client_epoch_value($unique[$key]['last_tx_epoch'] ?? $unique[$key]['tx_epoch'] ?? $unique[$key]['last_talk_epoch'] ?? 0),
            asr_client_epoch_value($unique[$key]['last_seen_epoch'] ?? $unique[$key]['last_seen'] ?? $unique[$key]['timestamp'] ?? 0),
        ) : -1;
        if (!isset($unique[$key]) || $epoch > $oldEpoch) $unique[$key] = $row;
    }
    return array_values($unique);
}

function asr_bridge_clients_state(): array {
    $clients = [];
    $counts = [];

    $readCurrentFile = static function (string $path): array {
        if (!is_readable($path)) return [];
        $mtime = (int) @filemtime($path);
        if ($mtime <= 0 || $mtime > time() + 300 || time() - $mtime > 45) return [];
        $payload = @file_get_contents($path);
        return is_string($payload) ? asr_decode_json_payload($payload) : [];
    };

    $externalPayload = $readCurrentFile(asrRuntimeFilePath('connected-clients.json'));
    $managedPayload = $readCurrentFile(__DIR__ . '/asr-connected-clients.json');
    $externalMeta = is_array($externalPayload['_asr_meta'] ?? null) ? $externalPayload['_asr_meta'] : [];
    $managedMeta = is_array($managedPayload['_asr_meta'] ?? null) ? $managedPayload['_asr_meta'] : [];
    $bridgeIds = array_unique(array_merge(array_keys($externalPayload), array_keys($managedPayload)));

    foreach ($bridgeIds as $id) {
        $id = (string) $id;
        if ($id === '_asr_meta') continue;
        if (!preg_match('/^[a-z][a-z0-9_-]{1,31}$/', $id)) continue;

        // Prefer a non-empty managed feed when a legacy external collector
        // publishes an empty array. This preserves authoritative external rows
        // while allowing ASR's protocol-aware collector to recover client data
        // that the legacy collector cannot observe through a local proxy.
        $fromExternal = array_key_exists($id, $externalPayload);
        $externalRows = $fromExternal && is_array($externalPayload[$id])
            ? $externalPayload[$id]
            : [];
        $managedRows = is_array($managedPayload[$id] ?? null)
            ? $managedPayload[$id]
            : [];
        $useExternal = $fromExternal && ($externalRows !== [] || $managedRows === []);
        $rows = $useExternal ? $externalRows : $managedRows;
        $meta = $useExternal ? ($externalMeta[$id] ?? []) : ($managedMeta[$id] ?? []);
        $kind = is_array($meta) ? strtolower((string) ($meta['kind'] ?? '')) : '';
        $mode = is_array($meta) && preg_match('/^[a-z][a-z0-9_-]{0,31}$/D', (string) ($meta['mode'] ?? ''))
            ? (string) $meta['mode']
            : asr_bridge_mode(['id' => $id]);
        // A flat external connected-clients file is a current-feed contract for
        // backward compatibility. ASR-managed fallback and recent-talker rows
        // are explicitly marked and are never certified as connected clients.
        $currentConnectedFeed = $useExternal ? $kind !== 'recent' && $kind !== 'fallback' : $kind === 'current';
        $clean = asr_dedupe_client_rows(asr_sanitize_client_rows($rows, $mode, $currentConnectedFeed));
        $clients[$id] = $clean;
        // Fallback feeds (notably YSF gateway snapshots) still contain the
        // current linked stations and should be rendered.  The kind controls
        // provenance, not whether an otherwise-current row is visible.
        $counts[$id] = count($clean);
    }

    $tgif = asr_tgif_user_status();
    $tgifRows = is_array($tgif['clients'] ?? null) ? $tgif['clients'] : [];
    $tgifConfigured = !empty($tgif['configured']);
    $dmrStandardIds = [];
    if (array_key_exists('dmr', $clients)) $dmrStandardIds['dmr'] = true;
    foreach ((array) (asr_raw_runtime_config()['bridges'] ?? []) as $bridgeConfig) {
        if (!is_array($bridgeConfig)) continue;
        $id = (string) ($bridgeConfig['id'] ?? '');
        $mode = asr_bridge_mode($bridgeConfig);
        $cardType = (string) ($bridgeConfig['cardType'] ?? 'standard');
        if ($mode === 'dmr' && $cardType === 'standard' && preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', $id)) {
            $dmrStandardIds[$id] = true;
        }
    }
    foreach (array_keys($dmrStandardIds) as $id) {
        $clean = $tgifConfigured
            ? asr_dedupe_client_rows(asr_sanitize_client_rows($tgifRows, 'dmr', true))
            : [];
        $clients[$id] = $clean;
        $counts[$id] = $tgifConfigured ? count($clean) : 0;
    }

    $meta = [];
    foreach ($bridgeIds as $id) {
        $id = (string) $id;
        if ($id === '_asr_meta' || !preg_match('/^[a-z][a-z0-9_-]{1,31}$/', $id)) continue;
        $fromExternal = array_key_exists($id, $externalPayload);
        $externalRows = $fromExternal && is_array($externalPayload[$id]) ? $externalPayload[$id] : [];
        $managedRows = is_array($managedPayload[$id] ?? null) ? $managedPayload[$id] : [];
        $useExternal = $fromExternal && ($externalRows !== [] || $managedRows === []);
        $sourceMeta = $useExternal ? ($externalMeta[$id] ?? []) : ($managedMeta[$id] ?? []);
        $meta[$id] = is_array($sourceMeta) ? [
            'kind' => substr(strtolower((string) ($sourceMeta['kind'] ?? ($useExternal ? 'current' : 'unknown'))), 0, 16),
            'mode' => substr(strtolower((string) ($sourceMeta['mode'] ?? asr_bridge_mode(['id' => $id]))), 0, 32),
        ] : ['kind' => $useExternal ? 'current' : 'unknown', 'mode' => asr_bridge_mode(['id' => $id])];
    }
    foreach (array_keys($dmrStandardIds) as $id) {
        $meta[$id] = ['kind' => $tgifConfigured ? 'current' : 'unavailable', 'mode' => 'dmr'];
    }
    return ['clients' => $clients, 'counts' => $counts, 'meta' => $meta];
}

function asr_bridge_clients_payload(): array {
    return asr_bridge_clients_state()['clients'];
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
    $number = (int) $value;
    return $number > 0 && $number < 2000;
}

function asr_lookup_is_iax_client(string $node, string $label, string $detail): bool {
    $node = trim($node);
    $text = $node . ' ' . $label . ' ' . $detail;
    return ($node !== '' && !preg_match('/^\d+$/', $node)) || preg_match('/\b(IAX|IaxRpt|Web Transceiver|WebTransceiver)\b/i', $text);
}

function asr_lookup_payload_uncached(): array {
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

function asr_lookup_payload(): array {
    $cacheTtl = 15;
    $readCache = static function () use ($cacheTtl): ?array {
        if (!is_readable(ASR_LOOKUP_DATA_CACHE)) return null;
        $modifiedAt = (int) @filemtime(ASR_LOOKUP_DATA_CACHE);
        if ($modifiedAt <= 0 || time() - $modifiedAt >= $cacheTtl) return null;
        $decoded = json_decode((string) file_get_contents(ASR_LOOKUP_DATA_CACHE), true);
        return is_array($decoded) && !empty($decoded['ok']) ? $decoded : null;
    };

    $cached = $readCache();
    if ($cached !== null) return $cached;

    $directory = dirname(ASR_LOOKUP_DATA_CACHE);
    $lock = is_dir($directory) && is_writable($directory)
        ? @fopen(ASR_LOOKUP_DATA_CACHE . '.lock', 'c')
        : false;
    if ($lock) {
        @flock($lock, LOCK_EX);
        clearstatcache(true, ASR_LOOKUP_DATA_CACHE);
        $cached = $readCache();
        if ($cached !== null) {
            @flock($lock, LOCK_UN);
            @fclose($lock);
            return $cached;
        }
    }

    $payload = asr_lookup_payload_uncached();
    if ($lock) {
        $json = json_encode($payload, JSON_UNESCAPED_SLASHES);
        if (is_string($json)) {
            $temporary = ASR_LOOKUP_DATA_CACHE . '.tmp.' . getmypid();
            if (@file_put_contents($temporary, $json . "\n", LOCK_EX) !== false) {
                @chmod($temporary, 0660);
                if (!@rename($temporary, ASR_LOOKUP_DATA_CACHE)) @unlink($temporary);
            }
        }
        @flock($lock, LOCK_UN);
        @fclose($lock);
    }
    return $payload;
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
        if (!empty($entry['resolved']) && ($entry['source'] ?? '') !== 'nominatim') continue;
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
    return asrFavoritesFiles(dirname(ASR_ETC_FAVORITES), $dir);
}

function asr_safe_favorites_file(string $requested = ''): string {
    $dir = asr_allscan_dir();
    return asrFavoritesFile(
        $requested,
        dirname(ASR_ETC_FAVORITES),
        $dir,
        ASR_DEFAULT_FAVORITES
    );
}

function asr_ini_values(string $contents, string $key): array {
    $pattern = '/^\s*' . preg_quote($key, '/') . '\s*\[\]\s*=\s*(?:"((?:\\\\.|[^"\\\\])*)"|\'((?:\\\\.|[^\'\\\\])*)\'|(.+?))\s*$/mi';
    preg_match_all($pattern, $contents, $matches, PREG_SET_ORDER);
    return array_map(static function (array $match): string {
        $value = ($match[1] ?? '') !== '' ? $match[1] : (($match[2] ?? '') !== '' ? $match[2] : ($match[3] ?? ''));
        return trim(str_replace(['\\"', '\\\\'], ['"', '\\'], $value));
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

function asr_favorites_user_data(string $file): array {
    if (!is_readable(ASR_FAVORITES_USER_STATE)) return ['nodes' => [], 'order' => []];
    $decoded = json_decode((string) file_get_contents(ASR_FAVORITES_USER_STATE), true);
    if (!is_array($decoded)) return ['nodes' => [], 'order' => []];
    $entry = $decoded['files'][basename($file)] ?? [];
    return is_array($entry) ? $entry + ['nodes' => [], 'order' => []] : ['nodes' => [], 'order' => []];
}

function asr_favorites_manager(array $arguments): array {
    if (!is_executable(ASR_FAVORITES_MANAGER_HELPER)) asr_error('Favorites manager is unavailable.', 500);
    $command = 'sudo -n ' . escapeshellarg(ASR_FAVORITES_MANAGER_HELPER);
    foreach ($arguments as $argument) $command .= ' ' . escapeshellarg((string) $argument);
    $raw = trim((string) shell_exec($command . ' 2>&1'));
    $result = json_decode($raw, true);
    if (!is_array($result) || empty($result['ok'])) {
        asr_error((string) ($result['error'] ?? 'Favorites operation failed.'), 400);
    }
    return $result;
}

function asr_favorites_payload(string $requested = ''): array {
    $selected = asr_safe_favorites_file($requested);
    $userData = asr_favorites_user_data($selected);
    $nodeData = is_array($userData['nodes'] ?? null) ? $userData['nodes'] : [];
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
        $custom = is_array($nodeData[$node] ?? null) ? $nodeData[$node] : [];
        $customDescription = trim((string) ($custom['description'] ?? ''));
        $rows[] = [
            'index' => (string) $index,
            'node' => $node,
            'label' => (string) $display['label'],
            'name' => (string) $display['name'],
            'description' => $customDescription !== '' ? $customDescription : (string) $display['name'],
            'frequency' => (string) $display['desc'],
            'desc' => $customDescription !== '' ? $customDescription : (string) $display['desc'],
            'referenceDesc' => (string) $display['desc'],
            'customDescription' => $customDescription,
            'location' => (string) $display['location'],
            'color' => (string) ($custom['color'] ?? ''),
            'rx' => '',
            'lcnt' => '',
            'href' => 'http://stats.allstarlink.org/stats/' . rawurlencode($node),
        ];
    }

    $order = array_values(array_filter(array_map('strval', (array) ($userData['order'] ?? []))));
    if ($order) {
        $positions = array_flip($order);
        usort($rows, static function (array $left, array $right) use ($positions): int {
            $a = $positions[$left['node']] ?? PHP_INT_MAX;
            $b = $positions[$right['node']] ?? PHP_INT_MAX;
            return $a <=> $b ?: ((int) $left['index'] <=> (int) $right['index']);
        });
    }
    foreach ($rows as $index => &$row) $row['index'] = (string) $index;
    unset($row);

    $files = array_map(static function (string $file) use ($selected): array {
        $real = realpath($file);
        return [
            'value' => basename($file),
            'label' => basename($file),
            'selected' => $file === $selected,
            'modifiable' => is_string($real) && str_starts_with($real, '/etc/allscan/favorites'),
        ];
    }, asr_favorites_files());

    return ['ok' => true, 'rows' => $rows, 'files' => $files, 'selectedFile' => basename($selected)];
}

function asr_favorite_action(string $action, string $node, string $requested): array {
    if (!in_array($action, ['addfav', 'delfav'], true)) asr_error('Invalid Favorites action.');
    if (!preg_match('/^[A-Za-z0-9*#]{3,8}$/', $node)) asr_error('Invalid node.');
    $file = asr_safe_favorites_file($requested);
    if (!is_writable($file)) asr_error('Favorites file is not writable.', 500);
    $real = realpath($file);
    if (!is_string($real) || !str_starts_with($real, '/etc/allscan/favorites')) {
        asr_error('Only shared Favorites files under /etc/allscan can be modified.', 400);
    }
    if (!is_executable(ASR_FAVORITES_UPDATE_HELPER)) {
        asr_error('Favorites update helper is unavailable.', 500);
    }

    $verb = $action === 'delfav' ? 'delete' : 'add';
    $label = '';
    if ($verb === 'add') {
        $record = asr_lookup_node_record($node);
        $label = $record ? asr_format_favorite_label($record, $node) : $node . ' ' . $node;
    }
    $command = 'sudo -n ' . escapeshellarg(ASR_FAVORITES_UPDATE_HELPER)
        . ' ' . escapeshellarg($verb)
        . ' --file ' . escapeshellarg($real)
        . ' --node ' . escapeshellarg($node);
    if ($verb === 'add') $command .= ' --label ' . escapeshellarg($label);
    $raw = trim((string) shell_exec($command . ' 2>&1'));
    $result = json_decode($raw, true);
    if (!is_array($result) || empty($result['ok'])) {
        asr_error('Favorites update failed.', 500);
    }
    return [
        'ok' => true,
        'changed' => !empty($result['changed']),
        'message' => $verb === 'delete'
            ? "Deleted {$node} from Favorites."
            : "Added {$node} to Favorites.",
    ];
}

function asr_favorite_manage_action(string $operation, string $requested, string $node = '', string $value = ''): array {
    $allowed = ['preview-import', 'import', 'update-description', 'reset-description', 'set-color', 'reset-appearance', 'reorder', 'reset-order', 'reset-favorite'];
    if (!in_array($operation, $allowed, true)) asr_error('Invalid Favorites operation.');
    if ($node !== '' && !preg_match('/^[A-Za-z0-9*#]{3,8}$/', $node)) asr_error('Invalid node.');
    if (strlen($value) > 20000) asr_error('Favorites value is too large.');
    $file = asr_safe_favorites_file($requested);
    $real = realpath($file);
    if (!is_string($real) || !str_starts_with($real, '/etc/allscan/favorites')) {
        asr_error('Only shared Favorites files under /etc/allscan can be modified.', 400);
    }
    $arguments = [$operation, '--file', $real];
    if ($node !== '') array_push($arguments, '--node', $node);
    if ($value !== '') array_push($arguments, '--value', $value);
    return asr_favorites_manager($arguments);
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
        asr_file_status(asrRuntimeFilePath('bridge-live.json')),
        asr_file_status(asrRuntimeFilePath('connected-clients.json')),
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
    $webBase = asr_web_base();
    if ($webBase !== '' && strpos($path, $webBase . '/') === 0) {
        $resolved = __DIR__ . substr($path, strlen($webBase));
    }
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
    $legacyEnvironment = '/etc/allscan-reimagined/connected-clients-daemon.env';
    $userStatus = asr_logged_in() ? asr_tgif_user_status() : ['configured' => false, 'callsign' => '', 'updatedEpoch' => 0, 'clients' => []];
    return [
        'mode' => 'per_user',
        'helper' => asr_root_file_brief(ASR_TGIF_USER_HELPER),
        'refreshTimer' => asr_unit_state('allscan-reimagined-tgif-user-sessions.timer'),
        'refreshService' => asr_unit_state('allscan-reimagined-tgif-user-sessions.service'),
        'currentUserConfigured' => !empty($userStatus['configured']),
        'currentUserCallsign' => substr((string) ($userStatus['callsign'] ?? ''), 0, 20),
        'currentUserUpdatedEpoch' => max(0, (int) ($userStatus['updatedEpoch'] ?? 0)),
        'currentUserClientCount' => is_array($userStatus['clients'] ?? null) ? count($userStatus['clients']) : 0,
        'legacyTokenEnvironment' => asr_root_file_brief($legacyEnvironment),
    ];
}

function asr_bridge_collector_required(array $bridges): bool {
    foreach ($bridges as $bridge) {
        if (!is_array($bridge)) continue;
        $source = (string) ($bridge['clientSource'] ?? 'auto');
        $url = trim((string) ($bridge['clientUrl'] ?? ''));
        if (in_array($source, ['local_json', 'http_api'], true) && $url !== '') return true;
        if (in_array($source, ['auto', 'disabled'], true)
            && (string) ($bridge['cardType'] ?? 'standard') === 'standard'
            && in_array(asr_bridge_mode($bridge), ['ysf', 'zello', 'p25', 'nxdn', 'm17'], true)) return true;
    }
    return false;
}

function asr_bridge_exact_readiness(array $bridge, array $control, string $linked): array {
    $mode = asr_bridge_mode($bridge);
    $cardType = (string) ($bridge['cardType'] ?? 'standard');
    $backendMode = (string) ($bridge['backendMode'] ?? (isset($bridge['bridgePermission']) ? 'managed' : 'display_only'));
    if ($cardType === 'standard' && in_array($mode, ['p25', 'nxdn', 'm17'], true) && $backendMode === 'display_only') {
        return ['state' => 'display_only', 'ready' => false, 'summary' => strtoupper($mode) . ' backend controls are not configured.', 'missing' => []];
    }
    if ($cardType === 'standard' && !in_array($mode, ['p25', 'nxdn', 'm17'], true)) {
        return ['state' => 'simple', 'ready' => $linked === 'yes', 'summary' => $linked === 'yes' ? ucfirst($mode) . ' Standard Bridge link is present.' : ucfirst($mode) . ' Standard Bridge link is not currently present.', 'missing' => $linked === 'yes' ? [] : ['AllStar bridge link is not present.']];
    }
    $ready = !empty($control['ready']);
    $reason = trim((string) ($control['reason'] ?? ''));
    $missing = is_array($control['missing'] ?? null) ? array_values(array_map('strval', $control['missing'])) : [];
    if (!$ready && $reason === '') $reason = strtoupper($mode) . ' backend not ready: status or helper evidence is unavailable.';
    return ['state' => $ready ? 'ready' : 'not_ready', 'ready' => $ready, 'summary' => $ready ? strtoupper($mode) . ' backend ready.' : $reason, 'missing' => $missing];
}

function asr_bridge_exact_resources(array $bridge): array {
    $mode = asr_bridge_mode($bridge);
    $cardType = (string) ($bridge['cardType'] ?? 'standard');
    $serviceKeys = [];
    $fileKeys = [];
    $helper = '';
    if ($cardType === 'dmr_net') {
        $fileKeys = ['abinfoPath', 'dvswitchScript', 'analogConfig'];
        $helper = ASR_BRIDGE_CONTROL_HELPER;
    } elseif ($cardType === 'ysf_net') {
        $fileKeys = ['ysfGatewayConfig', 'mmdvmConfig', 'ysfHostsPath'];
        $serviceKeys = ['ysfGatewayService', 'mmdvmService', 'analogBridgeService', 'emulatorService'];
        $helper = ASR_YSF_BRIDGE_CONTROL_HELPER;
    } elseif (in_array($mode, ['p25', 'nxdn'], true) && (string) ($bridge['backendMode'] ?? (isset($bridge['bridgePermission']) ? 'managed' : 'display_only')) === 'managed') {
        $fileKeys = ['gatewayConfig'];
        $serviceKeys = ['gatewayService', 'mmdvmService', 'analogBridgeService'];
        if ($mode === 'nxdn') $serviceKeys[] = 'emulatorService';
        $helper = asr_next_mode_helper_path($mode);
    } elseif ($mode === 'm17' && (string) ($bridge['backendMode'] ?? (isset($bridge['bridgePermission']) ? 'managed' : 'display_only')) === 'managed') {
        $helper = ASR_M17_BRIDGE_CONTROL_HELPER;
    }
    $files = [];
    foreach ($fileKeys as $key) {
        $path = trim((string) ($bridge[$key] ?? ''));
        $files[] = [
            'name' => $key,
            'path' => $path,
            'configured' => $path !== '',
            'exists' => $path !== '' && is_file($path),
            'readable' => $path !== '' && is_readable($path),
        ];
    }
    $services = [];
    foreach ($serviceKeys as $key) {
        $unit = trim((string) ($bridge[$key] ?? ''));
        $services[] = [
            'name' => $key,
            'unit' => $unit,
            'configured' => $unit !== '',
            'status' => $unit !== '' ? asr_unit_state($unit) : ['unit' => '', 'state' => 'not configured', 'enabled' => 'not configured'],
        ];
    }
    return [
        'node' => (string) ($bridge['node'] ?? ''),
        'linkAlias' => (string) ($bridge['linkAlias'] ?? ''),
        'helper' => $helper === '' ? ['configured' => false] : ['configured' => true, 'installed' => is_file($helper), 'executable' => is_executable($helper)],
        'files' => $files,
        'services' => $services,
        'mqtt' => in_array($mode, ['p25', 'nxdn'], true) ? [
            'authenticationRequired' => true,
            'gatewayTopic' => (string) ($bridge['mqttName'] ?? ''),
            'activityTopic' => (string) ($bridge['mmdvmMqttName'] ?? ''),
            'rootSecretRequired' => true,
        ] : null,
        'commandTransport' => (string) ($bridge['commandTransport'] ?? ''),
        'catalogConfigured' => $mode === 'ysf' ? trim((string) ($bridge['ysfHostsPath'] ?? '')) !== '' : null,
        'audioQualified' => $mode === 'm17' ? !empty($bridge['m17AudioQualified']) : null,
    ];
}

function asr_bridge_diagnostics(): array {
    $config = asr_raw_runtime_config();
    $runtime = asr_runtime_config();
    $bridges = is_array($config['bridges'] ?? null) ? $config['bridges'] : [];
    $clients = asr_bridge_clients_payload();
    $collectorTimer = asr_unit_state('allscan-reimagined-bridge-clients.timer');
    $collectorService = asr_unit_state('allscan-reimagined-bridge-clients.service');
    $clientFile = asr_file_status(asrRuntimeFilePath('connected-clients.json'));
    $asrClientFile = asr_file_status(__DIR__ . '/asr-connected-clients.json');
    $node = (string) ($runtime['node'] ?? '');
    $asteriskRead = '/usr/local/sbin/allscan-reimagined-asterisk-read';
    $lstats = $node !== '' ? implode("\n", asr_command_lines('sudo -n ' . escapeshellarg($asteriskRead) . ' lstats ' . escapeshellarg($node), 10000)) : '';
    $nodesOutput = $node !== '' ? implode("\n", asr_command_lines('sudo -n ' . escapeshellarg($asteriskRead) . ' nodes ' . escapeshellarg($node), 6000)) : '';
    $rows = [];
    $nextModeState = asr_next_mode_statuses();
    $controlStates = array_merge(asr_dmr_net_control_statuses(), asr_ysf_net_control_statuses(), $nextModeState['controls']);

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
        $source = (string) ($bridge['clientSource'] ?? 'auto');
        if ($source === 'disabled') $source = 'auto';
        $sourceStatus = ['status' => $source === 'auto' ? 'auto-detect' : 'configured'];
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
        $readiness = asr_bridge_exact_readiness($bridge, is_array($controlStates[$id] ?? null) ? $controlStates[$id] : [], $linked);
        $resources = asr_bridge_exact_resources($bridge);
        $exactServices = [];
        foreach ((array) ($resources['services'] ?? []) as $service) {
            $state = is_array($service['status'] ?? null) ? (string) ($service['status']['state'] ?? 'unknown') : 'unknown';
            $exactServices[] = ['unit' => (string) ($service['unit'] ?? ''), 'state' => $state];
        }
        $warnings = [];
        if ($id === 'zello' && $clientCount === 0) {
            $zelloTalkers = asr_file_status(asrRuntimeFilePath('zello-talkers.json'));
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
            'services' => $exactServices,
            'backendMode' => (string) ($bridge['backendMode'] ?? 'managed'),
            'readiness' => $readiness,
            'resources' => $resources,
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
        if (strpos($line, ' ' . asr_web_base() . '/') === false) continue;
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


function asr_custom_command_entries(): array {
    global $gCfg;
    $stored = $gCfg[cmdbuttons] ?? [];
    if (!is_array($stored)) $stored = $stored === '' ? [] : explode(',', (string) $stored);
    $commands = [];
    foreach ($stored as $index => $entry) {
        $text = trim((string) $entry);
        if ($text === '') continue;
        if (!preg_match('/^(.*?)(\\*[0-9A-Da-d#;]{1,40})$/D', $text, $match)) continue;
        $command = (string) $match[2];
        $label = trim((string) $match[1]);
        $commands[] = [
            'id' => (string) $index,
            'label' => $label !== '' ? $label : $command,
            'command' => $command,
        ];
    }
    return $commands;
}

function asr_custom_commands_payload(): array {
    global $gCfg;
    return [
        'ok' => true,
        'enabled' => !empty($gCfg[showcmdbuttons]),
        'commands' => asr_custom_command_entries(),
    ];
}

function asr_save_custom_commands(string $raw, string $enabled): array {
    global $cfgModel, $gCfg, $gCfgUpdated;
    $decoded = json_decode($raw, true);
    if (!is_array($decoded)) asr_error('Custom command data is invalid.');
    if (count($decoded) > 24) asr_error('A maximum of 24 custom commands is supported.');

    $stored = [];
    foreach ($decoded as $entry) {
        if (!is_array($entry)) asr_error('Custom command data is invalid.');
        $label = trim(strip_tags((string) ($entry['label'] ?? '')));
        $command = trim((string) ($entry['command'] ?? ''));
        if ($label === '' || strlen($label) > 40 || preg_match('/[,\\x00-\\x1F\\x7F]/', $label)) {
            asr_error('Each command needs a name of 40 characters or fewer without commas.');
        }
        if (!preg_match('/^\\*[0-9A-Da-d#;]{1,40}$/D', $command)) {
            asr_error('Each DTMF command must begin with * and contain only supported DTMF characters.');
        }
        $stored[] = $label === $command ? $command : $label . ' ' . $command;
    }

    $gCfg[cmdbuttons] = $stored;
    $gCfg[showcmdbuttons] = $enabled === '1' ? 1 : 0;
    $gCfgUpdated[cmdbuttons] = time();
    $gCfgUpdated[showcmdbuttons] = time();
    $cfgModel->saveCfgs();
    if ($cfgModel->error) asr_error('Custom commands could not be saved.', 500);
    return asr_custom_commands_payload();
}

$action = (string) ($_GET['action'] ?? $_POST['action'] ?? '');

if ($action === 'auth-status') asr_json(asr_auth_payload());
if ($action === 'custom-commands') {
    asr_require_read();
    asr_json(asr_custom_commands_payload());
}
if ($action === 'custom-commands-save') {
    asr_require_post();
    asr_require_same_origin();
    asr_require_admin();
    if ((string) ($_SERVER['HTTP_X_ASR_REQUESTED_WITH'] ?? '') !== 'custom-command-control') {
        asr_error('Invalid Custom Cmd request.', 403);
    }
    asr_json(asr_save_custom_commands(
        (string) ($_POST['commands'] ?? '[]'),
        (string) ($_POST['enabled'] ?? '0')
    ));
}
if ($action === 'tgif-user-status') {
    asr_require_read();
    asr_json(asr_tgif_user_status());
}
if ($action === 'tgif-user-login') {
    asr_require_post();
    asr_require_same_origin();
    if (!asr_logged_in()) asr_error('Login required for TGIF client tracking.', 403);
    if ((string) ($_SERVER['HTTP_X_ASR_REQUESTED_WITH'] ?? '') !== 'tgif-user-session') asr_error('Invalid TGIF session request.', 403);
    $callsign = strtoupper(trim((string) ($_POST['callsign'] ?? '')));
    $talkgroup = trim((string) ($_POST['talkgroup'] ?? ''));
    $password = (string) ($_POST['password'] ?? '');
    $captcha = strtolower(trim((string) ($_POST['captcha'] ?? '')));
    if (!preg_match('/^[A-Z0-9]{3,10}$/D', $callsign)) asr_error('Enter a valid TGIF callsign.');
    if (!preg_match('/^[1-9][0-9]{0,7}$/D', $talkgroup)) asr_error('Enter a valid TGIF talkgroup.');
    if ($password === '' || strlen($password) > 128) asr_error('Enter your TGIF password.');
    if ($captcha !== '' && !preg_match('/^[a-z0-9]{1,32}$/D', $captcha)) asr_error('Enter the CAPTCHA text shown.');
    $args = [$callsign, '--talkgroup', $talkgroup];
    if ($captcha !== '') { $args[] = '--captcha'; $args[] = $captcha; }
    asr_json(asr_tgif_user_command('login', $args, $password . "\n"));
}
if ($action === 'tgif-user-logout') {
    asr_require_post();
    asr_require_same_origin();
    if (!asr_logged_in()) asr_error('Login required for TGIF client tracking.', 403);
    if ((string) ($_SERVER['HTTP_X_ASR_REQUESTED_WITH'] ?? '') !== 'tgif-user-session') asr_error('Invalid TGIF session request.', 403);
    asr_json(asr_tgif_user_command('logout'));
}
if ($action === 'runtime-config') asr_json(asr_runtime_config());
if ($action === 'release-status') {
    asr_require_read();
    asr_json(asr_release_status_payload());
}
if (in_array($action, ['update-check', 'update-preflight', 'update-queue', 'update-recover', 'update-job-status'], true)) {
    header('Cache-Control: no-store');
    asr_require_admin();
    if ($action === 'update-check') asr_json(asr_updater_command('check'));
    if ($action === 'update-job-status') {
        asr_json(asr_updater_command('status', (string) ($_GET['jobId'] ?? '')));
    }
    asr_require_post();
    asr_require_same_origin();
    if ((string) ($_SERVER['HTTP_X_ASR_REQUESTED_WITH'] ?? '') !== 'asr-update-control') {
        asr_error('Invalid ASR update request.', 403);
    }
    asr_json(asr_updater_command($action === 'update-preflight' ? 'preflight' : ($action === 'update-recover' ? 'recover' : 'queue')));
}
if ($action === 'bridge-clients') {
    asr_require_read();
    asr_json(asr_bridge_clients_payload());
}
if ($action === 'bridge-status') {
    asr_require_read();
    asr_json(asr_bridge_status_payload());
}
if ($action === 'urf-status') {
    asr_require_read();
    asr_json(asr_urf_status_payload());
}
if ($action === 'asl-ban-list') {
    asr_require_admin();
    asr_json(asr_asl_ban_command('list'));
}
if ($action === 'asl-ban-control') {
    asr_require_post();
    asr_require_same_origin();
    asr_require_admin();
    if ((string) ($_SERVER['HTTP_X_ASR_REQUESTED_WITH'] ?? '') !== 'asl-ban-control') asr_error('Invalid ban request.', 403);
    asr_json(asr_asl_ban_command(
        strtolower(trim((string) ($_POST['verb'] ?? ''))),
        strtolower(trim((string) ($_POST['kind'] ?? ''))),
        (string) ($_POST['value'] ?? ''),
        strtolower(trim((string) ($_POST['duration'] ?? 'permanent'))),
        (string) ($_POST['reason'] ?? '')
    ));
}
if ($action === 'urf-access-list') {
    asr_require_admin();
    asr_json(asr_urf_admin_command('list'));
}
if ($action === 'urf-access-control') {
    asr_require_post();
    asr_require_same_origin();
    asr_require_admin();
    if ((string) ($_SERVER['HTTP_X_ASR_REQUESTED_WITH'] ?? '') !== 'urf-access-control') asr_error('Invalid Global Ban request.', 403);
    $verb = strtolower(trim((string) ($_POST['verb'] ?? '')));
    $rule = strtoupper(trim((string) ($_POST['rule'] ?? '')));
    $duration = strtolower(trim((string) ($_POST['duration'] ?? 'permanent')));
    if (!in_array($verb, ['ban', 'unban'], true)) asr_error('Invalid Global Ban action.');
    if ($verb === 'ban' && !in_array($duration, ['15m', '1h', '1w', '30d', 'permanent'], true)) asr_error('Invalid Global Ban duration.');
    asr_json(asr_urf_admin_command($verb, $rule, $verb === 'ban' ? $duration : ''));
}
if ($action === 'urf-client-kick') {
    asr_require_post();
    asr_require_same_origin();
    asr_require_admin();
    if ((string) ($_SERVER['HTTP_X_ASR_REQUESTED_WITH'] ?? '') !== 'urf-client-kick') asr_error('Invalid URF client-kick request.', 403);
    $callsign = strtoupper(trim((string) ($_POST['callsign'] ?? '')));
    $protocol = strtoupper(trim((string) ($_POST['protocol'] ?? '')));
    asr_json(asr_urf_kick_command($callsign, $protocol));
}
if ($action === 'standalone-client-kick') {
    asr_require_post();
    asr_require_same_origin();
    asr_require_admin();
    if ((string) ($_SERVER['HTTP_X_ASR_REQUESTED_WITH'] ?? '') !== 'standalone-client-kick') asr_error('Invalid bridge Kick request.', 403);
    asr_json(asr_standalone_kick_command((string) ($_POST['bridgeId'] ?? ''), (string) ($_POST['callsign'] ?? '')));
}
if ($action === 'bridge-destinations') {
    asr_require_read();
    asr_json(asr_bridge_destinations((string) ($_GET['bridgeId'] ?? '')));
}
if ($action === 'bridge-connect') {
    asr_require_post();
    asr_require_same_origin();
    asr_require_modify();
    if ((string) ($_SERVER['HTTP_X_ASR_REQUESTED_WITH'] ?? '') !== 'bridge-control') {
        asr_error('Invalid bridge-control request.', 403);
    }
    $bridgeId = (string) ($_POST['bridgeId'] ?? '');
    if (asr_next_mode_bridge_config($bridgeId) !== null) {
        asr_json(asr_next_mode_connect($bridgeId, (string) ($_POST['destination'] ?? '')));
    }
    if (asr_ysf_net_bridge_config($bridgeId) !== null) {
        asr_json(asr_ysf_net_connect($bridgeId, (string) ($_POST['destination'] ?? '')));
    }
    asr_json(asr_dmr_net_connect(
        $bridgeId,
        (string) ($_POST['talkgroup'] ?? ($_POST['destination'] ?? ''))
    ));
}
if ($action === 'bridge-disconnect') {
    asr_require_post();
    asr_require_same_origin();
    asr_require_modify();
    if ((string) ($_SERVER['HTTP_X_ASR_REQUESTED_WITH'] ?? '') !== 'bridge-control') {
        asr_error('Invalid bridge-control request.', 403);
    }
    $bridgeId = (string) ($_POST['bridgeId'] ?? '');
    if (asr_next_mode_bridge_config($bridgeId) !== null) {
        asr_json(asr_next_mode_disconnect($bridgeId));
    }
    if (asr_ysf_net_bridge_config($bridgeId) !== null) {
        asr_json(asr_ysf_net_disconnect($bridgeId));
    }
    asr_json(asr_dmr_net_disconnect($bridgeId));
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
if ($action === 'favorite-manage') {
    asr_require_post();
    asr_require_same_origin();
    asr_require_modify();
    asr_json(asr_favorite_manage_action(
        (string) ($_POST['operation'] ?? ''),
        (string) ($_POST['favsfile'] ?? ''),
        (string) ($_POST['node'] ?? ''),
        (string) ($_POST['value'] ?? '')
    ));
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
