#!/usr/bin/env php
<?php
declare(strict_types=1);

$root = dirname(__DIR__);
$api = (string) file_get_contents($root . '/server/asr-api.php');
$selectorPath = $root . '/compat/allscan-v1.02/include/asrCpuTemperature.php';
$clientPath = dirname(__DIR__) . '/src/lib/allscanLive.ts';
$appPath = dirname(__DIR__) . '/src/App.tsx';
if(!is_file($selectorPath)) throw new RuntimeException('Shared CPU temperature implementation could not be loaded.');
require_once $selectorPath;

function check(bool $condition, string $message): void {
    if(!$condition) throw new RuntimeException($message);
}
check(str_contains($api, "if (\$action === 'cpu-temp')") && str_contains($api, 'asr_json(asr_cpu_temp_payload())'), 'CPU temperature API action is not wired to the selector payload.');
check(!str_contains($api, 'function asr_cpu_temperature_reading'), 'API still embeds an independent CPU selector.');
if(is_file($clientPath) && is_file($appPath)) {
    $client = (string) file_get_contents($clientPath);
    $app = (string) file_get_contents($appPath);
    check(str_contains($client, '?action=cpu-temp') && str_contains($app, 'setCpuValue(next.value)'), 'React dashboard is not rendering the CPU API value.');
}

function putSensor(string $root, string $relative, string $value): void {
    $path = $root . '/sys/' . $relative;
    if(!is_dir(dirname($path)) && !mkdir(dirname($path), 0777, true) && !is_dir(dirname($path))) {
        throw new RuntimeException('Could not create sensor fixture directory.');
    }
    file_put_contents($path, $value . "\n");
}

function fixture(array $files): string {
    $root = sys_get_temp_dir() . '/asr-cpu-temperature-' . bin2hex(random_bytes(8));
    mkdir($root, 0700, true);
    foreach($files as $path => $value) putSensor($root, $path, (string) $value);
    return $root;
}

function removeFixture(string $root): void {
    $iterator = new RecursiveIteratorIterator(
        new RecursiveDirectoryIterator($root, FilesystemIterator::SKIP_DOTS),
        RecursiveIteratorIterator::CHILD_FIRST
    );
    foreach($iterator as $item) $item->isDir() ? rmdir($item->getPathname()) : unlink($item->getPathname());
    rmdir($root);
}

