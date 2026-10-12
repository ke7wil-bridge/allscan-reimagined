<?php

function asrUrfTalkerEvents($path = '/run/urf-wil/asr-live-events.jsonl') {
	static $cache = [];
	$stat = @stat($path);
	if(!is_array($stat) || !is_readable($path)) return [];
	$key = $path . ':' . (int)($stat['size'] ?? 0) . ':' . (int)($stat['mtime'] ?? 0);
	if(isset($cache[$key])) return $cache[$key];

	$handle = @fopen($path, 'rb');
	if($handle === false) return [];
	$size = (int)($stat['size'] ?? 0);
	$read = min($size, 262144);
	if($read < $size) @fseek($handle, -$read, SEEK_END);
	$raw = (string)@fread($handle, $read);
	@fclose($handle);
	if($read < $size) $raw = substr($raw, (int)strpos($raw, "\n") + 1);

	$events = [];
	foreach(preg_split('/\r?\n/', trim($raw)) ?: [] as $line) {
		$event = json_decode((string)$line, true);
		$name = strtolower(trim((string)($event['event'] ?? '')));
		$mode = strtoupper(trim((string)($event['mode'] ?? '')));
		if(str_starts_with($mode, 'DMR')) $mode = 'DMR';
		$callsign = strtoupper(trim((string)($event['callsign'] ?? $event['client'] ?? '')));
		$epoch = (int)($event['epoch'] ?? 0);
		if(!in_array($name, ['tx_start', 'tx_stop'], true)
			|| !in_array($mode, ['DMR', 'YSF', 'P25', 'NXDN', 'M17'], true)
			|| $epoch <= 0 || $callsign === '' || $callsign === '-') continue;
		$events[] = [
			'event' => $name,
			'mode' => $mode,
			'callsign' => $callsign,
			'module' => strtoupper(trim((string)($event['module'] ?? ''))),
			'epoch' => $epoch,
		];
	}
	$cache = [$key => $events];
	return $events;
}

function asrUrfTalkerTransmissions($events) {
	$open = [];
	$transmissions = [];
	foreach((array)$events as $event) {
		if(!is_array($event)) continue;
		$key = (string)($event['mode'] ?? '') . '|' . (string)($event['module'] ?? '');
		if(($event['event'] ?? '') === 'tx_start') {
			$open[$key] = $event;
			continue;
		}
		if(($event['event'] ?? '') !== 'tx_stop') continue;
		$start = is_array($open[$key] ?? null) ? $open[$key] : $event;
		$transmissions[] = [
			'mode' => (string)($event['mode'] ?? $start['mode'] ?? ''),
			'callsign' => (string)($event['callsign'] ?? $start['callsign'] ?? ''),
			'start_epoch' => (int)($start['epoch'] ?? $event['epoch'] ?? 0),
			'event_epoch' => (int)($event['epoch'] ?? 0),
		];
		unset($open[$key]);
	}
	foreach($open as $event) {
		$transmissions[] = [
			'mode' => (string)($event['mode'] ?? ''),
			'callsign' => (string)($event['callsign'] ?? ''),
			'start_epoch' => (int)($event['epoch'] ?? 0),
			'event_epoch' => 0,
		];
	}
	return $transmissions;
}

function asrBridgeIdentityText($value) {
	$value = strtoupper(trim((string)$value));
	return in_array($value, ['', '-', '--', 'UNKNOWN', 'NONE', 'N/A', 'NULL'], true) ? '' : $value;
}

