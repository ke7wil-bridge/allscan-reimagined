#!/usr/bin/env php
<?php
declare(strict_types=1);

require_once __DIR__ . '/../compat/allscan-v1.02/include/asrBridgeStatus.php';

function assertPrivacy(bool $condition, string $message): void {
    if (!$condition) throw new RuntimeException($message);
}

$controls = asrPublicBridgeControls([
    'dmr_net' => [
        'ready' => false,
        'linked' => true,
        'digitalLinked' => true,
        'allstarLinked' => false,
        'currentTg' => '3100',
        'currentDestination' => '3100',
        'currentDestinationLabel' => 'Example',
        'reason' => 'DMR backend not ready: private detail.',
        'missing' => ['private detail'],
        'abinfoAvailable' => true,
    ],
    '../invalid' => ['reason' => 'must not survive'],
]);
assertPrivacy(isset($controls['dmr_net']), 'valid bridge control was removed');
assertPrivacy(($controls['dmr_net']['ready'] ?? true) === false, 'ready state was not preserved');
assertPrivacy(($controls['dmr_net']['linked'] ?? false) === true, 'linked state was not preserved');
assertPrivacy(($controls['dmr_net']['currentTg'] ?? '') === '3100', 'current destination was not preserved');
assertPrivacy(!array_key_exists('reason', $controls['dmr_net']), 'backend reason reached the public payload');
assertPrivacy(!array_key_exists('missing', $controls['dmr_net']), 'missing readiness details reached the public payload');
assertPrivacy(!array_key_exists('abinfoAvailable', $controls['dmr_net']), 'internal availability flag reached the public payload');
assertPrivacy(!isset($controls['../invalid']), 'invalid bridge ID reached the public payload');

$live = asrPublicBridgeLiveStatuses([
    'standard' => ['warning' => 'Standard runtime warning.'],
    'dmr_net' => ['warning' => 'Detailed backend path failure.'],
    'ysf_net' => ['warning' => '-'],
], [
    ['id' => 'standard', 'cardType' => 'standard'],
    ['id' => 'dmr_net', 'cardType' => 'dmr_net'],
    ['id' => 'ysf_net', 'cardType' => 'ysf_net'],
]);
assertPrivacy(($live['standard']['warning'] ?? '') === 'Standard runtime warning.', 'standard warning was changed');
assertPrivacy(
    ($live['dmr_net']['warning'] ?? '') === 'Bridge status needs attention. Review Bridge Settings.',
    'Net Bridge warning was not made generic',
);
assertPrivacy(($live['ysf_net']['warning'] ?? 'unexpected') === '', 'empty Net Bridge warning was not normalized');

$eventLines = [
    json_encode(['event'=>'tx_start', 'mode'=>'DMR2', 'callsign'=>'W2HAZ', 'epoch'=>100]),
    json_encode(['event'=>'tx_stop', 'mode'=>'DMR2', 'callsign'=>'W2HAZ', 'epoch'=>101]),
];
for ($index = 0; $index < 140; $index++) {
    $eventLines[] = json_encode(['event'=>'tx_stop', 'mode'=>'USRP', 'callsign'=>'KE7WIL', 'epoch'=>200 + $index]);
}
$eventLines[] = json_encode(['event'=>'tx_stop', 'mode'=>'YSF', 'callsign'=>'YSF-LIVE', 'epoch'=>400]);
$window = asrRecentUrfEvents($eventLines, static fn(string $callsign, string $mode): bool => $callsign === 'YSF-LIVE');
$dmrEvents = array_values(array_filter($window, static fn(array $event): bool => ($event['mode'] ?? '') === 'DMR'));
$usrpEvents = array_values(array_filter($window, static fn(array $event): bool => ($event['mode'] ?? '') === 'USRP'));
assertPrivacy(count($dmrEvents) === 2 && ($dmrEvents[1]['callsign'] ?? '') === 'W2HAZ', 'noisy USRP traffic evicted DMR talker history');
assertPrivacy(count($usrpEvents) === 100, 'per-mode URF event history was not bounded');
assertPrivacy(count(array_filter($window, static fn(array $event): bool => ($event['mode'] ?? '') === 'YSF')) === 0, 'filtered service identity consumed bridge history');

echo "ASR bridge-status privacy self-test: ok\n";
