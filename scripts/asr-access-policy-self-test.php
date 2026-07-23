#!/usr/bin/env php
<?php
declare(strict_types=1);

define('PERMISSION_NONE', 0);
define('PERMISSION_READ_ONLY', 2);
define('PERMISSION_READ_MODIFY', 4);
define('PERMISSION_FULL', 6);
define('PERMISSION_ADMIN', 10);
define('PERMISSION_SUPERUSER', 14);
define('BR', PHP_EOL);

$policyFile = tempnam(sys_get_temp_dir(), 'asr-access-policy-');
if($policyFile === false)
	throw new RuntimeException('Could not create access-policy test file.');
define('ASR_ACCESS_POLICY_FILE', $policyFile);

function _count($value): int {
	return is_countable($value) ? count($value) : 0;
}
function arrayToCsv($value): string {
	return implode(',', $value);
}
function validDbID($value): bool {
	return is_numeric($value) && (int)$value > 0;
}
function pageInit(): void {}
function asExit($message=null): void {
	throw new RuntimeException((string)$message);
}
function msg($message): void {}

require_once dirname(__DIR__) . '/compat/allscan-v1.01/include/CfgModel.php';

class AsrAccessPolicyFakeDb {
	public array $writes = [];
	public array $queries = [];

	function getRecords($table, $where=null, $orderBy=null): array {
		$this->queries[] = (string)$where;
		return [(object)[
			'cfg_id' => publicPermission,
			'val' => PERMISSION_FULL,
			'updated' => time(),
		]];
	}
	function insertRow($table, $columns, $values): bool {
		$this->writes[] = ['insert', $values];
		return true;
	}
	function updateRow($table, $columns, $values, $where): bool {
		$this->writes[] = ['update', $values, $where];
		return true;
	}
	function deleteRows($table, $where): bool {
		$this->writes[] = ['delete', $where];
		return true;
	}
	function getRecordCount($table, $where=null): int {
		return 0;
	}
}

function asrAccessResetGlobals(): void {
	global $gCfg, $gCfgDef, $gCfgUpdated, $msg, $asdbfile;
	$gCfg = $gCfgDef;
	$gCfgUpdated = [];
	$msg = [];
	$asdbfile = '/etc/allscan/allscan.db';
}

function asrAccessAssert(bool $condition, string $message): void {
	if(!$condition)
		throw new RuntimeException($message);
}

try {
	file_put_contents($policyFile, json_encode(['requireLogin' => true]));
	$asdir = 'asr';
	asrAccessResetGlobals();
	$db = new AsrAccessPolicyFakeDb();
	$model = new CfgModel($db);
	asrAccessAssert(
		$gCfg[publicPermission] === PERMISSION_NONE,
		'ASR require-login policy was not applied in memory.'
	);

	$gCfg[publicPermission] = PERMISSION_FULL;
	$model->saveCfgs();
	asrAccessAssert($db->writes === [], 'ASR save wrote shared DB configuration.');
	$saveQuery = end($db->queries);
	preg_match('/IN\(([^)]*)\)/', (string)$saveQuery, $queryMatch);
	$savedIds = isset($queryMatch[1])
		? array_map('intval', explode(',', $queryMatch[1]))
		: [];
	asrAccessAssert(
		!in_array(publicPermission, $savedIds, true),
		'ASR save queried shared publicPermission for persistence.'
	);
	asrAccessAssert(
		$gCfg[publicPermission] === PERMISSION_NONE,
		'ASR save did not restore the effective in-memory policy.'
	);

	file_put_contents($policyFile, json_encode(['requireLogin' => false]));
	asrAccessResetGlobals();
	$db = new AsrAccessPolicyFakeDb();
	new CfgModel($db);
	asrAccessAssert(
		$gCfg[publicPermission] === PERMISSION_READ_ONLY,
		'ASR public-read policy was not applied in memory.'
	);

	$asdir = 'allscan';
	asrAccessResetGlobals();
	$db = new AsrAccessPolicyFakeDb();
	new CfgModel($db);
	asrAccessAssert(
		(int)$gCfg[publicPermission] === PERMISSION_FULL,
		'Stock publicPermission was overridden by ASR policy.'
	);

	$asdir = 'asr';
	unlink($policyFile);
	asrAccessResetGlobals();
	$db = new AsrAccessPolicyFakeDb();
	new CfgModel($db);
	asrAccessAssert(
		$gCfg[publicPermission] === PERMISSION_NONE,
		'Missing ASR access policy did not fail closed.'
	);

	echo "ASR-only access policy self-test: ok" . PHP_EOL;
} finally {
	@unlink($policyFile);
}