function asrBridgeTalkerEvidenceFromPayloads($config, $payloads, $zello = []) {
	$nodes = [];
	$transmissions = [];
	$seen = [];
	$bridges = [];
	foreach((array)($config['bridges'] ?? []) as $bridge) {
		if(!is_array($bridge)) continue;
		$id = trim((string)($bridge['id'] ?? ''));
		$node = trim((string)($bridge['node'] ?? ''));
		$mode = strtoupper(trim((string)($bridge['mode'] ?? '')));
		if($id === '' || !preg_match('/^[0-9]{3,10}$/D', $node) || $mode === '') continue;
		$title = trim((string)($bridge['title'] ?? $bridge['friendlyName'] ?? ''));
		if($title === '') $title = $mode . ' Bridge';
		$bridges[$id] = ['node'=>$node, 'mode'=>$mode, 'title'=>$title];
		$nodes[$node] = ['mode'=>$mode, 'title'=>$title];
	}
	$append = static function($bridge, $callsign, $start, $end = 0) use (&$transmissions, &$seen) {
		$callsign = asrBridgeIdentityText($callsign);
		$start = (int)$start;
		$end = (int)$end;
		if($callsign === '' || $start <= 0) return;
		$key = $bridge['node'] . '|' . $callsign . '|' . $start . '|' . $end;
		if(isset($seen[$key])) return;
		$seen[$key] = true;
		$transmissions[] = [
			'node'=>(string)$bridge['node'],
			'mode'=>(string)$bridge['mode'],
			'title'=>(string)$bridge['title'],
			'callsign'=>$callsign,
			'start_epoch'=>$start,
			'event_epoch'=>$end,
		];
	};
	foreach($bridges as $id => $bridge) {
		foreach((array)$payloads as $payload) {
			if(!is_array($payload)) continue;
			$entry = $payload['bridges'][$id] ?? null;
			if(!is_array($entry)) continue;
			$entryNode = trim((string)($entry['node'] ?? ''));
			if($entryNode !== '' && $entryNode !== $bridge['node']) continue;
			foreach((array)($entry['tx_events'] ?? $entry['recentTalkers'] ?? []) as $event) {
				if(!is_array($event)) continue;
				$end = (int)($event['epoch'] ?? $event['event_epoch'] ?? $event['eventEpoch'] ?? $event['last_tx_epoch'] ?? 0);
				$start = (int)($event['start_epoch'] ?? $event['started_epoch'] ?? $event['startEpoch'] ?? 0);
				if($start <= 0 && $end > 0)
					$start = max(1, $end - max(0, (int)round((float)($event['duration_seconds'] ?? $event['duration'] ?? 0))));
				$append($bridge, $event['callsign'] ?? $event['call'] ?? $event['name'] ?? $event['user'] ?? $event['talker'] ?? '', $start, $end);
			}
			$role = strtolower(trim((string)($entry['role'] ?? '')));
			if($role === 'source' || !empty($entry['active'])) {
				$append($bridge, $entry['current_user'] ?? $entry['caller'] ?? $entry['talker'] ?? '', $entry['active_start_epoch'] ?? $entry['inboundStartEpoch'] ?? $entry['activity_epoch'] ?? $entry['eventEpoch'] ?? 0, 0);
			}
			$lastEpoch = (int)($entry['last_source_epoch'] ?? $entry['last_time_epoch'] ?? 0);
			$append($bridge, $entry['last_source_user'] ?? $entry['last_user'] ?? '', $lastEpoch, $lastEpoch);
			foreach((array)($entry['recent_users'] ?? []) as $recent) {
				if(!is_array($recent)) continue;
				$epoch = (int)($recent['last_tx_epoch'] ?? $recent['last_seen_epoch'] ?? 0);
				$append($bridge, $recent['callsign'] ?? $recent['name'] ?? '', $epoch, $epoch);
			}
		}
		if($bridge['mode'] === 'ZELLO' && is_array($zello)) {
			$current = $zello['current_user'] ?? '';
			foreach((array)($zello['recent_users'] ?? []) as $recent) {
				if(!is_array($recent)) continue;
				$epoch = (int)($recent['last_seen_epoch'] ?? 0);
				$append($bridge, $recent['callsign'] ?? $recent['name'] ?? '', $epoch, $epoch);
				if(!empty($zello['active']) && strcasecmp((string)($recent['name'] ?? ''), (string)$current) === 0)
					$append($bridge, $current, $epoch, 0);
			}
		}
	}
	return ['nodes'=>$nodes, 'transmissions'=>$transmissions];
}

function asrTalkerJson($path) {
	$stat = @stat($path);
	if(is_link($path) || !is_array($stat) || !is_readable($path)
		|| (((int)($stat['mode'] ?? 0)) & 0170000) !== 0100000
		|| (int)($stat['size'] ?? -1) < 0 || (int)($stat['size'] ?? 0) > 2097152) return [];
	$decoded = json_decode((string)@file_get_contents($path), true);
	return is_array($decoded) ? $decoded : [];
}

