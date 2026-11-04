#!/usr/bin/env php
<?php

require_once dirname(__DIR__) . '/compat/allscan-v1.02/astapi/asrEchoLink.php';
if(!defined('NL'))
	define('NL', "\n");
require_once dirname(__DIR__) . '/compat/allscan-v1.02/astapi/AMI.php';

function asrEchoLinkCheck($condition, $message) {
	if(!$condition)
		throw new RuntimeException($message);
}

// Regression: invoking chan_echolink's node-table command from the long-lived
// ASTAPI AMI session can deadlock the EchoLink channel and leave app_rpt's
// telemetry channel permanently keyed. Callsign enrichment must remain
// independent of Asterisk status polling.
foreach(['1.02', '1.03', '1.04', '1.05'] as $version) {
	$serverSource = file_get_contents(dirname(__DIR__) . '/compat/allscan-v' . $version . '/astapi/server.php');
	asrEchoLinkCheck(is_string($serverSource), "AllScan $version ASTAPI server source was unavailable.");
	asrEchoLinkCheck(
		strpos($serverSource, "commandOutput(\$fp, 'echolink show nodes')") === false,
		"AllScan $version reintroduced the blocking EchoLink command into ASTAPI."
	);
	asrEchoLinkCheck(
		strpos($serverSource, 'asrEchoLinkOfficialLabelCached') !== false,
		"AllScan $version is missing independent EchoLink callsign enrichment."
	);
}

$fixture = "   Node  Call Sign  IP Address        Name\r\n"
	. " 123456  N0TEST-L   host-one          Test station\r\n"
	. " 654321  *EXAMPLE*  host-two\r\n"
	. " 111111  AB1CDEFGHIJ boundary-host     Boundary\r\n"
	. " 111112  AB1CDEFGHIJK rejected-host    Too long\r\n"
	. " 222222  BAD<script> rejected-host     Rejected\r\n";
$rows = asrEchoLinkConnectedCallsigns($fixture);
asrEchoLinkCheck(
	$rows === ['123456' => 'N0TEST-L', '654321' => '*EXAMPLE*', '111111' => 'AB1CDEFGHIJ'],
	'Connected EchoLink rows were not parsed safely.'
);
asrEchoLinkCheck(asrEchoLinkNodeNumber('3123456') === '123456', 'Asterisk EchoLink node conversion failed.');
asrEchoLinkCheck(asrEchoLinkNodeNumber('3000042') === '42', 'Zero-padded EchoLink node conversion failed.');
asrEchoLinkCheck(asrEchoLinkNodeNumber('123456') === '', 'A regular AllStar node was treated as EchoLink.');
asrEchoLinkCheck(asrEchoLinkNodeNumber('3000000') === '', 'Zero was treated as a valid EchoLink node.');
asrEchoLinkCheck(
	asrEchoLinkConnectedLabel($rows, '3123456') === 'N0TEST-L [EchoLink 123456]',
	'Connected EchoLink callsign label was not produced.'
);
asrEchoLinkCheck(asrEchoLinkConnectedLabel($rows, '3999999') === '', 'Unknown EchoLink row did not preserve the stock fallback.');

$officialFixture = '<table><tr><th>Callsign</th><th>Node Number</th></tr>'
	. '<tr><td>M6PBY</td><td align="right">934210</td></tr></table>';
asrEchoLinkCheck(
	asrEchoLinkOfficialCallsignFromHtml($officialFixture, '934210') === 'M6PBY',
	'Official EchoLink lookup response was not parsed.'
);
asrEchoLinkCheck(
	asrEchoLinkOfficialCallsignFromHtml('<tr><td>BAD&lt;TAG&gt;</td><td>934210</td></tr>', '934210') === '',
	'Unsafe official EchoLink callsign was accepted.'
);
$officialLoads = 0;
$officialCache = [];
$officialLoader = function($number) use (&$officialLoads) {
	$officialLoads++;
	return $number === '934210' ? 'M6PBY' : '';
};
asrEchoLinkCheck(
	asrEchoLinkOfficialLabelCached($officialCache, '3934210', $officialLoader, 10.0)
		=== 'M6PBY [EchoLink 934210]',
	'Official EchoLink label was not produced.'
);
asrEchoLinkOfficialLabelCached($officialCache, '3934210', $officialLoader, 20.0);
asrEchoLinkCheck($officialLoads === 1, 'Successful official EchoLink lookup was not cached.');
asrEchoLinkCheck(
	asrEchoLinkOfficialLabelCached($officialCache, '3123456', $officialLoader, 20.0) === '',
	'Failed official lookup did not preserve the honest fallback.'
);

