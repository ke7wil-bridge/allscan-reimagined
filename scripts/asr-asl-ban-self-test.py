#!/usr/bin/env python3
"""Behavior checks for native ASL bans without touching Asterisk or live policy."""
import importlib.util
import json
import tempfile
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("asr_asl_ban", Path(__file__).with_name("asr-asl-ban.py"))
ban = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ban)


class FakeAMI:
    def __init__(self, ast=(), echo=()):
        self.ast = set(ast)
        self.echo = set(echo)
        self.calls = []
        self.channels = []

    def action(self, name, **fields):
        if name == "GetConfig":
            return {"Response": "Success", "Line-000000-000000": "deny=" + ",".join(sorted(self.echo))} if self.echo else {"Response": "Success"}
        if name == "ModuleLoad":
            assert fields == {"Module": "chan_echolink.so", "LoadType": "refresh"}
            self.calls.append(("refresh", "chan_echolink.so"))
            return {"Response": "Success", "Message": "Module unloaded and loaded."}
        if name == "ModuleCheck":
            return {"Response": "Success"}
        if name == "UpdateConfig":
            assert fields["Cat-000000"] == "el0"
            assert fields["Var-000000"] == "deny"
            if fields["Action-000000"] == "Delete":
                self.echo.clear()
            else:
                self.echo = set(fields["Value-000000"].split(",")) if fields["Value-000000"] else set()
            self.calls.append(("echo", tuple(sorted(self.echo))))
            return {"Response": "Success"}
        raise AssertionError(name)

    def command(self, value):
        self.calls.append(("command", value))
        if value == "core show channels concise":
            return "\n".join(self.channels)
        if value.startswith("core show channel IAX2/web-2"):
            return "Caller ID Name: N0GLOBAL\nNODENUM=641890\nCALLSIGN=N0GLOBAL"
        if value.startswith("channel request hangup "):
            target = value.rsplit(" ", 1)[-1]
            self.channels = [line for line in self.channels if not line.startswith(target + "!")]
            return "Requested Hangup on channel " + target
        if value.startswith("database show denylist/"):
            return "\n".join("/denylist/641890/" + item + " : manual" for item in sorted(self.ast))
        if value.startswith("database put denylist/641890 "):
            self.ast.add(value.split()[3])
        if value.startswith("database del denylist/641890 "):
            self.ast.remove(value.split()[3])
        if value == "module show like chan_echolink.so":
            return "chan_echolink.so  Echolink Channel Driver  0  Running  extended"
        return "ok"


def run():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        ban.STATE = root / "state.json"
        ban.GLOBAL = root / "urfd.blacklist"
        ban.CONFIG = root / "config.json"
        ban.CONFIG.write_text(json.dumps({"node": "641890", "callsign": "N0OWNER", "bridges": []}))
        now = int(time.time())
        ban.GLOBAL.write_text("N0GLOBAL\n")
        fake = FakeAMI(ast={"765432"}, echo={"N0MANUAL", "N0GLOBAL"})
        fake.channels = [
            "IAX2/remote-1!radio-secure!641890!1!Up!Rpt!641890!123456!Remote!",
            "IAX2/web-2!allstar-public!s!1!Up!Rpt!641890!0!!",
            "ECHOLINK/el0-3!radio-secure!641890!1!Up!Rpt!641890!3000456!N0LOCAL-L!",
            "IAX2/local-4!radio-secure!1001!1!Up!Rpt!1001!641890!Local!",
        ]
        state = {"version": 1, "bans": [
            {"kind": "node", "value": "123456", "createdAt": now, "expiresAt": now + 3600, "reason": "", "actor": "test"},
            {"kind": "echolink-call", "value": "N0LOCAL", "createdAt": now, "expiresAt": now + 3600, "reason": "", "actor": "test"},
        ], "ownedAst": [], "ownedEcho": []}
        result = ban.reconcile(state, fake, "641890")
        assert fake.ast == {"765432", "123456", "N0GLOBAL"}
        assert fake.echo == {"N0MANUAL", "N0GLOBAL", "N0LOCAL"}
        assert ban.read_state()["ownedEcho"] == ["N0LOCAL"]
        assert "765432" in result["externalAst"]
        assert "N0MANUAL" in result["externalEcho"]
        assert result["removed"] == 3
        assert len(fake.channels) == 1 and "local-4" in fake.channels[0]
        assert "N0GLOBAL" in result["externalEcho"]  # pre-existing manual rule

        # Expiry removes only ASR owned restrictions; manual entries remain.
        for item in state["bans"]:
            item["expiresAt"] = now - 1
        ban.GLOBAL.write_text("")
        ban.reconcile(state, fake, "641890")
        assert fake.ast == {"765432"}
        assert fake.echo == {"N0MANUAL", "N0GLOBAL"}
        assert ban.read_state()["bans"] == []
        assert ban.read_state()["ownedAst"] == []
        assert ban.read_state()["ownedEcho"] == []
        # A local rule remains effective after its overlapping global rule ends.
        state["bans"] = [{"kind": "echolink-call", "value": "N0OVERLAP", "createdAt": now,
                          "expiresAt": now + 3600, "reason": "", "actor": "test"}]
        ban.GLOBAL.write_text("N0OVERLAP\n")
        ban.reconcile(state, fake, "641890")
        ban.GLOBAL.write_text("")
        ban.reconcile(state, fake, "641890")
        assert "N0OVERLAP" in fake.echo
        state["bans"] = []
        ban.reconcile(state, fake, "641890")
        assert "N0OVERLAP" not in fake.echo
        assert ban.base_call("n0example-L") == "N0EXAMPLE"
        for candidate in (";evil", "12345", "N0CALL\nAction: Command"):
            try:
                ban.check_call(candidate)
            except ValueError:
                pass
            else:
                raise AssertionError("Unsafe callsign accepted: " + candidate)
        assert ban.check_node("641890") == "641890"
        # A busy EchoLink module leaves the update pending; a later list retries it.
        state["bans"] = [{"kind": "echolink-call", "value": "N0RETRY", "createdAt": now,
                          "expiresAt": now + 3600, "reason": "", "actor": "test"}]
        fake.channels = ["ECHOLINK/other-1!radio-secure!641890!1!Up!Rpt!641890!0!N0OTHER!"]
        try:
            ban.reconcile(state, fake, "641890")
        except RuntimeError as exc:
            assert "active EchoLink session" in str(exc)
        else:
            raise AssertionError("EchoLink module restarted while a user was active")
        assert "N0RETRY" in fake.echo
        fake.channels = []
        ban.reconcile(state, fake, "641890")
        assert "N0RETRY" in ban.read_state()["appliedEcho"]
        assert ("refresh", "chan_echolink.so") in fake.calls
    print("ASL native ban policy self-test: ok")


if __name__ == "__main__":
    run()