function asrBridgeTalkerEvidence($configPath = '/etc/allscan-reimagined/config.json') {
	static $cache = null;
	static $cacheSecond = 0;
	$now = time();
	if(is_array($cache) && $cacheSecond === $now) return $cache;
	$paths = [
		'/run/allscan-reimagined-standard-bridge-status/bridge-live.json',
		'/run/allscan-reimagined-bridge-control/bridge-live.json',
		'/run/allscan-reimagined-ysf-bridge-control/ysf-live.json',
		'/run/allscan-reimagined-p25-bridge-control/status.json',
		'/run/allscan-reimagined-nxdn-bridge-control/status.json',
		'/run/allscan-reimagined-m17-bridge-control/status.json',
	];
	$payloads = array_map(static fn($path) => asrTalkerJson($path), $paths);
	$webRoot = dirname(dirname(__DIR__));
	$zello = asrTalkerJson($webRoot . '/allscan/zello-talkers.json');
	$cache = asrBridgeTalkerEvidenceFromPayloads(asrTalkerJson($configPath), $payloads, $zello);
	$cacheSecond = $now;
	return $cache;
}

function asrTalkerCallsignLocation($callsign, $path = '/var/lib/asterisk/astdb.txt') {
	static $cache = [];
	$callsign = strtoupper(trim((string)$callsign));
	$stat = @stat($path);
	if($callsign === '' || !is_array($stat) || !is_readable($path)) return '';
	$key = $path . ':' . (int)($stat['mtime'] ?? 0) . ':' . $callsign;
	if(array_key_exists($key, $cache)) return $cache[$key];
	$locations = [];
	$handle = @fopen($path, 'rb');
	if($handle !== false) {
		while(($line = fgets($handle)) !== false) {
			$parts = explode('|', trim($line), 4);
			if(count($parts) < 4 || strtoupper(trim($parts[1])) !== $callsign) continue;
			$location = trim($parts[3]);
			if($location !== '') $locations[strtoupper($location)] = $location;
		}
		@fclose($handle);
	}
	return $cache[$key] = count($locations) === 1 ? (string)reset($locations) : '';
}

function asrEnrichAllStarTalker($row, $astdb) {
	if(!is_array($row) || !is_array($astdb)) return $row;
	$node = trim((string)($row['node'] ?? ''));
	// These private transport nodes have a separate evidence-based identity
	// path. Never replace their real digital caller with an ASTDB label.
	if($node === '' || in_array($node, ['1001', '1999'], true)) return $row;
	$record = $astdb[$node] ?? null;
	if(!is_array($record) || count($record) < 4) return $row;

	$callsign = trim((string)($record[1] ?? ''));
	$description = trim((string)($record[2] ?? ''));
	$location = trim((string)($record[3] ?? ''));
	if($callsign !== '') $row['info'] = $callsign;
	if($description !== '') $row['description'] = $description;
	if($location !== '') $row['location'] = $location;
	if(trim((string)($row['source'] ?? '')) === '') $row['source'] = 'AllStar';
	if($callsign !== '' || $description !== '' || $location !== '')
		$row['identity_provenance'] = 'asl_node_database';
	return $row;
}

function asrEnrichAllStarTalkers($rows, $astdb) {
	return array_map(static fn($row) => asrEnrichAllStarTalker($row, $astdb), (array)$rows);
}

