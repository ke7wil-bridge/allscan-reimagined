#!/usr/bin/env python3
"""Focused regressions for evidence-based Standard Bridge status."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile

import asr_bridge_status as status


SCRIPTS = Path(__file__).resolve().parent
PAYLOAD_ROOT = SCRIPTS.parent


def installer_path():
    for candidate in (PAYLOAD_ROOT / "install.sh", PAYLOAD_ROOT.parent / "install.sh"):
        if candidate.is_file():
            return candidate
    return None


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dmr_source(at: str, caller: str = "N0CALL", slot: int = 2) -> str:
    return f"M: 2026-08-21 {at}.000 DMR Slot {slot}, received network voice header from {caller} to TG 12345"


def dmr_end(at: str, slot: int = 2) -> str:
    return f"M: 2026-08-21 {at}.000 DMR Slot {slot}, received network end of voice transmission, 5.0 seconds"


def dmr_watchdog(at: str, slot: int = 2) -> str:
    return f"W: 2026-08-21 {at}.000 DMR Slot {slot}, network watchdog has expired, 1.5 seconds"


def dmr_tx_on(at: str) -> str:
    return f"M: 2026-08-21 {at}.000 DMR, TX state = ON"


def dmr_tx_off(at: str, details: str = "DMR frame count was 118 frames") -> str:
    suffix = f", {details}" if details else ""
    return f"M: 2026-08-21 {at}.000 DMR, TX state = OFF{suffix}"


def ysf_source(at: str, caller: str = "W1YSF") -> str:
    return f"M: 2026-08-21 {at}.000 YSF, received network data from {caller}    to ALL        at {caller}"


def ysf_watchdog(at: str) -> str:
    return f"W: 2026-08-21 {at}.000 YSF, network watchdog has expired, 1.5 seconds"


def ysf_tx_on(at: str) -> str:
    return f"M: 2026-08-21 {at}.000 YSF, TX state = ON"


def ysf_tx_off(at: str, details: str = "YSF frame count was 42 frames") -> str:
    suffix = f", {details}" if details else ""
    return f"M: 2026-08-21 {at}.000 YSF, TX state = OFF{suffix}"


def test_shared_state_machine() -> None:
    state = status.initial_activity_state()
    status.apply_activity_line(state, dmr_source("12:00:00"), "dmr", 100)
    assert state["role"] == "source" and state["source_slot"] == 2

    # Missing EOT never becomes an invented timeout, including long 60-120s TX.
    status.reconcile_keyed_source(state, None, 160)
    assert state["role"] == "source"
    status.reconcile_keyed_source(state, True, 220)
    assert state["role"] == "source"

    # The payload-level safety cap retains normal long transmissions but
    # prevents a lost EOT from pinning a bridge active indefinitely.
    stale_config = {"node": "123456", "bridges": [
        {"id": "dmr", "mode": "dmr", "cardType": "standard", "node": "1111"},
    ]}
    stale_state = dict(state)
    stale_state["active_start_epoch"] = 300
    stale_payload = status.standard_live_payload(
        stale_config, {"dmr": stale_state}, [],
        300 + status.ACTIVE_STATE_MAX_AGE_SECONDS + 1, Path("/nonexistent"),
    )
    assert stale_payload["bridges"]["dmr"]["role"] == "idle"

    # Explicit keyed NO is honored only after the sampling grace.
    state["source_observed_at"] = 300
    status.reconcile_keyed_source(state, False, 302)
    assert state["role"] == "source"
    status.reconcile_keyed_source(state, False, 303)
    assert state["role"] == "idle" and state["current_user"] == ""

    # An anchored watchdog is real EOT; incidental embedded text is not.
    assert status.watchdog_event(dmr_watchdog("12:00:05"), "dmr", 0) is not None
    assert status.watchdog_event("prefix " + dmr_watchdog("12:00:05"), "dmr", 0) is None
    state = status.initial_activity_state()
    status.apply_activity_line(state, dmr_source("12:01:00", slot=1), "dmr", 400)
    status.apply_activity_line(state, dmr_watchdog("12:01:05", slot=2), "dmr", 405)
    assert state["role"] == "source", "another DMR slot cleared the active slot"
    status.apply_activity_line(state, dmr_watchdog("12:01:06", slot=1), "dmr", 406)
    assert state["role"] == "idle"

    # A delayed old EOT/watchdog cannot end a newer source.
    state = status.initial_activity_state()
    status.apply_activity_line(state, dmr_source("12:10:00", "NEW", 2), "dmr", 500)
    status.apply_activity_line(state, dmr_end("12:09:59", 2), "dmr", 501)
    status.apply_activity_line(state, dmr_watchdog("12:09:58", 2), "dmr", 502)
    assert state["role"] == "source" and state["current_user"] == "NEW"

    ysf = status.initial_activity_state()
    status.apply_activity_line(ysf, ysf_source("13:00:00"), "ysf", 600)
    assert ysf["role"] == "source" and ysf["current_user"] == "W1YSF"
    status.apply_activity_line(ysf, dmr_watchdog("13:00:01"), "ysf", 601)
    assert ysf["role"] == "source", "DMR evidence changed YSF state"
    status.apply_activity_line(ysf, ysf_watchdog("13:00:02"), "ysf", 602)
    assert ysf["role"] == "idle" and ysf["current_user"] == ""

    # A local YSF service identity marks relay traffic, not a new network
    # talker; its EOT must not reuse a previous caller and create phantom history.
    ysf = status.initial_activity_state()
    status.apply_activity_line(ysf, ysf_source("13:01:00", "REMOTE"), "ysf", 603)
    status.apply_activity_line(ysf, ysf_watchdog("13:01:02"), "ysf", 605)
    status.apply_activity_line(
        ysf, ysf_source("13:01:03", "N0LOCAL"), "ysf", 606,
        frozenset({"N0LOCAL"}),
    )
    status.apply_activity_line(
        ysf, ysf_watchdog("13:01:04"), "ysf", 607,
        frozenset({"N0LOCAL"}),
    )
    assert [row["callsign"] for row in ysf["tx_events"]] == ["REMOTE"]


def test_relay_tx_off_evidence() -> None:
    # Real MMDVM TX OFF lines append frame-count details. They must still end
    # Relay, including when the collector replays a completed pair at startup.
    config = {
        "node": "123456",
        "bridges": [
            {"id": "dmr", "mode": "dmr", "cardType": "standard", "node": "1111"},
            {"id": "ysf", "mode": "ysf", "cardType": "standard", "node": "2222"},
        ],
    }
    lines = [
        dmr_tx_on("19:31:00"),
        dmr_tx_off("19:31:07"),
        ysf_tx_on("19:32:00"),
        ysf_tx_off("19:32:08"),
    ]
    with tempfile.TemporaryDirectory(prefix="asr-relay-replay-") as raw:
        root = Path(raw)
        root.chmod(0o755)
        log = root / "MMDVM_Bridge-2026-08-21.log"
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.chmod(0o644)
        replayed = status.MmdvmFollower(root).read_lines()
        payload = status.standard_live_payload(config, {}, replayed, 1200, root)
    assert payload["bridges"]["dmr"]["role"] == "idle"
    assert payload["bridges"]["dmr"]["active"] is False
    assert payload["bridges"]["dmr"]["active_start_epoch"] == 0
    assert payload["bridges"]["ysf"]["role"] == "idle"
    assert payload["bridges"]["ysf"]["active"] is False
    assert payload["bridges"]["ysf"]["active_start_epoch"] == 0

    # Bare/singular/trailing-space forms remain valid, while incidental or
    # arbitrary suffixes and OFFLINE stay rejected.
    state = status.initial_activity_state()
    status.apply_activity_line(state, dmr_tx_on("19:33:00"), "dmr", 1201)
    status.apply_activity_line(state, dmr_tx_off("19:33:01", ""), "dmr", 1202)
    assert state["role"] == "idle"
    status.apply_activity_line(state, "prefix " + dmr_tx_on("19:33:02"), "dmr", 1203)
    assert state["role"] == "idle"
    status.apply_activity_line(state, dmr_tx_on("19:33:03"), "dmr", 1204)
    status.apply_activity_line(
        state, dmr_tx_off("19:33:04", "DMR frame count was 1 frame") + "  ", "dmr", 1205
    )
    assert state["role"] == "idle"
    for bad in (
        "prefix " + dmr_tx_off("19:33:05"),
        dmr_tx_off("19:33:05", "unexpected detail"),
        "M: 2026-08-21 19:33:05.000 DMR, TX state = OFFLINE",
    ):
        status.apply_activity_line(state, dmr_tx_on("19:33:04"), "dmr", 1206)
        status.apply_activity_line(state, bad, "dmr", 1207)
        assert state["role"] == "relay"

    # Mode isolation and TX OFF must not clear a received network source.
    status.apply_activity_line(state, ysf_tx_off("19:33:06"), "dmr", 1208)
    assert state["role"] == "relay"
    source = status.initial_activity_state()
    status.apply_activity_line(source, dmr_source("19:33:07"), "dmr", 1209)
    status.apply_activity_line(source, dmr_tx_off("19:33:08"), "dmr", 1210)
    assert source["role"] == "source"

    # An older delayed OFF cannot clear a newer Relay event.
    status.apply_activity_line(state, dmr_tx_on("19:34:00"), "dmr", 1211)
    status.apply_activity_line(state, dmr_tx_off("19:33:59"), "dmr", 1212)
    assert state["role"] == "relay"


def test_astapi_tri_state() -> None:
    with tempfile.TemporaryDirectory(prefix="asr-astapi-") as raw:
        cache_dir = Path(raw)
        path = cache_dir / "astapi-123456.json"
        path.write_text(json.dumps({
            "updated": 1000,
            "current": {"123456": {"remote_nodes": [
                {"node": "1111", "keyed": "yes"},
                {"node": "2222", "keyed": "no", "link": "Established"},
                {"node": "3333", "keyed": "N/A"},
            ], "current_talker": {
                "node": "511681", "info": "KR9RK", "started_epoch": 995,
            }}},
        }), encoding="utf-8")
        path.chmod(0o644)
        values = status.astapi_key_states("123456", {"1111", "2222", "3333", "4444"}, 1000, cache_dir)
        assert values == {"1111": True, "2222": False, "3333": None, "4444": None}
        evidence = status.astapi_bridge_evidence("123456", {"1111", "2222"}, 1000, cache_dir)
        assert evidence["connected"] == {"1111": False, "2222": True}
        assert evidence["caller"] == "KR9RK" and evidence["caller_node"] == "511681"
        path.chmod(0o664)
        assert all(value is None for value in status.astapi_key_states("123456", {"1111", "2222"}, 1000, cache_dir).values())
        path.chmod(0o644)
        assert all(value is None for value in status.astapi_key_states("123456", {"1111"}, 1010, cache_dir).values())


def test_november_8_standard_activity_pipeline() -> None:
    """Use the actual Netoholics P25 log shapes from the November 8 live test."""
    config = {
        "node": "674982",
        "bridges": [
            {"id": "p25", "mode": "p25", "cardType": "standard", "node": "1003"},
            {"id": "m17", "mode": "m17", "cardType": "standard", "node": "1004"},
        ],
    }
    incoming = [
        "p25\tM: 2026-11-08 22:38:46.875 P25, received network transmission from KE7WIL to TG 6749\n",
        "p25\tM: 2026-11-08 22:39:01.646 P25, network end of transmission, 15.1 seconds, 0% packet loss\n",
    ]
    outgoing_start = [
        "p25\tM: 2026-11-08 22:39:19.029 P25, TX state = ON\n",
    ]
    outgoing_end = [
        "p25\tM: 2026-11-08 22:39:31.674 P25, TX state = OFF\n",
    ]
    incoming_now = 1794177541
    outgoing_now = 1794177559
    with tempfile.TemporaryDirectory(prefix="asr-november-8-") as raw:
        root = Path(raw)
        astapi = root / "astapi-674982.json"
        astapi.write_text(json.dumps({
            "updated": incoming_now,
            "current": {"674982": {"remote_nodes": [
                {"node": "1003", "keyed": "no", "link": "Established"},
                {"node": "1004", "keyed": "no", "link": "Established"},
            ]}},
        }), encoding="utf-8")
        astapi.chmod(0o644)
        states: dict[str, dict] = {}
        first = status.standard_live_payload(
            config, states, [], incoming_now, root, external_lines=incoming,
        )
        assert first["bridges"]["p25"]["role"] == "idle"
        assert first["bridges"]["p25"]["last_source_user"] == "KE7WIL"
        assert first["bridges"]["p25"]["tx_events"][0]["duration_seconds"] == 15.1
        assert first["bridges"]["p25"]["tx_events"][0]["direction"] == "incoming"
        assert first["bridges"]["p25"]["tx_events"][0]["source_protocol"] == "P25"
        assert first["bridges"]["p25"]["online"] is True

        astapi.write_text(json.dumps({
            "updated": outgoing_now,
            "current": {"674982": {
                "remote_nodes": [
                    {"node": "1003", "keyed": "no", "link": "Established"},
                    {"node": "1004", "keyed": "no", "link": "Established"},
                ],
                "current_talker": {
                    "node": "674980", "info": "KN4EWT", "started_epoch": outgoing_now,
                },
            }},
        }), encoding="utf-8")
        second = status.standard_live_payload(
            config, states, [], outgoing_now, root, external_lines=outgoing_start,
        )
        assert second["bridges"]["p25"]["role"] == "relay"
        third = status.standard_live_payload(
            config, states, [], outgoing_now + 12, root, external_lines=outgoing_end,
        )
        p25 = third["bridges"]["p25"]
        assert p25["role"] == "idle" and p25["last_source_user"] == "KE7WIL"
        assert [row["callsign"] for row in p25["tx_events"]] == ["", "KE7WIL"]
        assert [row["identity_available"] for row in p25["tx_events"]] == [False, True]
        assert [row["direction"] for row in p25["tx_events"]] == ["outgoing", "incoming"]

        output = root / "bridge-live.json"
        status.atomic_json(output, third)
        restored = status.restore_standard_states(output, outgoing_now + 13)
        replayed = status.standard_live_payload(
            config, restored, [], outgoing_now + 13, root,
            external_lines=incoming + outgoing_start + outgoing_end,
        )
        assert len(replayed["bridges"]["p25"]["tx_events"]) == 2

        m17 = status.initial_activity_state()
        status.apply_m17_activity(
            m17, "M: 2026-11-08 23:05:03.487 USRP voice received as first frame", outgoing_now,
        )
        status.apply_m17_activity(
            m17, "M: 2026-11-08 23:05:05.090 USRP received end of voice transmission, 1.6 seconds", outgoing_now,
        )
        assert m17["tx_events"][0]["callsign"] == ""
        assert m17["tx_events"][0]["identity_available"] is False
        assert m17["tx_events"][0]["direction"] == "outgoing"


def test_simultaneous_relay_identity_and_connection_recovery() -> None:
    now = 1794179200
    config = {"node": "674982", "bridges": [
        {"id": "dmr", "mode": "dmr", "cardType": "standard", "node": "1001"},
        {"id": "ysf", "mode": "ysf", "cardType": "standard", "node": "1002"},
    ]}
    lines = [
        "M: 2026-11-08 23:06:40.000 DMR, TX state = ON",
        "M: 2026-11-08 23:06:40.000 YSF, TX state = ON",
        "M: 2026-11-08 23:06:41.000 DMR, TX state = OFF, DMR frame count was 1 frame",
        "M: 2026-11-08 23:06:41.000 YSF, TX state = OFF",
    ]
    with tempfile.TemporaryDirectory(prefix="asr-simultaneous-") as raw:
        root = Path(raw)
        cache = root / "astapi-674982.json"

        def write_cache(updated: int, nodes: list[str], caller: str = "KR9RK") -> None:
            cache.write_text(json.dumps({
                "updated": updated,
                "current": {"674982": {
                    "remote_nodes": [
                        {"node": node, "keyed": "no", "link": "Established"}
                        for node in nodes
                    ],
                    "current_talker": {
                        "node": "511681", "info": caller, "started_epoch": updated,
                    },
                }},
            }), encoding="utf-8")
            cache.chmod(0o644)

        write_cache(now, ["1001", "1002"])
        states: dict[str, dict] = {}
        payload = status.standard_live_payload(config, states, lines, now + 1, root)
        for bridge_id in ("dmr", "ysf"):
            entry = payload["bridges"][bridge_id]
            assert entry["role"] == "idle" and entry["online"] is True
            assert entry["last_source_user"] == ""
            assert entry["tx_events"][0]["callsign"] == ""
            assert entry["tx_events"][0]["identity_available"] is False
            assert entry["tx_events"][0]["direction"] == "outgoing"
            assert entry["tx_events"][0]["duration_seconds"] == 1.0

        write_cache(now + 2, ["1002"])
        lost = status.standard_live_payload(config, states, [], now + 2, root)
        assert lost["bridges"]["dmr"]["online"] is False
        assert lost["bridges"]["ysf"]["online"] is True
        write_cache(now + 3, ["1001", "1002"])
        recovered = status.standard_live_payload(config, states, [], now + 3, root)
        assert recovered["bridges"]["dmr"]["online"] is True

        stale = status.standard_live_payload(config, states, [], now + 20, root)
        assert stale["bridges"]["dmr"]["online"] is None
        assert stale["bridges"]["ysf"]["online"] is None

        # A bridge node is transport metadata and must never become a caller.
        write_cache(now + 4, ["1001", "1002"], "1001")
        evidence = status.astapi_bridge_evidence("674982", {"1001", "1002"}, now + 4, root)
        assert evidence["caller"] == ""


def test_direct_service_discovery() -> None:
    from unittest.mock import patch
    from types import SimpleNamespace
    config = {"bridges": [{
        "id": "dmr_direct", "mode": "dmr", "backendMode": "managed",
        "mmdvmService": "asr-dmr-mmdvm.service",
        "analogBridgeService": "asr-dmr-analog.service",
        "adminService": "asr-dmr-admin.service",
    }]}
    def show(args, **_kwargs):
        assert args[0:2] == ["systemctl", "show"]
        return SimpleNamespace(stdout="ActiveState=active\nNRestarts=0\nActiveEnterTimestampMonotonic=1000000\n", returncode=0)
    with patch.object(status.subprocess, "run", side_effect=show) as runner:
        units, issues = status.bridge_service_health(config, "dmr_direct")
    assert units == ["asr-dmr-mmdvm.service", "asr-dmr-analog.service", "asr-dmr-admin.service"]
    assert issues == [] and runner.call_count == 3

    # Historical service start-time skew is normal after maintenance; only a
    # fresh partial restart should take an otherwise active chain offline.
    def skewed_show(args, **_kwargs):
        epoch = 940_000_000 if args[2] == "asr-dmr-admin.service" else 900_000_000
        return SimpleNamespace(stdout=f"ActiveState=active\nNRestarts=0\nActiveEnterTimestampMonotonic={epoch}\n", returncode=0)
    with patch.object(status.time, "monotonic", return_value=1000), patch.object(status.subprocess, "run", side_effect=skewed_show):
        _, issues = status.bridge_service_health(config, "dmr_direct")
    assert len(issues) == 1 and "restarted without its companion" in issues[0]
    with patch.object(status.time, "monotonic", return_value=2000), patch.object(status.subprocess, "run", side_effect=skewed_show):
        _, issues = status.bridge_service_health(config, "dmr_direct")
    assert issues == []
    with patch.object(status.subprocess, "run", side_effect=__import__("subprocess").TimeoutExpired("systemctl", 3)):
        _, issues = status.bridge_service_health(config, "dmr_direct")
        assert len(issues) == 3 and all("could not be checked" in issue for issue in issues)
    with patch.object(status.subprocess, "run", return_value=SimpleNamespace(stdout="ActiveState=active\nNRestarts=bad\n", returncode=0)):
        _, issues = status.bridge_service_health(config, "dmr_direct")
        assert len(issues) == 3 and all("invalid service status" in issue for issue in issues)
    # An unmanaged bridge cannot inject arbitrary service names.
    with patch.object(status.subprocess, "run") as runner:
        assert status.bridge_service_health({"bridges": [{"id": "dmr_direct", "mmdvmService": "x.service"}]}, "dmr_direct") == ([], [])
        runner.assert_not_called()
    # Corrupt configuration must not crash status polling or run arbitrary units.
    with patch.object(status.subprocess, "run") as runner:
        assert status.bridge_service_health({"bridges": None}, "dmr_direct") == ([], [])
        assert status.bridge_service_health({"bridges": {"bad": "shape"}}, "dmr_direct") == ([], [])
        runner.assert_not_called()
    with patch.object(status.subprocess, "run", return_value=SimpleNamespace(stdout="", returncode=1)):
        _, issues = status.bridge_service_health(config, "dmr_direct")
        assert len(issues) == 3 and all("not active" in issue for issue in issues)

    ysf_units = [
        "netoholics-native-ysf-gateway.service",
        "netoholics-native-ysf-reflector.service",
        "netoholics-native-ysf-mmdvm.service",
        "netoholics-native-ysf-analog.service",
        "netoholics-native-ysf-vocoder.service",
    ]
    ysf_config = {"bridgeHealthServices": {"ysf_net": ysf_units}, "bridges": [{
        "id": "ysf_net", "mode": "ysf", "cardType": "standard",
        "backendMode": "managed",
        "ysfGatewayService": ysf_units[0],
        "mmdvmService": ysf_units[2],
        "analogBridgeService": ysf_units[3],
        "emulatorService": ysf_units[4],
    }]}
    with patch.object(status.subprocess, "run", side_effect=show):
        units, issues = status.bridge_service_health(ysf_config, "ysf_net")
    assert units == ysf_units and issues == []
    healthy = status.public_entry(ysf_config["bridges"][0], status.initial_activity_state(), units, issues)
    assert healthy["state"] == "Idle" and healthy["health_severity"] is None

    def one_ysf_offline(args, **_kwargs):
        active = args[2] != ysf_units[0]
        return SimpleNamespace(
            stdout=("ActiveState=active\nNRestarts=0\nActiveEnterTimestampMonotonic=1000000\n" if active else "ActiveState=inactive\n"),
            returncode=0,
        )
    with patch.object(status.subprocess, "run", side_effect=one_ysf_offline):
        units, issues = status.bridge_service_health(ysf_config, "ysf_net")
    offline = status.public_entry(ysf_config["bridges"][0], status.initial_activity_state(), units, issues)
    assert offline["health_severity"] == "offline"
    assert "gateway" in offline["warning"]


def test_standard_payload_and_ambiguity() -> None:
    for invalid in (None, {}, "not-a-list", 7):
        assert status.configured_net_log_stems({"bridges": invalid}) == frozenset()
        assert status.all_configured_standard_bridges({"bridges": invalid}) == []
        assert status.configured_standard_bridges({"bridges": invalid}) == {}
        assert status.standard_live_payload({"node": "123456", "bridges": invalid}, {}, [], 100)["bridges"] == {}
    config = {
        "node": "123456",
        "callsign": "N0LOCAL",
        "bridges": [
            {"id": "dmr", "mode": "dmr", "cardType": "standard", "node": "1111", "title": "DMR Bridge"},
            {"id": "ysf", "mode": "ysf", "cardType": "standard", "node": "2222", "title": "YSF Bridge"},
        ],
    }
    with tempfile.TemporaryDirectory(prefix="asr-standard-") as raw:
        cache_dir = Path(raw)
        states: dict[str, dict] = {}
        payload = status.standard_live_payload(
            config,
            states,
            [dmr_source("14:00:00", "DMRUSER", 1), ysf_source("14:00:01", "YSFUSER")],
            700,
            cache_dir,
        )
        assert payload["bridges"]["dmr"]["role"] == "source"
        assert payload["bridges"]["dmr"]["current_user"] == "DMRUSER"
        assert payload["bridges"]["ysf"]["role"] == "source"
        assert payload["bridges"]["ysf"]["current_user"] == "YSFUSER"

        payload = status.standard_live_payload(
            config,
            states,
            [dmr_watchdog("14:00:05", 1), ysf_watchdog("14:00:06")],
            706,
            cache_dir,
        )
        for mode in ("dmr", "ysf"):
            assert payload["bridges"][mode]["role"] == "idle"
            assert payload["bridges"][mode]["current_user"] == ""
            assert payload["bridges"][mode]["caller"] == ""
            assert payload["bridges"][mode]["last_user"] != "-"
            assert len(payload["bridges"][mode]["tx_events"]) == 1
            assert len(payload["bridges"][mode]["recent_users"]) == 1

        expires_at = max(states[mode]["last_source_epoch"] for mode in ("dmr", "ysf")) + status.BRIDGE_RECENT_MAX_AGE_SECONDS + 1
        payload = status.standard_live_payload(config, states, [], expires_at, cache_dir)
        for mode in ("dmr", "ysf"):
            assert payload["bridges"][mode]["last_user"] == "-"
            assert payload["bridges"][mode]["last_source_epoch"] == 0
            assert payload["bridges"][mode]["tx_events"] == []
            assert payload["bridges"][mode]["recent_users"] == []

        ambiguous = dict(config)
        ambiguous["bridges"] = config["bridges"] + [
            {"id": "dmr_two", "mode": "dmr", "cardType": "standard", "node": "3333"},
        ]
        assert "dmr" not in status.configured_standard_bridges(ambiguous)
        assert "ysf" in status.configured_standard_bridges(ambiguous)
        # Two DMR cards remain visible, but generic DMR logs must not be
        # attributed to either one (including stale pre-ambiguity activity).
        ambiguous_payload = status.standard_live_payload(
            ambiguous, states, [dmr_source("14:00:08", "WRONGCARD", 1)],
            expires_at + 1, cache_dir,
        )
        assert set(ambiguous_payload["bridges"]) == {"dmr", "dmr_two", "ysf"}
        for bridge_id in ("dmr", "dmr_two"):
            entry = ambiguous_payload["bridges"][bridge_id]
            assert entry["role"] == "idle" and entry["current_user"] == ""
            assert entry["last_user"] == "-" and entry["tx_events"] == []


def test_log_follower_safety() -> None:
    with tempfile.TemporaryDirectory(prefix="asr-mmdvm-logs-") as raw:
        log_dir = Path(raw)
        log_dir.chmod(0o755)
        safe = log_dir / "MMDVM_Bridge-2026-08-21.log"
        safe.write_text(dmr_source("14:30:00") + "\n", encoding="utf-8")
        safe.chmod(0o644)
        unsafe = log_dir / "MMDVM_Bridge_Unsafe-2026-08-21.log"
        unsafe.write_text(ysf_source("14:30:01") + "\n", encoding="utf-8")
        unsafe.chmod(0o666)
        linked = log_dir / "MMDVM_Bridge_Link-2026-08-21.log"
        linked.symlink_to(safe)
        net = log_dir / "MMDVM_Bridge_TestNet-2026-08-21.log"
        net.write_text(dmr_source("14:30:02", "NET") + "\n", encoding="utf-8")
        net.chmod(0o644)
        manual = log_dir / "MMDVM_Bridge_Manual-2026-08-21.log"
        manual.write_text(dmr_source("14:30:03", "MANUAL") + "\n", encoding="utf-8")
        manual.chmod(0o644)
        lines = status.MmdvmFollower(log_dir).read_lines(frozenset({"MMDVM_Bridge_TestNet"}))
        assert lines == [dmr_source("14:30:00") + "\n"]


def test_standard_net_log_isolation() -> None:
    with tempfile.TemporaryDirectory(prefix="asr-ysf-net-ini-") as raw_ini:
        ini = Path(raw_ini) / "MMDVM_Bridge.ini"
        ini.write_text(
            "[Log]\nFilePath=/var/log/mmdvm\nFileRoot=MMDVM_Bridge_CustomYsfNet\n",
            encoding="utf-8",
        )
        ini.chmod(0o644)
        assert status.mmdvm_ini_log_root(ini) == "MMDVM_Bridge_CustomYsfNet"
        ini.chmod(0o666)
        assert status.mmdvm_ini_log_root(ini) == ""

    config = {
        "node": "123456",
        "bridges": [
            {"id": "dmr", "mode": "dmr", "cardType": "standard", "node": "1111"},
            {"id": "ysf", "mode": "ysf", "cardType": "standard", "node": "2222"},
            {
                "id": "dmr_net", "mode": "dmr", "cardType": "dmr_net", "node": "3333",
                "dvswitchScript": "/opt/MMDVM_Bridge_DmrNet/dvswitch.sh",
            },
            {
                "id": "ysf_net", "mode": "ysf", "cardType": "ysf_net", "node": "4444",
                "mmdvmConfig": "/opt/MMDVM_Bridge_YsfNet/MMDVM_Bridge.ini",
            },
        ],
    }
    assert status.configured_net_log_stems(config) == frozenset({
        "MMDVM_Bridge_DmrNet", "MMDVM_Bridge_YsfNet",
    })
    malformed = {"bridges": [{
        "cardType": "dmr_net",
        "dvswitchScript": "/tmp/MMDVM_Bridge_DmrNet/dvswitch.sh",
    }]}
    assert not status.configured_net_log_stems(malformed)

    with tempfile.TemporaryDirectory(prefix="asr-standard-net-logs-") as raw:
        root = Path(raw)
        root.chmod(0o755)
        standard_log = root / "MMDVM_Bridge-2026-08-21.log"
        standard_log.write_text(
            dmr_source("14:40:00", "STANDARD_DMR", 2) + "\n"
            + ysf_source("14:40:01", "STANDARDYSF") + "\n",
            encoding="utf-8",
        )
        dmr_net_log = root / "MMDVM_Bridge_DmrNet-2026-08-21.log"
        dmr_net_log.write_text(dmr_source("14:40:02", "NET_DMR", 2) + "\n", encoding="utf-8")
        ysf_net_log = root / "MMDVM_Bridge_YsfNet-2026-08-21.log"
        ysf_net_log.write_text(ysf_source("14:40:03", "NETYSF") + "\n", encoding="utf-8")
        for path in (standard_log, dmr_net_log, ysf_net_log):
            path.chmod(0o644)
        lines = status.MmdvmFollower(root).read_lines(status.configured_net_log_stems(config))
        states: dict[str, dict] = {}
        payload = status.standard_live_payload(config, states, lines, 950, root)
        assert payload["bridges"]["dmr"]["current_user"] == "STANDARD_DMR"
        assert payload["bridges"]["ysf"]["current_user"] == "STANDARDYSF"

    conventional_conflict = {
        "node": "123456",
        "bridges": [
            {"id": "ysf", "mode": "ysf", "cardType": "standard", "node": "2222"},
            {
                "id": "ysf_net", "mode": "ysf", "cardType": "ysf_net", "node": "4444",
                "mmdvmConfig": "/opt/MMDVM_Bridge_YSF/MMDVM_Bridge.ini",
            },
        ],
    }
    with tempfile.TemporaryDirectory(prefix="asr-standard-net-conflict-") as raw:
        root = Path(raw)
        root.chmod(0o755)
        conflict_log = root / "MMDVM_Bridge_YSF-2026-08-21.log"
        conflict_log.write_text(ysf_source("14:50:00", "NETONLY") + "\n", encoding="utf-8")
        conflict_log.chmod(0o644)
        lines = status.MmdvmFollower(root).read_lines(
            status.configured_net_log_stems(conventional_conflict)
        )
        assert lines == []
        payload = status.standard_live_payload(conventional_conflict, {}, lines, 960, root)
        assert payload["bridges"]["ysf"]["role"] == "idle"
        assert payload["bridges"]["ysf"]["active"] is False


def test_standard_once_writer() -> None:
    with tempfile.TemporaryDirectory(prefix="asr-standard-once-") as raw:
        root = Path(raw)
        config_path = root / "config.json"
        config_path.write_text(json.dumps({
            "node": "123456",
            "bridges": [{
                "id": "dmr", "mode": "dmr", "cardType": "standard",
                "node": "1111", "title": "DMR Bridge",
            }],
        }), encoding="utf-8")
        config_path.chmod(0o664)
        assert status._safe_json(config_path, forbidden_write_mask=0o002) is not None
        log_dir = root / "logs"
        log_dir.mkdir(mode=0o755)
        log = log_dir / "MMDVM_Bridge-2026-08-21.log"
        current_stamp = status.time.strftime(
            "%Y-%m-%d %H:%M:%S", status.time.gmtime(status.time.time())
        )
        log.write_text(
            f"M: {current_stamp}.000 DMR Slot 2, received network voice header "
            "from ONCE to TG 91\n",
            encoding="utf-8",
        )
        log.chmod(0o644)
        output = root / "run/bridge-live.json"
        status.watch_standard_status(config_path, output, log_dir, once=True)
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["bridges"]["dmr"]["current_user"] == "ONCE"
        assert (output.stat().st_mode & 0o777) == 0o644
        config_path.chmod(0o666)
        assert status._safe_json(config_path, forbidden_write_mask=0o002) is None


def test_net_helpers() -> None:
    dmr = load_script("asr_dmr_net", "asr-bridge-control.py")
    state = dmr.initial_live_state()
    dmr.apply_mmdvm_activity_line(state, dmr_source("15:00:00", "NETDMR", 2), 800)
    dmr.apply_mmdvm_activity_line(state, dmr_watchdog("15:00:04", 2), 804)
    assert state["role"] == "idle" and state["last_user"] == "NETDMR"
    state = dmr.initial_live_state()
    dmr.apply_mmdvm_activity_line(state, dmr_source("15:10:00", "LONGDMR", 1), 900)
    dmr.reconcile_keyed_source(state, True, 1020)
    assert state["role"] == "source"

    ysf = load_script("asr_ysf_net", "asr-ysf-bridge-control.py")
    state = ysf.initial_activity_state()
    ysf.apply_activity_line(state, ysf_source("16:00:00", "NETYSF"), 1100)
    ysf.apply_activity_line(state, ysf_watchdog("16:00:04"), 1104)
    assert state["role"] == "idle" and state["last_user"] == "NETYSF"


def test_wiring_contracts() -> None:
    astapi = (PAYLOAD_ROOT / "compat/allscan-v1.02/astapi/server.php").read_text(encoding="utf-8")
    assert "@chmod($tmp, 0644);" in astapi
    reapply = (SCRIPTS / "asr-reapply.sh").read_text(encoding="utf-8")
    assert "allscan-reimagined-standard-bridge-status.service" in reapply
    assert "systemctl restart allscan-reimagined-standard-bridge-status.service" in reapply
    assert "CapabilityBoundingSet=" in reapply
    installer_file = installer_path()
    if installer_file is not None:
        installer = installer_file.read_text(encoding="utf-8")
        assert installer.count("allscan-reimagined-standard-bridge-status.service") >= 4
        assert "/usr/local/sbin/asr_bridge_status.py" in installer
        standard_check = installer.index(
            'validate_command "configured Standard Bridge status service is active"'
        )
        net_check = installer.index(
            'validate_command "configured DMR Net live service is active"'
        )
        assert standard_check < net_check
        standard_condition = installer.rfind(
            "if python3 - /etc/allscan-reimagined/config.json <<'PY'",
            0,
            standard_check,
        )
        assert standard_condition >= 0
        condition = installer[standard_condition:standard_check]
        assert 'item.get("cardType", "standard") == "standard"' in condition
        assert '.startswith(("dmr", "ysf", "p25", "nxdn", "m17"))' in condition


def main() -> None:
    test_shared_state_machine()
    test_relay_tx_off_evidence()
    test_astapi_tri_state()
    test_november_8_standard_activity_pipeline()
    test_simultaneous_relay_identity_and_connection_recovery()
    test_standard_payload_and_ambiguity()
    test_direct_service_discovery()
    test_log_follower_safety()
    test_standard_net_log_isolation()
    test_standard_once_writer()
    test_net_helpers()
    test_wiring_contracts()
    print("Standard Bridge stale/activity status self-test: ok")


if __name__ == "__main__":
    main()
