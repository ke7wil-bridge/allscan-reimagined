<?php
// AllScan Reimagined authoritative CPU-temperature selector.
//
// Keep sensor selection, formatting, thresholds, source metadata, and caching
// here so API, React, server-rendered headers, and Performance Stats cannot
// drift into independent interpretations of the host's thermal sensors.

const ASR_CPU_TEMP_SELECTOR_VERSION = 3;
const ASR_CPU_TEMP_CACHE = '/run/allscan-reimagined/cpu-temp.json';

function asr_cpu_temperature_value(string $path): ?float {
	$text = @file_get_contents($path);
	if(!is_string($text) || !preg_match('/^-?[0-9]+(?:\.[0-9]+)?$/D', trim($text)))
		return null;
	$celsius = (float)trim($text) / 1000.0;
	return $celsius >= 1.0 && $celsius <= 150.0 ? $celsius : null;
}

function asr_cpu_sensor_topology(string $sysRoot='/sys'): string {
	$parts = [];
	foreach((array)glob(rtrim($sysRoot, '/') . '/class/hwmon/hwmon*') as $hwmon) {
		$name = strtolower(trim((string)@file_get_contents($hwmon . '/name')));
		foreach((array)glob($hwmon . '/temp*_input') as $input) {
			$base = substr($input, 0, -6);
			$label = strtolower(trim((string)@file_get_contents($base . '_label')));
			$parts[] = 'h:' . basename($hwmon) . ':' . $name . ':' . basename($input) . ':' . $label . ':'
				. (is_readable($input) ? 'r' : '-') . ':' . (asr_cpu_temperature_value($input) !== null ? 'v' : '-');
		}
	}
	foreach((array)glob(rtrim($sysRoot, '/') . '/class/thermal/thermal_zone*') as $zone) {
		$type = strtolower(trim((string)@file_get_contents($zone . '/type')));
		$temp = $zone . '/temp';
		$parts[] = 't:' . basename($zone) . ':' . $type . ':' . (is_readable($temp) ? 'r' : '-')
			. ':' . (asr_cpu_temperature_value($temp) !== null ? 'v' : '-');
	}
	sort($parts, SORT_STRING);
	return hash('sha256', implode("\n", $parts));
}

function asr_cpu_has_sensor_inputs(string $sysRoot='/sys'): bool {
	foreach((array)glob(rtrim($sysRoot, '/') . '/class/hwmon/hwmon*/temp*_input') as $path)
		if(is_readable($path)) return true;
	foreach((array)glob(rtrim($sysRoot, '/') . '/class/thermal/thermal_zone*/temp') as $path)
		if(is_readable($path)) return true;
	return false;
}

function asr_cpu_temperature_reading(string $sysRoot='/sys'): ?array {
	$candidates = [];
	foreach((array)glob(rtrim($sysRoot, '/') . '/class/hwmon/hwmon*') as $hwmon) {
		$name = strtolower(trim((string)@file_get_contents($hwmon . '/name')));
		foreach((array)glob($hwmon . '/temp*_input') as $input) {
			$base = substr($input, 0, -6);
			$label = strtolower(trim((string)@file_get_contents($base . '_label')));
			$priority = null;
			if($name === 'coretemp' && str_starts_with($label, 'package id'))
				$priority = 100;
			elseif($name === 'k10temp' && in_array($label, ['tctl', 'tdie'], true))
				$priority = $label === 'tdie' ? 100 : 95;
			elseif($name === 'zenpower' && in_array($label, ['tdie', 'tctl'], true))
				$priority = $label === 'tdie' ? 100 : 95;
			if($priority === null) continue;
			$celsius = asr_cpu_temperature_value($input);
			if($celsius !== null)
				$candidates[] = [$priority, $celsius, 'x86', $name . ':' . ($label !== '' ? $label : basename($input))];
		}
	}
	foreach((array)glob(rtrim($sysRoot, '/') . '/class/thermal/thermal_zone*') as $zone) {
		$type = strtolower(trim((string)@file_get_contents($zone . '/type')));
		$priority = match($type) {
			'x86_pkg_temp' => 90,
			'tcpu' => 85,
			'cpu-thermal', 'cpu_thermal', 'soc-thermal', 'soc_thermal', 'bcm2835_thermal' => 80,
			default => null,
		};
		if($priority === null) continue;
		$celsius = asr_cpu_temperature_value($zone . '/temp');
		if($celsius !== null)
			$candidates[] = [$priority, $celsius, in_array($type, ['x86_pkg_temp', 'tcpu'], true) ? 'x86' : 'embedded', 'thermal:' . $type];
	}
	if(!$candidates) return null;
	usort($candidates, static fn($a, $b) => $b[0] <=> $a[0]);
	return [
		'celsius' => (float)$candidates[0][1],
		'class' => (string)$candidates[0][2],
		'source' => (string)$candidates[0][3],
		'priority' => (int)$candidates[0][0],
	];
}

