#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "asr-api.php").read_text(encoding="utf-8")
SERVER_API = (ROOT / "server/asr-api.php").read_text(encoding="utf-8")
APP = (ROOT / "src/App.tsx").read_text(encoding="utf-8")
URF_PATH = ROOT / "scripts/asr-urf-admin.py"


def check(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


spec = importlib.util.spec_from_file_location("asr_urf_admin", URF_PATH)
check(spec is not None and spec.loader is not None, "URF helper could not be imported")
urf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(urf)

check(API == SERVER_API, "API copies diverged")
check(urf.callsign_matches("KF0WSS", "KF0WSS B"), "YSF module suffix normalization regressed")
check(not urf.callsign_matches("KF0WSS", "KF0WSS-2"), "distinct YSF identity was over-normalized")
check(urf.callsign_matches("N0CALL*", "N0CALL-7"), "global prefix rule matching regressed")

xml = """<ROOT>
<NODE><Callsign>KF0WSS B</Callsign><Protocol>YSF</Protocol></NODE>
<NODE><Callsign>N0CALL</Callsign><Protocol>P25</Protocol></NODE>
</ROOT>"""
check(urf.roster_contains(xml, "KF0WSS", "YSF"), "YSF roster suffix match regressed")
check(not urf.roster_contains(xml, "KF0WSS", "P25"), "protocol-scoped roster match regressed")
check(urf.roster_contains(xml, "N0*", "*"), "global Ban roster match regressed")

with tempfile.TemporaryDirectory(prefix="asr-ysf-self-test-") as raw_tmp:
    tmp = Path(raw_tmp)
    urf.BLACKLIST = str(tmp / "urfd.blacklist")
    urf.TIMED_BANS = str(tmp / "asr-timed-bans.json")
    urf.AUDIT_LOG = str(tmp / "asr-admin-audit.jsonl")
    urf.DMR_ROSTER = str(tmp / "dmr-clients.json")
    urf.DMR_DIRECTORY_ROSTER = str(tmp / "connected-clients.json")
    urf.DMR_CONTROL = str(tmp / "dmr-admin.json")
    urf.DMR_RUNTIME_DIR = str(tmp)
    urf.CONFIG = str(tmp / "config.json")
    Path(urf.CONFIG).write_text(json.dumps({"bridges": [], "filteredStations": ["FEEDALIAS"]}), encoding="utf-8")
    Path(urf.DMR_ROSTER).write_text(json.dumps({"urf_dmr": [], "events": []}), encoding="utf-8")
    Path(urf.DMR_DIRECTORY_ROSTER).write_text(json.dumps({"urf_dmr": []}), encoding="utf-8")
    Path(urf.DMR_CONTROL).write_text(json.dumps({
        "banned_ids": [3224939],
        "banned_clients": {"3224939": "KE7WIL"},
        "kicked_ids": {},
    }), encoding="utf-8")

    urf.write_rules(["KE7WIL", "N0CALL*"])
    check(urf.read_rules() == ["KE7WIL", "N0CALL*"], "logical Global Ban List round-trip failed")
    urf.write_timed_bans({"KE7WIL": {"createdAt": 1000, "expiresAt": 1900}})
    timed = urf.read_timed_bans()
    check(timed["KE7WIL"]["expiresAt"] == 1900, "timed Ban persistence round-trip failed")
    payload = urf.ban_payload(urf.read_rules(), timed, 1300)
    check(next(row for row in payload if row["rule"] == "KE7WIL")["remainingSeconds"] == 600,
          "timed Ban countdown payload is incorrect")
    check(next(row for row in payload if row["rule"] == "N0CALL*")["expiresAt"] == 0,
          "Permanent Ban was not represented distinctly")
    transitions = urf.set_ban_expiration({}, "KE7WIL", "15m", 1000)
    check(transitions["KE7WIL"] == {"createdAt": 1000, "expiresAt": 1900},
          "15-minute Ban duration was calculated incorrectly")
    transitions = urf.set_ban_expiration(transitions, "KE7WIL", "permanent", 1100)
    check("KE7WIL" not in transitions, "timed-to-Permanent conversion retained stale timer metadata")
    transitions = urf.set_ban_expiration(transitions, "KE7WIL", "1h", 1200)
    check(transitions["KE7WIL"]["expiresAt"] == 4800,
          "Permanent-to-timed conversion did not create timer metadata")
    prefix_timed = urf.set_ban_expiration({}, "N0CALL*", "1w", 2000)
    check(prefix_timed["N0CALL*"]["expiresAt"] == 606800, "prefix timed Ban duration failed")
    try:
        urf.set_ban_expiration({}, "KE7WIL", "forever", 1000)
        raise AssertionError("invalid Ban duration was accepted")
    except ValueError:
        pass

    for protected_rule in ("RFCKRD0", "YSF-LIVE", "KF0WSS", "KF0*", "FEEDALIAS", "FEED*"):
        try:
            urf.assert_ban_allowed(protected_rule)
            raise AssertionError(f"protected identity rule was accepted: {protected_rule}")
        except ValueError:
            pass
    urf.assert_ban_allowed("KF0WSS-2")

    stable_timer = {"KE7WIL": {"createdAt": 1000, "expiresAt": 1900}}
    for corrupt in ("{", "[]", '{"KE7WIL":{"createdAt":1000}}', '{"bad rule":{"createdAt":1000,"expiresAt":1900}}'):
        Path(urf.TIMED_BANS).write_text(corrupt, encoding="utf-8")
        try:
            urf.read_timed_bans()
            raise AssertionError("corrupt timed-ban state was accepted")
        except RuntimeError as error:
            check(str(error) == "Timed-ban state is unreadable.", "corrupt timer failure was not explicit")
        check(urf.read_rules() == ["KE7WIL", "N0CALL*"], "corrupt timer state changed logical bans")
    urf.write_timed_bans(stable_timer)
    (tmp / ".asr-timed-bans.partial").write_text("{", encoding="utf-8")
    check(urf.read_timed_bans() == stable_timer, "orphaned partial timer write replaced valid state")

    urf.append_audit_event("ban", "admin name", rule="KE7WIL", duration="15m", expiresAt=1900)
    audit = urf.read_audit_events()
    check(audit[-1]["action"] == "ban" and audit[-1]["actor"] == "admin_name", "administrator audit record failed")

    now = int(urf.time.time())
    urf.write_rules(["KE7WIL", "N0CALL*", "AD7TG"])
    urf.write_timed_bans({
        "KE7WIL": {"createdAt": now - 1000, "expiresAt": now - 1},
        "AD7TG": {"createdAt": now, "expiresAt": now + 3600},
        "GHOST": {"createdAt": now, "expiresAt": now + 7200},
    })
    disconnects = []
    original_disconnect, original_sync = urf.request_urf_disconnect, urf.sync_dmr_ban
    urf.request_urf_disconnect = lambda rule, protocol, event, required: disconnects.append((rule, protocol, event)) or 0
    urf.sync_dmr_ban = lambda rule, state: None
    remaining, timed, expired = urf.expire_timed_bans(urf.read_rules(), urf.read_timed_bans())
    urf.request_urf_disconnect, urf.sync_dmr_ban = original_disconnect, original_sync
    check(expired == ["KE7WIL"] and remaining == ["AD7TG", "N0CALL*"],
          "one expired Ban incorrectly changed another active/Permanent Ban")
    check(set(timed) == {"AD7TG"}, "expired or orphaned timer metadata was retained")
    check(disconnects == [("KE7WIL", "*", "unban")], "expiry cleared the wrong live URFD enforcement")
    check(urf.read_rules() == ["AD7TG", "N0CALL*"], "expired Ban was not removed from saved rules")

    urf.sync_dmr_ban("KE7WIL", False)
    backend = json.loads(Path(urf.DMR_CONTROL).read_text(encoding="utf-8"))
    check(3224939 not in backend.get("banned_ids", []), "unban left a hidden DMR ID ban")
    check("3224939" not in backend.get("banned_clients", {}), "unban left hidden DMR identity metadata")

    clients = [
        {"dmrid": "3224939", "callsign": "KE7WIL"},
        {"dmrid": "3101001", "callsign": "N1AAA"},
        {"dmrid": "3101002", "callsign": "N2BBB"},
        {"dmrid": "3101003", "callsign": "N3CCC"},
    ]
    Path(urf.DMR_ROSTER).write_text(json.dumps({"urf_dmr": clients, "events": []}), encoding="utf-8")
    Path(urf.DMR_DIRECTORY_ROSTER).write_text(json.dumps({"urf_dmr": clients}), encoding="utf-8")
    Path(urf.DMR_CONTROL).write_text(json.dumps({
        "banned_ids": [], "banned_clients": {}, "kicked_ids": {"3224939": now},
    }), encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        urf.write_dmr_kick("KE7WIL", expected_id="3224939")
    backend = json.loads(Path(urf.DMR_CONTROL).read_text(encoding="utf-8"))
    check(int(backend.get("kicked_ids", {}).get("3224939", 0)) > 0,
          "DMR Kick did not publish active-stream suppression")
    roster = json.loads(Path(urf.DMR_ROSTER).read_text(encoding="utf-8"))
    remaining_ids = {row["dmrid"] for row in roster.get("urf_dmr", [])}
    check(remaining_ids == {"3101001", "3101002", "3101003"},
          "DMR Kick changed an unrelated connected client")

    before_control = Path(urf.DMR_CONTROL).read_text(encoding="utf-8")
    before_roster = Path(urf.DMR_ROSTER).read_text(encoding="utf-8")
    for callsign, dmrid in (("KE7WIL", "3101001"), ("GONE", "3999999")):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                urf.write_dmr_kick(callsign, expected_id=dmrid)
            raise AssertionError("stale or mismatched DMR client reference was accepted")
        except RuntimeError:
            pass
        check(Path(urf.DMR_CONTROL).read_text(encoding="utf-8") == before_control,
              "rejected DMR Kick changed administration state")
        check(Path(urf.DMR_ROSTER).read_text(encoding="utf-8") == before_roster,
              "rejected DMR Kick changed connected clients")

    ambiguous = clients[1:] + [{"dmrid": "3101999", "callsign": "N1AAA"}]
    Path(urf.DMR_ROSTER).write_text(json.dumps({"urf_dmr": ambiguous, "events": []}), encoding="utf-8")
    Path(urf.DMR_DIRECTORY_ROSTER).write_text(json.dumps({"urf_dmr": ambiguous}), encoding="utf-8")
    try:
        urf.dmr_client_id("N1AAA")
        raise AssertionError("ambiguous DMR callsign was accepted without an exact client ID")
    except RuntimeError as error:
        check("ambiguous" in str(error), "ambiguous DMR callsign failure was not explicit")
    check(urf.dmr_client_id("N1AAA", "3101001") == 3101001,
          "current callsign+DMR-ID pair did not target the selected client")

    Path(urf.DMR_ROSTER).write_text(json.dumps({"urf_dmr": clients[1:], "events": []}), encoding="utf-8")
    Path(urf.DMR_DIRECTORY_ROSTER).write_text(json.dumps({"urf_dmr": clients[1:]}), encoding="utf-8")
    Path(urf.DMR_CONTROL).write_text(json.dumps({
        "banned_ids": [3101003], "banned_clients": {"3101003": "N3CCC"}, "kicked_ids": {},
    }), encoding="utf-8")
    urf.sync_dmr_ban("N1AAA", True, "3101001")
    backend = json.loads(Path(urf.DMR_CONTROL).read_text(encoding="utf-8"))
    check(backend["banned_ids"] == [3101001, 3101003], "targeted DMR Ban did not preserve another ban")
    roster = json.loads(Path(urf.DMR_ROSTER).read_text(encoding="utf-8"))
    check({row["dmrid"] for row in roster["urf_dmr"]} == {"3101002", "3101003"},
          "targeted DMR Ban removed an unrelated client")
    urf.sync_dmr_ban("N1AAA", False)
    backend = json.loads(Path(urf.DMR_CONTROL).read_text(encoding="utf-8"))
    check(backend["banned_ids"] == [3101003] and backend["banned_clients"] == {"3101003": "N3CCC"},
          "DMR Unban changed an unrelated persistent ban")

    capabilities = urf.dmr_admin_capabilities()
    check(capabilities["available"] and capabilities["controlWritable"] and capabilities["rosterReadable"],
          "writable DMR runtime was not advertised as actionable")
    original_statvfs = urf.os.statvfs
    urf.os.statvfs = lambda _path: type("ReadOnlyFs", (), {"f_flag": getattr(urf.os, "ST_RDONLY", 1)})()
    try:
        capabilities = urf.dmr_admin_capabilities()
        check(not capabilities["available"] and not capabilities["controlWritable"] and not capabilities["capabilities"],
              "read-only DMR runtime advertised actions that cannot work")
    finally:
        urf.os.statvfs = original_statvfs

check("asr_urf_is_service_identity($callsign, $mode)" in API, "connected-client probe filter missing")
check("asr_urf_is_service_identity($normalizedCallsign" in API, "event probe filter missing")
check("foreach (array_reverse($modeEvents) as $event)" in API and "asr_urf_is_service_identity($normalizedCallsign" in API, "probe traffic can evict meaningful activity history")
check("request_urf_disconnect(rule, \"*\", \"ban\", False, args.actor)" in URF_PATH.read_text(encoding="utf-8"), "Ban does not request immediate URFD removal")
check("request_urf_disconnect(rule, \"*\", \"unban\", False, args.actor)" in URF_PATH.read_text(encoding="utf-8"), "Unban does not clear URFD session suppression")
check("BAN_DURATIONS" in URF_PATH.read_text(encoding="utf-8") and "expire_timed_bans" in URF_PATH.read_text(encoding="utf-8"), "timed Ban lifecycle is missing")
check("['15m', '1h', '1w', '30d', 'permanent']" in API, "API Ban-duration allowlist is missing")
check("URF_BAN_DURATIONS" in APP and "formatUrfBanRemaining" in APP, "timed Ban controls/countdown are missing")
check("window.setInterval(() => void refresh(), 15000)" in APP, "Manage Clients does not refresh automatic expiry")
check("<h3>Global Ban</h3>" in APP and "Applies everywhere ASR can enforce it." in APP, "global-only Ban dialog is missing")
check('function globalBanDurationSelect' in APP and '<option value="" disabled>Ban</option>' in APP, "compact Ban-duration dropdown is missing")
check("AllStar node only" not in APP and "AllStar callsign only" not in APP and "EchoLink callsign only" not in APP, "mode-specific Ban choices remain")
check("ASR Global Ban List" in APP, "global Ban action feedback missing")
check("protectedBanIdentities" in APP and "isProtectedBanConnection" in APP, "configured service-bridge Ban protection is missing")
check("Kick affects only the selected current bridge session" in APP, "Kick session scope warning missing")
check("Protected service and health-probe identities cannot be banned" in APP, "protected-identity explanation missing")
check("Timed-ban state is unreadable." in URF_PATH.read_text(encoding="utf-8") and "os.fsync" in URF_PATH.read_text(encoding="utf-8"), "timed-ban fail-safe durability is missing")
check("Recent bridge actions" in APP and "formatUrfBanExpiration" in APP, "audit history or exact expiration display missing")

print("YSF client-management self-test: ok")
