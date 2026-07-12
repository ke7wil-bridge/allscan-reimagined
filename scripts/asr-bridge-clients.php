#!/usr/bin/env php
<?php
declare(strict_types=1);

const ASR_CONFIG_FILE = '/etc/allscan-reimagined/config.json';
const ASR_SECRETS_FILE = '/etc/allscan-reimagined/secrets.json';
const ASR_DEFAULT_OUTPUT = '/var/www/html/allscan/asr-connected-clients.json';
const ASR_ZELLO_SOURCE_FILES = [
	'/var/www/html/allscan/zello-talkers.json',
	'/var/www/html/allscan/zello-status-data.json',
	'/var/www/html/allscan/zello-stream-debug.json',
];
const ASR_YSF_LOG_DIR = '/var/log/YSFReflector';
const ASR_CLIENT_MAX_SEEN_AGE = 180;
const ASR_CLIENT_MAX_TALK_AGE = 300;

function asrReadJson(string $path): array {
	if(!is_readable($path))
		return [];
	$data = json_decode((string) file_get_contents($path), true);
	return is_array($data) ? $data : [];
}

function asrArrayIsList(array $value): bool {
	if($value === [])
		return true;
	return array_keys($value) === range(0, count($value) - 1);
}

function asrSafeLocalJsonPath(string $requested): string {
	$requested = trim($requested);
	if($requested === '')
		return '';

	$candidate = $requested;
	if(strpos($requested, '/allscan/') === 0)
		$candidate = '/var/www/html' . $requested;

	$real = realpath($candidate);
	if(!$real || !is_file($real) || !is_readable($real))
		return '';
	if(!preg_match('/\.(json|txt)$/i', $real))
		return '';

	$allowedDirs = array_filter([
		realpath('/var/www/html/allscan'),
		realpath('/srv/http/allscan'),
		realpath('/etc/allscan-reimagined'),
	]);
	foreach($allowedDirs as $allowedDir) {
		$allowedDir = rtrim((string) $allowedDir, '/');
		if($real === $allowedDir || strpos($real, $allowedDir . '/') === 0)
			return $real;
	}

	return '';
}

function asrClientRowsFromPayload(array $payload, string $bridgeId): array {
	$candidates = [
		$payload[$bridgeId] ?? null,
		$payload['connected_clients'][$bridgeId] ?? null,
		$payload['recent_users'] ?? null,
		$payload['recent_talkers'] ?? null,
		$payload['recentTalkers'] ?? null,
		$payload['talkers'] ?? null,
		$payload['clients'] ?? null,
		$payload['users'] ?? null,
		$payload['connected'] ?? null,
		$payload['active'] ?? null,
		$payload['rows'] ?? null,
		$payload['data'] ?? null,
	];

	foreach($candidates as $candidate) {
		if(!is_array($candidate))
			continue;
		if(asrArrayIsList($candidate))
			return $candidate;
		if(isset($candidate[$bridgeId]) && is_array($candidate[$bridgeId]))
			return $candidate[$bridgeId];
	}

	return asrArrayIsList($payload) ? $payload : [];
}

function asrEpochValue(mixed $value): int {
	if(is_int($value) || is_float($value))
		return (int) $value;
	$text = trim((string) $value);
	if($text === '')
		return 0;
	if(preg_match('/^\d+(?:\.\d+)?$/', $text))
		return (int) floor((float) $text);
	$epoch = strtotime($text);
	return $epoch === false ? 0 : $epoch;
}

function asrClientRowIsFresh(array $row, string $bridgeId): bool {
	$now = time();
	$lastSeen = asrEpochValue($row['last_seen_epoch'] ?? $row['last_seen'] ?? $row['timestamp'] ?? 0);
	$lastTalk = asrEpochValue($row['last_tx_epoch'] ?? $row['tx_epoch'] ?? $row['last_talk_epoch'] ?? 0);
	$isCurrent = filter_var($row['active'] ?? $row['current'] ?? $row['connected'] ?? false, FILTER_VALIDATE_BOOLEAN);

	if($bridgeId === 'zello') {
		return $isCurrent && ($lastSeen === 0 || $now - $lastSeen <= ASR_CLIENT_MAX_SEEN_AGE);
	}

	if($bridgeId === 'ysf') {
		return $lastSeen > 0 && $now - $lastSeen <= ASR_CLIENT_MAX_SEEN_AGE;
	}

	if($lastSeen > 0)
		return $now - $lastSeen <= ASR_CLIENT_MAX_SEEN_AGE;
	if($lastTalk > 0)
		return $now - $lastTalk <= ASR_CLIENT_MAX_TALK_AGE;

	return $bridgeId !== 'zello' && $bridgeId !== 'ysf';
}

