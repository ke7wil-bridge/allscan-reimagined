#!/usr/bin/env python3
"""Regression: configured Standard Bridge cards must survive idle status polling."""
import importlib.util
import tempfile
import time
from pathlib import Path

source = Path(__file__).with_name('asr_bridge_status.py')
spec = importlib.util.spec_from_file_location('asr_bridge_status', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

protocols = ('dmr', 'ysf', 'p25', 'nxdn', 'm17')
bridges = [dict(id=f'{mode}_johnny2', mode=mode, node=str(1001 + index),
                cardType='standard', backendMode='managed')
           for index, mode in enumerate(protocols)]
config = {'node': '623123', 'bridges': bridges}
with tempfile.TemporaryDirectory() as directory:
    result = module.standard_live_payload(config, {}, [], int(time.time()),
                                          cache_dir=Path(directory))
assert set(result['bridges']) == {bridge['id'] for bridge in bridges}
assert all(entry['online'] is None and entry['linked'] is None
           for entry in result['bridges'].values())
assert set(module.configured_standard_bridges(config)) == {'dmr', 'ysf', 'p25', 'nxdn'}

# Fresh ASTAPI keyed evidence must work for every uniquely numbered protocol.
original_astapi = module.astapi_key_states
try:
    module.astapi_key_states = lambda local, nodes, now, cache: {
        node: node == '1004' for node in nodes
    }
    states = {}
    active = module.standard_live_payload(config, states, [], int(time.time()),
                                          cache_dir=Path('/nonexistent'))
    assert active['bridges']['nxdn_johnny2']['state'] == 'TX ACTIVE'
    assert all(active['bridges'][b['id']]['state'] == 'Idle'
               for b in bridges if b['id'] != 'nxdn_johnny2')
    module.astapi_key_states = lambda local, nodes, now, cache: {
        node: False for node in nodes
    }
    ended = module.standard_live_payload(config, states, [], int(time.time()) +
                                         module.KEYED_SAMPLE_GRACE_SECONDS + 1,
                                         cache_dir=Path('/nonexistent'))
    assert ended['bridges']['nxdn_johnny2']['state'] == 'Idle'
finally:
    module.astapi_key_states = original_astapi

api_text = (source.parents[1] / 'asr-api.php').read_text(encoding='utf-8')
assert "in_array($mode, ['dmr', 'ysf', 'p25', 'nxdn', 'm17'], true)" in api_text
assert "'online' => $online" in api_text
assert "'online' => !empty($urf['online'])" not in api_text
assert "$healthSeverity = empty($urf['online'])" not in api_text
assert "$bridge[$id] = array_merge(is_array($bridge[$id] ?? null) ? $bridge[$id] : [], $enrichment);" in api_text
assert "if (!empty($urf['ok'])) {\n            $urfClients[$id] = $modeClients;" in api_text
print('Five-protocol idle status: PASS')

# Real per-card MMDVM messages must produce attributed talker history.
now = int(time.time())
from datetime import datetime, timezone
stamp = datetime.fromtimestamp(now - 4, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
end_stamp = datetime.fromtimestamp(now - 1, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
for mode, caller, header, tail in (
    ('dmr', 'N1YKT', 'DMR Slot 2, received network voice header from N1YKT to TG 42450', 'DMR Slot 2, received network end of voice transmission, 3.7 seconds, 0% packet loss, BER: 0.0%'),
    ('p25', 'N1YKT', 'P25, received network transmission from N1YKT to TG 73', 'P25, network end of transmission, 3.1 seconds, 0% packet loss'),
    ('nxdn', 'M6LSJ', 'NXDN, received RF header from M6LSJ to TG 7486', 'NXDN, received RF end of transmission, 7.5 seconds, BER: 0.0%'),
):
    stem = f'MMDVM_Bridge_{mode}_johnny2'
    tagged = [f'{stem}\tM: {stamp} {header}\n', f'{stem}\tM: {end_stamp} {tail}\n']
    with tempfile.TemporaryDirectory() as directory:
        result = module.standard_live_payload(config, {}, [], now, cache_dir=Path(directory), tagged_lines=tagged)
    entry = result['bridges'][f'{mode}_johnny2']
    assert entry['last_user'] == caller and len(entry['tx_events']) == 1, (mode, entry)
    assert all(not result['bridges'][b['id']]['tx_events'] for b in bridges if b['mode'] != mode)
print('Managed protocol TX attribution: PASS')
