#!/usr/bin/env php
<?php
require_once dirname(__DIR__) . '/compat/allscan-v1.02/astapi/asrTalkerIdentity.php';

function check($condition, $message) {
	if(!$condition) throw new RuntimeException($message);
}

$events = [
	['event'=>'tx_start','mode'=>'YSF','callsign'=>'KQ6K','module'=>'A','epoch'=>1791321974],
	['event'=>'tx_stop','mode'=>'YSF','callsign'=>'KQ6K','module'=>'A','epoch'=>1791322050],
	['event'=>'tx_start','mode'=>'YSF','callsign'=>'KQ6K','module'=>'A','epoch'=>1791322063],
	['event'=>'tx_stop','mode'=>'YSF','callsign'=>'KQ6K','module'=>'A','epoch'=>1791322068],
];
$astdb = tempnam(sys_get_temp_dir(), 'asr-talker-astdb-');
file_put_contents($astdb, "497170|KQ6K|145.585|Moorpark, CA\n497171|KQ6K|446.02|Moorpark, CA\n");

$persisted = asrEnrichUrfTalker([
	'node'=>'1001', 'info'=>'URFWIL Multi-Mode Bridge', 'source'=>'AllStar',
	'started_epoch'=>1791322064, 'event_epoch'=>1791322067, 'duration'=>3,
], $events, $astdb);
check($persisted['info'] === 'KQ6K', 'node 1001 history did not retain the actual URF caller');
check($persisted['source'] === 'YSF', 'node 1001 history did not retain protocol provenance');
check($persisted['transport'] === 'URFWIL', 'URFWIL transport context was not preserved');
check($persisted['description'] === 'Multi-Mode Bridge', 'transport description was discarded during identity enrichment');
check($persisted['location'] === 'Moorpark, CA', 'caller location was not retained from the local node database');

$split = asrEnrichUrfTalker([
	'node'=>'1001', 'info'=>'URFWIL Multi-Mode Bridge', 'source'=>'AllStar',
	'started_epoch'=>1791321993, 'event_epoch'=>1791322049, 'duration'=>56,
], $events, $astdb);
check($split['info'] === 'KQ6K' && $split['source'] === 'YSF', 'overlapping lifecycle identity did not repair split AllStar history');

$live = asrEnrichUrfTalker([
	'node'=>'1001', 'info'=>'URFWIL Multi-Mode Bridge', 'source'=>'AllStar',
	'started_epoch'=>1791322064,
], array_slice($events, 0, 3), $astdb);
check($live['info'] === 'KQ6K' && $live['source'] === 'YSF', 'open transmission identity was not available at start');

$latched = asrEnrichUrfTalker([
	'node'=>'1001', 'info'=>'KQ6K', 'source'=>'YSF', 'transport'=>'URFWIL',
	'started_epoch'=>1791322064, 'event_epoch'=>1791322067,
], [], $astdb);
check($latched['info'] === 'KQ6K' && $latched['source'] === 'YSF', 'real identity regressed without ephemeral event data');
check($latched['description'] === 'Multi-Mode Bridge' && $latched['location'] === 'Moorpark, CA', 'latched identity metadata was not restored after refresh');



$genericUrf = asrEnrichBridgeTalker([
	'node'=>'1001', 'info'=>'URFWIL Multi-Mode Bridge', 'source'=>'AllStar',
	'started_epoch'=>1791500000, 'event_epoch'=>1791500002,
], [], $astdb);
check($genericUrf === null, 'unproven node 1001 transport leaked into Talker history');
$genericNet = asrEnrichBridgeTalker([
	'node'=>'1999', 'info'=>'Unified Net Bridge', 'source'=>'AllStar',
	'started_epoch'=>1791500000, 'event_epoch'=>1791500002,
], [], $astdb);
check($genericNet === null, 'unproven node 1999 transport leaked into Talker history');

$netLatched = asrEnrichBridgeTalker([
	'node'=>'1999', 'info'=>'KQ6K', 'source'=>'YSF', 'transport'=>'Net Bridge',
	'started_epoch'=>1791322064, 'event_epoch'=>1791322067,
], [], $astdb);
check($netLatched['info'] === 'KQ6K' && $netLatched['source'] === 'YSF', 'node 1999 real identity regressed after refresh');
check($netLatched['transport'] === 'Net Bridge', 'node 1999 transport context was not restored after refresh');
check($netLatched['location'] === 'Moorpark, CA', 'node 1999 latched metadata was not restored after refresh');

$staleFeed = asrSanitizeTalkerFeed(['641890' => [
	'current_talker' => ['node'=>'1999', 'info'=>'Unified Net Bridge', 'source'=>'AllStar'],
	'last_talkers' => [
		['node'=>'1001', 'info'=>'URFWIL Multi-Mode Bridge', 'source'=>'AllStar'],
		['node'=>'1999', 'info'=>'Unified Net Bridge', 'source'=>'AllStar'],
		['node'=>'1999', 'info'=>'KQ6K', 'source'=>'YSF', 'transport'=>'Net Bridge'],
	],
]], [], $astdb);
check($staleFeed['641890']['current_talker'] === null, 'stale ASTAPI cache revived the active node 1999 transport');
check(count($staleFeed['641890']['last_talkers']) === 1, 'stale ASTAPI history revived a generic private transport');
check($staleFeed['641890']['last_talkers'][0]['info'] === 'KQ6K', 'cache sanitization discarded proven digital identity');
check($staleFeed['641890']['last_talkers'][0]['source'] === 'YSF', 'cache sanitization discarded protocol provenance');
check($staleFeed['641890']['last_talkers'][0]['location'] === 'Moorpark, CA', 'cache sanitization discarded known location');

unlink($astdb);

echo "PASS: persisted bridge talker identity\n";
