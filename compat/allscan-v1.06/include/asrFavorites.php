<?php
declare(strict_types=1);

/**
 * Return every known favorites*.ini file from the shared AllScan directory
 * the ASR web directory, and the sibling stock AllScan web directory.
 * Shared files win when locations contain the same basename.
 *
 * @return array<int, string>
 */
function asrFavoritesFiles(string $etcDir, string $webDir): array {
	$byName = [];

	// Include stock AllScan's sibling web root: older installations may retain
	// an operator's original list there instead of the canonical /etc path.
	// Never follow a symlink outside the explicitly trusted roots.
	$stockDir = dirname(rtrim($webDir, DIRECTORY_SEPARATOR)) . DIRECTORY_SEPARATOR . 'allscan';
	$trustedRoots = [];
	foreach ([$etcDir, $webDir, $stockDir] as $candidate) {
		$resolved = realpath($candidate);
		if ($resolved !== false && is_dir($resolved)) $trustedRoots[$resolved] = true;
	}
	foreach (array_keys($trustedRoots) as $directory) {
		$matches = glob(rtrim($directory, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . 'favorites*.ini') ?: [];
		natcasesort($matches);
		foreach($matches as $path) {
			$name = basename($path);
			if(
				!preg_match('/^favorites[^\/]*\.ini$/i', $name)
				|| !is_file($path)
				|| ($resolvedFile = realpath($path)) === false
				|| !array_key_exists(dirname($resolvedFile), $trustedRoots)
				|| array_key_exists($name, $byName)
			)
				continue;
			$byName[$name] = $path;
		}
	}

	uksort($byName, static function(string $left, string $right): int {
		if($left === 'favorites.ini')
			return -1;
		if($right === 'favorites.ini')
			return 1;
		return strnatcasecmp($left, $right);
	});

	return array_values($byName);
}

function asrFavoritesFile(
	string $requested,
	string $etcDir,
	string $webDir,
	string $fallback,
	bool $requireRequested = false
): string {
	$files = asrFavoritesFiles($etcDir, $webDir);
	$byName = [];
	foreach($files as $file)
		$byName[basename($file)] = $file;

	$default = $byName['favorites.ini'] ?? ($files[0] ?? $fallback);
	if($requested === '')
		return $default;

	$name = basename($requested);
	if($requested === $name && $name !== '' && isset($byName[$name]))
		return $byName[$name];

	if($requireRequested)
		throw new RuntimeException('Selected Favorites file is missing or inaccessible; no substitute list was selected. Use Find Favorites files and backups to inspect or recover it.');
	return $default;
}
