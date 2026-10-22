<?php

function asrLinkStateSnapshot(string $node, string $cacheDirectory = '/run/allscan-reimagined', ?callable $helperRunner = null, ?float $now = null): array {
    if (!preg_match('/^[0-9]{3,10}$/D', $node)) return ['available' => false, 'nodes' => [], 'source' => 'none'];
    $now = $now ?? microtime(true);
    $cachePath = rtrim($cacheDirectory, '/') . '/astapi-' . $node . '.json';
    if (is_readable($cachePath)) {
        $cache = json_decode((string) file_get_contents($cachePath), true);
        $updated = (float) ($cache['updated'] ?? 0);
        $rows = $cache['current'][$node]['remote_nodes'] ?? null;
        if (is_array($rows) && $updated > 0 && $now - $updated <= 30) {
            $linked = [];
            foreach ($rows as $row) {
                if (!is_array($row)) continue;
                $rowNode = (string) ($row['node'] ?? '');
                $state = strtolower(trim((string) ($row['link'] ?? '')));
                if (preg_match('/^[0-9]{3,10}$/D', $rowNode) && $rowNode !== '1' && in_array($state, ['established', 'linked'], true)) $linked[$rowNode] = true;
                foreach ((array) ($row['lnodes'] ?? []) as $linkedNode) {
                    $linkedNode = (string) $linkedNode;
                    if (preg_match('/^[0-9]{3,10}$/D', $linkedNode)) $linked[$linkedNode] = true;
                }
            }
            return ['available' => true, 'nodes' => $linked, 'source' => 'live-status-cache'];
        }
    }
    if ($helperRunner === null) return ['available' => false, 'nodes' => [], 'source' => 'none'];
    [$lines, $status] = $helperRunner($node);
    if ($status !== 0 || !is_array($lines)) return ['available' => false, 'nodes' => [], 'source' => 'none'];
    $linked = [];
    foreach ($lines as $line) {
        if (preg_match('/^([0-9]{3,10})\s+.*\sESTABLISHED\s*$/Di', trim((string) $line), $match)) $linked[$match[1]] = true;
    }
    return ['available' => true, 'nodes' => $linked, 'source' => 'asterisk'];
}
