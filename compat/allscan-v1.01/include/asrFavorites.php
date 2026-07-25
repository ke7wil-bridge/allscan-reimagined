<?php
declare(strict_types=1);

/**
 * Return every usable favorites*.ini file from the shared AllScan directory
 * and the ASR web directory. Shared files win when both locations contain the
 * same basename.
 *
 * @return array<int, string>
 */
function asrFavoritesFiles(string $etcDir, string $webDir): array {
	$byName = [];

	foreach([$etcDir, $webDir] as $directory) {
		$matches = glob(rtrim($directory, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . 'favorites*.ini') ?: [];
		natcasesort($matches);
		foreach($matches as $path) {
			$name = basename($path);
			if(
				!preg_match('/^favorites[^\/]*\.ini$/i', $name)
				|| !is_file($path)
				|| !is_readable($path)
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
	string $fallback
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

	return $default;
}
