<?php
// Exercise the actual refresh block without starting the AMI service.
$source = file_get_contents(__DIR__ . '/../compat/allscan-v1.06/astapi/server.php');
$start = strpos($source, "\t// Build array of time values");
$end = strpos($source, "\t// Maintain a small, process-local talker history", $start);
if ($start === false || $end === false) throw new RuntimeException('Refresh block missing');
$block = substr($source, $start, $end - $start);
define('NBSP', '&nbsp;');
$node = '674982'; $info = ''; $nodeTime = []; $current = [];
function fixture($node, $elapsed, $last) { return ['node'=>$node, 'elapsed'=>$elapsed, 'last_keyed'=>$last]; }
function verify($current, $nodeTime, $node, $expected) {
    $rows = $current[$node]['remote_nodes']; $times = $nodeTime[$node]['remote_nodes'];
    if (array_column($rows, 'node') !== array_column($expected, 'node') || $times !== $expected) throw new RuntimeException('Connection/timing mismatch');
}
$a = fixture('560203','19:50:12','Never'); $b = fixture('29332','01:53:13','00:00:02'); $c = fixture('623129','53:51:08','Never');
foreach ([[$a,$b,$c],[$c,$a],[$b,$c,$a],[],[$b]] as $sortedConnectedNodes) {
    eval($block);
    verify($current,$nodeTime,$node,$sortedConnectedNodes);
}
echo "PASS: five refresh scenarios (initial, shrink, reorder, empty, reconnect)\n";
