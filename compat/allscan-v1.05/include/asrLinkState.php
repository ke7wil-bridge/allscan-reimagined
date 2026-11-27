<?php

function asrLinkStateSnapshot(string $node, string $cacheDirectory = '/run/allscan-reimagined', ?callable $helperRunner = null, ?float $now = null): array {
    if (!preg_match('/^[0-9]{3,10}$/D', $node)) return ['available' => false, 'nodes' => [], 'directNodes' => [], 'source' => 'none'];
    $now = $now ?? microtime(true);
    $cachePath = rtrim($cacheDirectory, '/') . '/astapi-' . $node . '.json';
    if (is_readable($cachePath)) {
        $cache = json_decode((string) file_get_contents($cachePath), true);
        $updated = (float) ($cache['updated'] ?? 0);
        $rows = $cache['current'][$node]['remote_nodes'] ?? null;
        if (is_array($rows) && $updated > 0 && $now - $updated <= 30) {
            $linked = [];
            $direct = [];
            foreach ($rows as $row) {
                if (!is_array($row)) continue;
                $rowNode = (string) ($row['node'] ?? '');
                $state = strtolower(trim((string) ($row['link'] ?? '')));
                if (preg_match('/^[0-9]{3,10}$/D', $rowNode) && $rowNode !== '1' && in_array($state, ['established', 'linked'], true)) $linked[$rowNode] = $direct[$rowNode] = true;
                foreach ((array) ($row['lnodes'] ?? []) as $linkedNode) {
                    $linkedNode = (string) $linkedNode;
                    if (preg_match('/^[0-9]{3,10}$/D', $linkedNode)) $linked[$linkedNode] = true;
                }
            }
            return ['available' => true, 'nodes' => $linked, 'directNodes' => $direct, 'source' => 'live-status-cache'];
        }
    }
    if ($helperRunner === null) return ['available' => false, 'nodes' => [], 'directNodes' => [], 'source' => 'none'];
    [$lines, $status] = $helperRunner($node);
    if ($status !== 0 || !is_array($lines)) return ['available' => false, 'nodes' => [], 'directNodes' => [], 'source' => 'none'];
    $linked = [];
    foreach ($lines as $line) {
        if (preg_match('/^([0-9]{3,10})\s+.*\sESTABLISHED\s*$/Di', trim((string) $line), $match)) $linked[$match[1]] = true;
    }
    return ['available' => true, 'nodes' => $linked, 'directNodes' => $linked, 'source' => 'asterisk'];
}

// Managed Standard Bridge controls operate an exact local doorway, not a
// propagated topology node. A manual hold must likewise never look linked
// solely because a remote hub advertises the same private node number.
function asrBridgeLinkNodes(array $snapshot, array $bridge): array {
    $managed = (string) ($bridge['backendMode'] ?? (isset($bridge['bridgePermission']) ? 'managed' : 'display_only')) === 'managed';
    $directOnly = (string) ($bridge['cardType'] ?? 'standard') === 'standard'
        && ($managed || ($bridge['manualDisconnect'] ?? false) === true);
    return (array) ($snapshot[$directOnly ? 'directNodes' : 'nodes'] ?? []);
}
