#!/usr/bin/php
<?php
// Native AllScan minute runner, extended by ASR for bounded durations,
// execution history, DST duplicate protection, and conflict serialization.
$dryRun = in_array('--dry-run', $argv, true);
$asRoot = realpath(__DIR__ . '/..');
$_SERVER['DOCUMENT_ROOT'] = dirname($asRoot);
$_SERVER['SCRIPT_NAME'] = '/' . basename($asRoot) . '/_tools/' . basename(__FILE__);
chdir(__DIR__);
require_once('../include/common.php');
require_once('../include/scheduleUtils.php');
require_once('../astapi/AMI.php');

$msg = [];
asInit($msg);
$asdir = basename($asRoot);
$db = dbInit();
checkTables($db, $msg);
$cfgModel = new CfgModel($db);

schedSetTimezone();
$now = time();
$stamp = date('Y-m-d H:i:s');
$lock = schedRunnerLock(true);
if(!$lock) {
	if($dryRun) echo "$stamp DRY RUN: scheduler is already running\n";
	exit(0);
}
if(!$dryRun) schedWrite(SCHED_HEARTBEAT, date('c'));
if(!schedEnabled()) exit(0);

$pending = schedPendingLoad();
$expired = [];
$remaining = [];
foreach($pending as $item) {
	if(!is_array($item) || !isset($item['due'], $item['node']) || !ctype_digit((string)$item['node'])) continue;
	if((int)$item['due'] <= $now) $expired[] = [
		'id' => (string)($item['id'] ?? ''), 'action' => 'disconnect', 'node' => (string)$item['node'],
		'time' => date('H:i', $now), 'days' => [(int)date('w', $now)], 'perm' => false,
		'autodisc' => false, 'duration' => 0, 'enabled' => true
	];
	else $remaining[] = $item;
}
$due = schedResolveConflicts(array_merge($expired, schedClaimDue(schedLoad(), $now, !$dryRun)));
if(!count($due)) exit(0);

if($dryRun) {
	foreach($due as $entry) echo "$stamp DRY RUN: " . schedDescribe($entry) . "\n";
	exit(0);
}

function schedRunnerFail($stamp, $text, $entries=[]) {
	schedActivity("ERROR: $text");
	foreach($entries as $entry) schedHistory('execution', $entry, false, $text);
	exit("$stamp $text\n");
}
if(!getAmiCfg($msg) || !$amicfg->host) schedRunnerFail($stamp, 'AMI credentials not found', $due);
$ami = new AMI();
$fp = $ami->connect($amicfg->host, $amicfg->port);
if($fp === false) schedRunnerFail($stamp, 'Could not connect to Asterisk Manager', $due);
if($ami->login($fp, $amicfg->user, $amicfg->pass) === false) schedRunnerFail($stamp, 'Could not login to Asterisk Manager', $due);

$expiredIds = array_fill_keys(array_map(function($entry) { return (string)$entry['id']; }, $expired), true);
foreach($due as $entry) {
	try {
		$result = schedExecute($ami, $fp, $amicfg->node, $entry);
		$line = schedDescribe($entry) . ': ' . $result;
		echo "$stamp $line\n";
		schedActivity($line);
		schedHistory(isset($expiredIds[(string)$entry['id']]) ? 'automatic-disconnect' : 'execution', $entry, true, $result);
		if(!empty($entry['duration']) && in_array($entry['action'], ['connect','monitor','localmonitor'], true)) {
			$remaining = array_values(array_filter($remaining, function($item) use ($entry) {
				return !is_array($item) || (string)($item['id'] ?? '') !== (string)$entry['id'] || (string)($item['node'] ?? '') !== (string)$entry['node'];
			}));
			$remaining[] = [
				'id' => (string)$entry['id'], 'node' => (string)$entry['node'],
				'due' => $now + ((int)$entry['duration'] * 60), 'created' => date('c', $now)
			];
			schedPendingSave($remaining);
		}
	} catch(Throwable $error) {
		schedHistory('execution', $entry, false, $error->getMessage());
		schedActivity('ERROR: ' . schedDescribe($entry) . ': ' . $error->getMessage());
	}
}
schedPendingSave($remaining);
fclose($fp);
