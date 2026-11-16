#!/usr/bin/env python3
"""Sensitive export safety checks (#127)."""
import importlib.util
import json
import os
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory

spec = importlib.util.spec_from_file_location("asr_config_export", Path(__file__).with_name("asr-config-export.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with TemporaryDirectory() as tmp:
    root = Path(tmp)
    source = root / "source"
    source.mkdir()
    (source / "config.json").write_text('{"bridges":[{"name":"test"}]}')
    (source / "secrets.json").write_text('{"password":"TEST-SECRET"}')
    archive = root / "backup.tar.gz"
    result = module.export(source, archive, "1.0.0-beta.8.4")
    assert result["containsSensitiveData"] and not result["restoreSupported"]
    assert archive.stat().st_mode & 0o777 == 0o600
    with tarfile.open(archive) as stream:
        assert sorted(stream.getnames()) == ["config.json", "manifest.json", "secrets.json"]
        manifest = json.load(stream.extractfile("manifest.json"))
        assert manifest["files"]["secrets.json"]["bytes"] > 0
        assert stream.extractfile("secrets.json").read() == b'{"password":"TEST-SECRET"}'
    try:
        module.export(source, archive, "1.0.0-beta.8.4")
        raise AssertionError("overwriting backup was allowed")
    except ValueError:
        pass
    (source / "secrets.json").unlink()
    (source / "secrets.json").symlink_to(source / "config.json")
    try:
        module.export(source, root / "bad.tar.gz", "1.0.0-beta.8.4")
        raise AssertionError("symlink export was allowed")
    except ValueError:
        pass
    assert not (root / "bad.tar.gz").exists()
    (source / "secrets.json").unlink()
    (source / "config.json").write_text("invalid json")
    try:
        module.export(source, root / "bad.tar.gz", "1.0.0-beta.8.4")
        raise AssertionError("invalid JSON export was allowed")
    except ValueError:
        pass
print("ASR sensitive configuration export self-test: PASS")