$loads = 0;
$cache = [];
$loader = function() use (&$loads, $fixture) {
	$loads++;
	return $fixture;
};
asrEchoLinkConnectedRowsCached($cache, $loader, 10.0);
asrEchoLinkConnectedRowsCached($cache, $loader, 11.9);
asrEchoLinkCheck($loads === 1, 'Fresh connected-node data was reloaded for every EchoLink row.');
asrEchoLinkConnectedRowsCached($cache, $loader, 12.1);
asrEchoLinkCheck($loads === 2, 'Expired connected-node data was not refreshed.');
$failedCache = [];
$failedRows = asrEchoLinkConnectedRowsCached($failedCache, function() { return 'ERROR'; }, 20.0);
asrEchoLinkCheck($failedRows === [], 'A failed Asterisk command created a false callsign.');

$fallbackLoads = 0;
$fallbackCache = [];
$amiPreferred = asrEchoLinkConnectedOutput($fixture, function() use (&$fallbackLoads) {
	$fallbackLoads++;
	return '';
}, $fallbackCache, 10.0);
asrEchoLinkCheck($amiPreferred === $fixture, 'Usable AMI EchoLink rows did not win.');
asrEchoLinkCheck($fallbackLoads === 0, 'CLI fallback ran despite usable AMI rows.');
$cliFallback = asrEchoLinkConnectedOutput('Message: Command output follows', function() use (&$fallbackLoads) {
	$fallbackLoads++;
	return " 123456  N0TEST-L   connected-host    Test station\n";
}, $fallbackCache, 20.0);
asrEchoLinkCheck(
	asrEchoLinkConnectedCallsigns($cliFallback) === ['123456' => 'N0TEST-L'],
	'CLI fallback rows were not used when AMI returned no rows.'
);
asrEchoLinkCheck($fallbackLoads === 1, 'CLI fallback did not run exactly once.');
$cachedFallback = asrEchoLinkConnectedOutput('Message: Command output follows', function() use (&$fallbackLoads) {
	$fallbackLoads++;
	return '';
}, $fallbackCache, 29.9);
asrEchoLinkCheck($cachedFallback === $cliFallback, 'CLI fallback cache did not preserve output.');
asrEchoLinkCheck($fallbackLoads === 1, 'CLI fallback reran before its cache expired.');
asrEchoLinkConnectedOutput('Message: Command output follows', function() use (&$fallbackLoads) {
	$fallbackLoads++;
	return '';
}, $fallbackCache, 30.1);
asrEchoLinkCheck($fallbackLoads === 2, 'CLI fallback did not retry after cache expiry.');

class AsrEchoLinkTestAMI extends AMI {
	public $fixture = [];
	function getResponse($fp, $actionID, $debug=false) {
		return $this->fixture;
	}
}

$ami = new AsrEchoLinkTestAMI();
$fp = fopen('php://temp', 'r+');
$ami->fixture = [
	'Response: Follows',
	'Privilege: Command',
	'ActionID: ignored-by-test',
	'Command output follows',
	'   Node  Call Sign  IP Address        Name',
	' 123456  N0TEST-L   connected-host    Test station',
	'--END COMMAND--',
];
$asl2Output = $ami->commandOutput($fp, 'echolink show nodes');
asrEchoLinkCheck(
	asrEchoLinkConnectedCallsigns($asl2Output) === ['123456' => 'N0TEST-L'],
	'ASL2 raw AMI command output was not preserved.'
);
$ami->fixture = [
	'Response: Success',
	'ActionID: ignored-by-test',
	'Output:    Node  Call Sign  IP Address        Name',
	'Output:  123456  N0TEST-L   connected-host    Test station',
	'Output: --END COMMAND--',
];
$asl3Output = $ami->commandOutput($fp, 'echolink show nodes');
asrEchoLinkCheck(
	asrEchoLinkConnectedCallsigns($asl3Output) === ['123456' => 'N0TEST-L'],
	'ASL3 Output-prefixed AMI command output was not preserved.'
);
$ami->fixture = [
	'Response: Error',
	'ActionID: ignored-by-test',
	'Message: Permission denied',
];
asrEchoLinkCheck(
	$ami->commandOutput($fp, 'echolink show nodes') === 'ERROR',
	'AMI error response did not fail closed.'
);
$ami->fixture = 'Timeout';
asrEchoLinkCheck(
	$ami->commandOutput($fp, 'echolink show nodes') === 'Timeout',
	'AMI timeout response was not preserved.'
);
fclose($fp);

echo "ASR EchoLink connected-callsign self-test: ok\n";
