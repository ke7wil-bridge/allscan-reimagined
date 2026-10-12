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
file_put_contents($astdb, "497170|KQ6K|145.585|Moorpark, CA\n497171|KQ6K|446.02|Moorpark, CA\n3100001|KO6LUZ|DMR|Mesa, Arizona\n3100002|SRVPROBA|YSF|Belgrade, Serbia\n");

$nodeDatabase = [
	'63916' => ['63916', 'NY7S', '147.300', 'Sun City West, Arizona'],
	'53430' => ['53430', 'KE7WIL', 'Full-Duplex 4 Life', 'Phoenix, Arizona'],
];
$ordinaryCurrent = asrEnrichAllStarTalker([
	'node'=>'63916', 'info'=>'NY7S 147.300 Sun City West, Arizona',
	'source'=>'AllStar', 'started_epoch'=>1791322064,
], $nodeDatabase);
check($ordinaryCurrent['info'] === 'NY7S', 'ordinary AllStar callsign was not separated from its node metadata');
check($ordinaryCurrent['description'] === '147.300', 'ordinary AllStar description was not restored from ASTDB');
check($ordinaryCurrent['location'] === 'Sun City West, Arizona', 'ordinary AllStar location was not restored from ASTDB');

$ordinaryHistory = asrEnrichAllStarTalkers(json_decode(json_encode([
	['node'=>'63916', 'info'=>'NY7S 147.300 Sun City West, Arizona', 'source'=>'AllStar', 'event_epoch'=>1791322067],
	['node'=>'53430', 'info'=>'KE7WIL Full-Duplex 4 Life  Phoenix, Arizona', 'source'=>'AllStar', 'event_epoch'=>1791322050],
]), true), $nodeDatabase);
check(count($ordinaryHistory) === 2, 'ordinary AllStar history events were lost or deduplicated');
check($ordinaryHistory[0]['description'] === '147.300' && $ordinaryHistory[0]['location'] === 'Sun City West, Arizona', 'persisted 63916 history metadata was not repaired');
check($ordinaryHistory[1]['description'] === 'Full-Duplex 4 Life' && $ordinaryHistory[1]['location'] === 'Phoenix, Arizona', 'persisted 53430 history metadata was not repaired');

$unknown = ['node'=>'7654321', 'info'=>'UNKNOWN Mixed Metadata', 'source'=>'AllStar'];
check(asrEnrichAllStarTalker($unknown, $nodeDatabase) === $unknown, 'unknown-node fallback metadata was modified without an exact ASTDB match');
$privateTransport = ['node'=>'1999', 'info'=>'KQ6K', 'source'=>'YSF', 'transport'=>'Net Bridge'];
check(asrEnrichAllStarTalker($privateTransport, $nodeDatabase) === $privateTransport, 'Net Bridge identity was modified by ordinary AllStar enrichment');

$localCos = asrSelectCurrentTalker([
	['node'=>'1', 'info'=>'COS-Detected', 'keyed'=>'', 'cos_keyed'=>1, 'tx_keyed'=>1],
	['node'=>'53430', 'info'=>'KE7WIL Full-Duplex 4 Life Phoenix, Arizona', 'keyed'=>'no', 'cos_keyed'=>0, 'tx_keyed'=>0],
], '63916', 'NY7S 147.300 Sun City West, Arizona', $nodeDatabase, 1791834000);
check($localCos['node'] === '63916', 'local COS was not attributed to the configured local node');
check($localCos['info'] === 'NY7S', 'local COS did not resolve the repeater callsign');
check($localCos['description'] === '147.300' && $localCos['location'] === 'Sun City West, Arizona', 'local COS card metadata was not separated');
check($localCos['started_epoch'] === 1791834000, 'local COS talker start time was not retained');

$txOnly = asrSelectCurrentTalker([
	['node'=>'1', 'info'=>'TX-Detected', 'keyed'=>'', 'cos_keyed'=>0, 'tx_keyed'=>1],
], '63916', 'NY7S 147.300 Sun City West, Arizona', $nodeDatabase, 1791834001);
check($txOnly === null, 'transmitter-only activity was incorrectly shown as a local RF talker');

