#!/usr/bin/env python3
"""Read-only drift regression cases for #132."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory

spec = importlib.util.spec_from_file_location("asr_version_drift", Path(__file__).with_name("asr-version-drift.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

with TemporaryDirectory() as tmp:
    root = Path(tmp)
    master, web = root / "release", root / "installed"
    (master / "server").mkdir(parents=True)
    (master / "web/assets").mkdir(parents=True)
    (web / "assets").mkdir(parents=True)
    (master / "package.json").write_text(json.dumps({"version": "1.0.0-beta.8.4"}))
    api = "<?php const ASR_VERSION = '1.0.0-beta.8.4';"
    (master / "server/asr-api.php").write_text(api)
    (web / "asr-api.php").write_text(api)
    (web / "index.html").write_text('<script src="/assets/index.js"></script><link href="/assets/index.css">')
    for name in ("index.js", "index.css"):
        (master / "web/assets" / name).write_text("correct")
        (web / "assets" / name).write_text("correct")
    assert module.audit(master, web)["ok"]
    (web / "assets/index.js").write_text("stale!!")
    result = module.audit(master, web)
    assert not result["ok"] and any(not x["ok"] and x["name"] == "asset-content" for x in result["checks"])
    (web / "assets/index.js").write_text("correct")
    (web / "asr-api.php").write_text("<?php const ASR_VERSION = '1.0.0-beta.8.3';")
    assert not module.audit(master, web)["ok"]
    (web / "asr-api.php").write_text(api)
    (web / "assets/index.css").unlink()
    assert not module.audit(master, web)["ok"]
    assert (master / "web/assets/index.css").read_text() == "correct"
print("ASR read-only version drift self-test: PASS")
