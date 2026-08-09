<?php
declare(strict_types=1);

require_once dirname(__DIR__) . '/compat/allscan-v1.01/include/asrRuntime.php';

function asrRuntimeAssert(bool $condition, string $message): void {
	if(!$condition)
		throw new RuntimeException($message);
}

$root = sys_get_temp_dir() . '/asr-runtime-source-self-test.' . getmypid();
$asrDir = $root . '/asr';
$stockDir = $root . '/allscan';

try {
	mkdir($asrDir, 0700, true);
	mkdir($stockDir, 0700, true);
	$filename = 'connected-clients.json';
	$asrPath = $asrDir . '/' . $filename;
	$stockPath = $stockDir . '/' . $filename;

	asrRuntimeAssert(
		asrRuntimeFilePath($filename, $asrDir, $stockDir) === $asrPath,
		'Missing runtime files must fall back to the ASR path.'
	);

	file_put_contents($stockPath, "{\"source\":\"stock\"}\n");
	touch($stockPath, time() - 20);
	asrRuntimeAssert(
		asrRuntimeFilePath($filename, $asrDir, $stockDir) === $stockPath,
		'A stock-only runtime file was not selected.'
	);

	file_put_contents($asrPath, "{\"source\":\"asr\"}\n");
	touch($asrPath, time() - 10);
	asrRuntimeAssert(
		asrRuntimeFilePath($filename, $asrDir, $stockDir) === $asrPath,
		'The newer ASR runtime file was not selected.'
	);

	touch($stockPath, time());
	asrRuntimeAssert(
		asrRuntimeFilePath($filename, $asrDir, $stockDir) === $stockPath,
		'The newer stock runtime file was not selected.'
	);

	touch($asrPath, time());
	asrRuntimeAssert(
		asrRuntimeFilePath($filename, $asrDir, $stockDir) === $asrPath,
		'Equal timestamps must prefer the ASR runtime file.'
	);

	$rejected = false;
	try {
		asrRuntimeFilePath('../unsafe.json', $asrDir, $stockDir);
	} catch(InvalidArgumentException $error) {
		$rejected = true;
	}
	asrRuntimeAssert($rejected, 'An unapproved runtime filename was accepted.');

	foreach(['', '-', '--', 'unknown', 'NONE', 'N/A', 'na', 'null'] as $placeholder) {
		asrRuntimeAssert(
			asrClientIdentityValue($placeholder) === '',
			'Placeholder client identity was treated as a real identity.'
		);
	}
	asrRuntimeAssert(
		asrClientIdentityValue('  K1ABC  ') === 'k1abc',
		'A real client identity was not normalized.'
	);
	$fixtureKeys = [];
	foreach([
		['callsign' => 'K1AAA', 'name' => '-', 'dmrid' => '100001', 'id' => '100001'],
		['callsign' => 'K2BBB', 'name' => '-', 'dmrid' => '100002', 'id' => '100002'],
		['callsign' => 'K3CCC', 'name' => '-', 'dmrid' => '100003', 'id' => '100003'],
	] as $row) {
		$keys = asrClientIdentityKeys($row);
		asrRuntimeAssert(!in_array('name:-', $keys, true), 'Placeholder name entered client identity keys.');
		$fixtureKeys[] = $keys;
	}
	asrRuntimeAssert(
		count(array_intersect($fixtureKeys[0], $fixtureKeys[1])) === 0
		&& count(array_intersect($fixtureKeys[0], $fixtureKeys[2])) === 0
		&& count(array_intersect($fixtureKeys[1], $fixtureKeys[2])) === 0,
		'Distinct clients sharing a placeholder name were merged.'
	);

	echo "ASR side-by-side runtime source self-test: ok\n";
} finally {
	if(is_dir($root)) {
		$iterator = new RecursiveIteratorIterator(
			new RecursiveDirectoryIterator(
				$root,
				FilesystemIterator::SKIP_DOTS
			),
			RecursiveIteratorIterator::CHILD_FIRST
		);
		foreach($iterator as $path) {
			if($path->isDir())
				rmdir($path->getPathname());
			else
				unlink($path->getPathname());
		}
		rmdir($root);
	}
}
