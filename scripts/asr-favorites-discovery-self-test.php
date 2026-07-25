#!/usr/bin/env php
<?php
declare(strict_types=1);

require_once dirname(__DIR__) . '/compat/allscan-v1.01/include/asrFavorites.php';

function asrFavoritesDiscoveryAssert(bool $condition, string $message): void {
	if(!$condition)
		throw new RuntimeException($message);
}

function asrFavoritesDiscoveryRemoveTree(string $root): void {
	if(!is_dir($root))
		return;
	$iterator = new RecursiveIteratorIterator(
		new RecursiveDirectoryIterator($root, FilesystemIterator::SKIP_DOTS),
		RecursiveIteratorIterator::CHILD_FIRST
	);
	foreach($iterator as $path) {
		if($path->isDir() && !$path->isLink())
			rmdir($path->getPathname());
		else
			unlink($path->getPathname());
	}
	rmdir($root);
}

$root = sys_get_temp_dir() . '/asr-favorites-discovery-self-test.' . getmypid();
$etcDir = $root . '/etc-allscan';
$webDir = $root . '/asr';

try {
	mkdir($etcDir, 0700, true);
	mkdir($webDir, 0700, true);

	file_put_contents($etcDir . '/favorites.ini', "label[] = \"Primary\"\n");
	file_put_contents($etcDir . '/favorites-UK-Full.ini', "label[] = \"UK\"\n");
	file_put_contents($etcDir . '/favorites-USA-East.ini', "label[] = \"Shared East\"\n");
	file_put_contents($webDir . '/favorites-Sample.ini', "label[] = \"Sample\"\n");
	file_put_contents($webDir . '/favorites-USA-East.ini', "label[] = \"Wrong duplicate\"\n");
	file_put_contents($webDir . '/not-favorites.ini', "label[] = \"Ignore\"\n");
	mkdir($webDir . '/favorites-directory.ini');

	$files = asrFavoritesFiles($etcDir, $webDir);
	$names = array_map('basename', $files);
	asrFavoritesDiscoveryAssert(
		$names === [
			'favorites.ini',
			'favorites-Sample.ini',
			'favorites-UK-Full.ini',
			'favorites-USA-East.ini',
		],
		'Favorites discovery did not return the expected ordered, deduplicated list.'
	);

	$eastIndex = array_search('favorites-USA-East.ini', $names, true);
	asrFavoritesDiscoveryAssert(
		$eastIndex !== false && $files[$eastIndex] === $etcDir . '/favorites-USA-East.ini',
		'The shared /etc Favorites file did not win a duplicate basename.'
	);
	asrFavoritesDiscoveryAssert(
		asrFavoritesFile('', $etcDir, $webDir, $webDir . '/favorites.ini')
			=== $etcDir . '/favorites.ini',
		'The primary shared Favorites file was not selected by default.'
	);
	asrFavoritesDiscoveryAssert(
		asrFavoritesFile('favorites-UK-Full.ini', $etcDir, $webDir, $webDir . '/favorites.ini')
			=== $etcDir . '/favorites-UK-Full.ini',
		'A requested shared Favorites list was not selected.'
	);
	asrFavoritesDiscoveryAssert(
		asrFavoritesFile('../../favorites-USA-East.ini', $etcDir, $webDir, $webDir . '/favorites.ini')
			=== $etcDir . '/favorites.ini',
		'A path-qualified Favorites request did not fall back safely.'
	);
	asrFavoritesDiscoveryAssert(
		asrFavoritesFile('../../etc/passwd', $etcDir, $webDir, $webDir . '/favorites.ini')
			=== $etcDir . '/favorites.ini',
		'An unknown path did not fall back to the primary Favorites file.'
	);

	echo "ASR Favorites discovery self-test: ok\n";
} finally {
	asrFavoritesDiscoveryRemoveTree($root);
}
