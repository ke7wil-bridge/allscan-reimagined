<?php
// AllScan v1.06 native scheduler with ASR-compatible duration/history safety.
define('SCHED_FILE', '/etc/allscan/schedule.json');
define('SCHED_HEARTBEAT', '/etc/allscan/schedule.heartbeat');
define('SCHED_MAX_ENTRIES', 50);
define('SCHED_ACTIVITY', '/etc/allscan/schedule.activity');
define('SCHED_ACTIVITY_MAXLINES', 200);
define('SCHED_LOG', '/etc/allscan/schedule.log');
define('SCHED_PENDING', '/etc/allscan/schedule.pending.json');
define('SCHED_STATE', '/etc/allscan/schedule.state.json');
define('SCHED_HISTORY', '/etc/allscan/schedule.history.jsonl');
define('SCHED_LOCK', '/etc/allscan/schedule.lock');
define('SCHED_HISTORY_MAXLINES', 500);

$schedActions = [
	'connect' => 'Connect', 'monitor' => 'Monitor',
	'localmonitor' => 'Local Monitor', 'disconnect' => 'Disconnect'
];

function schedSetTimezone() {
	$tz = @trim(file_get_contents('/etc/timezone'));
	if($tz && @date_default_timezone_set($tz)) return;
	$tz = @readlink('/etc/localtime');
	if($tz && preg_match('#zoneinfo/(.+)$#', $tz, $m)) @date_default_timezone_set($m[1]);
}

function schedLoadJsonArray($file) {
	if(!file_exists($file)) return [];
	$data = json_decode((string)@file_get_contents($file), true);
	return is_array($data) ? array_values($data) : [];
}
function schedLoad() { return schedLoadJsonArray(SCHED_FILE); }

function schedAtomicJsonSave($file, $data, &$msg) {
	$dir = dirname($file);
	if(is_link($file) || !is_dir($dir) || !is_writable($dir)) {
		$msg = 'Unsafe or unwritable scheduler state path';
		return false;
	}
	$tmp = @tempnam($dir, '.' . basename($file) . '.');
	if($tmp === false) { $msg = 'Could not stage scheduler state'; return false; }
	$json = json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
	$ok = @file_put_contents($tmp, $json . "\n", LOCK_EX) !== false && @chmod($tmp, 0664) && @rename($tmp, $file);
	if(!$ok) { @unlink($tmp); $msg = 'Write ' . $file . ' failed. Check directory/file permissions'; }
	return $ok;
}
function schedSave($entries, &$msg) { return schedAtomicJsonSave(SCHED_FILE, array_values($entries), $msg); }

function schedValidate($in, &$err) {
	global $schedActions;
	$action = $in['action'] ?? '';
	if(!isset($schedActions[$action])) { $err = 'Invalid action'; return false; }
	$node = $in['node'] ?? '';
	if($action === 'disconnect' && $node === '0') {
		// Native AllScan compatibility. ASR Admin rejects disconnect-all separately.
	} elseif(!ctype_digit((string)$node) || strlen((string)$node) < 1 || strlen((string)$node) > 7) {
		$err = 'Invalid node number'; return false;
	}
	$time = $in['time'] ?? '';
	if(!preg_match('/^([01][0-9]|2[0-3]):([0-5][0-9])$/D', (string)$time)) {
		$err = 'Time must be HH:MM (24 hour)'; return false;
	}
	$days = [];
	foreach(explode(',', (string)($in['days'] ?? '')) as $d) {
		if($d === '') continue;
		if(!ctype_digit($d) || (int)$d > 6) { $err = 'Invalid day'; return false; }
		$days[(int)$d] = (int)$d;
	}
	sort($days);
	if(!count($days)) $days = [0, 1, 2, 3, 4, 5, 6];
	$durationRaw = trim((string)($in['duration'] ?? ''));
	$duration = $durationRaw === '' ? 0 : (int)$durationRaw;
	if($durationRaw !== '' && (!ctype_digit($durationRaw) || $duration < 1 || $duration > 1440)) {
		$err = 'Duration must be 1-1440 minutes or blank'; return false;
	}
	$perm = ($in['perm'] ?? '') === 'true';
	if($duration && $action === 'disconnect') { $err = 'Disconnect schedules cannot have a duration'; return false; }
	if($duration && $perm) { $err = 'A permanent connection cannot also have automatic disconnect'; return false; }
	return [
		'id' => bin2hex(random_bytes(4)), 'action' => $action, 'node' => (string)$node,
		'time' => $time, 'days' => $days, 'perm' => $perm,
		'autodisc' => ($in['autodisc'] ?? '') === 'true', 'duration' => $duration, 'enabled' => true
	];
}

function schedDue($e, $now) {
	return !empty($e['enabled']) && ($e['time'] ?? '') === date('H:i', $now)
		&& in_array((int)date('w', $now), (array)($e['days'] ?? []), true);
}

