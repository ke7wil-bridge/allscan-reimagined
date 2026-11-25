#!/usr/bin/env python3
"""#291: ASRPlus remote agents must apply callsign bans through ASR Global Ban.

A callsign ban sent from ASRPlus to a remote hub used to reach only the
AllStar/EchoLink list, so the same person could still connect by web
transceiver (callsign, no AllStar node) or through a digital bridge.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = (ROOT / "ops/asrplus/staging/phoenix/asrplus-agent",
          ROOT / "ops/asrplus/staging/ky2/asrplus-ky-agent")
GLOBAL = "/usr/local/sbin/allscan-reimagined-global-ban"
ASL = "/usr/local/sbin/allscan-reimagined-asl-ban"


def load(path: Path):
    loader = importlib.machinery.SourceFileLoader(f"agent_{path.parent.name}", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Recorder:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, arguments, **kwargs):
        self.calls.append(list(arguments))
        if arguments[0] == GLOBAL and arguments[1] == "list":
            out = {"ok": True, "bans": [{"rule": "KC8FQV", "createdAt": 1, "expiresAt": 0},
                                        {"rule": "N0*", "createdAt": 1, "expiresAt": 0}]}
        elif arguments[0] == ASL and arguments[1] == "list":
            out = {"ok": True, "bans": [{"kind": "node", "value": "123456", "createdAt": 1, "expiresAt": 0}]}
        else:
            out = {"ok": True}
        return subprocess.CompletedProcess(arguments, 0, json.dumps(out) + "\n", "")


def main() -> int:
    assert AGENTS[0].read_bytes() == AGENTS[1].read_bytes(), "Phoenix and ky2 agents diverged"
    agent = load(AGENTS[0])
    config = {"hub": "ky2", "node": "674980"}
    base = {"hub": "ky2", "localNode": "674980", "actor": "KE7WIL"}
    recorder = Recorder()
    agent.subprocess.run = recorder

    result = agent.execute_action(config, base | {"action": "ban", "kind": "allstar-call",
                                                  "value": "kc8fqv", "duration": "permanent"})
    assert result["ok"], result
    assert recorder.calls[-1] == [GLOBAL, "ban", "KC8FQV", "permanent", "--actor", "KE7WIL"], recorder.calls[-1]

    agent.execute_action(config, base | {"action": "ban", "kind": "echolink-call",
                                         "value": "KC8FQV-L", "duration": "1h"})
    assert recorder.calls[-1] == [GLOBAL, "ban", "KC8FQV", "1h", "--actor", "KE7WIL"], recorder.calls[-1]

    agent.execute_action(config, base | {"action": "unban", "kind": "allstar-call", "value": "KC8FQV"})
    assert recorder.calls[-1] == [GLOBAL, "unban", "KC8FQV", "--actor", "KE7WIL"], recorder.calls[-1]

    # Node bans stay on the AllStar node denylist helper.
    agent.execute_action(config, base | {"action": "ban", "kind": "node", "value": "123456",
                                         "duration": "15m", "reason": "test"})
    assert recorder.calls[-1][:2] == [ASL, "ban"] and "--duration" in recorder.calls[-1], recorder.calls[-1]

    # Invalid input never reaches a helper.
    before = len(recorder.calls)
    bad = agent.execute_action(config, base | {"action": "ban", "kind": "allstar-call",
                                               "value": "KC8FQV", "duration": "forever"})
    assert not bad["ok"] and len(recorder.calls) == before

    # Manage Bans lists Global Ban callsigns alongside native node bans.
    listed = agent.execute_action(config, base | {"action": "ban-list"})
    values = {(row["kind"], row["value"]) for row in listed["bans"]}
    assert ("node", "123456") in values and ("allstar-call", "KC8FQV") in values, listed
    assert not any(row["value"] == "N0*" for row in listed["bans"]), "prefix rules are not callsign bans"
    print("ASRPlus agent ban routing self-test: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