function asr_cpu_temp_thresholds(string $class): array {
	// x86 CPUs routinely operate above node-device/SBC temperatures. Preserve
	// the established 130F/150F policy for embedded and compatibility sensors.
	return $class === 'x86'
		? ['warnC' => 80.0, 'alarmC' => 90.0]
		: ['warnC' => (130 - 32) / 1.8, 'alarmC' => (150 - 32) / 1.8];
}

function asr_cpu_temp_payload(string $sysRoot='/sys', string $cache=ASR_CPU_TEMP_CACHE, ?callable $fallback=null): array {
	$topology = asr_cpu_sensor_topology($sysRoot);
	if(is_readable($cache) && (int)@filemtime($cache) >= time() - 15) {
		$decoded = json_decode((string)file_get_contents($cache), true);
		if(is_array($decoded)
			&& ($decoded['selectorVersion'] ?? null) === ASR_CPU_TEMP_SELECTOR_VERSION
			&& is_string($decoded['sensorTopology'] ?? null)
			&& hash_equals($topology, $decoded['sensorTopology']))
			return $decoded;
	}

	$reading = asr_cpu_temperature_reading($sysRoot);
	if($reading !== null) {
		$celsius = (float)$reading['celsius'];
		$thresholds = asr_cpu_temp_thresholds((string)$reading['class']);
		$ct = (int)round($celsius);
		$ft = (int)round($celsius * 1.8 + 32);
		$payload = [
			'ok' => true,
			'value' => $ft . '°F / ' . $ct . '°C',
			'celsius' => $celsius,
			'bgColor' => $celsius < $thresholds['warnC'] ? 'darkgreen' : ($celsius < $thresholds['alarmC'] ? '#660' : 'red'),
			'updated' => gmdate('c'),
			'sensorSource' => (string)$reading['source'],
			'sensorClass' => (string)$reading['class'],
		];
	} elseif(asr_cpu_has_sensor_inputs($sysRoot)) {
		// Never relabel arbitrary ambient, ACPI, storage, skin, or radio sensors.
		$payload = [
			'ok' => true,
			'value' => '--°F / --°C',
			'celsius' => null,
			'bgColor' => '#59461c',
			'updated' => gmdate('c'),
			'sensorSource' => 'unavailable',
			'sensorClass' => 'unavailable',
		];
	} else {
		// Stock compatibility only: retain upstream support such as vcgencmd on
		// hardware that exposes no sysfs temperature inputs at all.
		if($fallback === null)
			$fallback = function(): string { return function_exists('cpuTemp') ? (string)cpuTemp() : ''; };
		$raw = (string)$fallback();
		$text = trim(html_entity_decode(strip_tags($raw), ENT_QUOTES | ENT_HTML5));
		preg_match('/background-color\s*:\s*([^;"\']+)/i', $raw, $backgroundMatch);
		preg_match('/CPU Temp:\s*(.+?)\s*@/i', $text, $temperature);
		$value = trim((string)($temperature[1] ?? preg_replace('/^CPU Temp:\s*/i', '', $text)));
		preg_match('/\/\s*([0-9]+(?:\.[0-9]+)?)°C/i', $value, $celsiusMatch);
		$payload = [
			'ok' => true,
			'value' => $value !== '' ? $value : '--°F / --°C',
			'celsius' => isset($celsiusMatch[1]) ? (float)$celsiusMatch[1] : null,
			'bgColor' => trim((string)($backgroundMatch[1] ?? '#59461c')),
			'updated' => gmdate('c'),
			'sensorSource' => 'legacy-fallback',
			'sensorClass' => 'embedded',
		];
	}
	$payload['selectorVersion'] = ASR_CPU_TEMP_SELECTOR_VERSION;
	$payload['sensorTopology'] = $topology;
	if(is_dir(dirname($cache))) @file_put_contents($cache, json_encode($payload), LOCK_EX);
	return $payload;
}
