#!/usr/bin/env python3
"""Prevent ASR packaging, reapply and Docker host install from reviving retired URF."""
from pathlib import Path
root = Path(__file__).resolve().parent.parent
build = (root / 'build-release.sh').read_text()
reapply = (root / 'scripts/asr-reapply.sh').read_text()
docker = (root / 'docker/layered-reapply.sh').read_text()
host = (root / 'scripts/asr-container-host-install.py').read_text()
for forbidden in ('cp -a runtime/urf', 'install -m 755 scripts/asr-urf-admin.py', 'asr-bridge-setup-urf-install.py asr-bridge-setup-zello'):
    assert forbidden not in build, f'URF in release: {forbidden}'
for forbidden in ('cp -a "$MASTER_DIR/runtime/urf"', 'install -o root -g root -m 755 "$MASTER_DIR/scripts/asr-urf-admin.py"'):
    assert forbidden not in reapply, f'URF in reapply: {forbidden}'
assert 'install_helper asr-urf-admin.py' not in docker
assert 'repair_managed_urf_images(profile)' not in host
assert 'shutil.copytree(runtime_source, temporary_runtime)' not in host
print('URF absence self-test passed')
