#!/usr/bin/env python3
"""Guard intentional ARM32 TCD/QEMU fallback until a native build is qualified."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import sys
root = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('urf_native', root / 'asr-bridge-urf-native-runtime.py')
native = importlib.util.module_from_spec(spec); sys.modules[spec.name] = native; spec.loader.exec_module(native)
for machine, arch, cross in [('x86_64','amd64',True),('aarch64','arm64',True),('armv7l','armhf',False)]:
    with patch.object(native.platform, 'machine', return_value=machine):
        assert native.architecture() == arch
        packages = native.packages()
        assert ('qemu-user-static' in packages) == cross
        assert ('gcc-arm-linux-gnueabihf' in packages) == cross
with patch.object(native.platform, 'machine', return_value='riscv64'):
    try: native.architecture()
    except native.SourceError: pass
    else: raise AssertionError('unsupported architecture accepted')
builder = (root / 'asr-bridge-urf-native-runtime.py').read_text()
installer = (root / 'asr-bridge-setup-urf-install.py').read_text()
assert 'shutil.copy2(tcd / "tcd", bin_dir / "tcd-armhf")' in builder
assert 'qemu-arm-static -L /usr/arm-linux-gnueabihf' in installer
assert 'if platform.machine().lower() != "armv7l"' in installer
assert 'bin/tcd-armhf' in builder
print('URF ARM32/QEMU architecture fallback: PASS (native x86-64 not yet qualified)')
