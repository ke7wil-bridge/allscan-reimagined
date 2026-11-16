#!/usr/bin/env python3
"""Execute Zello aggregate state reducer from the actual PHP API offline."""
from pathlib import Path
import json
import subprocess
import tempfile
import time

source = (Path(__file__).resolve().parent.parent / 'server/asr-api.php').read_text()
evidence_start = source.index('function asr_zello_connection_evidence(')
evidence_end = source.index('\n}\n', evidence_start) + 3
evidence_function = source[evidence_start:evidence_end]
start = source.index("        $relayEpochMs = max(0, (int) ($relayPayload['updated_epoch_ms'] ?? 0));")
end = source.index("\n    }\n    return [", start)
reducer = source[start:end]
assert 'last_time_epoch' in reducer and '$relayFresh' in reducer
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / 'runner.php'
    php = '''<?php
function asr_secure_service_runtime_json($path, $directory) { return []; }
function asr_pcm_audio_meter($payload, $now) { return null; }
$payload = json_decode($argv[1], true);
$relayPayload = $payload['relay'];
$zelloOnline = null;
$bridge = ['zello' => $payload['previous']];
function asr_secure_service_runtime_json($path, $dir) { return null; }
function asr_pcm_audio_meter($payload, $now) { return []; }
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
    evidence_path = Path(tmp) / 'evidence.php'
    evidence_path.write_text(
        '<?php\n' + evidence_function
        + '\n$payload=json_decode($argv[1],true); echo json_encode(asr_zello_connection_evidence($payload,(int)$argv[2]));\n'
    )
    def connection(payload, now):
        result = subprocess.run(
            ['php', str(evidence_path), json.dumps(payload), str(now)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    assert connection({'state': 'online', 'epoch': 1000}, 1001) is True
    assert connection({'state': 'offline', 'epoch': 1002}, 1002) is False
    assert connection({'state': 'online', 'epoch': 1000}, 1121) is None
    assert connection({}, 1000) is None
print('Zello aggregate state and Last Activity retention: PASS')
