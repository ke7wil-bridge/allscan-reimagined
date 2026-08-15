#!/usr/bin/env php
<?php
declare(strict_types=1);

const ASR_CONFIG_FILE = '/etc/allscan-reimagined/config.json';
const ASR_SECRETS_FILE = '/etc/allscan-reimagined/secrets.json';
const ASR_LOCK_FILE = '/run/allscan-reimagined/bridge-clients.lock';
const ASR_ZELLO_SOURCE_FILES = [
	'/var/www/html/asr/zello-talkers.json',
	'/var/www/html/asr/zello-status-data.json',
	'/var/www/html/asr/zello-stream-debug.json',
	'/var/www/html/allscan/zello-talkers.json',
	'/var/www/html/allscan/zello-status-data.json',
	'/var/www/html/allscan/zello-stream-debug.json',
	'/srv/http/asr/zello-talkers.json',
	'/srv/http/asr/zello-status-data.json',
	'/srv/http/asr/zello-stream-debug.json',
	'/srv/http/allscan/zello-talkers.json',
	'/srv/http/allscan/zello-status-data.json',
	'/srv/http/allscan/zello-stream-debug.json',
];
const ASR_YSF_LOG_DIR = '/var/log/YSFReflector';
const ASR_CLIENT_MAX_SEEN_AGE = 180;
const ASR_CLIENT_MAX_TALK_AGE = 300;
const ASR_CLOCK_FUTURE_TOLERANCE = 300;
const ASR_REFLECTOR_SNAPSHOT_MAX_AGE = 180;
const ASR_M17_JSON_MAX_AGE = 45;
const ASR_REFLECTOR_LOG_TAIL_BYTES = 2097152;
const ASR_REFLECTOR_LOG_TAIL_LINES = 12000;
const ASR_DMR_CONFIG_GLOBS = [
	'/opt/MMDVM_Bridge*/MMDVM_Bridge.ini',
	'/etc/MMDVM_Bridge*.ini',
	'/etc/mmdvmbridge*.ini',
];

function asrAcquireLock() {
	$lock = @fopen(ASR_LOCK_FILE, 'c');
	if($lock === false)
		throw new RuntimeException('Could not open bridge-client collection lock.');
	if(!@flock($lock, LOCK_EX | LOCK_NB)) {
		fclose($lock);
		return false;
	}
	return $lock;
}

function asrPathWithin(string $path, string $root): bool {
	$root = rtrim($root, '/');
	return $path === $root || strpos($path, $root . '/') === 0;
}

function asrCanonicalPlannedPath(string $path): string {
	$cursor = rtrim($path, '/');
	if($cursor === '')
		$cursor = '/';
	$suffix = [];
	while(!file_exists($cursor) && !is_link($cursor)) {
		$parent = dirname($cursor);
		if($parent === $cursor)
			throw new RuntimeException('Could not resolve managed path.');
		array_unshift($suffix, basename($cursor));
		$cursor = $parent;
	}
	$resolved = realpath($cursor);
	if($resolved === false)
		throw new RuntimeException('Could not resolve managed path.');
	return rtrim($resolved, '/') . ($suffix === [] ? '' : '/' . implode('/', $suffix));
}

function asrDetectedWebRoot(?array $candidateRoots = null): string {
	if($candidateRoots === null) {
		$override = trim((string) (getenv('ASR_WEB_ROOT') ?: ''));
		if($override !== '') {
			if(strpos($override, "\0") !== false || strpos($override, '/') !== 0)
				throw new RuntimeException('ASR web root must be an absolute path.');
			return rtrim((string) preg_replace('#/+#', '/', $override), '/');
		}
		$candidateRoots = ['/var/www/html', '/srv/http'];
	}
	foreach(['allscan', 'asr'] as $requiredDir) {
		foreach($candidateRoots as $root) {
			$root = rtrim((string) $root, '/');
			if($root !== '' && is_dir($root . '/' . $requiredDir))
				return $root;
		}
	}
	$fallback = rtrim((string) ($candidateRoots[0] ?? '/var/www/html'), '/');
	return $fallback !== '' ? $fallback : '/var/www/html';
}

function asrDefaultOutputPath(?string $webRoot = null): string {
	$webRoot = rtrim($webRoot ?? asrDetectedWebRoot(), '/');
	return $webRoot . '/asr/asr-connected-clients.json';
}

function asrManagedOutputPath(
	string $requested,
	?array $allowedRoots = null,
	?array $protectedRoots = null
): string {
	$requested = trim($requested);
	if($requested === '')
		$requested = asrDefaultOutputPath();
	if(strpos($requested, "\0") !== false || strpos($requested, '/') !== 0)
		throw new RuntimeException('Connected-client output must be an absolute path.');

	$normalized = preg_replace('#/+#', '/', $requested);
	if(!is_string($normalized) || preg_match('#(?:^|/)\.\.(?:/|$)#', $normalized))
		throw new RuntimeException('Connected-client output path is unsafe.');
	if(!preg_match('/\.json$/i', $normalized))
		throw new RuntimeException('Connected-client output must be a JSON file.');

	$allowedRoots ??= [
		'/var/www/html/asr',
		'/srv/http/asr',
		'/run/allscan-reimagined',
		'/var/lib/allscan-reimagined',
	];
	$protectedRoots ??= [
		'/var/www/html/allscan',
		'/srv/http/allscan',
	];
	$canonicalTarget = asrCanonicalPlannedPath($normalized);
	foreach($allowedRoots as $allowedRoot) {
		$allowedRoot = rtrim((string) $allowedRoot, '/');
		if($allowedRoot === '' || !asrPathWithin($normalized, $allowedRoot))
			continue;
		$canonicalAllowedRoot = asrCanonicalPlannedPath($allowedRoot);
		if(!asrPathWithin($canonicalTarget, $canonicalAllowedRoot))
			continue;
		foreach($protectedRoots as $protectedRoot) {
			$canonicalProtectedRoot = asrCanonicalPlannedPath(rtrim((string) $protectedRoot, '/'));
			if(asrPathWithin($canonicalTarget, $canonicalProtectedRoot))
				throw new RuntimeException('Connected-client output resolves inside the stock AllScan web root.');
		}
		return $normalized;
	}

	throw new RuntimeException('Connected-client output must stay in an ASR-owned path.');
}

function asrManagedBridges(array $bridges): array {
	return array_values(array_filter($bridges, static function(mixed $bridge): bool {
		if(!is_array($bridge))
			return false;
		$mode = asrBridgeMode($bridge);
		if($mode === 'dstar' || (string) ($bridge['cardType'] ?? 'standard') !== 'standard')
			return false;
		$source = (string) ($bridge['clientSource'] ?? 'auto');
		$url = trim((string) ($bridge['clientUrl'] ?? ''));
		$explicitSource = in_array($source, ['local_json', 'http_api'], true) && $url !== '';
		$builtInOwnedReflector = in_array($mode, ['p25', 'nxdn', 'm17'], true)
			&& in_array($source, ['auto', 'disabled'], true)
			&& $url === '';
		$autoDetectedSimpleSource = in_array($mode, ['ysf', 'zello'], true)
			&& in_array($source, ['auto', 'disabled'], true)
			&& $url === '';
		return $explicitSource || $builtInOwnedReflector || $autoDetectedSimpleSource;
	}));
}

