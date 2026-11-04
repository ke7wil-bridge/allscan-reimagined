<?php

function asrAmiGuardTest($condition, $message) {
	if(!$condition)
		throw new RuntimeException($message);
}

function validIpAddr($ip) { return filter_var($ip, FILTER_VALIDATE_IP) !== false; }
function logToFile($message, $path='') {}
function getScriptName() { return 'asr-ami-guard-self-test'; }
function _count($value) { return is_countable($value) ? count($value) : 0; }
if(!defined('NL')) define('NL', "\n");

require_once dirname(__DIR__) . '/compat/allscan-v1.02/astapi/AMI.php';
require_once dirname(__DIR__) . '/compat/allscan-v1.02/astapi/asrAmiGuard.php';

$pair = stream_socket_pair(STREAM_PF_UNIX, STREAM_SOCK_STREAM, STREAM_IPPROTO_IP);
asrAmiGuardTest(is_array($pair) && count($pair) === 2, 'Unable to create socket pair.');
stream_set_timeout($pair[0], 0, 200000);
$ami = new AMI(1);
$started = microtime(true);
$response = $ami->getResponse($pair[0], 'blocked-action');
$elapsed = microtime(true) - $started;
asrAmiGuardTest($response === 'Timeout', 'Blocked AMI read did not report Timeout.');
asrAmiGuardTest($elapsed < 1.0, 'Blocked AMI read exceeded its socket deadline.');
fclose($pair[0]);
fclose($pair[1]);

$pair = stream_socket_pair(STREAM_PF_UNIX, STREAM_SOCK_STREAM, STREAM_IPPROTO_IP);
stream_set_timeout($pair[0], 1);
fwrite($pair[1], "Response: Success\r\nActionID: corestatus1\r\nCoreStartupDate: 2026-09-25\r\nCoreStartupTime: 16:25:04\r\n\r\n");
$response = $ami->getResponse($pair[0], 'corestatus1');
asrAmiGuardTest(is_array($response), 'Valid AMI response was rejected.');
asrAmiGuardTest(in_array('CoreStartupDate: 2026-09-25', $response, true), 'AMI response body was truncated.');
fclose($pair[0]);
fclose($pair[1]);

// Make the generated action id deterministic so the fixture can supply the
// exact response that coreStartupId() expects.
mt_srand(1234);
$expectedActionId = 'corestatus' . mt_rand();
mt_srand(1234);
$fixture = "Response: Success\r\nActionID: $expectedActionId\r\nCoreStartupDate: 2026-09-25\r\nCoreStartupTime: 16:25:04\r\n\r\n";
$pair = stream_socket_pair(STREAM_PF_UNIX, STREAM_SOCK_STREAM, STREAM_IPPROTO_IP);
stream_set_timeout($pair[0], 1);
fwrite($pair[1], $fixture);
$bootId = $ami->coreStartupId($pair[0]);
asrAmiGuardTest($bootId === '2026-09-25T16:25:04', 'CoreStatus boot identity was not parsed.');
fclose($pair[0]);
fclose($pair[1]);

