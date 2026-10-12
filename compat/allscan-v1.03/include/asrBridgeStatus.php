<?php
declare(strict_types=1);

function asrRecentUrfEvents(array $lines, callable $isServiceIdentity, int $perMode = 100): array {
    $supportedModes = ['DMR'=>true, 'YSF'=>true, 'P25'=>true, 'NXDN'=>true, 'M17'=>true, 'USRP'=>true];
    $limit = max(1, min(500, $perMode));
    $counts = [];
    $events = [];
    foreach (array_reverse($lines) as $line) {
        $event = json_decode((string) $line, true);
        if (!is_array($event) || (int) ($event['epoch'] ?? 0) <= 0) continue;
        $mode = strtoupper(trim((string) ($event['mode'] ?? '')));
        if (str_starts_with($mode, 'DMR')) $mode = 'DMR';
        if (!isset($supportedModes[$mode]) || (int) ($counts[$mode] ?? 0) >= $limit) continue;
        $event['mode'] = $mode;
        $callsign = (string) ($event['callsign'] ?? $event['client'] ?? '');
        if ($isServiceIdentity($callsign, $mode)) continue;
        $events[] = $event;
        $counts[$mode] = (int) ($counts[$mode] ?? 0) + 1;
        if (count($counts) === count($supportedModes) && min($counts) >= $limit) break;
    }
    return array_reverse($events);
}

function asrPublicBridgeControls(array $controls): array {
    $public = [];
    $booleanKeys = ['ready', 'linked', 'digitalLinked', 'allstarLinked'];
    $stringKeys = ['currentTg', 'currentDestination', 'currentDestinationLabel'];
    foreach ($controls as $id => $control) {
        $id = (string) $id;
        if (!preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', $id) || !is_array($control)) continue;
        $clean = [];
        foreach ($booleanKeys as $key) {
            if (array_key_exists($key, $control)) $clean[$key] = !empty($control[$key]);
        }
        foreach ($stringKeys as $key) {
            if (array_key_exists($key, $control)) {
                $clean[$key] = substr(trim((string) $control[$key]), 0, 120);
            }
        }
        $public[$id] = $clean;
    }
    return $public;
}

function asrPublicBridgeLiveStatuses(array $live, array $configuredBridges): array {
    $netIds = [];
    $netTypes = ['dmr_net', 'ysf_net', 'p25_net', 'nxdn_net', 'm17_net'];
    foreach ($configuredBridges as $bridge) {
        if (!is_array($bridge) || !in_array((string) ($bridge['cardType'] ?? ''), $netTypes, true)) continue;
        $id = (string) ($bridge['id'] ?? '');
        if (preg_match('/^[a-z][a-z0-9_-]{1,31}$/D', $id)) $netIds[$id] = true;
    }
    foreach (array_keys($netIds) as $id) {
        if (!isset($live[$id]) || !is_array($live[$id])) continue;
        $warning = trim((string) ($live[$id]['warning'] ?? ''));
        $live[$id]['warning'] = $warning === '' || $warning === '-'
            ? ''
            : 'Bridge status needs attention. Review Bridge Settings.';
    }
    return $live;
}
