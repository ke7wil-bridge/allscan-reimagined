#!/usr/bin/env php
<?php
declare(strict_types=1);

$sourceRoot = dirname(__DIR__);
$sourcePath = is_file($sourceRoot . '/server/asr-api.php')
    ? $sourceRoot . '/server/asr-api.php'
    : $sourceRoot . '/asr-api.php';

function fail_test(string $message): never {
    fwrite(STDERR, "lookup/map self-test failed: {$message}\n");
    exit(1);
}

function assert_test(bool $condition, string $message): void {
    if (!$condition) fail_test($message);
}

function source_function(string $sourcePath, string $name): string {
    $tokens = token_get_all((string) file_get_contents($sourcePath));
    $count = count($tokens);
    for ($index = 0; $index < $count; $index++) {
        if (!is_array($tokens[$index]) || $tokens[$index][0] !== T_FUNCTION) continue;
        $candidate = $index + 1;
        while ($candidate < $count) {
            $token = $tokens[$candidate];
            if (is_array($token) && in_array($token[0], [T_WHITESPACE, T_AMPERSAND_NOT_FOLLOWED_BY_VAR_OR_VARARG], true)) {
                $candidate++;
                continue;
            }
            break;
        }
        if ($candidate >= $count || !is_array($tokens[$candidate]) || $tokens[$candidate][0] !== T_STRING || $tokens[$candidate][1] !== $name) {
            continue;
        }

        $text = '';
        $depth = 0;
        $opened = false;
        for ($cursor = $index; $cursor < $count; $cursor++) {
            $piece = is_array($tokens[$cursor]) ? $tokens[$cursor][1] : $tokens[$cursor];
            $text .= $piece;
            if ($piece === '{') {
                $opened = true;
                $depth++;
            } elseif ($piece === '}') {
                $depth--;
                if ($opened && $depth === 0) return $text;
            }
        }
    }
    fail_test("could not extract {$name} from asr-api.php");
}

function load_functions(string $sourcePath, array $names): void {
    foreach ($names as $name) {
        eval(source_function($sourcePath, $name));
    }
}

function run_qrz_tests(string $sourcePath): void {
    $GLOBALS['mockHttpPayloads'] = [];
    $GLOBALS['mockHttpCalls'] = 0;

    function asr_http_get(string $url, int $timeout = 5): string {
        $GLOBALS['mockHttpCalls']++;
        return (string) array_shift($GLOBALS['mockHttpPayloads']);
    }

    load_functions($sourcePath, ['asr_xml_value', 'asr_qrz_session', 'asr_qrz_station']);

    $before = $GLOBALS['mockHttpCalls'];
    assert_test(asr_qrz_session(['qrz' => []]) === '', 'missing QRZ credentials must skip login');
    assert_test($GLOBALS['mockHttpCalls'] === $before, 'missing QRZ credentials made an HTTP request');

    $GLOBALS['mockHttpPayloads'][] = '<QRZDatabase><Session><Error>Invalid credentials</Error></Session></QRZDatabase>';
    assert_test(asr_qrz_session(['qrz' => ['username' => 'test', 'password' => 'bad']]) === '', 'bad QRZ credentials must not produce a session');

    $GLOBALS['mockHttpPayloads'][] = '<QRZDatabase><Session><Key>session-key</Key></Session></QRZDatabase>';
    assert_test(asr_qrz_session(['qrz' => ['username' => 'test', 'password' => 'valid']]) === 'session-key', 'valid QRZ credentials did not produce a session');

    $GLOBALS['mockHttpPayloads'][] = '<QRZDatabase><Callsign><call>N7YO</call><fname>Jim</fname><name>Operator</name><addr2>Phoenix</addr2><state>AZ</state><country>USA</country><lat>33.448376</lat><lon>-112.074036</lon></Callsign><Session><Key>next-key</Key></Session></QRZDatabase>';
    $session = 'session-key';
    $station = asr_qrz_station('N7YO', $session);
    assert_test(!empty($station['resolved']), 'valid QRZ station response was not resolved');
    assert_test(($station['lat'] ?? null) === 33.45 && ($station['lng'] ?? null) === -112.07, 'QRZ coordinates were not rounded');
    assert_test($session === 'next-key', 'QRZ session refresh key was not retained');

    $GLOBALS['mockHttpPayloads'][] = '<QRZDatabase><Callsign><call>N7YO</call><lat>999</lat><lon>-112</lon></Callsign></QRZDatabase>';
    $station = asr_qrz_station('N7YO', $session);
    assert_test(($station['resolved'] ?? true) === false, 'invalid QRZ coordinates were accepted');

    $before = $GLOBALS['mockHttpCalls'];
    $emptySession = '';
    assert_test(asr_qrz_station('N7YO', $emptySession) === [], 'empty QRZ session must return no station');
    assert_test($GLOBALS['mockHttpCalls'] === $before, 'empty QRZ session made an HTTP request');
}