$dir = sys_get_temp_dir() . '/asr-ami-guard-' . getmypid() . '-' . mt_rand();
asrAmiGuardTest(mkdir($dir, 0700), 'Unable to create guard test directory.');
$path = $dir . '/node.breaker.json';
asrAmiGuardTest(asrAmiGuardOpen($path, 'boot-one', 'XStat response timed out'), 'Unable to open guard.');
$state = asrAmiGuardLoad($path);
asrAmiGuardTest(asrAmiGuardBlocks($state, 'boot-one'), 'Guard did not block the failed Asterisk instance.');
asrAmiGuardTest(!asrAmiGuardBlocks($state, 'boot-two'), 'Guard did not allow a restarted Asterisk instance.');
asrAmiGuardTest(asrAmiGuardBlocks($state, ''), 'Guard failed open when CoreStatus was unavailable.');
asrAmiGuardTest(!asrAmiGuardRecoveryDue($state, (float) $state['opened'] + 59), 'Guard recovery ignored cooldown.');
asrAmiGuardTest(asrAmiGuardRecoveryDue($state, (float) $state['opened'] + 60), 'Guard recovery did not start after cooldown.');
asrAmiGuardTest(asrAmiGuardRecordProbeFailure($path, $state, 'XStat response timed out', 1000), 'Failed probe state was not recorded.');
$failedState = asrAmiGuardLoad($path);
asrAmiGuardTest((int) $failedState['probe_failures'] === 1, 'Failed probe count was not incremented.');
asrAmiGuardTest(!asrAmiGuardRecoveryDue($failedState, 1299) && asrAmiGuardRecoveryDue($failedState, 1300), 'First failed probe did not apply five-minute backoff.');
asrAmiGuardTest(asrAmiGuardRecordProbeFailure($path, $failedState, 'SawStat response timed out', 1300), 'Second failed probe state was not recorded.');
$failedState = asrAmiGuardLoad($path);
asrAmiGuardTest(!asrAmiGuardRecoveryDue($failedState, 2199) && asrAmiGuardRecoveryDue($failedState, 2200), 'Second failed probe did not apply fifteen-minute backoff.');
asrAmiGuardTest(asrAmiGuardRecordProbeFailure($path, $failedState, 'Asterisk recovery connection failed', 2200), 'Third failed probe state was not recorded.');
$failedState = asrAmiGuardLoad($path);
asrAmiGuardTest(!asrAmiGuardRecoveryDue($failedState, 5799) && asrAmiGuardRecoveryDue($failedState, 5800), 'Third failed probe did not apply one-hour backoff.');
$attemptCases = [
	[false, false, '641890', 'boot-one', '', null, 'failure', 'connection'],
	[true, false, '641890', 'boot-one', '', null, 'failure', 'login'],
	[true, true, '641890', 'boot-one', '', null, 'failure', 'identity'],
	[true, true, '', 'boot-one', 'boot-one', null, 'failure', 'node'],
	[true, true, '641890', 'boot-one', 'boot-one', ['ok' => false, 'error' => 'XStat response timed out'], 'failure', 'XStat'],
	[true, true, '641890', 'boot-one', 'boot-one', ['ok' => true], 'success', ''],
	[true, true, '641890', 'boot-one', 'boot-two', null, 'restarted', ''],
];
foreach($attemptCases as [$connected, $loggedIn, $testNode, $blockedId, $currentId, $result, $outcome, $errorPart]) {
	$attempt = asrAmiGuardClassifyRecoveryAttempt($connected, $loggedIn, $testNode, $blockedId, $currentId, $result);
	asrAmiGuardTest($attempt['outcome'] === $outcome, "Recovery attempt was not classified as $outcome.");
	if($errorPart !== '')
		asrAmiGuardTest(stripos($attempt['error'], $errorPart) !== false, "Recovery $errorPart failure was not identified.");
}
asrAmiGuardTest(asrAmiGuardResponseOk(['Response: Success', 'ActionID: probe1'], 'probe1'), 'Successful AMI response was rejected.');
asrAmiGuardTest(!asrAmiGuardResponseOk(['Response: Error', 'ActionID: probe1'], 'probe1'), 'AMI error response was accepted.');
asrAmiGuardTest(!asrAmiGuardResponseOk(['Response: Success'], 'probe1'), 'AMI response without matching ActionID was accepted.');
$successes = asrAmiGuardRecoveryAdvance(0, true);
asrAmiGuardTest($successes === 1 && !asrAmiGuardRecoveryReady($successes), 'Guard recovered after one probe.');
$successes = asrAmiGuardRecoveryAdvance($successes, true);
asrAmiGuardTest($successes === 2 && !asrAmiGuardRecoveryReady($successes), 'Guard recovered after two probes.');
$successes = asrAmiGuardRecoveryAdvance($successes, false);
asrAmiGuardTest($successes === 0, 'Failed probe did not reset recovery progress.');
$successes = asrAmiGuardRecoveryAdvance(asrAmiGuardRecoveryAdvance(asrAmiGuardRecoveryAdvance(0, true), true), true);
asrAmiGuardTest(asrAmiGuardRecoveryReady($successes), 'Guard did not recover after three probes.');

