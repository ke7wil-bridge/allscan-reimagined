<?php
/** Standalone, no-node-IO regression checks for the M17 meter API boundary. */
$api = file_get_contents(__DIR__ . '/../server/asr-api.php');
$start = strpos($api, 'function asr_pcm_audio_meter(');
$end = strpos($api, 'function asr_m17_runtime_status(', $start);
if ($start === false || $end === false) throw new RuntimeException('M17 audio meter helper missing');
eval(substr($api, $start, $end - $start));
$now = 2000000000.0;
$sample = [
    'inboundAudioEpoch' => $now - .2,
    'inboundAudioPeak' => 32768,
    'inboundAudioRms' => 8192,
    'outboundAudioEpoch' => $now - .1,
    'outboundAudioPeak' => 16384,
    'outboundAudioRms' => 4096,
];
$meter = asr_m17_audio_meter($sample, $now);
if ($meter !== ['inboundpeak'=>1.0, 'inboundrms'=>.25, 'outboundpeak'=>.5, 'outboundrms'=>.125])
    throw new RuntimeException('M17 meter scaling or direction mapping incorrect: ' . json_encode($meter));
$sample['inboundAudioEpoch'] = $now - 2;
$sample['outboundAudioPeak'] = INF;
$meter = asr_m17_audio_meter($sample, $now);
if ($meter['inboundpeak'] !== 0.0 || $meter['inboundrms'] !== 0.0 || $meter['outboundpeak'] !== 0.0 || $meter['outboundrms'] !== .125)
    throw new RuntimeException('M17 stale/invalid values were not cleared independently');
$sample['outboundAudioEpoch'] = $now + 5;
$meter = asr_m17_audio_meter($sample, $now);
if ($meter['outboundpeak'] !== 0.0 || $meter['outboundrms'] !== 0.0)
    throw new RuntimeException('Future timestamp accepted');
// Reject malformed telemetry, numeric strings, future timestamps, negative
// samples, and independently stale directions without fabricating audio.
$invalid = $sample;
$invalid['inboundAudioEpoch'] = $now - .1;
$invalid['inboundAudioPeak'] = '32768';
$invalid['inboundAudioRms'] = -200;
$invalid['outboundAudioEpoch'] = $now + .01;
$invalid['outboundAudioPeak'] = 32768;
$invalid['outboundAudioRms'] = 32768;
$meter = asr_pcm_audio_meter($invalid, $now);
if ($meter !== ['inboundpeak'=>0.0, 'inboundrms'=>0.0, 'outboundpeak'=>0.0, 'outboundrms'=>0.0])
    throw new RuntimeException('Malformed or future audio telemetry was accepted');
$invalid['inboundAudioEpoch'] = (string)($now - .1);
$invalid['inboundAudioPeak'] = 32768;
$meter = asr_pcm_audio_meter($invalid, $now);
if ($meter['inboundpeak'] !== 0.0)
    throw new RuntimeException('Untrusted timestamp coercion accepted');
$invalid['inboundAudioEpoch'] = $now - .75;
$meter = asr_pcm_audio_meter($invalid, $now);
if ($meter['inboundpeak'] !== 0.0)
    throw new RuntimeException('Expired audio telemetry accepted');
// Confirm P25/NXDN can only render independently sampled PCM data, not
// synthetic levels inferred from backend connection status.
if (!str_contains($api, "is_array(asr_bridge_audio_pcm(\$id)) ? asr_pcm_audio_meter"))
    throw new RuntimeException('P25/NXDN PCM telemetry boundary missing');
// Zello must read only its private, ownership-checked runtime meter, and
// must not use its public relay/health/connection data as audio evidence.
if (!str_contains($api, "'/opt/allscan-reimagined-bridges/zello/runtime/audio-meter.json'")
    || !str_contains($api, '\'audio_meter\' => $zelloMeter')
    || !str_contains($api, 'asr_secure_service_runtime_json('))
    throw new RuntimeException('Zello audio telemetry is not securely integrated');

// URF telemetry must be read from the bridge-specific runtime directory,
// checked for ownership and normalized from real TCD PCM samples.
if (!str_contains($api, "'/opt/allscan-reimagined-bridges/urf/' . \$id . '/data'")
    || !str_contains($api, "'audio_meter' => \$urfMeter")
    || !str_contains($api, 'asr_secure_service_runtime_json(')
    || !str_contains($api, "preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', \$id)"))
    throw new RuntimeException('URF per-bridge PCM telemetry is not securely integrated');