$roots = [];
try {
    $multiple = fixture([
        'class/hwmon/hwmon0/name' => 'nvme',
        'class/hwmon/hwmon0/temp1_label' => 'Composite',
        'class/hwmon/hwmon0/temp1_input' => '44000',
        'class/hwmon/hwmon6/name' => 'coretemp',
        'class/hwmon/hwmon6/temp1_label' => 'Package id 0',
        'class/hwmon/hwmon6/temp1_input' => '53000',
        'class/hwmon/hwmon6/temp2_label' => 'Core 0',
        'class/hwmon/hwmon6/temp2_input' => '52000',
        'class/thermal/thermal_zone0/type' => 'INT3400 Thermal',
        'class/thermal/thermal_zone0/temp' => '20000',
    ]); $roots[] = $multiple;
    $reading = asr_cpu_temperature_reading($multiple . '/sys');
    check($reading !== null && $reading['celsius'] === 53.0 && $reading['source'] === 'coretemp:package id 0', 'coretemp Package id 0 did not beat NVMe/core/generic thermal sensors.');
    $multipleCache = $multiple . '/cache.json';
    file_put_contents($multipleCache, json_encode(['ok'=>true,'value'=>'68°F / 20°C','bgColor'=>'darkgreen','updated'=>gmdate('c')]));
    $payload = asr_cpu_temp_payload($multiple . '/sys', $multipleCache, static fn(): string => 'CPU Temp: 68°F / 20°C @ darkgreen');
    check($payload['value'] === '127°F / 53°C', 'API payload did not format 53°C as 127°F / 53°C.');
    check($payload['sensorSource'] === 'coretemp:package id 0', 'API payload lost selected sensor identity.');
    check(($payload['selectorVersion'] ?? null) === 3, 'Obsolete unversioned cache entry was accepted.');

    $x86 = fixture(['class/thermal/thermal_zone0/type'=>'x86_pkg_temp','class/thermal/thermal_zone0/temp'=>'51000']); $roots[] = $x86;
    check(asr_cpu_temperature_reading($x86 . '/sys')['source'] === 'thermal:x86_pkg_temp', 'x86_pkg_temp fallback failed.');

    $tcpu = fixture(['class/thermal/thermal_zone0/type'=>'TCPU','class/thermal/thermal_zone0/temp'=>'49000']); $roots[] = $tcpu;
    check(asr_cpu_temperature_reading($tcpu . '/sys')['source'] === 'thermal:tcpu', 'TCPU fallback failed.');

    $sbc = fixture(['class/thermal/thermal_zone0/type'=>'soc_thermal','class/thermal/thermal_zone0/temp'=>'48000']); $roots[] = $sbc;
    $sbcReading = asr_cpu_temperature_reading($sbc . '/sys');
    check($sbcReading['source'] === 'thermal:soc_thermal' && $sbcReading['class'] === 'embedded', 'SBC CPU thermal support failed.');

    foreach(['k10temp'=>'Tdie', 'zenpower'=>'Tctl'] as $driver => $label) {
        $amd = fixture(['class/hwmon/hwmon0/name'=>$driver,'class/hwmon/hwmon0/temp1_label'=>$label,'class/hwmon/hwmon0/temp1_input'=>'57000']); $roots[] = $amd;
        check(asr_cpu_temperature_reading($amd . '/sys')['celsius'] === 57.0, "$driver AMD sensor fallback failed.");
    }

    $invalid = fixture([
        'class/hwmon/hwmon0/name'=>'coretemp', 'class/hwmon/hwmon0/temp1_label'=>'Package id 0', 'class/hwmon/hwmon0/temp1_input'=>'not-a-number',
        'class/hwmon/hwmon1/name'=>'k10temp', 'class/hwmon/hwmon1/temp1_label'=>'Tdie', 'class/hwmon/hwmon1/temp1_input'=>'999999',
        'class/thermal/thermal_zone0/type'=>'acpitz', 'class/thermal/thermal_zone0/temp'=>'20000',
    ]); $roots[] = $invalid;
    check(asr_cpu_temperature_reading($invalid . '/sys') === null, 'Malformed, impossible, or unrelated sensors were accepted.');

    $cacheRoot = fixture(['class/thermal/thermal_zone0/type'=>'INT3400 Thermal','class/thermal/thermal_zone0/temp'=>'20000']); $roots[] = $cacheRoot;
    $cachePath = $cacheRoot . '/cache.json';
    $fallback = asr_cpu_temp_payload($cacheRoot . '/sys', $cachePath, static fn(): string => 'CPU Temp: 68°F / 20°C @ darkgreen');
    check($fallback['sensorSource'] === 'unavailable' && $fallback['value'] === '--°F / --°C', 'Generic 20°C thermal zone was relabeled as CPU temperature.');
    putSensor($cacheRoot, 'class/hwmon/hwmon9/name', 'coretemp');
    putSensor($cacheRoot, 'class/hwmon/hwmon9/temp1_label', 'Package id 0');
    putSensor($cacheRoot, 'class/hwmon/hwmon9/temp1_input', '53000');
    $upgraded = asr_cpu_temp_payload($cacheRoot . '/sys', $cachePath, static fn(): string => 'CPU Temp: 68°F / 20°C @ darkgreen');
    check($upgraded['value'] === '127°F / 53°C' && $upgraded['sensorSource'] === 'coretemp:package id 0', 'Cache preserved a 20°C fallback after coretemp appeared.');

    $recoveredRoot = fixture([
        'class/hwmon/hwmon0/name'=>'coretemp', 'class/hwmon/hwmon0/temp1_label'=>'Package id 0', 'class/hwmon/hwmon0/temp1_input'=>'not-a-number',
        'class/thermal/thermal_zone0/type'=>'TCPU', 'class/thermal/thermal_zone0/temp'=>'49000',
    ]); $roots[] = $recoveredRoot;
    $recoveredCache = $recoveredRoot . '/cache.json';
    check(asr_cpu_temp_payload($recoveredRoot . '/sys', $recoveredCache)['sensorSource'] === 'thermal:tcpu', 'Valid TCPU fallback was not initially selected.');
    putSensor($recoveredRoot, 'class/hwmon/hwmon0/temp1_input', '53000');
    $recovered = asr_cpu_temp_payload($recoveredRoot . '/sys', $recoveredCache);
    check($recovered['sensorSource'] === 'coretemp:package id 0' && $recovered['value'] === '127°F / 53°C', 'Cache preserved TCPU after an existing package sensor became valid.');

    $legacyRoot = fixture([]); $roots[] = $legacyRoot;
    $legacy = asr_cpu_temp_payload($legacyRoot . '/sys', $legacyRoot . '/cache.json', static fn(): string => 'CPU Temp: 122°F / 50°C @ darkgreen');
    check($legacy['sensorSource'] === 'legacy-fallback' && $legacy['value'] === '122°F / 50°C', 'Legacy no-sysfs hardware fallback failed.');

    echo "CPU temperature selector self-test: ok (coretemp Package id 0 53°C -> 127°F; generic 20°C rejected)\n";
} finally {
    foreach(array_reverse($roots) as $root) if(is_dir($root)) removeFixture($root);
}