function schedDescribe($e) {
	global $schedActions;
	$names = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
	$days = (array)($e['days'] ?? []);
	$d = count($days) == 7 ? 'Daily' : implode(',', array_map(function($i) use ($names) { return $names[$i] ?? '?'; }, $days));
	$duration = !empty($e['duration']) ? ' for ' . (int)$e['duration'] . ' min' : '';
	return ($schedActions[$e['action']] ?? 'Unknown') . ' ' . (($e['node'] ?? '') === '0' ? 'all' : ($e['node'] ?? ''))
		. (!empty($e['perm']) ? ' (perm)' : '') . $duration . ' at ' . ($e['time'] ?? '') . ' ' . $d;
}

function schedExecute($ami, $fp, $localnode, $e) {
	$node = $e['node']; $out = [];
	switch($e['action']) {
		case 'connect':
			if(!empty($e['autodisc'])) { $out[] = $ami->command($fp, "rpt cmd $localnode ilink 6 0"); usleep(500000); }
			$ilink = !empty($e['perm']) ? 13 : 3; break;
		case 'monitor':
			if(!empty($e['autodisc'])) { $out[] = $ami->command($fp, "rpt cmd $localnode ilink 6 0"); usleep(500000); }
			$ilink = !empty($e['perm']) ? 12 : 2; break;
		case 'localmonitor': $ilink = !empty($e['perm']) ? 18 : 8; break;
		case 'disconnect': $ilink = ($node === '0') ? 6 : 11; break;
		default: return 'Unknown action';
	}
	$out[] = $ami->command($fp, "rpt cmd $localnode ilink $ilink $node");
	return implode(' ', $out);
}

function schedWrite($file, $data, $flags=0) {
	if(is_link($file)) return false;
	if(@file_put_contents($file, $data, $flags) !== false) return true;
	if(file_exists($file) && is_writable(dirname($file)) && @unlink($file))
		return @file_put_contents($file, $data, $flags) !== false;
	return false;
}

function schedActivity($text) {
	$text = trim(preg_replace('/\s+/', ' ', $text));
	$line = 'Schedule ' . date('H:i:s') . ': ' . $text . "\n";
	schedWrite(SCHED_ACTIVITY, $line, FILE_APPEND | LOCK_EX);
	$lines = @file(SCHED_ACTIVITY);
	if($lines && count($lines) > SCHED_ACTIVITY_MAXLINES * 1.25)
		schedWrite(SCHED_ACTIVITY, implode('', array_slice($lines, -SCHED_ACTIVITY_MAXLINES)), LOCK_EX);
}

