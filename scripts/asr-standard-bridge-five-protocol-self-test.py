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
assert set(module.configured_standard_bridges(config)) == {'dmr', 'ysf'}

api_text = (source.parents[1] / 'asr-api.php').read_text(encoding='utf-8')
assert "in_array($mode, ['dmr', 'ysf', 'p25', 'nxdn', 'm17'], true)" in api_text
assert "'online' => $online" in api_text
assert "'online' => !empty($urf['online'])" not in api_text
assert "$healthSeverity = empty($urf['online'])" not in api_text
assert "$bridge[$id] = array_merge(is_array($bridge[$id] ?? null) ? $bridge[$id] : [], $enrichment);" in api_text
assert "if (!empty($urf['ok'])) {\n            $urfClients[$id] = $modeClients;" in api_text
print('Five-protocol idle status: PASS')