$confirmed = ['641890' => [
	'current_talker' => ['node' => '123456', 'info' => 'N0TEST'],
	'remote_nodes' => [
		['node' => '123456', 'keyed' => 'yes', 'cos_keyed' => 1, 'tx_keyed' => 1, 'keyed_started_epoch' => 1234],
		['node' => '654321', 'keyed' => 'no', 'cos_keyed' => 0, 'tx_keyed' => 0],
	],
]];
$unavailable = asrStatusWithoutStaleKeying($confirmed);
$healthy = asrStatusForAmiAvailability($confirmed, false);
$guarded = asrStatusForAmiAvailability($confirmed, true);
asrAmiGuardTest($confirmed['641890']['remote_nodes'][0]['keyed'] === 'yes', 'Unavailable-state conversion mutated confirmed AMI data.');
asrAmiGuardTest($healthy === $confirmed, 'Healthy AMI projection altered confirmed keyed or idle state.');
asrAmiGuardTest($guarded === $unavailable, 'Guarded AMI projection did not remove stale key evidence.');
asrAmiGuardTest($unavailable['641890']['current_talker'] === null, 'Unavailable AMI state retained a stale current talker.');
foreach($unavailable['641890']['remote_nodes'] as $row) {
	asrAmiGuardTest($row['keyed'] === 'unknown', 'Unavailable AMI state did not use the explicit unknown key state.');
	asrAmiGuardTest($row['cos_keyed'] === 0 && $row['tx_keyed'] === 0, 'Unavailable AMI state retained stale local key flags.');
	asrAmiGuardTest(!isset($row['keyed_started_epoch']), 'Unavailable AMI state retained a stale key start time.');
}

foreach(['v1.02', 'v1.03', 'v1.04', 'v1.05'] as $compatVersion) {
	$server = file_get_contents(dirname(__DIR__) . "/compat/allscan-$compatVersion/astapi/server.php");
	$guard = file_get_contents(dirname(__DIR__) . "/compat/allscan-$compatVersion/astapi/asrAmiGuard.php");
	asrAmiGuardTest(str_contains($server, 'writeSharedStatus($cachePath, $payload[\'current\']'), "$compatVersion does not publish AMI unavailability to the shared cache.");
	asrAmiGuardTest(str_contains($server, '$guardOpened = $lastGuarded === false && $guarded;'), "$compatVersion shared viewers do not observe breaker transitions.");
	asrAmiGuardTest(str_contains($server, 'asrStatusForAmiAvailability($cleanCurrent, $guarded)'), "$compatVersion shared viewers do not preserve healthy AMI state.");
	asrAmiGuardTest(str_contains($guard, 'function asrStatusForAmiAvailability'), "$compatVersion does not expose the guarded AMI status projection.");
	asrAmiGuardTest(str_contains($guard, 'function asrAmiGuardClassifyRecoveryAttempt'), "$compatVersion does not expose pure recovery-attempt classification.");
	asrAmiGuardTest(str_contains($server, '$probeDue = asrAmiGuardRecoveryDue($state);'), "$compatVersion does not capture whether each recovery attempt is due.");
	asrAmiGuardTest(str_contains($server, 'if($probeDue && $probeLoggedIn'), "$compatVersion can run XStat/SawStat while recovery is not due.");
	asrAmiGuardTest(str_contains($server, 'if($probeDue) {') && str_contains($server, 'asrAmiGuardClassifyRecoveryAttempt('), "$compatVersion does not classify every due recovery attempt.");
	asrAmiGuardTest(str_contains($server, "if(\$attempt['outcome'] === 'failure')\n\t\t\t\t\tasrAmiGuardRecordProbeFailure"), "$compatVersion does not record backoff for every classified due-attempt failure.");
	asrAmiGuardTest(strpos($server, 'if($currentBootId !== \'\' && $bootId !== \'\' && !hash_equals') < strpos($server, 'if($probeDue && $probeLoggedIn'), "$compatVersion no longer clears a confirmed restarted Asterisk before recovery probe/backoff handling.");
}
asrAmiGuardTest(asrAmiGuardClear($path) && !file_exists($path), 'Guard did not clear.');
rmdir($dir);

echo "ASR AMI guard self-test passed.\n";