function run_map_tests(string $sourcePath): void {
    $temporaryDirectory = sys_get_temp_dir() . '/asr-lookup-map-test-' . getmypid();
    if (!mkdir($temporaryDirectory, 0700) && !is_dir($temporaryDirectory)) {
        fail_test('could not create temporary cache directory');
    }
    define('ASR_STATION_MAP_CACHE', $temporaryDirectory . '/station-map-cache.json');

    $GLOBALS['mapQrzMode'] = 'none';
    $GLOBALS['mapQrzCalls'] = 0;
    $GLOBALS['mapNominatimCalls'] = 0;
    $GLOBALS['mapNominatimResult'] = [
        'resolved' => true,
        'location' => 'Phoenix, AZ',
        'lat' => 33.45,
        'lng' => -112.07,
        'source' => 'nominatim',
    ];

    function asr_runtime_secrets(): array {
        return [];
    }

    function asr_qrz_session(array $secrets): string {
        return $GLOBALS['mapQrzMode'] === 'valid' ? 'session-key' : '';
    }

    function asr_qrz_station(string $callsign, string &$session): array {
        $GLOBALS['mapQrzCalls']++;
        return [
            'resolved' => true,
            'callsign' => $callsign,
            'name' => 'QRZ Operator',
            'location' => 'Phoenix, AZ, USA',
            'lat' => 33.45,
            'lng' => -112.07,
            'source' => 'qrz',
        ];
    }

    function asr_nominatim_location(string $location): array {
        $GLOBALS['mapNominatimCalls']++;
        return array_merge($GLOBALS['mapNominatimResult'], ['location' => $location]);
    }

    function asr_lookup_payload(): array {
        return ['ok' => true, 'items' => []];
    }

    load_functions($sourcePath, [
        'asr_station_map_cache_read',
        'asr_station_map_cache_write',
        'asr_clean_location_hint',
        'asr_station_map_payload',
    ]);

    $request = [['callsign' => 'N7YO', 'locationHint' => 'Phoenix, AZ']];

    $GLOBALS['mapQrzMode'] = 'valid';
    $payload = asr_station_map_payload($request);
    assert_test(count($payload['points'] ?? []) === 1, 'QRZ success did not produce a map point');
    assert_test($GLOBALS['mapQrzCalls'] === 1, 'QRZ success did not perform one station lookup');
    assert_test($GLOBALS['mapNominatimCalls'] === 0, 'public fallback ran after QRZ success');

    @unlink(ASR_STATION_MAP_CACHE);
    $GLOBALS['mapQrzMode'] = 'none';
    $GLOBALS['mapQrzCalls'] = 0;
    $GLOBALS['mapNominatimCalls'] = 0;
    $payload = asr_station_map_payload($request);
    assert_test(count($payload['points'] ?? []) === 1, 'no-credential public fallback did not produce a map point');
    assert_test($GLOBALS['mapQrzCalls'] === 0, 'no-credential path attempted a QRZ station request');
    assert_test($GLOBALS['mapNominatimCalls'] === 1, 'no-credential public fallback did not run exactly once');

    $GLOBALS['mapNominatimCalls'] = 0;
    $secondRequest = [['callsign' => 'W7TEST', 'locationHint' => 'Tucson, AZ']];
    $payload = asr_station_map_payload($secondRequest);
    assert_test($GLOBALS['mapNominatimCalls'] === 0, 'public fallback exceeded one uncached request per 15 seconds');
    assert_test(count($payload['unmapped'] ?? []) === 1, 'rate-limited station should remain listed as unmapped');

    $old = time() - (90 * 86400) - 1;
    file_put_contents(ASR_STATION_MAP_CACHE, json_encode([
        'callsigns' => [
            'N7YO' => [
                'resolved' => true,
                'callsign' => 'N7YO',
                'location' => 'Phoenix, AZ',
                'lat' => 33.40,
                'lng' => -112.00,
                'source' => 'nominatim',
                'fetchedAt' => $old,
            ],
        ],
        'geocodes' => [
            'phoenix, az' => [
                'resolved' => true,
                'location' => 'Phoenix, AZ',
                'lat' => 33.40,
                'lng' => -112.00,
                'source' => 'nominatim',
                'fetchedAt' => $old,
            ],
        ],
        'lastNominatimAt' => 0,
    ], JSON_PRETTY_PRINT));
    $GLOBALS['mapNominatimCalls'] = 0;
    $GLOBALS['mapNominatimResult']['lat'] = 33.46;
    $GLOBALS['mapNominatimResult']['lng'] = -112.08;
    $payload = asr_station_map_payload($request);
    assert_test($GLOBALS['mapNominatimCalls'] === 1, '90-day fallback cache entry was not refreshed');
    assert_test(($payload['points'][0]['lat'] ?? null) === 33.46, 'refreshed fallback coordinates were not returned');

    foreach ([ASR_STATION_MAP_CACHE, ASR_STATION_MAP_CACHE . '.lock'] as $path) {
        if (is_file($path)) @unlink($path);
    }
    @rmdir($temporaryDirectory);
}

$mode = (string) ($argv[1] ?? '');
if ($mode === 'qrz') {
    run_qrz_tests($sourcePath);
    exit(0);
}
if ($mode === 'map') {
    run_map_tests($sourcePath);
    exit(0);
}

foreach (['qrz', 'map'] as $childMode) {
    $command = escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg(__FILE__) . ' ' . escapeshellarg($childMode);
    passthru($command, $status);
    if ($status !== 0) exit($status);
}
fwrite(STDOUT, "lookup/map backend self-test: ok\n");