function asrEnrichBridgeTalker($row, $events, $astdbPath = '/var/lib/asterisk/astdb.txt', $bridgeEvidence = []) {
	if(!is_array($row)) return $row;
	$transportNode = (string)($row['node'] ?? '');
	$bridgeNode = is_array($bridgeEvidence['nodes'][$transportNode] ?? null)
		? $bridgeEvidence['nodes'][$transportNode] : null;
	$isLegacyPrivateTransport = in_array($transportNode, ['1001', '1999'], true);
	if($bridgeNode === null && !$isLegacyPrivateTransport) return $row;
	$source = strtoupper(trim((string)($row['source'] ?? '')));
	$info = strtoupper(trim((string)($row['info'] ?? '')));
	$hasRealIdentity = $source !== '' && $source !== 'ALLSTAR'
		&& $info !== '' && !str_starts_with($info, 'URFWIL');
	$best = $hasRealIdentity ? [
		'callsign'=>$info,
		'mode'=>$source,
		'title'=>(string)($bridgeNode['title'] ?? 'Multi-Mode Bridge'),
		'transport'=>(string)($bridgeNode['title'] ?? ($transportNode === '1999' ? 'Net Bridge' : 'URFWIL')),
	] : null;
	$originalDescription = '';
	if(!$hasRealIdentity) {
		$originalInfo = trim((string)($row['info'] ?? ''));
		$originalDescription = trim((string)preg_replace('/^[A-Z0-9]{3,10}\s*/i', '', $originalInfo));
		$rowStart = (int)($row['started_epoch'] ?? 0);
		$rowEnd = (int)($row['event_epoch'] ?? $rowStart);
		if($rowStart <= 0) return null;
		if($rowEnd <= 0) $rowEnd = $rowStart;
		$bestDistance = PHP_INT_MAX;
		$bestOverlap = -1;
		$candidates = [];
		foreach((array)($bridgeEvidence['transmissions'] ?? []) as $tx) {
			if(is_array($tx) && (string)($tx['node'] ?? '') === $transportNode) $candidates[] = $tx;
		}
		// URFWIL owns the reflector lifecycle journal. Node 1999 is a separate
		// Net Bridge transport, so never guess its caller from URFWIL events.
		if($bridgeNode === null && $transportNode === '1001')
			$candidates = array_merge($candidates, asrUrfTalkerTransmissions($events));
		foreach($candidates as $tx) {
			$txStart = (int)($tx['start_epoch'] ?? 0);
			$txEnd = (int)($tx['event_epoch'] ?? 0);
			if($txStart <= 0) continue;
			if($txEnd <= 0) $txEnd = max($rowEnd, $txStart);
			$overlap = min($rowEnd, $txEnd) - max($rowStart, $txStart);
			$distance = $overlap >= 0 ? 0 : min(abs($rowStart - $txEnd), abs($rowEnd - $txStart));
			if($distance > 3) continue;
			if($overlap > $bestOverlap || ($overlap === $bestOverlap && $distance < $bestDistance)) {
				$best = $tx;
				$bestOverlap = $overlap;
				$bestDistance = $distance;
			}
		}
	}
	// A private transport is never a station identity. If we cannot prove the
	// actual caller, suppress the transport row instead of persisting a fake
	// URFWIL/Unified talker into Last Talkers.
	if(!is_array($best)) return null;
	$row['info'] = (string)$best['callsign'];
	$row['source'] = (string)$best['mode'];
	$row['transport'] = (string)($best['transport'] ?? $best['title'] ?? ($transportNode === '1999' ? 'Net Bridge' : 'URFWIL'));
	$row['identity_provenance'] = $bridgeNode === null ? 'urf_lifecycle' : 'bridge_runtime';
	$description = trim((string)($row['description'] ?? ''));
	if($description === '' || strtoupper($description) === 'BRIDGE') {
		$row['description'] = trim((string)($best['title'] ?? '')) !== ''
			? (string)$best['title']
			: ($originalDescription !== '' ? $originalDescription : 'Multi-Mode Bridge');
	}
	if(trim((string)($row['location'] ?? '')) === '') {
		$location = asrTalkerCallsignLocation((string)$best['callsign'], $astdbPath);
		if($location !== '') $row['location'] = $location;
	}
	return $row;
}

function asrEnrichBridgeTalkers($rows, $events, $astdbPath = '/var/lib/asterisk/astdb.txt', $bridgeEvidence = []) {
	$enriched = array_map(static fn($row) => asrEnrichBridgeTalker($row, $events, $astdbPath, $bridgeEvidence), (array)$rows);
	return array_values(array_filter($enriched, static fn($row) => is_array($row)));
}

function asrSanitizeTalkerFeed($current, $events, $astdbPath = '/var/lib/asterisk/astdb.txt', $bridgeEvidence = []) {
	if(!is_array($current)) return [];
	foreach($current as $node => $entry) {
		if(!is_array($entry)) continue;
		if(array_key_exists('current_talker', $entry))
			$entry['current_talker'] = asrEnrichBridgeTalker($entry['current_talker'], $events, $astdbPath, $bridgeEvidence);
		if(array_key_exists('last_talkers', $entry))
			$entry['last_talkers'] = asrEnrichBridgeTalkers($entry['last_talkers'], $events, $astdbPath, $bridgeEvidence);
		$current[$node] = $entry;
	}
	return $current;
}

// Backward-compatible names for older callers and installer payloads.
function asrEnrichUrfTalker($row, $events, $astdbPath = '/var/lib/asterisk/astdb.txt') {
	return asrEnrichBridgeTalker($row, $events, $astdbPath);
}
function asrEnrichUrfTalkers($rows, $events, $astdbPath = '/var/lib/asterisk/astdb.txt') {
	return asrEnrichBridgeTalkers($rows, $events, $astdbPath);
}
