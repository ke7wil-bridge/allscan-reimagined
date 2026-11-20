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
		'zello-relay.json',
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

function asrExpandUrfProtocolBridges(array $bridges): array {
	$protocols = ['dmr' => 'DMR', 'ysf' => 'YSF', 'p25' => 'P25', 'nxdn' => 'NXDN', 'm17' => 'M17'];
	$expandedBridges = [];
	foreach($bridges as $bridge) {
		if(!is_array($bridge) || empty($bridge['urfReflector']) || strtolower(trim((string)($bridge['mode'] ?? ''))) !== 'urf') {
			$expandedBridges[] = $bridge;
			continue;
		}
		// Missing modes is the legacy all-protocol default. An explicit field,
		// including an empty or invalid value, enables only listed protocols.
		$selectedModes = array_key_exists('modes', $bridge) && is_array($bridge['modes'])
			? array_map(static fn($mode): string => strtolower(trim((string)$mode)), $bridge['modes'])
			: (array_key_exists('modes', $bridge) ? [] : array_keys($protocols));
		foreach($protocols as $mode => $label) {
			if(!in_array($mode, $selectedModes, true)) continue;
			$expanded = $bridge;
			$expanded['id'] = 'urf_' . $mode;
			$expanded['mode'] = $mode;
			$expanded['title'] = $label . ' Bridge';
			$expanded['detailTitle'] = 'Connected Clients';
			$expanded['friendlyName'] = trim((string)($bridge['friendlyName'] ?? ''));
			if($expanded['friendlyName'] === '')
				$expanded['friendlyName'] = trim((string)($bridge['title'] ?? 'URF Multi-Mode Bridge'));
			$expanded['urfGroupId'] = (string)($bridge['urfGroupId'] ?? 'urf');
			$expanded['urfName'] = trim((string)($bridge['urfName'] ?? ''));
			$expandedBridges[] = $expanded;
		}
	}
	return $expandedBridges;
}
