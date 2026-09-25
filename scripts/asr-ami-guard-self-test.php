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

require_once dirname(__DIR__) . '/compat/allscan-v1.01/astapi/AMI.php';
require_once dirname(__DIR__) . '/compat/allscan-v1.01/astapi/asrAmiGuard.php';

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
asrAmiGuardTest(asrAmiGuardClear($path) && !file_exists($path), 'Guard did not clear.');
rmdir($dir);

echo "ASR AMI guard self-test passed.\n";
