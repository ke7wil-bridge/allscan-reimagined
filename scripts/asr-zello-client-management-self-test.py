#!/usr/bin/env python3
import ast
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "src/App.tsx").read_text(encoding="utf-8")
ZELLO_COMPOSE_PATH = ROOT.parent / "asl3/docker-compose.yml"
ZELLO_COMPOSE = ZELLO_COMPOSE_PATH.read_text(encoding="utf-8") if ZELLO_COMPOSE_PATH.exists() else ""
ZELLO_PATH = ROOT.parent / "asl3/zello-bridge/asl_zello_bridge/zello.py"


def check(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


check(not ZELLO_COMPOSE or "/run/asr-global:ro" in ZELLO_COMPOSE,
      "Zello global-ban read-only mount missing")
check("Recent Zello Talkers" in APP and "managedConnectedCards" in APP,
      "Zello Manage Clients presentation missing")
check("kickUrfConnectedClient(identity, 'ZELLO')" not in APP,
      "Zello Kick button remains")

with tempfile.TemporaryDirectory(prefix="asr-zello-self-test-") as raw_tmp:
    tmp = Path(raw_tmp)
    if ZELLO_PATH.exists():
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

print("Zello client-management self-test: ok")
