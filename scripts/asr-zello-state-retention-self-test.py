#!/usr/bin/env python3
"""Execute Zello aggregate state reducer from the actual PHP API offline."""
from pathlib import Path
import json
import subprocess
import tempfile
import time

source = (Path(__file__).resolve().parent.parent / 'server/asr-api.php').read_text()
start = source.index("        $relayEpochMs = max(0, (int) ($relayPayload['updated_epoch_ms'] ?? 0));")
end = source.index("\n    }\n    return [", start)
reducer = source[start:end]
assert 'last_time_epoch' in reducer and '$relayFresh' in reducer
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / 'runner.php'
    php = '''<?php
$payload = json_decode($argv[1], true);
$relayPayload = $payload['relay'];
$bridge = ['zello' => $payload['previous']];
'''+reducer+'''
echo json_encode($bridge['zello']);
'''
    # The reducer is taken from inside an `if ($zelloConfigured)` block;
    # supply the opening block in this isolated harness.
    php = php.replace("$bridge = ['zello' => $payload['previous']];\n", "$bridge = ['zello' => $payload['previous']];\nif (true) {\n")
    php = php.replace("echo json_encode($bridge['zello']);", "}\necho json_encode($bridge['zello']);")
    path.write_text(php)
    def run(relay, previous):
        result = subprocess.run(['php', str(path), json.dumps({'relay': relay, 'previous': previous})], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    prior = {'role': 'idle', 'last_time_epoch': 1800000000, 'last_user': 'N1YKT'}
    old = run({'active': True, 'updated_epoch_ms': (int(time.time())-120)*1000}, prior)
    assert old['role'] == 'idle' and old['state'] == 'Idle' and old['last_time_epoch'] >= 1800000000
    current = run({'active': True, 'updated_epoch_ms': int(time.time()*1000)}, prior)
    assert current['role'] == 'relay' and current['state'] == 'RELAY'
    ended = run({'active': False, 'updated_epoch_ms': int(time.time()*1000)}, current)
    assert ended['role'] == 'idle' and ended['last_time_epoch'] >= current['last_time_epoch']
    restart = run({}, ended)
    assert restart['role'] == 'idle' and restart['last_time_epoch'] == ended['last_time_epoch']
print('Zello aggregate state and Last Activity retention: PASS')
