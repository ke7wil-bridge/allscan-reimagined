<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');
header('X-Content-Type-Options: nosniff');

require_once('../include/common.php');

function asrRollbackStatusJson(array $payload, int $status = 200): void {
	http_response_code($status);
	echo json_encode($payload, JSON_UNESCAPED_SLASHES);
	exit;
}

$msg = [];
asInit($msg);
$db = dbInit();
checkTables($db, $msg);
$userModel = new UserModel($db);
$user = $userModel->validate();

if(empty($user) || !isset($user->user_id) || !validDbID($user->user_id) || !adminUser())
	asrRollbackStatusJson(['ok' => false, 'error' => 'Admin login required.'], 403);

$jobId = trim((string) ($_GET['job'] ?? ''));
if(!preg_match('/^\d{8}-\d{6}-[a-f0-9]{8}$/D', $jobId))
	asrRollbackStatusJson(['ok' => false, 'error' => 'Invalid rollback job.'], 400);

$helper = '/usr/local/sbin/allscan-reimagined-rollback';
if(!is_executable($helper) || !function_exists('exec'))
	asrRollbackStatusJson(['ok' => false, 'error' => 'Rollback status is unavailable.'], 503);

$command = 'sudo -n ' . escapeshellarg($helper) . ' --status-json ' . escapeshellarg($jobId) . ' 2>/dev/null';
$output = [];
$status = 1;
exec($command, $output, $status);
$json = implode("\n", $output);
if($status !== 0 || strlen($json) > 65536)
	asrRollbackStatusJson(['ok' => false, 'error' => 'Rollback status is unavailable.'], 503);

$payload = json_decode($json, true);
if(!is_array($payload) || empty($payload['ok']) || (string) ($payload['jobId'] ?? '') !== $jobId)
	asrRollbackStatusJson(['ok' => false, 'error' => 'Rollback status is unavailable.'], 503);

asrRollbackStatusJson([
	'ok' => true,
	'jobId' => $jobId,
	'state' => (string) ($payload['state'] ?? 'unknown'),
]);
