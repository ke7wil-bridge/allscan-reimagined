<?php
declare(strict_types=1);

function asrRuntimeFilePath(
	string $filename,
	?string $asrDir = null,
	?string $stockDir = null
): string {
	$allowed = [
		'bridge-live.json',
		'connected-clients.json',
		'zello-talkers.json',
	];
	if(!in_array($filename, $allowed, true))
		throw new InvalidArgumentException('Unsupported ASR runtime filename.');

	$asrDir = $asrDir ?? dirname(__DIR__);
	$stockDir = $stockDir ?? dirname($asrDir) . '/allscan';
	$asrPath = rtrim($asrDir, '/') . '/' . $filename;
	$stockPath = rtrim($stockDir, '/') . '/' . $filename;
	$selected = $asrPath;
	$selectedMtime = is_readable($asrPath) ? (int) @filemtime($asrPath) : -1;

	if(is_readable($stockPath)) {
		$stockMtime = (int) @filemtime($stockPath);
		if($stockMtime > $selectedMtime)
			$selected = $stockPath;
	}

	return $selected;
}

function asrClientIdentityValue(mixed $value): string {
	$normalized = strtolower(trim((string) $value));
	if(in_array($normalized, ['', '-', '--', 'unknown', 'none', 'n/a', 'na', 'null'], true))
		return '';
	return $normalized;
}

function asrClientIdentityKeys(array $row): array {
	$keys = [];
	foreach(['dmrid', 'dmr_id', 'id'] as $key) {
		$id = asrClientIdentityValue($row[$key] ?? '');
		if($id !== '')
			$keys[] = 'id:' . $id;
	}
	foreach(['callsign', 'call', 'station', 'username', 'name', 'display_name', 'displayName', 'user', 'current_user'] as $key) {
		$name = asrClientIdentityValue($row[$key] ?? '');
		if($name !== '')
			$keys[] = 'name:' . $name;
	}
	return array_values(array_unique($keys));
}