$remoteWins = asrSelectCurrentTalker([
	['node'=>'1', 'info'=>'COS-Detected', 'keyed'=>'', 'cos_keyed'=>1, 'tx_keyed'=>1],
	['node'=>'53430', 'info'=>'KE7WIL Full-Duplex 4 Life Phoenix, Arizona', 'keyed'=>'yes', 'last_keyed'=>'00:00:03'],
], '63916', 'NY7S 147.300 Sun City West, Arizona', $nodeDatabase, 1791834002);
check($remoteWins['node'] === '53430' && $remoteWins['info'] === 'KE7WIL', 'a keyed linked node did not retain priority over the local COS fallback');
check($remoteWins['description'] === 'Full-Duplex 4 Life' && $remoteWins['location'] === 'Phoenix, Arizona', 'linked AllStar metadata regressed during talker selection');

$idle = asrSelectCurrentTalker([
	['node'=>'1', 'info'=>'NO CONNECTION', 'keyed'=>'', 'cos_keyed'=>0, 'tx_keyed'=>0],
	['node'=>'53430', 'info'=>'KE7WIL Full-Duplex 4 Life Phoenix, Arizona', 'keyed'=>'no'],
], '63916', 'NY7S 147.300 Sun City West, Arizona', $nodeDatabase, 1791834003);
check($idle === null, 'idle status produced a false talker');

$state = asrAdvanceTalkerState(null, $localCos, [], 1791834000);
check($state['current']['started_epoch'] === 1791834000 && !$state['history_changed'], 'local COS did not start a new current-talker event');
$steadyLocal = asrSelectCurrentTalker([
	['node'=>'1', 'info'=>'COS-Detected', 'cos_keyed'=>1, 'tx_keyed'=>1],
], '63916', 'NY7S 147.300 Sun City West, Arizona', $nodeDatabase, 1791834004);
$state = asrAdvanceTalkerState($state['current'], $steadyLocal, $state['history'], 1791834004);
check($state['current']['started_epoch'] === 1791834000 && count($state['history']) === 0, 'steady local COS reset duration or created duplicate history');
$state = asrAdvanceTalkerState($state['current'], null, $state['history'], 1791834007);
check($state['current'] === null && $state['history_changed'], 'local COS release did not close the current-talker event');
check($state['history'][0]['node'] === '63916' && $state['history'][0]['duration'] === 7 && $state['history'][0]['event_epoch'] === 1791834007, 'local COS history lost node, duration, or event time');

for($event = 1; $event <= 5; $event++) {
	$start = 1791834010 + ($event * 10);
	$next = asrSelectCurrentTalker([
		['node'=>'1', 'info'=>'COS-Detected', 'cos_keyed'=>1, 'tx_keyed'=>1],
	], '63916', 'NY7S 147.300 Sun City West, Arizona', $nodeDatabase, $start);
	$state = asrAdvanceTalkerState($state['current'], $next, $state['history'], $start);
	$state = asrAdvanceTalkerState($state['current'], null, $state['history'], $start + $event);
}
check(count($state['history']) === 4, 'talker history did not remain capped at four transmission events');
check(array_column($state['history'], 'duration') === [5, 4, 3, 2], 'repeated local transmissions were lost, deduplicated, or misordered');
check(count(array_unique(array_column($state['history'], 'info'))) === 1 && $state['history'][0]['info'] === 'NY7S', 'local callsign did not survive repeated talker history events');

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

$bridgeEvidence = asrBridgeTalkerEvidenceFromPayloads([
	'bridges' => [
		['id'=>'dmr', 'mode'=>'dmr', 'node'=>'1883', 'title'=>'DMR Bridge'],
		['id'=>'ysf', 'mode'=>'ysf', 'node'=>'1889', 'title'=>'YSF Bridge'],
		['id'=>'p25', 'mode'=>'p25', 'node'=>'1887', 'title'=>'P25 Bridge'],
		['id'=>'zello', 'mode'=>'zello', 'node'=>'1001', 'title'=>'Zello Bridge'],
	],
], [[
	'bridges' => [
		'dmr' => ['node'=>'1883', 'role'=>'idle', 'tx_events'=>[
			['callsign'=>'KO6LUZ', 'start_epoch'=>1791834557, 'epoch'=>1791834559, 'duration_seconds'=>2],
		]],
		'ysf' => ['node'=>'1889', 'role'=>'source', 'current_user'=>'SRVPROBA', 'active_start_epoch'=>1791834600],
		'p25' => ['node'=>'1887', 'recentTalkers'=>[
			['callsign'=>'N0CALL', 'startEpoch'=>1791834700, 'eventEpoch'=>1791834704, 'duration'=>4],
		]],
	],
]], [
	'active'=>false,
	'recent_users'=>[['name'=>'ny2us', 'last_seen_epoch'=>1791834800]],
]);
check(count($bridgeEvidence['nodes']) === 4, 'configured bridge transport map is incomplete');

