#!/usr/bin/env python3
import ast
import importlib.util
import json
import os
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "src/App.tsx").read_text(encoding="utf-8")
ZELLO_COMPOSE_PATH = ROOT.parent / "asl3/docker-compose.yml"
ZELLO_COMPOSE = ZELLO_COMPOSE_PATH.read_text(encoding="utf-8") if ZELLO_COMPOSE_PATH.exists() else ""
ZELLO_PATH = Path(os.environ.get("ASR_ZELLO_RUNTIME_SOURCE", str(ROOT / "runtime/zello"))) / "asl_zello_bridge/zello.py"


def check(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


check(not ZELLO_COMPOSE or "/run/asr-global:ro" in ZELLO_COMPOSE,
      "Zello global-ban read-only mount missing")
INSTALLER = (ROOT / "scripts/asr-bridge-setup-zello-install.py").read_text(encoding="utf-8")
check("ExecStartPre=/usr/bin/test -f /var/lib/allscan-reimagined/admin/urfd.blacklist" in INSTALLER,
      "Zello startup must require the native Global Ban blacklist")
check("/var/lib/allscan-reimagined/global-ban" not in INSTALLER,
      "Zello installer still initializes obsolete Global Ban directory")
check("Recent Zello Talkers" in APP and "managedConnectedCards" in APP,
      "Zello Manage Clients presentation missing")
check("current or next Zello transmission for up to five minutes" in APP
      and "protocol === 'ZELLO'" in APP,
      "Zello one-shot Kick is not available for recent talkers")

spec = importlib.util.spec_from_file_location("asr_urf_admin", ROOT / "scripts/asr-urf-admin.py")
urf_admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(urf_admin)
with tempfile.TemporaryDirectory(prefix="asr-zello-kick-self-test-") as raw_tmp:
    tmp = Path(raw_tmp)
    urf_admin.ZELLO_TALKERS = str(tmp / "zello-talkers.json")
    urf_admin.ZELLO_KICKS = str(tmp / "asr-zello-kicks.json")
    urf_admin.append_audit_event = lambda *args, **kwargs: None
    emitted = []
    urf_admin.emit = lambda **kwargs: emitted.append(kwargs)
    now = int(time.time())
    Path(urf_admin.ZELLO_TALKERS).write_text(json.dumps({
        "active": False,
        "current_user": "",
        "recent_users": [{"name": "N0CALL", "last_seen_epoch": now - 30}],
    }), encoding="utf-8")
    urf_admin.write_zello_kick("n0call", "self-test")
    kick = json.loads(Path(urf_admin.ZELLO_KICKS).read_text(encoding="utf-8"))
    check("N0CALL" in kick["sessions"], "recent Zello talker Kick was not armed")
    check(emitted[-1]["enforcement"] == "current-or-next-zello-stream"
          and emitted[-1]["expiresSeconds"] == 300,
          "recent Zello talker Kick did not report its one-shot contract")

with tempfile.TemporaryDirectory(prefix="asr-zello-self-test-") as raw_tmp:
    tmp = Path(raw_tmp)
    if not ZELLO_PATH.exists():
        print("Zello runtime ban enforcement: NOT TESTED (source unavailable)")
        if os.environ.get("ASR_REQUIRE_ZELLO_ENFORCEMENT_TEST") == "1":
            raise AssertionError("Zello source unavailable; cannot certify ban enforcement")
    else:
        source = ZELLO_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {"_ke7wil_clean_talker_name", "_ke7wil_zello_is_banned"}
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
        namespace = {"os": os}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ZELLO_PATH), "exec"), namespace)
        namespace["ZELLO_GLOBAL_BANS"] = str(tmp / "global.blacklist")
        (tmp / "global.blacklist").write_text("N0CALL*\nAD7TG\n", encoding="utf-8")
        check(namespace["_ke7wil_zello_is_banned"]("n0call-7"), "Zello prefix ban failed")
        check(namespace["_ke7wil_zello_is_banned"]("AD7TG"), "Zello exact ban failed")
        check(not namespace["_ke7wil_zello_is_banned"]("KE7WIL"), "Zello ban overmatched")

print("Zello client-management UI/static checks: ok")