// Every protocol's collector may consume PCM only when its own backend
// supplies it; these source contracts intentionally do not fabricate data.
$contracts = [
    'direct DMR/YSF' => "'audio_meter' => \$liveFresh && is_array(asr_bridge_audio_pcm(\$id))",
    'standard DMR/YSF' => "'audio_meter' => is_array(asr_bridge_audio_pcm(\$id))",
    'P25/NXDN' => "is_array(asr_bridge_audio_pcm(\$id)) ? asr_pcm_audio_meter",
    'trusted collector' => "'/run/allscan-reimagined-bridge-audio/levels.json'",
];
foreach ($contracts as $label => $fragment) {
    if (!str_contains($api, $fragment))
        throw new RuntimeException($label . ' PCM telemetry integration missing');
}
if (substr_count($api, "'audio_meter' => \$liveFresh && is_array") !== 2)
    throw new RuntimeException('DMR and YSF telemetry freshness guards must be independent');
// Confirm per-direction boundary behavior across a matrix of sample values.
foreach ([0, 1, 1000, 8192, 16384, 32767, 32768, 65536] as $amplitude) {
    $sample['inboundAudioEpoch'] = $now - .1;
    $sample['outboundAudioEpoch'] = $now - .1;
    $sample['inboundAudioPeak'] = $amplitude;
    $sample['inboundAudioRms'] = $amplitude / 2;
    $sample['outboundAudioPeak'] = $amplitude / 4;
    $sample['outboundAudioRms'] = 0;
    $meter = asr_pcm_audio_meter($sample, $now);
    if ($meter['inboundpeak'] !== min(1.0, $amplitude / 32768.0)
        || $meter['inboundrms'] !== min(1.0, $amplitude / 65536.0)
        || $meter['outboundpeak'] !== min(1.0, $amplitude / 131072.0)
        || $meter['outboundrms'] !== 0.0)
        throw new RuntimeException('PCM level matrix failed at ' . $amplitude);
}
// Exercise the actual secure runtime reader against scratch files. Reject
// links, writable files/directories, hardlinks and non-JSON input.
$readerStart = strpos($api, 'function asr_secure_service_runtime_json(');
$readerEnd = strpos($api, 'function asr_standard_bridge_live_statuses(', $readerStart);
if ($readerStart === false || $readerEnd === false) throw new RuntimeException('Secure meter reader missing');
eval(substr($api, $readerStart, $readerEnd - $readerStart));
$dir = sys_get_temp_dir() . '/asr136-meter-' . bin2hex(random_bytes(8));
if (!mkdir($dir, 0700)) throw new RuntimeException('Cannot create test directory');
try {
    $path = $dir . '/audio-meter.json';
    $sample = ['inboundAudioPeak'=>1000, 'inboundAudioRms'=>100, 'inboundAudioEpoch'=>$now];
    file_put_contents($path, json_encode($sample));
    chmod($path, 0600);
    if (asr_secure_service_runtime_json($path, $dir) != $sample)
        throw new RuntimeException('Private PCM runtime file rejected');
    if (asr_secure_service_runtime_json($path, $dir . '/other') !== null)
        throw new RuntimeException('Runtime directory mismatch accepted');
    chmod($path, 0666);
    clearstatcache(true, $path);
    if (asr_secure_service_runtime_json($path, $dir) !== null)
        throw new RuntimeException('Writable telemetry file accepted');
    chmod($path, 0600);
    chmod($dir, 0777);
    clearstatcache(true, $dir);
    if (asr_secure_service_runtime_json($path, $dir) !== null)
        throw new RuntimeException('Writable runtime directory accepted');
    chmod($dir, 0700);
    file_put_contents($path, '{bad json');
    clearstatcache(true, $path);
    if (asr_secure_service_runtime_json($path, $dir) !== null)
        throw new RuntimeException('Malformed runtime JSON accepted');
    file_put_contents($path, json_encode($sample));
    $hardlink = $dir . '/hardlink.json';
    if (!link($path, $hardlink)) throw new RuntimeException('Hardlink test setup failed');
    clearstatcache(true, $path);
    if (asr_secure_service_runtime_json($path, $dir) !== null)
        throw new RuntimeException('Hardlinked telemetry file accepted');
    unlink($hardlink);
    $symlink = $dir . '/symlink.json';
    if (!symlink($path, $symlink)) throw new RuntimeException('Symlink test setup failed');
    if (asr_secure_service_runtime_json($symlink, $dir) !== null)
        throw new RuntimeException('Symlinked telemetry file accepted');
    unlink($symlink);
} finally {
    @chmod($dir, 0700);
    @unlink($dir . '/hardlink.json');
    @unlink($dir . '/symlink.json');
    @unlink($dir . '/audio-meter.json');
    @rmdir($dir);
}
print "PCM audio meter API self-test: PASS\n";
