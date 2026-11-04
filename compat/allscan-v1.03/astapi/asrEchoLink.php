<?php

function asrEchoLinkNodeNumber($node) {
	$text = trim((string)$node);
	if(!preg_match('/^3([0-9]{6})$/D', $text, $match))
		return '';
	$number = (int)$match[1];
	return $number > 0 ? (string)$number : '';
}

function asrEchoLinkConnectedCallsigns($output) {
	$rows = [];
	foreach(preg_split('/\R/', (string)$output) as $line) {
		if(!preg_match('/^\s*([0-9]{1,6})\s+([A-Z0-9*][A-Z0-9*\/-]{1,10})\s+\S+(?:\s+.*)?$/i', $line, $match))
			continue;
		$number = (int)$match[1];
		$callsign = strtoupper($match[2]);
		if($number <= 0 || !preg_match('/^[A-Z0-9*][A-Z0-9*\/-]{1,10}$/D', $callsign))
			continue;
		$rows[(string)$number] = $callsign;
	}
	return $rows;
}

function asrEchoLinkConnectedLabel($rows, $node) {
	$number = asrEchoLinkNodeNumber($node);
	if($number === '' || !is_array($rows) || !isset($rows[$number]))
		return '';
	return $rows[$number] . ' [EchoLink ' . $number . ']';
}

function asrEchoLinkOfficialCallsignFromHtml($html, $number) {
	$number = trim((string)$number);
	if(!preg_match('/^[0-9]{1,6}$/D', $number) || !is_string($html)) return '';
	$pattern = '~<tr[^>]*>\s*<td[^>]*>\s*([A-Z0-9*][A-Z0-9*\/-]{1,10})\s*</td>\s*<td[^>]*>\s*' . preg_quote($number, '~') . '\s*</td>\s*</tr>~i';
	if(preg_match($pattern, $html, $match) !== 1) return '';
	$callsign = strtoupper($match[1]);
	return preg_match('/^[A-Z0-9*][A-Z0-9*\/-]{1,10}$/D', $callsign) ? $callsign : '';
}
function asrEchoLinkOfficialLookup($number) {
	$number = trim((string)$number); if(!preg_match('/^[0-9]{1,6}$/D', $number)) return '';
	$context = stream_context_create(['http' => ['method' => 'POST', 'header' => "Content-Type: application/x-www-form-urlencoded\r\nConnection: close\r\n", 'content' => http_build_query(['call' => $number], '', '&', PHP_QUERY_RFC3986), 'timeout' => 2, 'ignore_errors' => true], 'ssl' => ['verify_peer' => true, 'verify_peer_name' => true]]);
	$html = @file_get_contents('https://www.echolink.org/validation/node_lookup.jsp', false, $context);
	return asrEchoLinkOfficialCallsignFromHtml($html, $number);
}
function asrEchoLinkOfficialLabelCached(&$cache, $node, $loader=null, $now=null) {
	$number = asrEchoLinkNodeNumber($node); if($number === '') return '';
	$checkTime = $now === null ? microtime(true) : (float)$now;
	if(isset($cache[$number]) && is_array($cache[$number]) && (float)($cache[$number]['expires'] ?? 0) > $checkTime) $callsign = (string)($cache[$number]['callsign'] ?? '');
	else { $lookup = is_callable($loader) ? $loader : 'asrEchoLinkOfficialLookup'; $callsign = (string)call_user_func($lookup, $number); $cache[$number] = ['expires' => $checkTime + ($callsign !== '' ? 3600.0 : 60.0), 'callsign' => $callsign]; }
	return $callsign === '' ? '' : $callsign . ' [EchoLink ' . $number . ']';
}

function asrEchoLinkConnectedOutput($amiOutput, $fallbackLoader, &$fallbackCache, $now = null) {
	$output = is_string($amiOutput) ? $amiOutput : '';
	if(count(asrEchoLinkConnectedCallsigns($output)) > 0)
		return $output;
	$checkTime = $now === null ? microtime(true) : (float)$now;
	if(is_array($fallbackCache)
		&& (float)($fallbackCache['expires'] ?? 0) > $checkTime
		&& is_string($fallbackCache['output'] ?? null)) {
		return $fallbackCache['output'];
	}
	$fallback = is_callable($fallbackLoader) ? call_user_func($fallbackLoader) : '';
	$output = is_string($fallback) ? $fallback : '';
	$fallbackCache = ['expires' => $checkTime + 10.0, 'output' => $output];
	return $output;
}

function asrEchoLinkCliOutput() {
	$lines = [];
	$status = 1;
	exec(
		'/usr/bin/sudo -n /usr/local/sbin/allscan-reimagined-asterisk-read echolink-nodes 2>/dev/null',
		$lines,
		$status
	);
	return $status === 0 ? implode("\n", $lines) : '';
}

function asrEchoLinkConnectedRowsCached(&$cache, $loader, $now = null) {
	$checkTime = $now === null ? microtime(true) : (float)$now;
	if(is_array($cache)
		&& (float)($cache['expires'] ?? 0) > $checkTime
		&& is_array($cache['rows'] ?? null)) {
		return $cache['rows'];
	}
	$output = is_callable($loader) ? call_user_func($loader) : '';
	$refreshedAt = $now === null ? microtime(true) : $checkTime;
	$cache = [
		'expires' => $refreshedAt + 2.0,
		'rows' => asrEchoLinkConnectedCallsigns(is_string($output) ? $output : ''),
	];
	return $cache['rows'];
}
