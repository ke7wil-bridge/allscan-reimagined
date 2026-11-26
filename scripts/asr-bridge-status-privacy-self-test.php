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

if (is_file(__DIR__ . '/../asr-api.php')) {
$api = file_get_contents(__DIR__ . '/../asr-api.php');
$start = strpos($api, "if (\$action === 'standard-bridge-link') {");
$end = strpos($api, "if (\$action === 'standard-bridge-repair') {", $start);
assertPrivacy($start !== false && $end !== false, 'standard link action missing');
$route = substr($api, $start, $end - $start);
foreach (['asr_require_post()', 'asr_require_same_origin()', 'asr_require_modify()', 'asr-standard-link', 'escapeshellarg($verb)', 'escapeshellarg($bridgeId)'] as $guard) {
    assertPrivacy(str_contains($route, $guard), 'standard link action missing guard: ' . $guard);
}
}
echo "ASR bridge-status privacy self-test: ok\n";
