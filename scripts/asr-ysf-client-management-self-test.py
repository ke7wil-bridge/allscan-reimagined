#!/usr/bin/env python3
"""Global Ban policy and client-administration UI regression checks."""
import importlib.util
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "asr-api.php").read_text(encoding="utf-8")
SERVER_API = (ROOT / "server/asr-api.php").read_text(encoding="utf-8")
APP = (ROOT / "src/App.tsx").read_text(encoding="utf-8")
BAN_PATH = ROOT / "scripts/asr-global-ban.py"
BAN_SOURCE = BAN_PATH.read_text(encoding="utf-8")


def check(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


spec = importlib.util.spec_from_file_location("asr_global_ban", BAN_PATH)
check(spec is not None and spec.loader is not None, "Global Ban helper could not be imported")
ban = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ban)

check(API == SERVER_API, "API copies diverged")

with tempfile.TemporaryDirectory(prefix="asr-global-ban-self-test-") as raw_tmp:
    tmp = Path(raw_tmp)
    ban.BLACKLIST = str(tmp / "global-bans.list")
    ban.TIMED_BANS = str(tmp / "asr-timed-bans.json")
    ban.AUDIT_LOG = str(tmp / "asr-admin-audit.jsonl")
    ban.CONFIG = str(tmp / "config.json")
    Path(ban.CONFIG).write_text(json.dumps({"bridges": [], "filteredStations": ["FEEDALIAS"]}), encoding="utf-8")

    ban.write_rules(["KE7WIL", "N0CALL*"])
    check(ban.read_rules() == ["KE7WIL", "N0CALL*"], "logical Global Ban List round-trip failed")
    ban.write_timed_bans({"KE7WIL": {"createdAt": 1000, "expiresAt": 1900}})
    timed = ban.read_timed_bans()
    check(timed["KE7WIL"]["expiresAt"] == 1900, "timed Ban persistence round-trip failed")
    payload = ban.ban_payload(ban.read_rules(), timed, 1300)
    check(next(row for row in payload if row["rule"] == "KE7WIL")["remainingSeconds"] == 600,
          "timed Ban countdown payload is incorrect")
    check(next(row for row in payload if row["rule"] == "N0CALL*")["expiresAt"] == 0,
          "Permanent Ban was not represented distinctly")
    transitions = ban.set_ban_expiration({}, "KE7WIL", "15m", 1000)
    check(transitions["KE7WIL"] == {"createdAt": 1000, "expiresAt": 1900},
          "15-minute Ban duration was calculated incorrectly")
    transitions = ban.set_ban_expiration(transitions, "KE7WIL", "permanent", 1100)
    check("KE7WIL" not in transitions, "timed-to-Permanent conversion retained stale timer metadata")
    transitions = ban.set_ban_expiration(transitions, "KE7WIL", "1h", 1200)
    check(transitions["KE7WIL"]["expiresAt"] == 4800,
          "Permanent-to-timed conversion did not create timer metadata")
    prefix_timed = ban.set_ban_expiration({}, "N0CALL*", "1w", 2000)
    check(prefix_timed["N0CALL*"]["expiresAt"] == 606800, "prefix timed Ban duration failed")
    try:
        ban.set_ban_expiration({}, "KE7WIL", "forever", 1000)
        raise AssertionError("invalid Ban duration was accepted")
    except ValueError:
        pass

    for protected_rule in ("RFCKRD0", "YSF-LIVE", "KF0WSS", "KF0*", "FEEDALIAS", "FEED*"):
        try:
            ban.assert_ban_allowed(protected_rule)
            raise AssertionError(f"protected identity rule was accepted: {protected_rule}")
        except ValueError:
            pass
    ban.assert_ban_allowed("KF0WSS-2")

    stable_timer = {"KE7WIL": {"createdAt": 1000, "expiresAt": 1900}}
    for corrupt in ("{", "[]", '{"KE7WIL":{"createdAt":1000}}', '{"bad rule":{"createdAt":1000,"expiresAt":1900}}'):
        Path(ban.TIMED_BANS).write_text(corrupt, encoding="utf-8")
        try:
            ban.read_timed_bans()
            raise AssertionError("corrupt timed-ban state was accepted")
        except RuntimeError as error:
            check(str(error) == "Timed-ban state is unreadable.", "corrupt timer failure was not explicit")
        check(ban.read_rules() == ["KE7WIL", "N0CALL*"], "corrupt timer state changed logical bans")
    ban.write_timed_bans(stable_timer)
    (tmp / ".asr-timed-bans.partial").write_text("{", encoding="utf-8")
    check(ban.read_timed_bans() == stable_timer, "orphaned partial timer write replaced valid state")

    ban.append_audit_event("ban", "admin name", rule="KE7WIL", duration="15m", expiresAt=1900)
    audit = ban.read_audit_events()
    check(audit[-1]["action"] == "ban" and audit[-1]["actor"] == "admin_name", "administrator audit record failed")

    now = int(ban.time.time())
    ban.write_rules(["KE7WIL", "N0CALL*", "AD7TG"])
    ban.write_timed_bans({
        "KE7WIL": {"createdAt": now - 1000, "expiresAt": now - 1},
        "AD7TG": {"createdAt": now, "expiresAt": now + 3600},
        "GHOST": {"createdAt": now, "expiresAt": now + 7200},
    })
    remaining, timed, expired = ban.expire_timed_bans(ban.read_rules(), ban.read_timed_bans())
    check(expired == ["KE7WIL"] and remaining == ["AD7TG", "N0CALL*"],
          "one expired Ban incorrectly changed another active/Permanent Ban")
    check(set(timed) == {"AD7TG"}, "expired or orphaned timer metadata was retained")
    check(ban.read_rules() == ["AD7TG", "N0CALL*"], "expired Ban was not removed from saved rules")

check("['15m', '1h', '1w', '30d', 'permanent']" in API, "API Ban-duration allowlist is missing")
check("GLOBAL_BAN_DURATIONS" in APP and "formatGlobalBanRemaining" in APP, "timed Ban controls/countdown are missing")
check("window.setInterval(() => void refresh(), 15000)" in APP, "Manage Clients does not refresh automatic expiry")
check("'Global Ban'" in APP and "Applies everywhere ASR can enforce it." in APP
      and "'AllStar Node Ban'" in APP and "Blocks this AllStar node in Asterisk." in APP,
      "protocol-aware Ban dialog is missing")
check('function globalBanDurationSelect' in APP and '<option value="" disabled>Ban</option>' in APP, "compact Ban-duration dropdown is missing")
check("AllStar node only" not in APP and "AllStar callsign only" not in APP and "EchoLink callsign only" not in APP, "mode-specific Ban choices remain")
check("ASR Global Ban List" in APP, "global Ban action feedback missing")
check("protectedBanIdentities" in APP and "isProtectedBanConnection" in APP, "configured service-bridge Ban protection is missing")
check("Kick affects only the selected current bridge session" in APP, "Kick session scope warning missing")
check("Protected service and health-probe identities cannot be banned" in APP, "protected-identity explanation missing")
check("Timed-ban state is unreadable." in BAN_SOURCE and "os.fsync" in BAN_SOURCE, "timed-ban fail-safe durability is missing")
check("Recent bridge actions" in APP and "formatGlobalBanExpiration" in APP, "audit history or exact expiration display missing")

check("BAN_DURATIONS" in BAN_SOURCE and "expire_timed_bans" in BAN_SOURCE, "timed Ban lifecycle is missing")

print("Global Ban policy self-test: ok")
