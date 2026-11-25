#!/usr/bin/env python3
"""#291: banning an AllStar node also bans its owner on EchoLink and every bridge.

EchoLink and the digital bridges only know a station by callsign, so a node ban
(KF8DRJ's node 67098) used to stop that operator on AllStar only. The AllStar
ban helper now looks up the node owner and applies the same ban to that
callsign through ASR Global Ban, and lifts it again on unban.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

spec = importlib.util.spec_from_file_location("asr_asl_ban_linked", Path(__file__).with_name("asr-asl-ban.py"))
ban = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ban)


class QuietAMI:
    """Minimal AMI: an AllStar denylist, no EchoLink module, no channels."""
    ast: set[str] = set()

    def action(self, name, **fields):
        raise AssertionError(f"unexpected AMI action {name}")

    def command(self, value):
        if value.startswith("database show denylist/"):
            return "\n".join(f"/denylist/641890/{item} : ASR-managed" for item in sorted(self.ast))
        if value.startswith("database put denylist/641890 "):
            QuietAMI.ast.add(value.split()[3])
        if value.startswith("database del denylist/641890 "):
            QuietAMI.ast.discard(value.split()[3])
        return ""

    def close(self):
        pass


def run_helper(*arguments: str) -> tuple[int, dict]:
    sys.argv = ["asr-asl-ban", *arguments]
    out = io.StringIO()
    code = 0
    with redirect_stdout(out):
        try:
            ban.main()
        except Exception as exc:  # mirrors the helper's __main__ wrapper
            print(json.dumps({"ok": False, "error": str(exc)}))
            code = 1
    return code, json.loads(out.getvalue().strip().splitlines()[-1])


def main() -> int:
    os.environ["ASRPLUS_NO_FANOUT"] = "1"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        ban.STATE = root / "state.json"
        ban.LOCK = root / ".lock"
        ban.GLOBAL = root / "global-bans.list"
        ban.CONFIG = root / "config.json"
        ban.EXTENSIONS = root / "no-extensions.conf"
        ban.ASTDB_FILES = (root / "astdb.txt",)
        ban.AMI = QuietAMI
        ban.CONFIG.write_text(json.dumps({"node": "641890", "callsign": "N0OWNER", "bridges": [],
                                          "filteredStations": ["N0SERVICE"]}))
        ban.ASTDB_FILES[0].write_text("41199|KF8DRJ||Allen Park MI\n67098|KF8DRJ|Crossbander,|Allen Park MI\n"
                                      "55555|N0SERVICE|probe|\n55556|N0OWNER|local|\n77777|KC8FQV||\n88888|||\n")
        ban.GLOBAL.write_text("KC8FQV\n")

        log = root / "global-ban.log"
        helper = root / "global-ban"
        helper.write_text("#!/bin/sh\nprintf '%%s\\n' \"$*\" >> '%s'\n"
                          "if [ -e '%s' ]; then printf '%%s\\n' '{\"ok\": false, \"error\": \"bridge down\"}'; exit 1; fi\n"
                          "printf '%%s\\n' '{\"ok\": true}'\n" % (log, root / "fail"))
        helper.chmod(0o755)
        ban.GLOBAL_BAN_HELPER = str(helper)

        def calls() -> list[str]:
            return log.read_text().splitlines() if log.exists() else []

        # Node ban -> owner callsign banned everywhere with the same duration.
        code, result = run_helper("ban", "--kind", "node", "--value", "67098", "--duration", "15m", "--actor", "Thomas")
        assert code == 0 and result["ok"], result
        assert calls() == ["ban KF8DRJ 15m --actor Thomas"], calls()
        assert result["linkedCallsign"] == {"callsign": "KF8DRJ", "action": "ban", "ok": True, "error": ""}
        row = [item for item in ban.read_state()["bans"] if item["value"] == "67098"][0]
        assert row["linkedCall"] == "KF8DRJ" and row["linkedCallOwned"] is True
        assert "67098" in QuietAMI.ast

        # A second node of the same owner: the callsign ban stays until the last one is lifted.
        code, result = run_helper("ban", "--kind", "node", "--value", "41199", "--duration", "1h", "--actor", "Thomas")
        assert code == 0 and calls()[-1] == "ban KF8DRJ 1h --actor Thomas", calls()
        code, result = run_helper("unban", "--kind", "node", "--value", "67098", "--actor", "Thomas")
        assert code == 0 and result["linkedCallsign"] is None and len(calls()) == 2, calls()
        code, result = run_helper("unban", "--kind", "node", "--value", "41199", "--actor", "Thomas")
        assert code == 0 and calls()[-1] == "unban KF8DRJ --actor Thomas", calls()
        assert result["linkedCallsign"]["ok"] and "67098" not in QuietAMI.ast

        # An existing Global Ban (KC8FQV, permanent) is never shortened or lifted by a node ban.
        before = len(calls())
        code, result = run_helper("ban", "--kind", "node", "--value", "77777", "--duration", "15m", "--actor", "Thomas")
        assert code == 0 and len(calls()) == before and result["linkedCallsign"] is None, calls()
        code, result = run_helper("unban", "--kind", "node", "--value", "77777", "--actor", "Thomas")
        assert code == 0 and len(calls()) == before, "unbanning a node lifted an unrelated Global Ban"

        # Service/probe calls, the local callsign and nodes with no owner stay node-only.
        for node in ("55555", "55556", "88888", "99999"):
            code, result = run_helper("ban", "--kind", "node", "--value", node, "--duration", "15m", "--actor", "Thomas")
            assert code == 0 and result["linkedCallsign"] is None and len(calls()) == before, (node, calls())
            run_helper("unban", "--kind", "node", "--value", node, "--actor", "Thomas")

        # A failed callsign ban is reported, never silent success.
        (root / "fail").touch()
        code, result = run_helper("ban", "--kind", "node", "--value", "67098", "--duration", "15m", "--actor", "Thomas")
        assert code == 1 and not result["ok"] and "KF8DRJ" in result["error"] and "bridge down" in result["error"], result
        assert "67098" in QuietAMI.ast, "the node ban itself must still apply"
        (root / "fail").unlink()

        # Missing Global Ban helper is reported too.
        ban.GLOBAL_BAN_HELPER = str(root / "missing")
        code, result = run_helper("ban", "--kind", "node", "--value", "67098", "--duration", "1h", "--actor", "Thomas")
        assert code == 1 and "not installed" in result["error"], result
    os.environ.pop("ASRPLUS_NO_FANOUT", None)
    print("ASR node ban -> owner callsign self-test: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