function asrUsesBuiltinOwnedReflector(array $bridge): bool {
	return (string) ($bridge['cardType'] ?? 'standard') === 'standard'
		&& in_array(asrBridgeMode($bridge), ['p25', 'nxdn', 'm17'], true)
		&& in_array((string) ($bridge['clientSource'] ?? 'auto'), ['auto', 'disabled'], true)
		&& trim((string) ($bridge['clientUrl'] ?? '')) === '';
}

function asrAssertSelfTest(bool $condition, string $message): void {
	if(!$condition)
		throw new RuntimeException($message);
}

function asrSelfTest(): void {
	$tmp = sys_get_temp_dir() . '/asr-bridge-clients-test-' . getmypid() . '-' . bin2hex(random_bytes(4));
	if(!mkdir($tmp . '/srv/http/allscan', 0775, true) || !mkdir($tmp . '/srv/http/asr', 0775, true))
		throw new RuntimeException('Could not create bridge-client self-test directories.');
	try {
		$detectedRoot = asrDetectedWebRoot([$tmp . '/var/www/html', $tmp . '/srv/http']);
		asrAssertSelfTest(
			$detectedRoot === $tmp . '/srv/http',
			'Collector did not detect the /srv/http web root.'
		);
		asrAssertSelfTest(
			asrDefaultOutputPath($detectedRoot) === $tmp . '/srv/http/asr/asr-connected-clients.json',
			'Collector did not derive its output under /srv/http/asr.'
		);
		asrAssertSelfTest(
			asrLocalWebPathCandidate('/asr/feed.json', $detectedRoot) === $tmp . '/srv/http/asr/feed.json',
			'ASR-local source URL did not map under /srv/http.'
		);
		asrAssertSelfTest(
			asrLocalWebPathCandidate('/allscan/feed.json', $detectedRoot) === $tmp . '/srv/http/allscan/feed.json',
			'Stock source URL did not map under /srv/http.'
		);

		$allowedRoot = $tmp . '/allowed/asr';
		$stockRoot = $tmp . '/allowed/allscan';
		mkdir($allowedRoot, 0775, true);
		mkdir($stockRoot, 0775, true);
		$aliasRoot = $tmp . '/allowed/asr-alias';
		if(!symlink($stockRoot, $aliasRoot))
			throw new RuntimeException('Could not create bridge-client self-test symlink.');
		$rejectedAlias = false;
		try {
			asrManagedOutputPath(
				$aliasRoot . '/feed.json',
				[$aliasRoot],
				[$stockRoot]
			);
		} catch(RuntimeException $error) {
			$rejectedAlias = true;
		}
		asrAssertSelfTest($rejectedAlias, 'Stock-root symlink output bypass was accepted.');
	} finally {
		asrRemoveSelfTestTree($tmp);
	}

	asrAssertSelfTest(
		asrDefaultOutputPath('/var/www/html') === '/var/www/html/asr/asr-connected-clients.json',
		'Side-by-side collector output does not target the ASR web root.'
	);
	foreach([
		'/var/www/html/asr/feed.json',
		'/run/allscan-reimagined/feed.json',
		'/var/lib/allscan-reimagined/feed.json',
	] as $allowedOutput) {
		asrAssertSelfTest(
			asrManagedOutputPath($allowedOutput) === $allowedOutput,
			"ASR-owned output was rejected: $allowedOutput"
		);
	}
	foreach([
		'/var/www/html/allscan/feed.json',
		'/srv/http/allscan/feed.json',
		'/var/www/html/asr/../allscan/feed.json',
	] as $stockOutput) {
		$rejected = false;
		try {
			asrManagedOutputPath($stockOutput);
		} catch(RuntimeException $error) {
			$rejected = true;
		}
		asrAssertSelfTest($rejected, "Stock AllScan output was accepted: $stockOutput");
	}
	asrAssertSelfTest(
		asrLocalWebPathCandidate('/asr/feed.json', '/var/www/html') === '/var/www/html/asr/feed.json',
		'ASR-local client source URL was not mapped to the ASR web root.'
	);
	asrAssertSelfTest(
		asrLocalWebPathCandidate('/allscan/feed.json', '/var/www/html') === '/var/www/html/allscan/feed.json',
		'Stock AllScan client source URL compatibility was not preserved.'
	);
	asrAssertSelfTest(
		asrLocalWebPathCandidate('/etc/allscan-reimagined/feed.json', '/var/www/html') === '/etc/allscan-reimagined/feed.json',
		'Absolute ASR config source path was unexpectedly rewritten.'
	);
	$bridges = [
		['id' => 'dmr', 'clientSource' => 'disabled', 'clientUrl' => ''],
		['id' => 'ysf', 'clientSource' => 'local_json', 'clientUrl' => ''],
		['id' => 'zello', 'clientSource' => 'local_json', 'clientUrl' => '/var/www/html/allscan/zello-talkers.json'],
		['id' => 'dstar', 'clientSource' => 'http_api', 'clientUrl' => 'https://example.invalid/clients'],
		['id' => 'p25', 'mode' => 'p25', 'cardType' => 'standard', 'clientSource' => 'disabled', 'clientUrl' => ''],
		['id' => 'nxdn_net', 'mode' => 'nxdn', 'cardType' => 'nxdn_net', 'clientSource' => 'local_json', 'clientUrl' => '/asr/nxdn.json'],
		['id' => 'm17', 'mode' => 'm17', 'cardType' => 'standard', 'clientSource' => 'disabled', 'clientUrl' => ''],
	];
	$managed = asrManagedBridges($bridges);
	$ids = array_column($managed, 'id');
	asrAssertSelfTest($ids === ['zello', 'p25', 'm17'], 'Managed-source selection or Net/unsupported-mode exclusion self-test failed.');
	asrAssertSelfTest(
		asrUsesBuiltinOwnedReflector($managed[1])
			&& asrUsesBuiltinOwnedReflector($managed[2])
			&& !asrUsesBuiltinOwnedReflector($managed[0]),
		'Built-in owned-reflector eligibility self-test failed.'
	);

	$now = time();
	foreach(['dmr', 'ysf', 'zello', 'p25', 'm17', 'nxdn', 'unknown'] as $mode) {
		$stale = [
			'callsign' => strtoupper($mode) . 'OLD',
			'last_seen_epoch' => $now - ASR_CLIENT_MAX_SEEN_AGE - 1,
			'last_tx_epoch' => $now - ASR_CLIENT_MAX_TALK_AGE - 1,
		];
		asrAssertSelfTest(
			asrSanitizeClientRows([$stale], $mode) === [],
			"Stale $mode client was not removed."
		);
		$fresh = ['callsign' => strtoupper($mode) . 'NEW', 'last_seen_epoch' => $now - 5];
		asrAssertSelfTest(
			count(asrSanitizeClientRows([$fresh], $mode)) === 1,
			"Fresh $mode client was removed."
		);
	}
	asrAssertSelfTest(asrBridgeMode(['id' => 'ysf_netbridge']) === 'ysf', 'YSF instance mode was not normalized.');
	asrAssertSelfTest(asrBridgeMode(['id' => 'zello_primary']) === 'zello', 'Zello instance mode was not normalized.');

	$durationRow = asrSanitizeClientRows([['callsign' => 'N7CURRENT', 'connected' => '00:12:04']], 'dmr');
	asrAssertSelfTest($durationRow === [], 'Timestamp-free non-current client was retained.');
	$durationRow = asrSanitizeClientRows(
		[['callsign' => 'N7CURRENT', 'connected' => '00:12:04']],
		'dmr',
		true
	);
	asrAssertSelfTest(count($durationRow) === 1, 'Current connection-duration text was mistaken for a false state.');
	$disconnectedRow = asrSanitizeClientRows([['callsign' => 'N7OLD', 'connected' => false]], 'dmr');
	asrAssertSelfTest($disconnectedRow === [], 'Explicitly disconnected client was retained.');
	$stringDisconnectedRow = asrSanitizeClientRows(
		[['callsign' => 'N7OLD', 'connected' => '0']],
		'dmr',
		true
	);
	asrAssertSelfTest($stringDisconnectedRow === [], 'String-zero disconnected client was retained.');
	$falseLikeRows = asrSanitizeClientRows([
		['callsign' => 'N7FALSE', 'connected' => 'false'],
		['callsign' => 'N7OFF', 'connected' => 'off'],
		['callsign' => 'N7NONE', 'connected' => 'none'],
	], 'dmr', true);
	asrAssertSelfTest($falseLikeRows === [], 'False-like disconnected clients were retained.');
	$futureRow = asrSanitizeClientRows([['callsign' => 'N7FUTURE', 'last_seen_epoch' => $now + 3600]], 'dmr');
	asrAssertSelfTest($futureRow === [], 'Implausible future client timestamp was retained.');
	$identitylessRow = asrSanitizeClientRows([['last_seen_epoch' => $now]], 'dmr');
	asrAssertSelfTest($identitylessRow === [], 'Identityless client row was retained.');
	$currentWithoutTimestamp = asrSanitizeClientRows([['callsign' => 'N7LIVE']], 'dmr', true);
	asrAssertSelfTest(
		count($currentWithoutTimestamp) === 1,
		'Current connected-client feed required a timestamp.'
	);

	$rows = [];
	for($index = 0; $index < 125; $index++)
		$rows[] = ['callsign' => sprintf('N7%03d', $index), 'last_seen_epoch' => $now - 1];
	asrAssertSelfTest(count(asrSanitizeClientRows($rows, 'dmr')) === 125, 'Client list was capped.');

	$sorted = asrDedupeAndSortClientRows([
		['callsign' => 'ALPHA'],
		['callsign' => 'RECENT', 'last_tx_epoch' => $now - 2],
		['callsign' => 'OLDER', 'last_tx_epoch' => $now - 20],
		['callsign' => 'BRAVO'],
		['callsign' => 'RECENT', 'last_tx_epoch' => $now - 10],
	]);
	asrAssertSelfTest(
		array_column($sorted, 'callsign') === ['RECENT', 'OLDER', 'ALPHA', 'BRAVO'],
		'Recent-TX sorting or identity deduplication failed.'
	);
	$mixedIdentity = asrDedupeAndSortClientRows([
		['callsign' => '', 'name' => 'N7MIXED', 'last_seen_epoch' => $now - 2],
		['name' => 'N7MIXED', 'last_seen_epoch' => $now - 1],
	]);
	asrAssertSelfTest(count($mixedIdentity) === 1, 'Repeated mixed-field client identity was not deduplicated.');

	$secretRow = asrSanitizeClientRows([[
		'callsign' => 'N7SAFE',
		'last_seen_epoch' => $now,
		'token' => 'do-not-copy',
		'password' => 'do-not-copy',
		'authorization' => 'do-not-copy',
	]], 'dmr');
	asrAssertSelfTest(
		count($secretRow) === 1
			&& !isset($secretRow[0]['token'])
			&& !isset($secretRow[0]['password'])
			&& !isset($secretRow[0]['authorization']),
		'Secret-bearing fields escaped client-row sanitization.'
	);

	$ports = asrDmrPortsFromIni(<<<'INI'
[DMR Network]
Address=example.invalid
Port=62033

[DMR Network 2]
RemotePort=62031

[General]
Port=1234
INI);
	asrAssertSelfTest($ports === [62031, 62033], 'Configured DMR port parsing failed.');
	$portPayload = [
		'clients' => [['callsign' => 'UNSCOPED']],
		'connected_clients' => [
			'62031' => [['callsign' => 'WRONGPORT']],
			'62033' => [['callsign' => 'RIGHTPORT']],
		],
	];
	$portRows = asrClientRowsFromPayload($portPayload, 'dmr', [62033]);
	asrAssertSelfTest(
		count($portRows) === 1 && ($portRows[0]['callsign'] ?? '') === 'RIGHTPORT',
		'Configured DMR payload-port selection failed.'
	);
	$multiPortRows = asrClientRowsFromPayload($portPayload, 'dmr', [62031, 62033]);
	asrAssertSelfTest(
		array_column($multiPortRows, 'callsign') === ['WRONGPORT', 'RIGHTPORT'],
		'Multiple configured DMR ports were not collected without first-port bias.'
	);
	$emptyPortRows = asrClientRowsFromPayload([
		'clients' => [['callsign' => 'PHANTOM']],
		'connected_clients' => ['62033' => []],
	], 'dmr', [62033]);
	asrAssertSelfTest($emptyPortRows === [], 'Empty configured DMR port fell back to a generic list.');
	$wrongOnlyPortRows = asrClientRowsFromPayload([
		'clients' => [['callsign' => 'PHANTOM']],
		'connected_clients' => [
			'62031' => [['callsign' => 'WRONGPORT']],
		],
	], 'dmr', [62033]);
	asrAssertSelfTest(
		$wrongOnlyPortRows === [],
		'Wrong-only DMR port map fell back to a generic list.'
	);
	$fallbackKind = '';
	$fallbackRows = asrClientRowsFromPayload(
		['clients' => [['callsign' => 'ENDPOINTSCOPED']]],
		'dmr',
		[62033],
		$fallbackKind
	);
	asrAssertSelfTest(
		array_column($fallbackRows, 'callsign') === ['ENDPOINTSCOPED'] && $fallbackKind === 'current',
		'Port-key-free current endpoint did not use its generic client list.'
	);
	$nestedPortRows = asrClientRowsFromPayload([
		'connected_clients' => [
			'dmr' => [
				'62031' => [['callsign' => 'NESTEDWRONG']],
				'62033' => [['callsign' => 'NESTEDRIGHT']],
			],
		],
	], 'dmr', [62033]);
	asrAssertSelfTest(
		array_column($nestedPortRows, 'callsign') === ['NESTEDRIGHT'],
		'Bridge-nested configured DMR port selection failed.'
	);
	$recentKind = '';
	$recentRows = asrClientRowsFromPayload([
		'recent_talkers' => [['callsign' => 'N7STALE']],
	], 'dmr', [], $recentKind);
	asrAssertSelfTest(
		$recentKind === 'recent' && asrSanitizeClientRows($recentRows, 'dmr', false) === [],
		'Timestamp-free recent-talker row was retained.'
	);
	$currentKind = '';
	$currentRows = asrClientRowsFromPayload([
		'users' => [['callsign' => 'N7CONNECTED']],
	], 'dmr', [], $currentKind);
	asrAssertSelfTest(
		$currentKind === 'current'
			&& count(asrSanitizeClientRows($currentRows, 'dmr', true)) === 1,
		'Timestamp-free current-user row was removed.'
	);
	$firstBridgePorts = asrConfiguredDmrPorts([
		'id' => 'dmr-primary',
		'mode' => 'dmr',
		'clientPort' => 62031,
		'clientUrl' => 'https://example.invalid:62033/clients',
	], true);
	$secondBridgePorts = asrConfiguredDmrPorts([
		'id' => 'dmr-secondary',
		'mode' => 'dmr',
		'clientPort' => 62033,
	], false);
	asrAssertSelfTest(
		$firstBridgePorts === [62031] && $secondBridgePorts === [62033],
		'Explicit DMR ports were combined across bridge cards.'
	);
	asrAssertSelfTest(
		asrDecodeHttpJsonPayload('{"clients":[{"callsign":"N7OK"}]}', ['HTTP/1.1 200 OK']) !== [],
		'Successful HTTP JSON was rejected.'
	);
	asrAssertSelfTest(
		asrDecodeHttpJsonPayload(
			'{"clients":[{"callsign":"N7STALE"}]}',
			['HTTP/1.1 503 Unavailable']
		) === [],
		'Non-2xx HTTP JSON was accepted.'
	);

	$m17Rows = asrM17RowsFromPayload([
		'Clients' => [
			['Callsign' => 'N7LOCAL N', 'IP' => '127.0.0.1', 'Protocol' => 'M17'],
			['Callsign' => 'N7REMOTE', 'IP' => '198.51.100.8', 'Protocol' => 'M17', 'LastHeardTime' => 1],
		],
		'Peers' => [['Callsign' => 'PEERDECOY', 'IP' => '198.51.100.9']],
		'Users' => [['Callsign' => 'USERDECOY', 'IP' => '198.51.100.10']],
	], ['127.0.0.1' => true, '::1' => true]);
	asrAssertSelfTest(
		is_array($m17Rows)
			&& array_column($m17Rows, 'callsign') === ['N7REMOTE']
			&& !isset($m17Rows[0]['IP'])
			&& !isset($m17Rows[0]['address']),
		'M17 current-client parsing, loopback exclusion, or address redaction failed.'
	);
	asrAssertSelfTest(asrM17RowsFromPayload(['Clients' => []]) === [], 'Empty current M17 client list was not authoritative.');
	asrAssertSelfTest(asrM17RowsFromPayload(['Users' => []]) === null, 'Invalid M17 schema was treated as current.');

	$logNow = strtotime('2026-08-11 20:02:30 UTC');
	$reflectorLines = [
		'M: 2026-08-11 20:00:30.000 Currently linked repeaters:',
		'M: 2026-08-11 20:00:30.000     N7LOCAL   : 127.0.0.1:12345 0/120',
		'M: 2026-08-11 20:00:30.000     N7OLD     : 198.51.100.11:41000 0/120',
		'M: 2026-08-11 20:01:00.000 Adding N7NEW     (198.51.100.12:42000)',
		'M: 2026-08-11 20:01:10.000 Removing N7OLD     (198.51.100.11:41000) unlinked',
	];
	$reflectorAvailable = false;
	$reflectorRows = asrReflectorRowsFromLogLines(
		$reflectorLines,
		'p25',
		(int) $logNow,
		['127.0.0.1' => true, '::1' => true],
		$reflectorAvailable
	);
	asrAssertSelfTest(
		$reflectorAvailable
			&& array_column($reflectorRows, 'callsign') === ['N7NEW']
			&& !isset($reflectorRows[0]['address']),
		'P25/NXDN snapshot, event replay, loopback exclusion, or address redaction failed.'
	);
	$emptyAvailable = false;
	$emptyRows = asrReflectorRowsFromLogLines(
		['M: 2026-08-11 20:02:00.000 No repeaters linked'],
		'nxdn',
		(int) $logNow,
		[],
		$emptyAvailable
	);
	asrAssertSelfTest($emptyAvailable && $emptyRows === [], 'Authoritative empty NXDN snapshot was rejected.');
	$incompleteAvailable = false;
	asrReflectorRowsFromLogLines(
		['M: 2026-08-11 20:02:00.000 Currently linked repeaters:'],
		'p25',
		(int) $logNow,
		[],
		$incompleteAvailable
	);
	asrAssertSelfTest(!$incompleteAvailable, 'Incomplete reflector snapshot was accepted.');
	$restartAvailable = false;
	asrReflectorRowsFromLogLines(
		array_merge($reflectorLines, ['M: 2026-08-11 20:02:05.000 Starting P25Reflector-20210912']),
		'p25',
		(int) $logNow,
		[],
		$restartAvailable
	);
	asrAssertSelfTest(!$restartAvailable, 'Pre-restart reflector snapshot was retained.');
	$ipv6Rows = asrM17RowsFromPayload([
		'Clients' => [
			['Callsign' => 'N7V6LOCAL', 'IP' => '::1', 'Protocol' => 'M17'],
			['Callsign' => 'N7V6REMOTE', 'IP' => '2001:db8::7', 'Protocol' => 'M17'],
		],
	], ['::1' => true]);
	asrAssertSelfTest(is_array($ipv6Rows) && array_column($ipv6Rows, 'callsign') === ['N7V6REMOTE'], 'IPv6 loopback exclusion failed.');

	echo "bridge-client source, freshness, owned-reflector, sorting, port, and redaction self-test: ok" . PHP_EOL;
}