function asrSanitizeClientRows(array $rows, string $bridgeId = ''): array {
	$clean = [];
	foreach($rows as $row) {
		if(is_string($row)) {
			$value = trim($row);
			if($value !== '' && $bridgeId !== 'zello' && $bridgeId !== 'ysf')
				$clean[] = ['name' => substr($value, 0, 120)];
			continue;
		}
		if(!is_array($row))
			continue;
		if(asrIsLocalClientRow($row))
			continue;

		$item = [];
		foreach(['callsign', 'call', 'station', 'username', 'name', 'display_name', 'displayName', 'user', 'current_user', 'dmrid', 'dmr_id', 'id', 'ip', 'address', 'host', 'remote_addr', 'last_tx_epoch', 'tx_epoch', 'last_talk_epoch', 'last_seen_epoch', 'last_seen', 'timestamp', 'active', 'current', 'connected'] as $key) {
			if(!array_key_exists($key, $row))
				continue;
			$value = $row[$key];
			if(is_scalar($value))
				$item[$key] = is_string($value) ? substr(trim($value), 0, 160) : $value;
		}
		if($item !== [] && asrClientRowIsFresh($item, $bridgeId))
			$clean[] = $item;
	}
	return $clean;
}

function asrIsLocalClientRow(array $row): bool {
	foreach(['ip', 'address', 'host', 'remote_addr', 'remoteAddress'] as $key) {
		$value = trim((string) ($row[$key] ?? ''));
		if($value === '')
			continue;
		if($value === '127.0.0.1' || $value === '::1' || strcasecmp($value, 'localhost') === 0)
			return true;
	}
	return false;
}

function asrDedupeClientRows(array $rows): array {
	$seen = [];
	$clean = [];
	foreach($rows as $row) {
		$name = strtolower((string) ($row['callsign'] ?? $row['call'] ?? $row['station'] ?? $row['username'] ?? $row['name'] ?? $row['display_name'] ?? $row['displayName'] ?? $row['user'] ?? ''));
		$id = strtolower((string) ($row['dmrid'] ?? $row['dmr_id'] ?? $row['id'] ?? ''));
		$epoch = (string) ($row['last_tx_epoch'] ?? $row['tx_epoch'] ?? $row['last_talk_epoch'] ?? $row['last_seen_epoch'] ?? $row['last_seen'] ?? $row['timestamp'] ?? '');
		$key = trim($name . '|' . $id . '|' . $epoch, '|');
		if($key === '')
			$key = json_encode($row);
		if(isset($seen[$key]))
			continue;
		$seen[$key] = true;
		$clean[] = $row;
	}
	return $clean;
}

function asrBuiltinZelloRows(): array {
	$rows = [];
	foreach(ASR_ZELLO_SOURCE_FILES as $file) {
		if(!is_readable($file))
			continue;
		$mtime = (int) filemtime($file);
		if($mtime <= 0 || time() - $mtime > ASR_CLIENT_MAX_SEEN_AGE)
			continue;
		$payload = asrReadJson($file);
		if($payload === [])
			continue;
		$name = trim((string) ($payload['current_user'] ?? $payload['currentUser'] ?? ''));
		$active = filter_var($payload['active'] ?? false, FILTER_VALIDATE_BOOLEAN);
		if($active && $name !== '') {
			$rows[] = [
				'name' => $name,
				'active' => true,
				'last_seen_epoch' => $mtime,
			];
		}
	}
	return asrDedupeClientRows(asrSanitizeClientRows($rows, 'zello'));
}

function asrLatestYsfLog(): string {
	$today = ASR_YSF_LOG_DIR . '/YSFReflector-' . gmdate('Y-m-d') . '.log';
	if(is_readable($today))
		return $today;

	$files = glob(ASR_YSF_LOG_DIR . '/YSFReflector*.log') ?: [];
	$files = array_filter($files, 'is_readable');
	usort($files, fn($left, $right) => filemtime($right) <=> filemtime($left));
	return $files[0] ?? '';
}

function asrYsfLogEpoch(string $timestamp): int {
	$epoch = strtotime($timestamp . ' UTC');
	return $epoch === false ? 0 : $epoch;
}

