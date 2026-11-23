#!/usr/bin/env python3
"""AllStar audio relayed into M17 must stay RELAY, never an M17 talker (#134).

On Netoholics (674982) the USRP2M17 bridge joins the reflector as
"KN4EWT N". Every time AllStar keys, the bridge relays that audio into the
reflector and mrefd logs "Opening stream ... from client KN4EWT N". The card
then showed TX ACTIVE with talker KN4EWT while the node was only relaying.
The bridge's own reflector callsign comes from its ini (or m17Callsign for a
managed bridge), not from the node callsign, so a real station using the
node owner's callsign on their own radio still shows.
"""
from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("asr_bridge_status", ROOT / "scripts/asr_bridge_status.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


EPOCH = 1795449953  # 2026-11-23 16:05:53 UTC, a real relay on 674982.
BRIDGE = {"id": "m17", "mode": "m17", "node": "1004",
          "activityLogDirectory": "/var/log/m17", "activityLogStem": "USRP2M17"}


def opening(client: str, user: str, epoch: int = EPOCH) -> str:
    return (f"{epoch}.890187 nth-serv-p01 mrefd[981772]: Opening stream on module A "
            f"from client {client} with id 0x346d by user {user}")


def closing(epoch: int) -> str:
    return f"{epoch}.360068 nth-serv-p01 mrefd[981772]: Closing stream on module A"


with tempfile.TemporaryDirectory() as tmp:
    ini = Path(tmp) / "usrp2m17.ini"
    ini.write_text("[M17 Network]\nCallsign=KN4EWT N\nLocalPort=17000\n\n[Log]\nDisplayLevel=1\n", encoding="utf-8")
    os.chmod(ini, 0o644)
    identity = module.m17_bridge_identity(BRIDGE, ini)
    check(identity == "KN4EWT-N", f"bridge identity not read from its ini: {identity!r}")
    check(module.m17_bridge_identity({**BRIDGE, "m17Callsign": "N0CALL-M"}, ini) == "N0CALL-M",
          "a managed bridge's m17Callsign was not preferred")
    check(module.m17_bridge_identity({**BRIDGE, "activityLogStem": "OTHER"}, ini) == "",
          "an unrelated card borrowed the legacy bridge ini")
    os.chmod(ini, 0o666)
    check(module.m17_bridge_identity(BRIDGE, ini) == "", "a world-writable ini was trusted")
    os.chmod(ini, 0o644)

    # The live sequence: USRP start (AllStar relayed out), then the bridge's own
    # reflector stream, then end. The node callsign is NOT the bridge callsign
    # here, which is how the old guard let it through.
    state = module.initial_activity_state()
    module.apply_m17_activity(state, "M: 2026-11-23 16:05:53.887 USRP voice received as first frame", EPOCH + 1)
    module.apply_m17_journal(state, opening("KN4EWT N", "KN4EWT N"), EPOCH + 1, "N4NHO", identity)
    check(state["role"] == "relay", f"relayed AllStar audio became an M17 talker: {state['role']} {state['current_user']!r}")
    check(state["current_user"] == "", "relayed AllStar audio was given a callsign")
    module.apply_m17_activity(state, "M: 2026-11-23 16:06:13.344 USRP received end of voice transmission, 19.5 seconds",
                              EPOCH + 21)
    module.apply_m17_journal(state, closing(EPOCH + 20), EPOCH + 21, "N4NHO", identity)
    check(state["role"] == "idle", "the card did not return to Idle")
    check(all(row.get("direction") == "outgoing" and not row.get("callsign") for row in state["tx_events"]),
          f"relay was recorded as an incoming talker: {state['tx_events']}")
    check(not state.get("last_source_user"), "relay set Last Talker")

    # A real M17 station keying the reflector is still shown, including one
    # using the bridge's base callsign from a different client (own radio).
    for client, expected in (("W1AW D", "W1AW"), ("KN4EWT D", "KN4EWT")):
        real = module.initial_activity_state()
        module.apply_m17_journal(real, opening(client, client), EPOCH + 1, "N4NHO", identity)
        check(real["role"] == "source" and real["current_user"] == expected,
              f"genuine M17 station {client} was hidden: {real['role']} {real['current_user']!r}")

    # End to end through build_status: the bridge's stream never reaches the card.
    module.M17_BRIDGE_INI_PATH = ini
    config = {"node": "674982", "callsign": "N4NHO", "bridges": [{**BRIDGE, "cardType": "standard"}]}
    status = module.standard_live_payload(config, {}, [], EPOCH + 1, cache_dir=Path(tmp),
                                 m17_journal_lines=["m17\t" + opening("KN4EWT N", "KN4EWT N")])
    card = status["bridges"]["m17"]
    check(card["state"] != "TX ACTIVE" and card["current_user"] == "",
          f"card shows the bridge as a talker: {card['state']} {card['current_user']!r}")
print("M17 relay loopback self-test: ok")
