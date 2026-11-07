#!/usr/bin/env python3
"""Offline regression for integrity-check master release consistency gate."""
import pathlib, subprocess, tempfile, json
source = pathlib.Path(__file__).with_name("asr-integrity-check.sh").read_text()
start = source.index("import json, pathlib, re, sys\n", source.index("PY_ASR_MASTER_RELEASE"))
end = source.index("\nPY_ASR_MASTER_RELEASE", start)
code = source[start:end]
with tempfile.TemporaryDirectory() as temporary:
    root = pathlib.Path(temporary)
    (root / "server").mkdir(); (root / "web/assets").mkdir(parents=True)
    (root / "package.json").write_text(json.dumps({"version": "1.0.0-beta.8.4"}))
    (root / "server/asr-api.php").write_text("const ASR_VERSION = '1.0.0-beta.8.4';")
    (root / "web/index.html").write_text('<script src="/assets/app.js"></script>')
    (root / "web/assets/app.js").write_text("test")
    def run():
        return subprocess.run(["python3", "-c", code, str(root)], capture_output=True).returncode
    assert run() == 0, "matching master must pass"
    (root / "server/asr-api.php").write_text("const ASR_VERSION = '1.0.0-beta.8.3';")
    assert run() != 0, "version mismatch must block repair"
    (root / "server/asr-api.php").write_text("const ASR_VERSION = '1.0.0-beta.8.4';")
    (root / "web/assets/app.js").unlink()
    assert run() != 0, "missing referenced asset must block repair"
print("ASR integrity master release consistency: PASS")
