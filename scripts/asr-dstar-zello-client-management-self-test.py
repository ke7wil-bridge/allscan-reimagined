#!/usr/bin/env python3
import ast
import importlib.util
import os
import re
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "asr-api.php").read_text(encoding="utf-8")
SERVER_API = (ROOT / "server/asr-api.php").read_text(encoding="utf-8")
APP = (ROOT / "src/App.tsx").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
ZELLO_COMPOSE_PATH = ROOT.parent / "asl3/docker-compose.yml"
ZELLO_COMPOSE = ZELLO_COMPOSE_PATH.read_text(encoding="utf-8") if ZELLO_COMPOSE_PATH.exists() else ""
ZELLO_PATH = ROOT.parent / "asl3/zello-bridge/asl_zello_bridge/zello.py"
URF_PATH = ROOT / "scripts/asr-urf-admin.py"


def check(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


spec = importlib.util.spec_from_file_location("asr_urf_admin_dstar_zello", URF_PATH)
check(spec is not None and spec.loader is not None, "administration helper could not be imported")
urf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(urf)

check(API == SERVER_API, "API copies diverged")
check('"DSTAR": "DSTAR"' in URF_PATH.read_text(encoding="utf-8"), "D-Star helper protocol missing")
check('"ZELLO": "ZELLO"' in URF_PATH.read_text(encoding="utf-8"), "Zello helper protocol missing")
check("/run/dstar-reflector-admin" in COMPOSE and "/run/dstar-reflector-control" in COMPOSE,
      "ASR D-Star writable administration mounts missing")
check(not ZELLO_COMPOSE or "/run/asr-global:ro" in ZELLO_COMPOSE, "Zello global-ban read-only mount missing")
check("Recent Zello Talkers" in APP and "managedConnectedCards" in APP,
      "D-Star/Zello Manage Clients presentation missing")
check("kickUrfConnectedClient(identity, 'ZELLO')" not in APP, "Zello Kick button remains")
check("['DMRMMDVM', 'YSF', 'P25', 'NXDN', 'M17', 'DSTAR']" in API,
      "Zello is still accepted by Kick API")

with tempfile.TemporaryDirectory(prefix="asr-dstar-zello-self-test-") as raw_tmp:
    tmp = Path(raw_tmp)
    urf.CONFIG = str(tmp / "config.json")
    Path(urf.CONFIG).write_text('{"bridges":[{"id":"dstar","mode":"dstar"}]}\n', encoding="utf-8")
    urf.DSTAR_BLACKLIST = str(tmp / "xlxd.blacklist")
    urf.write_dstar_rules(["N0CALL*", "AD7TG"])
    lines = (tmp / "xlxd.blacklist").read_text(encoding="utf-8").splitlines()
    check(lines[-2:] == ["AD7TG", "N0CALL*"], "D-Star blacklist synchronization failed")
    check(not urf.dstar_rule_supported("3224939"), "numeric DMR ID leaked into D-Star rules")
    check(not urf.dstar_rule_supported("TOO-LONG"), "punctuation-bearing D-Star rule was accepted")
    check(urf.normalize("Josh_KE7WIL") == "JOSH_KE7WIL", "Zello username underscore was rejected")
    check(not urf.dstar_rule_supported("JOSH_KE7WIL"), "Zello-only identity leaked into D-Star rules")

    reload_events = []
    original_request_dstar_event = urf.request_dstar_event
    urf.request_dstar_event = lambda rule, event, required=False: reload_events.append((rule, event, required)) or 0
    urf.sync_dstar_rules(["W7JHQ", "3224939"])
    urf.request_dstar_event = original_request_dstar_event
    check(reload_events == [("*", "reload", False)], "D-Star synchronization did not use reload")
    check(urf.read_dstar_rules() == ["W7JHQ"], "D-Star synchronization retained an unsafe rule")

    urf.DSTAR_RUNTIME_DIR = str(tmp)
    urf.DSTAR_REQUEST = str(tmp / "asr-dstar-client.request")
    urf.DSTAR_RESULT = str(tmp / "asr-dstar-client.result")

    def confirm_dstar():
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            request = Path(urf.DSTAR_REQUEST)
            if request.exists():
                check(request.read_text(encoding="utf-8").strip() == "AD7TG|DSTAR|kick",
                      "D-Star request format changed")
                Path(urf.DSTAR_RESULT).write_text("AD7TG|DSTAR|kick|1\n", encoding="utf-8")
                return
            time.sleep(0.01)

    thread = threading.Thread(target=confirm_dstar)
    thread.start()
    check(urf.request_dstar_event("AD7TG", "kick", True) == 1, "D-Star kick confirmation failed")
    thread.join(timeout=2)

    Path(urf.CONFIG).write_text('{"bridges":[]}\n', encoding="utf-8")
    check(urf.sync_dstar_rules(["N0CALL"]) == [], "Absent D-Star backend was not skipped")
    urf.RUNTIME_DIR = str(tmp / "missing-urf-runtime")
    check(urf.request_urf_disconnect("N0CALL", "*", "ban", False) == 0,
          "Absent URF backend was not skipped for global Ban reconciliation")

    if ZELLO_PATH.exists():
        source = ZELLO_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {"_ke7wil_clean_talker_name", "_ke7wil_zello_is_banned"}
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
        namespace = {"os": os, "re": re}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ZELLO_PATH), "exec"), namespace)
        namespace["ZELLO_GLOBAL_BANS"] = str(tmp / "global.blacklist")
        (tmp / "global.blacklist").write_text("N0CALL*\nAD7TG\n", encoding="utf-8")
        check(namespace["_ke7wil_zello_is_banned"]("n0call-7"), "Zello prefix ban failed")
        check(namespace["_ke7wil_zello_is_banned"]("AD7TG"), "Zello exact ban failed")
        check(not namespace["_ke7wil_zello_is_banned"]("KE7WIL"), "Zello ban overmatched")

print("D-Star/Zello client-management self-test: ok")
