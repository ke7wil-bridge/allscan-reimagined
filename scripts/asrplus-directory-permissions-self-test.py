#!/usr/bin/env python3
"""Guard against silently changing existing shared directory permissions."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

control = load('asrplus_control_permissions', 'asrplus-control.py')
endpoint = load('asrplus_endpoint_permissions', 'asrplus-remote-endpoint.py')
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    for writer in (control.atomic_json, endpoint.atomic_json):
        shared = root / writer.__module__
        shared.mkdir(mode=0o755)
        shared.chmod(0o755)
        writer(shared / 'result.json', {'ok': True})
        assert shared.stat().st_mode & 0o777 == 0o755
        assert json.loads((shared / 'result.json').read_text()) == {'ok': True}
    queue = root / 'queue' / 'phoenix'
    queue.mkdir(parents=True)
    queue.chmod(0o755)
    with patch.object(endpoint, 'QUEUE_ROOT', root / 'queue'), \
         patch.object(endpoint, 'load_central_hub', return_value={'mode': 'optional'}):
        assert endpoint.poll('phoenix')['command'] is None
    assert queue.stat().st_mode & 0o777 == 0o755
print('ASR Plus existing directory permission preservation: PASS')
