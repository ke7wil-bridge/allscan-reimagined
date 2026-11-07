#!/usr/bin/env php
<?php
declare(strict_types=1);

require_once dirname(__DIR__) . '/compat/allscan-v1.02/include/asrFavorites.php';

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

	foreach(['favorites-missing.ini', '../../favorites-USA-East.ini'] as $requested) {
		$rejected = false;
		try {
			asrFavoritesFile($requested, $etcDir, $webDir, $webDir . '/favorites.ini', true);
		} catch(RuntimeException $error) {
			$rejected = str_contains($error->getMessage(), 'no substitute list');
		}
		asrFavoritesDiscoveryAssert($rejected, 'An explicit unavailable selection silently used a different list.');
	}

	// An unreadable canonical file still owns its name. A readable web copy must
	// never hide the operator's actual file or make permissions look like data loss.
	chmod($etcDir . '/favorites-USA-East.ini', 0000);
	clearstatcache();
	asrFavoritesDiscoveryAssert(
		asrFavoritesFile('favorites-USA-East.ini', $etcDir, $webDir, $webDir . '/favorites.ini', true)
			=== $etcDir . '/favorites-USA-East.ini',
		'Unreadable canonical Favorites was silently replaced by a readable duplicate.'
	);
	chmod($etcDir . '/favorites-USA-East.ini', 0600);

	$sharedHelper = file_get_contents(dirname(__DIR__) . '/compat/allscan-v1.02/include/asrFavorites.php');
	foreach(['1.03', '1.04', '1.05'] as $version)
		asrFavoritesDiscoveryAssert(
			file_get_contents(dirname(__DIR__) . '/compat/allscan-v' . $version . '/include/asrFavorites.php') === $sharedHelper,
			'Favorites source selection drifted between supported AllScan versions.'
		);

	echo "ASR Favorites discovery self-test: ok\n";
} finally {
	asrFavoritesDiscoveryRemoveTree($root);
}
