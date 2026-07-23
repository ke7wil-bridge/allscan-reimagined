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
		'dstar-clients.json',
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