function asrRemoveSelfTestTree(string $path): void {
	if(!is_dir($path) || is_link($path))
		return;
	foreach(scandir($path) ?: [] as $entry) {
		if($entry === '.' || $entry === '..')
			continue;
		$child = $path . '/' . $entry;
		if(is_dir($child) && !is_link($child))
			asrRemoveSelfTestTree($child);
		else
			@unlink($child);
	}
	@rmdir($path);
}

function asrReadJson(string $path): array {
	if(!is_readable($path))
		return [];
	$data = json_decode((string) file_get_contents($path), true);
	return is_array($data) ? $data : [];
}

function asrReadFreshJson(string $path, int $maxAge = ASR_CLIENT_MAX_SEEN_AGE): array {
	if(!is_readable($path))
		return [];
	$mtime = (int) @filemtime($path);
	if($mtime <= 0 || time() - $mtime > $maxAge)
		return [];
	return asrReadJson($path);
}

function asrLocalWebPathCandidate(string $requested, ?string $webRoot = null): string {
	$requested = trim($requested);
	if(strpos($requested, '/asr/') === 0 || strpos($requested, '/allscan/') === 0)
		return rtrim($webRoot ?? asrDetectedWebRoot(), '/') . $requested;
	return $requested;
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

	$candidate = asrLocalWebPathCandidate($requested);

	$real = realpath($candidate);
	if(!$real || !is_file($real) || !is_readable($real))
		return '';
	if(!preg_match('/\.(json|txt)$/i', $real))
		return '';

	$allowedDirs = array_filter([
		realpath('/var/www/html/asr'),
		realpath('/var/www/html/allscan'),
		realpath('/srv/http/asr'),
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

function asrPayloadPortKeys(int $port): array {
	return [(string) $port, 'port:' . $port, 'dmr:' . $port, 'tgif:' . $port];
}

function asrRowsAtPayloadKey(array $candidate, string $key): ?array {
	if(!array_key_exists($key, $candidate) || !is_array($candidate[$key]))
		return null;
	$rows = $candidate[$key];
	if(asrArrayIsList($rows))
		return $rows;
	return null;
}

function asrHasPortStructuredRows(array $candidate): bool {
	if(asrArrayIsList($candidate))
		return false;
	foreach($candidate as $key => $rows) {
		if(
			is_array($rows)
				&& preg_match('/^(?:\d+|(?:port|dmr|tgif):\d+)$/i', (string) $key)
		)
			return true;
	}
	return false;
}

function asrClientRowsFromPayload(
	array $payload,
	string $bridgeId,
	array $ports = [],
	?string &$feedKind = null
): array {
	$feedKind = 'unknown';
	$portRows = [];
	$portKeyFound = false;
	$portCandidates = [
		$payload,
		is_array($payload[$bridgeId] ?? null) ? $payload[$bridgeId] : [],
		is_array($payload['connected_clients'] ?? null) ? $payload['connected_clients'] : [],
		is_array($payload['connected_clients'][$bridgeId] ?? null)
			? $payload['connected_clients'][$bridgeId]
			: [],
		is_array($payload['clients'] ?? null) ? $payload['clients'] : [],
		is_array($payload['ports'] ?? null) ? $payload['ports'] : [],
		is_array($payload['data'] ?? null) ? $payload['data'] : [],
	];
	$portStructuredMapFound = false;
	foreach($portCandidates as $candidate) {
		if(asrHasPortStructuredRows($candidate)) {
			$portStructuredMapFound = true;
			break;
		}
	}
	foreach($ports as $port) {
		$port = (int) $port;
		if($port < 1 || $port > 65535)
			continue;
		foreach(asrPayloadPortKeys($port) as $key) {
			foreach($portCandidates as $candidate) {
				$rows = asrRowsAtPayloadKey($candidate, $key);
				if($rows !== null) {
					$portKeyFound = true;
					array_push($portRows, ...$rows);
				}
			}
		}
	}
	if($portKeyFound) {
		$feedKind = 'current';
		return $portRows;
	}
	if($portStructuredMapFound)
		return [];

	$candidates = [
		[$payload[$bridgeId] ?? null, 'current'],
		[$payload['connected_clients'][$bridgeId] ?? null, 'current'],
		[$payload['recent_users'] ?? null, 'recent'],
		[$payload['recent_talkers'] ?? null, 'recent'],
		[$payload['recentTalkers'] ?? null, 'recent'],
		[$payload['talkers'] ?? null, 'recent'],
		[$payload['clients'] ?? null, 'current'],
		[$payload['users'] ?? null, 'current'],
		[$payload['connected'] ?? null, 'current'],
		[$payload['active'] ?? null, 'current'],
		[$payload['rows'] ?? null, 'current'],
		[$payload['data'] ?? null, 'current'],
	];

	foreach($candidates as [$candidate, $candidateKind]) {
		if(!is_array($candidate))
			continue;
		if(asrArrayIsList($candidate)) {
			$feedKind = $candidateKind;
			return $candidate;
		}
		if(isset($candidate[$bridgeId]) && is_array($candidate[$bridgeId])) {
			$feedKind = $candidateKind;
			return $candidate[$bridgeId];
		}
	}

	if(asrArrayIsList($payload)) {
		$feedKind = 'current';
		return $payload;
	}
	return [];
}

function asrEpochValue(mixed $value): int {
	if(is_int($value) || is_float($value))
		return (int) $value;
	$text = trim((string) $value);
	if($text === '')
		return 0;
	if(preg_match('/^\d+(?:\.\d+)?$/', $text)) {
		$epoch = (float) $text;
		if($epoch >= 100000000000.0)
			$epoch /= 1000.0;
		return (int) floor($epoch);
	}
	$epoch = strtotime($text);
	return $epoch === false ? 0 : $epoch;
}

function asrExplicitBooleanState(array $row): ?bool {
	foreach(['active', 'current', 'connected'] as $key) {
		if(!array_key_exists($key, $row))
			continue;
		$value = $row[$key];
		if(is_bool($value))
			return $value;
		if(is_int($value) || is_float($value)) {
			if((float) $value === 1.0)
				return true;
			if((float) $value === 0.0)
				return false;
			continue;
		}
		$text = strtolower(trim((string) $value));
		if(in_array($text, ['1', 'true', 'yes', 'on', 'active', 'current', 'connected'], true))
			return true;
		if(in_array($text, ['0', '0.0', 'false', 'no', 'off', 'none', 'null', 'inactive', 'disconnected'], true))
			return false;
	}
	return null;
}

function asrTimestampIsFresh(int $epoch, int $maxAge, int $now): bool {
	if($epoch <= 0 || $epoch > $now + ASR_CLOCK_FUTURE_TOLERANCE)
		return false;
	return $now - $epoch <= $maxAge;
}

function asrClientRowIsFresh(array $row, string $bridgeId, bool $currentConnectedFeed = false): bool {
	$now = time();
	$lastSeen = asrEpochValue($row['last_seen_epoch'] ?? $row['last_seen'] ?? $row['timestamp'] ?? 0);
	$lastTalk = asrEpochValue($row['last_tx_epoch'] ?? $row['tx_epoch'] ?? $row['last_talk_epoch'] ?? 0);
	$currentState = asrExplicitBooleanState($row);
	if($currentState === false)
		return false;

	if($bridgeId === 'zello') {
		if($lastSeen > 0 || $lastTalk > 0)
			return asrTimestampIsFresh($lastSeen, ASR_CLIENT_MAX_SEEN_AGE, $now)
					|| asrTimestampIsFresh($lastTalk, ASR_CLIENT_MAX_SEEN_AGE, $now);
		return $currentConnectedFeed || $currentState === true;
	}

	if($bridgeId === 'ysf') {
		if($lastSeen > 0 || $lastTalk > 0)
			return asrTimestampIsFresh($lastSeen, ASR_CLIENT_MAX_SEEN_AGE, $now)
				|| asrTimestampIsFresh($lastTalk, ASR_CLIENT_MAX_TALK_AGE, $now);
		return $currentConnectedFeed || $currentState === true;
	}

	if($lastSeen > 0 || $lastTalk > 0)
		return asrTimestampIsFresh($lastSeen, ASR_CLIENT_MAX_SEEN_AGE, $now)
			|| asrTimestampIsFresh($lastTalk, ASR_CLIENT_MAX_TALK_AGE, $now);

	return $currentConnectedFeed || $currentState === true;
}

function asrClientRowHasIdentity(array $row): bool {
	if(asrClientName($row) !== '')
		return true;
	foreach(['dmrid', 'dmr_id', 'id', 'ip', 'address', 'host', 'remote_addr'] as $key) {
		if(trim((string) ($row[$key] ?? '')) !== '')
			return true;
	}
	return false;
}

function asrSanitizeClientRows(
	array $rows,
	string $bridgeId = '',
	bool $currentConnectedFeed = false
): array {
	$clean = [];
	foreach($rows as $row) {
		if(is_string($row)) {
			$value = trim($row);
			if(
				$value !== ''
				&& $currentConnectedFeed
				&& $bridgeId !== 'zello'
				&& $bridgeId !== 'ysf'
			)
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
		if(
			$item !== []
				&& asrClientRowHasIdentity($item)
				&& asrClientRowIsFresh($item, $bridgeId, $currentConnectedFeed)
		)
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

function asrClientName(array $row): string {
	foreach(['callsign', 'call', 'station', 'username', 'name', 'display_name', 'displayName', 'user', 'current_user'] as $key) {
		$value = trim((string) ($row[$key] ?? ''));
		if($value !== '')
			return $value;
	}
	return '';
}

function asrClientLastTalkEpoch(array $row): int {
	return asrEpochValue($row['last_tx_epoch'] ?? $row['tx_epoch'] ?? $row['last_talk_epoch'] ?? 0);
}

function asrClientActivityEpoch(array $row): int {
	return max(
		asrClientLastTalkEpoch($row),
		asrEpochValue($row['last_seen_epoch'] ?? $row['last_seen'] ?? $row['timestamp'] ?? 0)
	);
}

function asrClientIdentityKey(array $row): string {
	$name = strtolower(asrClientName($row));
	foreach(['dmrid', 'dmr_id', 'id'] as $key) {
		$id = strtolower(trim((string) ($row[$key] ?? '')));
		if($id !== '')
			return 'id:' . $id;
	}
	if($name !== '')
		return 'name:' . $name;
	$host = strtolower(trim((string) ($row['ip'] ?? $row['address'] ?? $row['host'] ?? $row['remote_addr'] ?? '')));
	if($host !== '')
		return 'host:' . $host;
	return 'row:' . hash('sha256', (string) json_encode($row));
}

function asrDedupeAndSortClientRows(array $rows): array {
	$byIdentity = [];
	foreach($rows as $row) {
		if(!is_array($row))
			continue;
		$key = asrClientIdentityKey($row);
		if(!isset($byIdentity[$key]) || asrClientActivityEpoch($row) > asrClientActivityEpoch($byIdentity[$key]))
			$byIdentity[$key] = $row;
	}
	$clean = array_values($byIdentity);
	usort($clean, static function(array $left, array $right): int {
		$leftTalk = asrClientLastTalkEpoch($left);
		$rightTalk = asrClientLastTalkEpoch($right);
		if($leftTalk !== $rightTalk)
			return $rightTalk <=> $leftTalk;
		return strcasecmp(asrClientName($left), asrClientName($right));
	});
	return $clean;
}

function asrTrustedReadableFile(string $path, bool $requireRootOwner = false): string {
	if($path === '' || strpos($path, "\0") !== false || $path[0] !== '/' || is_link($path))
		return '';
	$real = realpath($path);
	if($real === false || $real !== $path || !is_file($real) || !is_readable($real))
		return '';
	$stat = @lstat($real);
	if(!is_array($stat)
		|| (($stat['mode'] ?? 0) & 0170000) !== 0100000
		|| (int) ($stat['nlink'] ?? 0) !== 1
		|| (($stat['mode'] ?? 0) & 0022) !== 0
		|| ($requireRootOwner && (int) ($stat['uid'] ?? -1) !== 0))
		return '';

	$directory = dirname($real);
	while($directory !== '/') {
		$dirStat = @lstat($directory);
		if(!is_array($dirStat)
			|| (($dirStat['mode'] ?? 0) & 0170000) !== 0040000
			|| (($dirStat['mode'] ?? 0) & 0022) !== 0)
			return '';
		$parent = dirname($directory);
		if($parent === $directory)
			return '';
		$directory = $parent;
	}
	return $real;
}

function asrRunningReflectorConfig(string $binaryName): string {
	if(!preg_match('/^(?:P25Reflector|NXDNReflector|mrefd)$/D', $binaryName))
		return '';
	$configs = [];
	foreach(glob('/proc/[0-9]*/cmdline') ?: [] as $cmdlinePath) {
		$raw = @file_get_contents($cmdlinePath, false, null, 0, 65536);
		if(!is_string($raw) || $raw === '')
			continue;
		$arguments = array_values(array_filter(explode("\0", $raw), static fn(string $value): bool => $value !== ''));
		if($arguments === [] || basename($arguments[0]) !== $binaryName)
			continue;
		foreach(array_slice($arguments, 1) as $argument) {
			if(!is_string($argument) || !preg_match('#^/[^\0]+\.(?:ini|cfg|conf)$#iD', $argument))
				continue;
			$trusted = asrTrustedReadableFile($argument, true);
			if($trusted !== '')
				$configs[$trusted] = true;
		}
	}
	return count($configs) === 1 ? (string) array_key_first($configs) : '';
}

function asrSafeClientCallsign(mixed $value): string {
	$callsign = strtoupper(trim((string) $value));
	if(!preg_match('/^[A-Z0-9][A-Z0-9 .\/-]{1,15}$/D', $callsign))
		return '';
	return $callsign;
}

function asrEndpointHost(string $endpoint): string {
	$endpoint = trim($endpoint);
	if($endpoint === '')
		return '';
	if(preg_match('/^\[([^\]]+)\](?::\d+)?$/D', $endpoint, $match))
		return strtolower($match[1]);
	if(filter_var($endpoint, FILTER_VALIDATE_IP) !== false)
		return strtolower($endpoint);
	if(preg_match('/^(.+):(\d+)$/D', $endpoint, $match)
		&& filter_var($match[1], FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) !== false)
		return strtolower($match[1]);
	return '';
}

function asrLocalIpAddresses(): array {
	$addresses = ['127.0.0.1' => true, '::1' => true, '::ffff:127.0.0.1' => true];
	foreach(array_unique([gethostname() ?: '', php_uname('n')]) as $hostname) {
		if($hostname === '')
			continue;
		foreach(gethostbynamel($hostname) ?: [] as $address)
			$addresses[strtolower($address)] = true;
	}
	return $addresses;
}

function asrEndpointIsLocal(string $endpoint, ?array $localAddresses = null): bool {
	$host = asrEndpointHost($endpoint);
	if($host === '')
		return false;
	if(str_starts_with($host, '127.') || $host === '::1' || str_starts_with($host, '::ffff:127.'))
		return true;
	$localAddresses ??= asrLocalIpAddresses();
	return isset($localAddresses[$host]);
}

function asrM17RowsFromPayload(array $payload, array $localAddresses = []): ?array {
	if(!array_key_exists('Clients', $payload) || !is_array($payload['Clients']) || !asrArrayIsList($payload['Clients']))
		return null;
	$rows = [];
	foreach($payload['Clients'] as $client) {
		if(!is_array($client))
			continue;
		$callsign = asrSafeClientCallsign($client['Callsign'] ?? '');
		$address = trim((string) ($client['IP'] ?? ''));
		if($callsign === '' || $address === '' || asrEndpointIsLocal($address, $localAddresses))
			continue;
		$protocol = strtoupper(trim((string) ($client['Protocol'] ?? 'M17')));
		if($protocol !== '' && $protocol !== 'M17')
			continue;
		$rows[] = ['callsign' => $callsign, 'connected' => true];
	}
	return asrDedupeAndSortClientRows($rows);
}

function asrM17JsonPathFromConfig(string $configPath): string {
	$configPath = asrTrustedReadableFile($configPath, true);
	if($configPath === '')
		return '';
	foreach(@file($configPath, FILE_IGNORE_NEW_LINES) ?: [] as $line) {
		if(!preg_match('/^\s*JsonPath\s*=\s*(\/[^\0\r\n]+?)\s*$/iD', (string) $line, $match))
			continue;
		return asrTrustedReadableFile(trim($match[1]), false);
	}
	return '';
}

function asrBuiltinM17Rows(?bool &$available = null): array {
	$available = false;
	$configPath = asrRunningReflectorConfig('mrefd');
	$jsonPath = $configPath === '' ? '' : asrM17JsonPathFromConfig($configPath);
	if($jsonPath === '')
		return [];
	$mtime = (int) @filemtime($jsonPath);
	if($mtime <= 0 || time() - $mtime > ASR_M17_JSON_MAX_AGE || $mtime > time() + ASR_CLOCK_FUTURE_TOLERANCE)
		return [];
	$payload = asrReadJson($jsonPath);
	$rows = asrM17RowsFromPayload($payload);
	if($rows === null)
		return [];
	$available = true;
	return $rows;
}

function asrIniValue(array $payload, string $sectionName, string $keyName): string {
	foreach($payload as $section => $values) {
		if(strcasecmp((string) $section, $sectionName) !== 0 || !is_array($values))
			continue;
		foreach($values as $key => $value) {
			if(strcasecmp((string) $key, $keyName) === 0)
				return trim((string) $value);
		}
	}
	return '';
}

function asrReflectorLogPathFromConfig(string $configPath): string {
	$configPath = asrTrustedReadableFile($configPath, true);
	if($configPath === '')
		return '';
	$ini = @parse_ini_file($configPath, true, INI_SCANNER_RAW);
	if(!is_array($ini))
		return '';
	$directory = asrIniValue($ini, 'Log', 'FilePath');
	$root = asrIniValue($ini, 'Log', 'FileRoot');
	if(!preg_match('#^/[^\0]+$#D', $directory) || !preg_match('/^[A-Za-z0-9_.-]{1,100}$/D', $root))
		return '';
	$candidates = array_filter(glob(rtrim($directory, '/') . '/' . $root . '*.log') ?: [], 'is_file');
	usort($candidates, static fn(string $left, string $right): int => ((int) @filemtime($right)) <=> ((int) @filemtime($left)));
	if($candidates === [])
		return '';
	return asrTrustedReadableFile($candidates[0], false);
}

function asrTailLines(string $path): array {
	$path = asrTrustedReadableFile($path, false);
	if($path === '')
		return [];
	$handle = @fopen($path, 'rb');
	if($handle === false)
		return [];
	try {
		$size = (int) (@filesize($path) ?: 0);
		$offset = max(0, $size - ASR_REFLECTOR_LOG_TAIL_BYTES);
		if($offset > 0) {
			fseek($handle, $offset);
			fgets($handle);
		}
		$lines = [];
		while(($line = fgets($handle)) !== false)
			$lines[] = rtrim($line, "\r\n");
		if(count($lines) > ASR_REFLECTOR_LOG_TAIL_LINES)
			$lines = array_slice($lines, -ASR_REFLECTOR_LOG_TAIL_LINES);
		return $lines;
	} finally {
		fclose($handle);
	}
}

function asrReflectorLogLine(string $line): ?array {
	if(!preg_match('/^[A-Z]:\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\s+(.*)$/D', $line, $match))
		return null;
	$epoch = strtotime($match[1] . ' UTC');
	return $epoch === false ? null : [(int) $epoch, (string) $match[2]];
}

function asrReflectorRowsFromLogLines(
	array $lines,
	string $mode,
	int $now,
	array $localAddresses = [],
	?bool &$available = null
): array {
	$available = false;
	if(!in_array($mode, ['p25', 'nxdn'], true))
		return [];
	$state = [];
	$baselineEpoch = 0;
	$pendingEpoch = 0;
	$pendingRows = [];
	$pendingHeader = false;

	$finishPending = static function() use (&$state, &$baselineEpoch, &$pendingEpoch, &$pendingRows, &$pendingHeader): void {
		if(!$pendingHeader)
			return;
		if($pendingRows !== []) {
			$state = $pendingRows;
			$baselineEpoch = $pendingEpoch;
		}
		$pendingEpoch = 0;
		$pendingRows = [];
		$pendingHeader = false;
	};

	foreach($lines as $line) {
		$parsed = asrReflectorLogLine((string) $line);
		if($parsed === null)
			continue;
		[$epoch, $message] = $parsed;
		if($pendingHeader) {
			if($epoch === $pendingEpoch
				&& preg_match('/^\s*([A-Z0-9][A-Z0-9 .\/-]{1,15})\s+:\s+(\S+)\s+(\d+)\/(\d+)\s*$/D', $message, $rowMatch)) {
				$timer = (int) $rowMatch[3];
				$timeout = (int) $rowMatch[4];
				$callsign = asrSafeClientCallsign($rowMatch[1]);
				$endpoint = trim($rowMatch[2]);
				if($callsign !== '' && $endpoint !== '' && $timeout > 0 && $timer >= 0 && $timer < $timeout)
					$pendingRows[$endpoint] = $callsign;
				continue;
			}
			$finishPending();
		}

		if(preg_match('/^Starting\s+' . strtoupper($mode) . 'Reflector-/iD', $message)) {
			$state = [];
			$baselineEpoch = 0;
			continue;
		}
		if($message === 'Currently linked repeaters:') {
			$pendingHeader = true;
			$pendingEpoch = $epoch;
			$pendingRows = [];
			continue;
		}
		if($message === 'No repeaters linked') {
			$state = [];
			$baselineEpoch = $epoch;
			continue;
		}
		if($baselineEpoch <= 0 || $epoch < $baselineEpoch)
			continue;
		if(preg_match('/^Adding\s+(.+?)\s+\(([^)]+)\)\s*$/D', $message, $eventMatch)) {
			$callsign = asrSafeClientCallsign($eventMatch[1]);
			$endpoint = trim($eventMatch[2]);
			if($callsign !== '' && $endpoint !== '')
				$state[$endpoint] = $callsign;
			continue;
		}
		if(preg_match('/^Removing\s+.+?\s+\(([^)]+)\)\s+(?:unlinked|disappeared)\s*$/D', $message, $eventMatch))
			unset($state[trim($eventMatch[1])]);
	}
	$finishPending();

	if($baselineEpoch <= 0
		|| $baselineEpoch > $now + ASR_CLOCK_FUTURE_TOLERANCE
		|| $now - $baselineEpoch > ASR_REFLECTOR_SNAPSHOT_MAX_AGE)
		return [];
	$available = true;
	$rows = [];
	foreach($state as $endpoint => $callsign) {
		if(asrEndpointIsLocal((string) $endpoint, $localAddresses))
			continue;
		$rows[] = ['callsign' => (string) $callsign, 'connected' => true];
	}
	return asrDedupeAndSortClientRows($rows);
}

function asrBuiltinP25OrNxdnRows(string $mode, ?bool &$available = null): array {
	$available = false;
	$binary = $mode === 'p25' ? 'P25Reflector' : ($mode === 'nxdn' ? 'NXDNReflector' : '');
	if($binary === '')
		return [];
	$configPath = asrRunningReflectorConfig($binary);
	$logPath = $configPath === '' ? '' : asrReflectorLogPathFromConfig($configPath);
	if($logPath === '')
		return [];
	$mtime = (int) @filemtime($logPath);
	if($mtime <= 0 || $mtime > time() + ASR_CLOCK_FUTURE_TOLERANCE || time() - $mtime > ASR_REFLECTOR_SNAPSHOT_MAX_AGE)
		return [];
	return asrReflectorRowsFromLogLines(asrTailLines($logPath), $mode, time(), [], $available);
}

function asrBuiltinOwnedReflectorRows(string $mode, ?bool &$available = null): array {
	if($mode === 'm17')
		return asrBuiltinM17Rows($available);
	if(in_array($mode, ['p25', 'nxdn'], true))
		return asrBuiltinP25OrNxdnRows($mode, $available);
	$available = false;
	return [];
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
	return asrDedupeAndSortClientRows(asrSanitizeClientRows($rows, 'zello'));
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

function asrBridgeMode(array $bridge): string {
	foreach([$bridge['mode'] ?? '', $bridge['type'] ?? '', $bridge['id'] ?? ''] as $value) {
		$compact = preg_replace('/[^a-z0-9]/', '', strtolower(trim((string) $value)));
		foreach(['dstar', 'dmr', 'ysf', 'zello', 'p25', 'm17', 'nxdn'] as $knownMode) {
			if(str_starts_with((string) $compact, $knownMode))
				return $knownMode;
		}
	}
	return 'unknown';
}

function asrDmrPortsFromIni(string $contents): array {
	$ports = [];
	$section = '';
	foreach(preg_split('/\R/', $contents) ?: [] as $line) {
		$line = trim((string) preg_replace('/[;#].*$/', '', $line));
		if($line === '')
			continue;
		if(preg_match('/^\[([^\]]+)\]$/', $line, $match)) {
			$section = strtolower(trim($match[1]));
			continue;
		}
		if(!preg_match('/^(port|remoteport|masterport)\s*=\s*(\d{1,5})$/i', $line, $match))
			continue;
		if(!preg_match('/(dmr|tgif|network)/i', $section))
			continue;
		$port = (int) $match[2];
		if($port >= 1 && $port <= 65535)
			$ports[] = $port;
	}
	$ports = array_values(array_unique($ports));
	sort($ports, SORT_NUMERIC);
	return $ports;
}

function asrConfiguredDmrPorts(array $bridge, bool $allowGlobalDiscovery = true): array {
	if(asrBridgeMode($bridge) !== 'dmr')
		return [];

	$explicitPorts = [];
	foreach(['clientPort', 'dmrPort', 'masterPort', 'port'] as $key) {
		$port = (int) ($bridge[$key] ?? 0);
		if($port >= 1 && $port <= 65535)
			$explicitPorts[] = $port;
	}
	$explicitPorts = array_values(array_unique(array_map('intval', $explicitPorts)));
	sort($explicitPorts, SORT_NUMERIC);
	if($explicitPorts !== [])
		return $explicitPorts;

	$ports = [];
	$url = trim((string) ($bridge['clientUrl'] ?? ''));
	if($url !== '') {
		$urlPort = (int) (parse_url($url, PHP_URL_PORT) ?: 0);
		if($urlPort >= 1 && $urlPort <= 65535)
			$ports[] = $urlPort;
	}

	$ports = array_values(array_unique(array_map('intval', $ports)));
	sort($ports, SORT_NUMERIC);
	if($ports !== [] || !$allowGlobalDiscovery)
		return $ports;

	foreach(ASR_DMR_CONFIG_GLOBS as $pattern) {
		foreach(glob($pattern) ?: [] as $path) {
			if(!is_file($path) || !is_readable($path))
				continue;
			$contents = @file_get_contents($path);
			if(is_string($contents))
				array_push($ports, ...asrDmrPortsFromIni($contents));
		}
	}

	$ports = array_values(array_unique(array_map('intval', $ports)));
	sort($ports, SORT_NUMERIC);
	return $ports;
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

function asrHttpStatusCode(array $headers): int {
	$status = 0;
	foreach($headers as $header) {
		if(preg_match('#^HTTP/\S+\s+(\d{3})(?:\s|$)#i', trim((string) $header), $match))
			$status = (int) $match[1];
	}
	return $status;
}

function asrDecodeHttpJsonPayload(string $raw, array $headers): array {
	$status = asrHttpStatusCode($headers);
	if($status < 200 || $status >= 300 || $raw === '')
		return [];
	$data = json_decode($raw, true);
	return is_array($data) ? $data : [];
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
	$http_response_header = [];
	$raw = @file_get_contents($url, false, $context);
	if(!is_string($raw))
		return [];
	return asrDecodeHttpJsonPayload($raw, $http_response_header);
}

function asrWriteJsonAtomic(string $path, array $payload): void {
	$dir = dirname($path);
	if(!is_dir($dir))
		mkdir($dir, 0775, true);
	$encoded = json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . PHP_EOL;
	if(is_readable($path) && (string) file_get_contents($path) === $encoded) {
		@touch($path);
		@chmod($path, 0664);
		return;
	}
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

if(in_array('--self-test', $argv ?? [], true)) {
	asrSelfTest();
	exit(0);
}

$config = asrReadJson(ASR_CONFIG_FILE);
$secrets = asrReadJson(ASR_SECRETS_FILE);
$bridges = is_array($config['bridges'] ?? null) ? $config['bridges'] : [];
$bridges = asrManagedBridges($bridges);

$lock = asrAcquireLock();
if($lock === false) {
	echo "Bridge client collection is already running; timer state synchronized." . PHP_EOL;
	exit(0);
}

$passwords = is_array($secrets['bridgeClientPasswords'] ?? null) ? $secrets['bridgeClientPasswords'] : [];
$output = asrManagedOutputPath((string) (getenv('ASR_CONNECTED_CLIENTS_JSON') ?: ''));
$result = [];
$resultMeta = [];
$builtInModeCounts = ['p25' => 0, 'nxdn' => 0, 'm17' => 0];
foreach($bridges as $bridge) {
	if(!asrUsesBuiltinOwnedReflector($bridge))
		continue;
	$builtInModeCounts[asrBridgeMode($bridge)]++;
}
$dmrBridgeCount = count(array_filter(
	$bridges,
	static fn(array $bridge): bool => asrBridgeMode($bridge) === 'dmr'
));

foreach($bridges as $bridge) {
	$id = (string) ($bridge['id'] ?? '');
	if(!preg_match('/^[a-z][a-z0-9_-]{1,31}$/', $id))
		continue;
	$mode = asrBridgeMode($bridge);
	$ports = asrConfiguredDmrPorts($bridge, $dmrBridgeCount <= 1);

	$source = (string) ($bridge['clientSource'] ?? 'auto');
	$payload = [];
	$rows = [];
	$feedKind = '';
	$usedBuiltIn = false;
	if($source === 'local_json') {
		$path = asrSafeLocalJsonPath((string) ($bridge['clientUrl'] ?? ''));
		if($path !== '')
			$payload = asrReadFreshJson($path);
	} elseif($source === 'http_api') {
		$payload = asrHttpPayload($bridge, (string) ($passwords[$id] ?? ''));
	} elseif(asrUsesBuiltinOwnedReflector($bridge) && ($builtInModeCounts[$mode] ?? 0) === 1) {
		$available = false;
		$rows = asrBuiltinOwnedReflectorRows($mode, $available);
		$feedKind = $available ? 'current' : 'empty';
		$usedBuiltIn = true;
	}

	if(!$usedBuiltIn) {
		$payloadRows = asrClientRowsFromPayload($payload, $id, $ports, $feedKind);
		$rows = asrSanitizeClientRows($payloadRows, $mode, $feedKind === 'current');
	}
	if($rows === [] && $mode === 'zello') {
		$rows = asrBuiltinZelloRows();
		$feedKind = $rows === [] ? 'empty' : 'fallback';
	}
	if($rows === [] && $mode === 'ysf') {
		$rows = asrSanitizeClientRows(asrBuiltinYsfRows(), 'ysf');
		$feedKind = $rows === [] ? 'empty' : 'fallback';
	}
	$result[$id] = asrDedupeAndSortClientRows($rows);
	$resultMeta[$id] = [
		'kind' => in_array($feedKind, ['current', 'recent', 'fallback'], true) ? $feedKind : 'empty',
		'mode' => $mode,
	];
}

$result['_asr_meta'] = $resultMeta;
asrWriteJsonAtomic($output, $result);
echo "Wrote connected clients to $output" . PHP_EOL;
