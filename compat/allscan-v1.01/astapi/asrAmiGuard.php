<?php

const ASR_AMI_GUARD_SCHEMA = 1;

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
	$payload = json_encode([
		'schema' => ASR_AMI_GUARD_SCHEMA,
		'open' => true,
		'opened' => microtime(true),
		'boot_id' => trim((string) $bootId),
		'reason' => trim((string) $reason),
	]);
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

function asrAmiGuardClear($path) {
	return !file_exists($path) || @unlink($path);
}