function schedHistory($event, $entry, $ok, $detail='') {
	$record = [
		'timestamp' => date('c'), 'event' => $event, 'id' => (string)($entry['id'] ?? ''),
		'action' => (string)($entry['action'] ?? ''), 'node' => (string)($entry['node'] ?? ''),
		'ok' => (bool)$ok, 'detail' => substr(trim(preg_replace('/\s+/', ' ', (string)$detail)), 0, 500)
	];
	schedWrite(SCHED_HISTORY, json_encode($record, JSON_UNESCAPED_SLASHES) . "\n", FILE_APPEND | LOCK_EX);
	$lines = @file(SCHED_HISTORY);
	if($lines && count($lines) > SCHED_HISTORY_MAXLINES * 1.25)
		schedWrite(SCHED_HISTORY, implode('', array_slice($lines, -SCHED_HISTORY_MAXLINES)), LOCK_EX);
}
function schedHistoryLoad($limit=30) {
	$lines = @file(SCHED_HISTORY, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: [];
	$result = [];
	foreach(array_reverse(array_slice($lines, -max(1, min(100, (int)$limit)))) as $line) {
		$row = json_decode($line, true); if(is_array($row)) $result[] = $row;
	}
	return $result;
}

function schedRunnerLock($nonBlocking=true) {
	if(is_link(SCHED_LOCK)) return false;
	$handle = @fopen(SCHED_LOCK, 'c');
	if(!$handle) return false;
	$mode = LOCK_EX | ($nonBlocking ? LOCK_NB : 0);
	if(!@flock($handle, $mode)) { fclose($handle); return false; }
	return $handle;
}
function schedStateLoad() {
	$data = json_decode((string)@file_get_contents(SCHED_STATE), true);
	return is_array($data) ? $data : [];
}
function schedStateSave($state) { $err = ''; return schedAtomicJsonSave(SCHED_STATE, $state, $err); }
function schedPendingLoad() { return schedLoadJsonArray(SCHED_PENDING); }
function schedPendingSave($pending) { $err = ''; return schedAtomicJsonSave(SCHED_PENDING, array_values($pending), $err); }

// Prevent a repeated cron invocation and the fall-back DST hour from executing
// the same schedule twice for one local wall-clock minute.
function schedWallMinute($now) { return date('Y-m-d H:i', $now); }
function schedClaimDue($entries, $now, $commit=true) {
	$state = schedStateLoad(); $due = []; $wall = schedWallMinute($now);
	$validIds = [];
	foreach($entries as $candidate) {
		$id = is_array($candidate) ? (string)($candidate['id'] ?? '') : '';
		if(preg_match('/^[a-f0-9]{8}$/D', $id)) $validIds[$id] = true;
	}
	$state = array_intersect_key($state, $validIds);
	foreach($entries as $entry) {
		if(!is_array($entry) || !schedDue($entry, $now)) continue;
		$id = (string)($entry['id'] ?? '');
		if(!preg_match('/^[a-f0-9]{8}$/D', $id) || ($state[$id] ?? '') === $wall) continue;
		$due[] = $entry;
		if($commit) $state[$id] = $wall;
	}
	if($commit) schedStateSave($state);
	return $due;
}

// Resolve simultaneous schedules deterministically. A disconnect for a node
// wins; otherwise the lowest stable schedule ID wins and skipped entries are logged.
function schedResolveConflicts($entries, $record=true) {
	$groups = [];
	foreach($entries as $entry) $groups[(string)($entry['node'] ?? '')][] = $entry;
	$result = [];
	foreach($groups as $node => $items) {
		usort($items, function($a, $b) {
			$ad = ($a['action'] ?? '') === 'disconnect' ? 0 : 1;
			$bd = ($b['action'] ?? '') === 'disconnect' ? 0 : 1;
			return $ad === $bd ? strcmp((string)($a['id'] ?? ''), (string)($b['id'] ?? '')) : $ad - $bd;
		});
		$result[] = $items[0];
		if($record) foreach(array_slice($items, 1) as $skipped)
			schedHistory('conflict-skipped', $skipped, false, 'Conflicts with schedule ' . ($items[0]['id'] ?? ''));
	}
	return $result;
}

function schedRunnerPath() { return realpath(__DIR__ . '/../_tools/runSchedule.php'); }
function schedCronLine() { return '* * * * * ' . schedRunnerPath() . ' >> ' . SCHED_LOG . ' 2>&1'; }
function schedCrontabBin() {
	foreach(['/usr/bin/crontab', '/bin/crontab', '/usr/sbin/crontab'] as $f) if(is_executable($f)) return $f;
	return false;
}
function schedCronRead() {
	$bin = schedCrontabBin(); if(!$bin) return false; $out = [];
	exec(escapeshellarg($bin) . ' -l 2>/dev/null', $out, $rc);
	return ($rc === 0 || !count($out)) ? $out : false;
}
function schedCronHasEntry($lines) {
	foreach($lines ?: [] as $line)
		if(strpos($line, 'runSchedule.php') !== false && substr(ltrim($line), 0, 1) !== '#') return true;
	return false;
}
function schedRunnerActive() { return file_exists(SCHED_HEARTBEAT) && time() - filemtime(SCHED_HEARTBEAT) < 180; }
function schedEnabled() { global $gCfg; return !empty($gCfg[schedulerEnabled]); }
function schedCronSync($enable, &$err) { return $enable ? schedCronEnable($err) : schedCronDisable($err); }
function schedCronWrite($lines, &$err) {
	$bin = schedCrontabBin(); if(!$bin) { $err = 'crontab command not found'; return false; }
	$p = proc_open([$bin, '-'], [0=>['pipe','r'],1=>['pipe','w'],2=>['pipe','w']], $pipes);
	if(!is_resource($p)) { $err = 'Unable to run crontab'; return false; }
	fwrite($pipes[0], implode("\n", $lines) . (count($lines) ? "\n" : '')); fclose($pipes[0]);
	$out = trim(stream_get_contents($pipes[1]) . ' ' . stream_get_contents($pipes[2])); fclose($pipes[1]); fclose($pipes[2]);
	if(proc_close($p) !== 0) { $err = "crontab failed: $out"; return false; }
	return true;
}
function schedCronEnable(&$err) {
	$lines = schedCronRead();
	if($lines === false) { $err = 'Unable to read crontab. Check cron.allow and cron.deny'; return false; }
	if(schedCronHasEntry($lines) || schedRunnerActive()) return true;
	$runner = schedRunnerPath();
	if(!$runner || !is_executable($runner)) { $err = '_tools/runSchedule.php is missing or not executable'; return false; }
	$lines[] = schedCronLine(); return schedCronWrite($lines, $err);
}
function schedCronDisable(&$err) {
	$lines = schedCronRead(); if($lines === false) { $err = 'Unable to read crontab'; return false; }
	$keep = array_values(array_filter($lines, function($line) { return strpos($line, 'runSchedule.php') === false; }));
	return count($keep) === count($lines) ? true : schedCronWrite($keep, $err);
}
