#!/usr/bin/env php
<?php
declare(strict_types=1);
require_once __DIR__ . '/../compat/allscan-v1.02/include/asrPerformanceContract.php';
set_error_handler(static function(int $severity, string $message): never { throw new ErrorException($message, 0, $severity); });
$payload = [
    'ok'=>true,'updated'=>'2026-09-26T18:24:25+00:00','mode'=>'Standard','cpuTemp'=>'144°F / 62°C',
    'load'=>['one'=>0.16,'five'=>0.23,'fifteen'=>0.43],
    'memory'=>['used'=>'3.1 GB','total'=>'6.4 GB','percent'=>48.4],
    'disk'=>['used'=>'102.8 GB','total'=>'232.6 GB','percent'=>44.2],
    'uptime'=>asrPerformanceUptimeLabel(445408.25),'activeViewers'=>1,'requestsLastMinute'=>3,
    'apacheWorkers'=>12,'asteriskRunning'=>false,'statusCacheAge'=>1,
    'bridgeCollector'=>'unknown','integrityTimer'=>'unknown',
];
assert($payload['uptime'] === '5d 3h 43m');
assert(asrPerformancePayloadValid($payload));
$json = json_encode($payload, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES);
assert(is_array(json_decode($json, true, 512, JSON_THROW_ON_ERROR)));
unset($payload['load']);
assert(!asrPerformancePayloadValid($payload));
restore_error_handler();
echo "ASR Performance Stats response-contract self-test passed\n";