function asrBuiltinYsfRows(): array {
	$log = asrLatestYsfLog();
	if($log === '')
		return [];

	$lines = @file($log, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
	if(!is_array($lines))
		return [];

	$rows = [];
	$inLinkedBlock = false;
	foreach(array_slice($lines, -500) as $line) {
		if(!preg_match('/^[A-Z]:\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\s+(.*)$/', $line, $match))
			continue;

		$epoch = asrYsfLogEpoch($match[1]);
		$message = $match[2];
		if(str_starts_with($message, 'Currently linked repeaters/gateways:')) {
			$inLinkedBlock = true;
			continue;
		}

		if($inLinkedBlock && preg_match('/^\s*([A-Z0-9\/ -]{2,16})\s+:\s+([0-9a-fA-F:.]+):(\d+)\s+(\d+)\/(\d+)/', $message, $linked)) {
			$callsign = trim($linked[1]);
			$address = trim($linked[2]);
			if($address === '127.0.0.1' || $address === '::1')
				continue;
			if($callsign !== '') {
				$key = strtolower($callsign);
				$rows[$key]['callsign'] = $callsign;
				$rows[$key]['last_seen_epoch'] = max((int) ($rows[$key]['last_seen_epoch'] ?? 0), $epoch);
			}
			continue;
		}
		$inLinkedBlock = false;

	}

	return array_values($rows);
}

function asrHttpPayload(array $bridge, string $password): array {
	$url = trim((string) ($bridge['clientUrl'] ?? ''));
	if(!preg_match('#^https?://#i', $url))
		return [];

	$headers = ['Accept: application/json'];
	$username = trim((string) ($bridge['clientUsername'] ?? ''));
	if($username !== '' && $password !== '') {
		$headers[] = 'Authorization: Basic ' . base64_encode($username . ':' . $password);
	} elseif($password !== '') {
		$headers[] = 'Authorization: Bearer ' . $password;
	}

	$context = stream_context_create([
		'http' => [
			'method' => 'GET',
			'header' => implode("\r\n", $headers),
			'timeout' => 4,
			'ignore_errors' => true,
		],
	]);
	$raw = @file_get_contents($url, false, $context);
	if(!is_string($raw) || $raw === '')
		return [];
	$data = json_decode($raw, true);
	return is_array($data) ? $data : [];
}

function asrWriteJsonAtomic(string $path, array $payload): void {
	$dir = dirname($path);
	if(!is_dir($dir))
		mkdir($dir, 0775, true);
	$encoded = json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . PHP_EOL;
	if(is_readable($path) && (string) file_get_contents($path) === $encoded)
		return;
	$tmp = tempnam($dir, '.connected-clients.');
	if($tmp === false)
		throw new RuntimeException("Could not create temporary file in $dir.");
	file_put_contents($tmp, $encoded);
	chmod($tmp, 0664);
	if(!rename($tmp, $path)) {
		@unlink($tmp);
		throw new RuntimeException("Could not replace $path.");
	}
	chmod($path, 0664);
}

$config = asrReadJson(ASR_CONFIG_FILE);
$secrets = asrReadJson(ASR_SECRETS_FILE);
$bridges = is_array($config['bridges'] ?? null) ? $config['bridges'] : [];
$timerUnit = 'allscan-reimagined-bridge-clients.timer';
if(function_exists('posix_geteuid') && posix_geteuid() === 0) {
	if($bridges === [])
		@shell_exec('systemctl disable --now ' . escapeshellarg($timerUnit) . ' 2>/dev/null');
	else
		@shell_exec('systemctl enable --now ' . escapeshellarg($timerUnit) . ' 2>/dev/null');
}
$passwords = is_array($secrets['bridgeClientPasswords'] ?? null) ? $secrets['bridgeClientPasswords'] : [];
$output = getenv('ASR_CONNECTED_CLIENTS_JSON') ?: ASR_DEFAULT_OUTPUT;
$result = [];

foreach($bridges as $bridge) {
	if(!is_array($bridge))
		continue;
	$id = (string) ($bridge['id'] ?? '');
	if(!preg_match('/^[a-z][a-z0-9_-]{1,31}$/', $id))
		continue;

	$source = (string) ($bridge['clientSource'] ?? 'disabled');
	$payload = [];
	if($source === 'local_json') {
		$path = asrSafeLocalJsonPath((string) ($bridge['clientUrl'] ?? ''));
		if($path !== '')
			$payload = asrReadJson($path);
	} elseif($source === 'http_api') {
		$payload = asrHttpPayload($bridge, (string) ($passwords[$id] ?? ''));
	}

	$rows = asrSanitizeClientRows(asrClientRowsFromPayload($payload, $id), $id);
	if($rows === [] && $id === 'zello') {
		$rows = asrBuiltinZelloRows();
	}
	if($rows === [] && $id === 'ysf') {
		$rows = asrSanitizeClientRows(asrBuiltinYsfRows(), 'ysf');
	}
	$result[$id] = $rows;
}

asrWriteJsonAtomic($output, $result);
echo "Wrote connected clients to $output" . PHP_EOL;
