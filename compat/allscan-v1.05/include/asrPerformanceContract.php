<?php

function asrPerformanceUptimeLabel(float $seconds): string {
    $seconds = max(0.0, $seconds);
    $days = (int) floor($seconds / 86400.0);
    $hours = (int) floor(fmod($seconds, 86400.0) / 3600.0);
    $minutes = (int) floor(fmod($seconds, 3600.0) / 60.0);
    return ($days > 0 ? $days . 'd ' : '') . $hours . 'h ' . $minutes . 'm';
}

function asrPerformancePayloadValid(array $payload): bool {
    foreach (['ok','updated','mode','cpuTemp','load','memory','disk','uptime','activeViewers','requestsLastMinute','apacheWorkers','asteriskRunning','statusCacheAge','bridgeCollector','integrityTimer'] as $key) {
        if (!array_key_exists($key, $payload)) return false;
    }
    if ($payload['ok'] !== true || !is_string($payload['updated']) || strtotime($payload['updated']) === false) return false;
    foreach (['load','memory','disk'] as $group) if (!is_array($payload[$group])) return false;
    foreach (['one','five','fifteen'] as $key) if (!isset($payload['load'][$key]) || !is_numeric($payload['load'][$key])) return false;
    foreach (['used','total','percent'] as $key) if (!array_key_exists($key, $payload['memory']) || !array_key_exists($key, $payload['disk'])) return false;
    return json_encode($payload, JSON_UNESCAPED_SLASHES) !== false;
}
