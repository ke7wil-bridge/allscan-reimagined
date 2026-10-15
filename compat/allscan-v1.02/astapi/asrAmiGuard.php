<?php

const ASR_AMI_GUARD_SCHEMA = 1;
const ASR_AMI_GUARD_RECOVERY_COOLDOWN = 60;
const ASR_AMI_GUARD_RECOVERY_SUCCESSES = 3;

function asrAmiGuardStore($path, $state) {
	$payload = json_encode($state);
	if($payload === false)
		return false;
	$tmp = $path . '.' . getmypid() . '.tmp';
	if(@file_put_contents($tmp, $payload, LOCK_EX) === false) {
		@unlink($tmp);
		return false;
	}
	@chmod($tmp, 0644);
	if(!@rename($tmp, $path)) {
		@unlink($tmp);
		return false;
	}
	return true;
}

function asrAmiGuardLoad($path) {
	if(!is_readable($path))
		return [];
	$state = json_decode((string) @file_get_contents($path), true);
	if(!is_array($state) || (int) ($state['schema'] ?? 0) !== ASR_AMI_GUARD_SCHEMA)
		return [];
	return $state;
}

function asrAmiGuardBlocks($state, $bootId) {
	if(!is_array($state) || empty($state['open']))
		return false;
	$blockedBoot = trim((string) ($state['boot_id'] ?? ''));
	$bootId = trim((string) $bootId);
	// Fail closed if CoreStatus is temporarily unavailable. Repeating RptStatus
	// against an unknown Asterisk instance could recreate the worker leak.
	return $blockedBoot === '' || $bootId === '' || hash_equals($blockedBoot, $bootId);
}

function asrAmiGuardOpen($path, $bootId, $reason) {
	$opened = microtime(true);
	return asrAmiGuardStore($path, [
		'schema' => ASR_AMI_GUARD_SCHEMA,
		'open' => true,
		'opened' => $opened,
		'boot_id' => trim((string) $bootId),
		'reason' => trim((string) $reason),
		'probe_failures' => 0,
		'next_probe' => $opened + ASR_AMI_GUARD_RECOVERY_COOLDOWN,
	]);
}

function asrAmiGuardClear($path) {
	return !file_exists($path) || @unlink($path);
}

function asrAmiGuardRecoveryDue($state, $now=null) {
	if(!is_array($state) || empty($state['open']))
		return false;
	$now = $now === null ? microtime(true) : (float) $now;
	$nextProbe = (float) ($state['next_probe'] ?? 0);
	if($nextProbe <= 0) {
		$opened = (float) ($state['opened'] ?? 0);
		$nextProbe = $opened > 0 ? $opened + ASR_AMI_GUARD_RECOVERY_COOLDOWN : 0;
	}
	return $nextProbe > 0 && $now >= $nextProbe;
}

function asrAmiGuardRecordProbeFailure($path, $state, $error='', $now=null) {
	if(!is_array($state) || empty($state['open']))
		return false;
	$now = $now === null ? microtime(true) : (float) $now;
	$failures = max(0, (int) ($state['probe_failures'] ?? 0)) + 1;
	$delays = [300, 900, 3600];
	$delay = $delays[min($failures - 1, count($delays) - 1)];
	$state['probe_failures'] = $failures;
	$state['last_probe_failure'] = $now;
	$state['next_probe'] = $now + $delay;
	if(trim((string) $error) !== '')
		$state['last_probe_error'] = trim((string) $error);
	return asrAmiGuardStore($path, $state);
}

function asrAmiGuardRecoveryAdvance($successes, $probeOk) {
	if(!$probeOk)
		return 0;
	return min(ASR_AMI_GUARD_RECOVERY_SUCCESSES, max(0, (int) $successes) + 1);
}

function asrAmiGuardRecoveryReady($successes) {
	return (int) $successes >= ASR_AMI_GUARD_RECOVERY_SUCCESSES;
}

function asrAmiGuardResponseOk($response, $actionId) {
	if(!is_array($response) || !in_array('ActionID: ' . $actionId, $response, true))
		return false;
	foreach($response as $line) {
		if(preg_match('/^Response:\s*Error\b/i', trim((string) $line)))
			return false;
	}
	return true;
}
