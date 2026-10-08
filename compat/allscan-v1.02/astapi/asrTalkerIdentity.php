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

function asrEnrichUrfTalker($row, $events, $astdbPath = '/var/lib/asterisk/astdb.txt') {
	if(!is_array($row) || (string)($row['node'] ?? '') !== '1001') return $row;
	$source = strtoupper(trim((string)($row['source'] ?? '')));
	$info = strtoupper(trim((string)($row['info'] ?? '')));
	$hasRealIdentity = $source !== '' && $source !== 'ALLSTAR'
		&& $info !== '' && !str_starts_with($info, 'URFWIL');
	$best = $hasRealIdentity ? ['callsign'=>$info, 'mode'=>$source] : null;
	$originalDescription = '';
	if(!$hasRealIdentity) {
		$originalInfo = trim((string)($row['info'] ?? ''));
		$originalDescription = trim((string)preg_replace('/^[A-Z0-9]{3,10}\s*/i', '', $originalInfo));
		$rowStart = (int)($row['started_epoch'] ?? 0);
		$rowEnd = (int)($row['event_epoch'] ?? $rowStart);
		if($rowStart <= 0) return $row;
		if($rowEnd <= 0) $rowEnd = $rowStart;
		$bestDistance = PHP_INT_MAX;
		$bestOverlap = -1;
		foreach(asrUrfTalkerTransmissions($events) as $tx) {
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
	if(!is_array($best)) return $row;
	$row['info'] = (string)$best['callsign'];
	$row['source'] = (string)$best['mode'];
	$row['transport'] = 'URFWIL';
	$row['identity_provenance'] = 'urf_lifecycle';
	if(trim((string)($row['description'] ?? '')) === '') {
		$row['description'] = $originalDescription !== '' ? $originalDescription : 'Multi-Mode Bridge';
	}
	if(trim((string)($row['location'] ?? '')) === '') {
		$location = asrTalkerCallsignLocation((string)$best['callsign'], $astdbPath);
		if($location !== '') $row['location'] = $location;
	}
	return $row;
}

function asrEnrichUrfTalkers($rows, $events, $astdbPath = '/var/lib/asterisk/astdb.txt') {
	return array_map(static fn($row) => asrEnrichUrfTalker($row, $events, $astdbPath), (array)$rows);
}
