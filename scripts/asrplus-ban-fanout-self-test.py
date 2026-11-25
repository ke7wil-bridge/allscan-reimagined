#!/usr/bin/env python3
"""#291: a ban applies on every Netoholics hub automatically, with no extra step.

- ASRPlus ban/unban for any hub applies on MasterHub and queues to every remote hub.
- The MasterHub Global Ban / AllStar ban helpers fan out each ban/unban to every hub.
- No fan-out loop: MasterHub's own application of a fanned-out ban does not fan out again.
- A failed hub is reported; the result never claims success everywhere.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "ops/asrplus/staging/masterhub"


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def run_control(control, request: dict) -> tuple[int, dict]:
    sys.stdin = io.StringIO(json.dumps(request))
    out = io.StringIO()
    with redirect_stdout(out):
        code = control.main()
    return code, json.loads(out.getvalue().strip().splitlines()[-1])


def control_tests() -> None:
    sys.path.insert(0, str(MASTER))
    control = load("asrplus_control_fanout", MASTER / "asrplus-control.py")
    assert set(control.REMOTE_HUBS.values()) == {"phoenix", "ky2", "texas", "uk"}, control.REMOTE_HUBS
    queued: list[tuple[str, dict]] = []
    master_calls: list[tuple[list[str], dict]] = []
    failing: set[str] = set()

    def fake_queue(command, local_node, requested_hub):
        if command["hub"] in failing:
            raise RuntimeError("remote command queue is full")
        queued.append((command["hub"], dict(command)))
        return command

    def fake_run(arguments, **kwargs):
        master_calls.append((list(arguments), dict(kwargs.get("env") or {})))
        return subprocess.CompletedProcess(arguments, 0, json.dumps({"ok": True}) + "\n", "")

    control.queue_remote = fake_queue
    control.load_central_hub = lambda path, hub: {"key": b"k" * 32, "keyId": "test", "mode": "required"}
    real_run = subprocess.run
    subprocess.run = fake_run  # control uses the shared subprocess module
    control.audit_outcome = lambda payload: None

    # Ban requested for ky2 only -> MasterHub + every remote hub.
    code, result = run_control(control, {"hub": "ky2", "action": "ban", "kind": "allstar-call",
                                         "value": "KC8FQV", "duration": "permanent", "actor": "KE7WIL"})
    assert code == 0 and result["ok"], result
    assert sorted(hub for hub, _ in queued) == ["ky2", "phoenix", "texas", "uk"], queued
    assert all(cmd["action"] == "ban" and cmd["value"] == "KC8FQV" and cmd["duration"] == "permanent"
               and cmd["localNode"] == {v: k for k, v in control.REMOTE_HUBS.items()}[hub]
               for hub, cmd in queued)
    arguments, env = master_calls[-1]
    assert arguments == [control.GLOBAL_BAN_HELPER, "ban", "KC8FQV", "permanent", "--actor", "KE7WIL"], arguments
    assert env.get("ASRPLUS_NO_FANOUT") == "1", "MasterHub application must not fan out again"
    assert set(result["hubs"]) == {"masterhub", "ky2", "phoenix", "texas", "uk"}

    # A node ban from any hub also goes everywhere; MasterHub uses the AllStar helper.
    queued.clear()
    code, result = run_control(control, {"hub": "phoenix", "action": "ban", "kind": "node", "value": "123456",
                                         "duration": "1h", "actor": "KE7WIL"})
    assert code == 0 and len(queued) == 4 and master_calls[-1][0][:2] == [control.ASL_BAN_HELPER, "ban"]

    # Unban from the master hub entry also reaches every hub.
    queued.clear()
    code, result = run_control(control, {"hub": "masterhub", "action": "unban", "kind": "allstar-call",
                                         "value": "KC8FQV", "actor": "KE7WIL"})
    assert code == 0 and len(queued) == 4 and master_calls[-1][0][:3] == [control.GLOBAL_BAN_HELPER, "unban", "KC8FQV"]

    # One unreachable hub: reported, and never "applied everywhere".
    queued.clear()
    failing.add("texas")
    code, result = run_control(control, {"hub": "ky2", "action": "ban", "kind": "allstar-call",
                                         "value": "KC8FQV", "duration": "1h", "actor": "KE7WIL"})
    assert code == 1 and not result["ok"] and "texas" in result["message"], result
    assert result["hubs"]["texas"]["ok"] is False and len(queued) == 3
    failing.clear()

    # Helper-initiated fan-out queues to remote hubs only (MasterHub already applied it).
    queued.clear()
    before = len(master_calls)
    code, result = run_control(control, {"action": "fanout", "verb": "ban", "kind": "allstar-call",
                                         "value": "KC8FQV", "duration": "30d", "actor": "KE7WIL"})
    assert code == 0 and result["ok"] and len(queued) == 4 and len(master_calls) == before, result

    # Invalid fan-out never queues.
    queued.clear()
    code, result = run_control(control, {"action": "fanout", "verb": "ban", "kind": "allstar-call",
                                         "value": "N0*", "duration": "1h"})
    assert code == 1 and not queued and not result["ok"]
    subprocess.run = real_run
    sys.stdin = sys.__stdin__
    sys.path.remove(str(MASTER))


def real_queue_tests() -> None:
    """Exercise the real queue_remote path (the stubbed tests above missed a
    NameError there) with a temporary queue and no network."""
    sys.path.insert(0, str(MASTER))
    control = load("asrplus_control_queue", MASTER / "asrplus-control.py")
    with tempfile.TemporaryDirectory() as directory:
        control.QUEUE_ROOT = Path(directory) / "queue"
        control.AUDIT_PATH = Path(directory) / "audit.jsonl"

        def integrity(path, hub):
            if hub == "texas":
                raise ValueError("hub integrity configuration is unavailable")
            return {"key": b"k" * 32, "keyId": "test", "mode": "required"}

        control.load_central_hub = integrity
        control.sign_command = lambda command, key, key_id: command | {"signature": "test", "keyId": key_id}
        popen_calls = []
        real_popen = subprocess.Popen
        subprocess.Popen = lambda *args, **kwargs: popen_calls.append(args)
        hubs = control.fan_out("ban", "allstar-call", "N0QA291", "15m", "", "test", "fanout")
        assert hubs["texas"] == {"ok": True, "queued": False, "skipped": "not provisioned"}, hubs
        for hub in ("ky2", "phoenix", "uk"):
            assert hubs[hub]["ok"] and hubs[hub]["queued"], hubs
            files = list((control.QUEUE_ROOT / hub).glob("*.json"))
            assert len(files) == 1 and files[0].stem == hubs[hub]["id"], (hub, files)
            queued = json.loads(files[0].read_text())
            assert queued["action"] == "ban" and queued["value"] == "N0QA291" and queued["hub"] == hub
        subprocess.Popen = real_popen
        assert len(popen_calls) == 1, "ky2 notification should fire once"
    sys.path.remove(str(MASTER))


def helper_tests() -> None:
    for relative in ("scripts/asr-global-ban.py", "scripts/asr-asl-ban.py"):
        module = load("fanout_" + Path(relative).stem.replace("-", "_"), ROOT / relative)
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "asrplus-control"
            log = Path(directory) / "requests.jsonl"
            fake.write_text("#!/bin/sh\ncat >> '%s'\necho >> '%s'\n"
                            "printf '%%s\\n' '{\"ok\": true, \"hubs\": {\"ky2\": {\"ok\": true}}}'\n" % (log, log))
            fake.chmod(0o755)
            module.ASRPLUS_CONTROL = str(fake)
            os.environ.pop("ASRPLUS_NO_FANOUT", None)
            result = module.fan_out_to_hubs("ban", "allstar-call", "KC8FQV", "permanent", "KE7WIL")
            assert result["ok"] and result["hubs"]["ky2"]["ok"], (relative, result)
            request = json.loads(log.read_text().strip().splitlines()[-1])
            assert request == {"action": "fanout", "verb": "ban", "kind": "allstar-call", "value": "KC8FQV",
                               "duration": "permanent", "actor": "KE7WIL"}, request
            os.environ["ASRPLUS_NO_FANOUT"] = "1"
            assert module.fan_out_to_hubs("ban", "allstar-call", "KC8FQV", "1h") is None, "loop guard missing"
            os.environ.pop("ASRPLUS_NO_FANOUT", None)
            module.ASRPLUS_CONTROL = str(Path(directory) / "missing")
            assert module.fan_out_to_hubs("ban", "allstar-call", "KC8FQV", "1h") is None, "remote hubs must not fan out"
            fake.write_text("#!/bin/sh\nprintf '%s\\n' '{\"ok\": false, \"hubs\": {\"texas\": {\"ok\": false}}}'\nexit 1\n")
            module.ASRPLUS_CONTROL = str(fake)
            assert module.fan_out_to_hubs("unban", "allstar-call", "KC8FQV")["ok"] is False
    ban = (ROOT / "scripts/asr-global-ban.py").read_text(encoding="utf-8")
    assert "hubFanout=hub_fanout" in ban and "if hub_fanout is not None and not hub_fanout[\"ok\"]:\n        verified = False" in ban
    assert "hubFanout\": hub_fanout" in (ROOT / "scripts/asr-asl-ban.py").read_text(encoding="utf-8")


def main() -> int:
    control_tests()
    real_queue_tests()
    helper_tests()
    print("ASRPlus all-hub ban fan-out self-test: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