$dmrHistory = asrEnrichBridgeTalker([
	'node'=>'1883', 'info'=>'DMR Bridge', 'description'=>'Bridge', 'source'=>'AllStar',
	'duration'=>1, 'started_epoch'=>1791834558, 'event_epoch'=>1791834559,
], [], $astdb, $bridgeEvidence);
check($dmrHistory['info'] === 'KO6LUZ', 'DMR transport did not resolve to its evidence-backed callsign');
check($dmrHistory['source'] === 'DMR', 'DMR protocol provenance was not retained');
check($dmrHistory['description'] === 'DMR Bridge', 'DMR transport description was not normalized');
check($dmrHistory['location'] === 'Mesa, Arizona', 'DMR callsign location was not resolved');
check($dmrHistory['duration'] === 1 && $dmrHistory['started_epoch'] === 1791834558 && $dmrHistory['event_epoch'] === 1791834559, 'DMR card timing fields regressed');
check($dmrHistory['identity_provenance'] === 'bridge_runtime', 'DMR runtime provenance was not recorded');

$ysfCurrent = asrEnrichBridgeTalker([
	'node'=>'1889', 'info'=>'YSF Bridge', 'source'=>'AllStar', 'started_epoch'=>1791834600,
], [], $astdb, $bridgeEvidence);
check($ysfCurrent['info'] === 'SRVPROBA' && $ysfCurrent['source'] === 'YSF', 'active YSF transport did not resolve to its caller');
check($ysfCurrent['description'] === 'YSF Bridge' && $ysfCurrent['location'] === 'Belgrade, Serbia', 'active YSF card metadata regressed');

$p25History = asrEnrichBridgeTalker([
	'node'=>'1887', 'info'=>'P25 Bridge', 'source'=>'AllStar',
	'started_epoch'=>1791834700, 'event_epoch'=>1791834704, 'duration'=>4,
], [], $astdb, $bridgeEvidence);
check($p25History['info'] === 'N0CALL' && $p25History['source'] === 'P25', 'P25 recent-talker evidence was not applied');

$zelloHistory = asrEnrichBridgeTalker([
	'node'=>'1001', 'info'=>'Zello Bridge', 'source'=>'AllStar',
	'started_epoch'=>1791834800, 'event_epoch'=>1791834800, 'duration'=>0,
], [], $astdb, $bridgeEvidence);
check($zelloHistory['info'] === 'NY2US' && $zelloHistory['source'] === 'ZELLO', 'Zello recent-talker evidence was not applied');

$unprovenConfigured = asrEnrichBridgeTalker([
	'node'=>'1883', 'info'=>'DMR Bridge', 'source'=>'AllStar',
	'started_epoch'=>1791834900, 'event_epoch'=>1791834902,
], [], $astdb, $bridgeEvidence);
check($unprovenConfigured === null, 'unproven configured bridge transport leaked into Talker history');

$cardFeed = asrSanitizeTalkerFeed(['674982'=>[
	'current_talker'=>['node'=>'1889', 'info'=>'YSF Bridge', 'source'=>'AllStar', 'started_epoch'=>1791834600],
	'last_talkers'=>[
		['node'=>'1883', 'info'=>'DMR Bridge', 'description'=>'Bridge', 'source'=>'AllStar', 'duration'=>1, 'started_epoch'=>1791834558, 'event_epoch'=>1791834559],
		['node'=>'63916', 'info'=>'NY7S', 'description'=>'147.300', 'location'=>'Sun City West, Arizona', 'source'=>'AllStar', 'duration'=>5, 'started_epoch'=>1791834500, 'event_epoch'=>1791834505],
	],
]], [], $astdb, $bridgeEvidence);
check($cardFeed['674982']['current_talker']['info'] === 'SRVPROBA', 'current card lost the bridge callsign');
check(count($cardFeed['674982']['last_talkers']) === 2, 'valid bridge or AllStar card history was lost');
check($cardFeed['674982']['last_talkers'][0]['info'] === 'KO6LUZ', 'last card lost the DMR callsign');
check($cardFeed['674982']['last_talkers'][1]['location'] === 'Sun City West, Arizona', 'ordinary AllStar location regressed while enriching bridges');

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
