#!/usr/bin/env python3
"""ASR Global Ban: one callsign policy enforced across every ASR bridge.

The policy is stored under /var/lib/allscan-reimagined/admin. Native bridge
adapters (DMR, YSF, P25, NXDN, M17) reconcile it through the standalone
administration helper, AllStar/EchoLink through the ASL ban helper, and
Zello reads it directly. Zello Kick suppresses the current or next stream.
"""
import argparse
import fcntl
import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

GLOBAL_BAN_ROOT = Path(os.environ.get("ASR_GLOBAL_BAN_ROOT", "/var/lib/allscan-reimagined/admin"))
BLACKLIST = str(GLOBAL_BAN_ROOT / "global-bans.list")
TIMED_BANS = str(GLOBAL_BAN_ROOT / "asr-timed-bans.json")
AUDIT_LOG = str(GLOBAL_BAN_ROOT / "asr-admin-audit.jsonl")
ADMIN_LOCK = str(GLOBAL_BAN_ROOT / ".asr-global-ban.lock")
CONFIG = "/etc/allscan-reimagined/config.json"
ZELLO_TALKERS = "/var/www/html/allscan/zello-talkers.json"
ZELLO_KICKS = str(GLOBAL_BAN_ROOT / "asr-zello-kicks.json")
STANDALONE_ADMIN_HELPER = "/usr/local/sbin/allscan-reimagined-standalone-admin"
ASL_BAN_HELPER = "/usr/local/sbin/allscan-reimagined-asl-ban"
TGIF_SESSION_HELPER = os.environ.get("ASR_TGIF_SESSION_HELPER", "/usr/local/sbin/allscan-reimagined-tgif-user-session")
BAN_DURATIONS = {"15m": 15 * 60, "1h": 60 * 60, "1w": 7 * 24 * 60 * 60, "30d": 30 * 24 * 60 * 60, "permanent": 0}
RULE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_./-]{0,14}\*?$")
PROTECTED_IDENTITIES = {"RFCKRD0", "YSF-LIVE", "KF0WSS"}


def normalize(value: str) -> str:
    rule = value.strip().upper()
    if not RULE_RE.fullmatch(rule):
        raise ValueError("Global Ban rule must be an identity/prefix using A-Z, 0-9, _, /, . or -, with optional trailing *.")
    return rule


def protected_identities():
    identities = set(PROTECTED_IDENTITIES)
    try:
        with open(CONFIG, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return identities
    if not isinstance(payload, dict):
        return identities
    local_callsign = str(payload.get("callsign") or "").strip().upper()
    if re.fullmatch(r"[A-Z0-9][A-Z0-9_./-]{0,14}", local_callsign):
        identities.add(local_callsign)
    for value in payload.get("filteredStations", []):
        identity = str(value).strip().upper()
        if re.fullmatch(r"[A-Z0-9][A-Z0-9_./-]{0,14}", identity):
            identities.add(identity)
    # The node's own AllStar number, its bridge nodes and the shared Net
    # Bridge node are infrastructure, never ban targets. A prefix rule that
    # would cover any of them (for example 6749* or 1*) is refused too.
    nodes = [payload.get("node"), "1999"]
    nodes += [bridge.get("node") for bridge in payload.get("bridges", []) if isinstance(bridge, dict)]
    for value in nodes:
        node = str(value or "").strip()
        if re.fullmatch(r"[0-9]{3,10}", node):
            identities.add(node)
    return identities


def protected_identity_for_rule(rule):
    prefix = rule[:-1] if rule.endswith("*") else rule
    return next((identity for identity in sorted(protected_identities()) if identity == rule or (rule.endswith("*") and identity.startswith(prefix))), "")


def assert_ban_allowed(rule):
    identity = protected_identity_for_rule(rule)
    if identity:
        raise ValueError(f"{identity} is a protected ASR service/probe identity and cannot be globally banned.")


@contextmanager
def admin_lock():
    directory = os.path.dirname(ADMIN_LOCK)
    if not os.path.isdir(directory):
        raise RuntimeError("ASR Global Ban storage is unavailable.")
    descriptor = os.open(ADMIN_LOCK, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def reconcile_standalone_bans(actor="system"):
    if not os.path.isfile(STANDALONE_ADMIN_HELPER) or not os.access(STANDALONE_ADMIN_HELPER, os.X_OK):
        # Never report successful global enforcement when a configured native
        # bridge has no policy reconciliation helper installed.
        with open(CONFIG, "r", encoding="utf-8") as handle:
            config = json.load(handle)
        native = any(
            isinstance(bridge, dict)
            and str(bridge.get("mode") or bridge.get("type") or bridge.get("id") or "").lower().startswith(("dmr", "ysf", "p25", "nxdn", "m17"))
            for bridge in config.get("bridges", [])
        )
        if native:
            raise RuntimeError("Native ASR Global Ban enforcement helper is unavailable.")
        return {"verified": False, "applied": [], "skipped": [], "unavailable": True}
    try:
        completed = subprocess.run(
            [STANDALONE_ADMIN_HELPER, "reconcile-all", "--actor", actor],
            check=False, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Standalone bridge Global Ban reconciliation failed.") from exc
    payload = None
    for line in reversed(completed.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict):
            payload = candidate
            break
    if completed.returncode != 0 or not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("verified") is not True:
        raise RuntimeError(str((payload or {}).get("error") or "Standalone bridge Global Ban reconciliation failed."))
    return payload


def global_enforcement_verified(asl, standalone):
    """Never certify a global operation when a native adapter was skipped."""
    return (asl.get("status") == "applied"
            and not ((asl.get("applied") or {}).get("unmappedGlobalRules"))
            and standalone.get("verified") is True
            and not standalone.get("skipped")
            and not standalone.get("unavailable"))


def reconcile_asl_bans():
    if not os.path.isfile(ASL_BAN_HELPER) or not os.access(ASL_BAN_HELPER, os.X_OK):
        return {"status": "unavailable", "error": "AllStar/EchoLink adapter is unavailable."}
    try:
        result = subprocess.run([ASL_BAN_HELPER, "sync"], capture_output=True, text=True, timeout=25)
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return {"status": "failed", "error": "AllStar/EchoLink ban reconciliation did not complete."}
    if result.returncode != 0 or payload.get("ok") is not True:
        return {"status": "failed", "error": str(payload.get("error") or "AllStar/EchoLink ban reconciliation failed.")[:180]}
    applied = payload.get("applied") if isinstance(payload.get("applied"), dict) else {}
    pending = payload.get("applied", {}).get("pending", {}) if applied else {}
    if isinstance(pending, dict) and pending.get("echolink"):
        return {"status": "failed", "error": str(pending["echolink"])[:180], **payload}
    status = "partial" if applied.get("unmappedGlobalRules") else "applied"
    return {"status": status, **payload}


def refresh_connected_clients():
    """Refresh the bridge cards' connected lists now instead of in ~20 s."""
    try:
        subprocess.run(["systemctl", "start", "--no-block", "allscan-reimagined-bridge-clients.service"],
                       capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def reconcile_tgif_bans(force=False):
    """Mirror callsign bans onto the signed-in TGIF talkgroup's access list.

    TGIF is a separate network: its result is reported on its own and never
    changes whether ASR's own enforcement is verified.
    """
    if not os.path.isfile(TGIF_SESSION_HELPER) or not os.access(TGIF_SESSION_HELPER, os.X_OK):
        return {"status": "unavailable", "error": "TGIF session helper is unavailable."}
    command = [TGIF_SESSION_HELPER, "ban-sync", "node"] + (["--force"] if force else [])
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "failed", "error": "TGIF ban sync did not finish."}
    payload = None
    for line in reversed(completed.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict):
            payload = candidate
            break
    if not isinstance(payload, dict):
        return {"status": "failed", "error": "TGIF ban sync returned no result."}
    if payload.get("configured") is False:
        return {"status": "not-configured", "error": str(payload.get("error") or "")[:180]}
    keep = {key: payload[key] for key in ("talkgroup", "rules", "added", "removed", "noDmrId", "errors", "cached") if key in payload}
    status = "applied" if payload.get("ok") is True and payload.get("verified") is True else "failed"
    return {"status": status, **keep}


def read_rules():
    if not os.path.isfile(BLACKLIST):
        return []
    rules = []
    with open(BLACKLIST, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            token = re.split(r"[\s,]+", line, maxsplit=1)[0].upper()
            if RULE_RE.fullmatch(token) and token not in rules:
                rules.append(token)
    return rules


def write_rules(rules):
    directory = os.path.dirname(BLACKLIST)
    if not os.path.isdir(directory):
        raise RuntimeError("ASR Global Ban storage is unavailable.")
    fd, tmp = tempfile.mkstemp(prefix=".global-bans.", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("# Managed by AllScan Reimagined Global Ban\n")
            for rule in sorted(set(rules)):
                handle.write(rule + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, BLACKLIST)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_timed_bans():
    try:
        with open(TIMED_BANS, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise RuntimeError("Timed-ban state is unreadable.")
    if not isinstance(payload, dict):
        raise RuntimeError("Timed-ban state is unreadable.")
    records = {}
    try:
        for rule, value in payload.items():
            normalized_rule = str(rule).upper()
            if not RULE_RE.fullmatch(normalized_rule) or not isinstance(value, dict):
                raise ValueError
            if set(value) != {"createdAt", "expiresAt"}:
                raise ValueError
            created = int(value["createdAt"])
            expires = int(value["expiresAt"])
            if created < 0 or expires <= 0 or (created > 0 and expires <= created):
                raise ValueError
            records[normalized_rule] = {"createdAt": created, "expiresAt": expires}
    except (TypeError, ValueError, OverflowError):
        raise RuntimeError("Timed-ban state is unreadable.")
    return records


def write_timed_bans(records):
    directory = os.path.dirname(TIMED_BANS)
    if not os.path.isdir(directory):
        raise RuntimeError("ASR Global Ban storage is unavailable.")
    fd, tmp = tempfile.mkstemp(prefix=".asr-timed-bans.", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(records, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, TIMED_BANS)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_audit_events(limit=100):
    try:
        with open(AUDIT_LOG, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except FileNotFoundError:
        return []
    except OSError:
        raise RuntimeError("Administration audit history is unavailable.")
    events = []
    for line in lines[-max(1, min(int(limit), 250)):]:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("timestamp"), int) and event.get("action"):
            events.append(event)
    return events


def append_audit_event(action, actor="system", **details):
    if action not in {"kick", "ban", "unban", "expire"}:
        raise ValueError("Unsupported administration audit event.")
    directory = os.path.dirname(AUDIT_LOG)
    if not os.path.isdir(directory):
        raise RuntimeError("ASR Global Ban storage is unavailable.")
    clean_actor = re.sub(r"[^A-Za-z0-9_.@+-]", "_", str(actor or "system"))[:80] or "system"
    record = {"timestamp": int(time.time()), "action": action, "actor": clean_actor}
    record.update({key: value for key, value in details.items() if value not in (None, "")})
    descriptor = os.open(AUDIT_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
    try:
        os.write(descriptor, (json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def set_ban_expiration(timed, rule, duration, now=None):
    if duration not in BAN_DURATIONS:
        raise ValueError("Ban duration must be 15m, 1h, 1w, 30d or permanent.")
    current = int(time.time()) if now is None else int(now)
    records = dict(timed)
    if BAN_DURATIONS[duration] > 0:
        records[rule] = {"createdAt": current, "expiresAt": current + BAN_DURATIONS[duration]}
    else:
        records.pop(rule, None)
    return records


def ban_payload(rules, timed, now=None):
    current = int(time.time()) if now is None else int(now)
    return [
        {
            "rule": rule,
            "createdAt": int(timed.get(rule, {}).get("createdAt") or 0),
            "expiresAt": int(timed.get(rule, {}).get("expiresAt") or 0),
            "remainingSeconds": max(0, int(timed.get(rule, {}).get("expiresAt") or 0) - current) if rule in timed else 0,
        }
        for rule in sorted(set(rules))
    ]


def write_zello_kick(callsign, actor="system"):
    """Mute a recent Zello talker on this bridge for one minute (#134)."""
    try:
        with open(ZELLO_TALKERS, "r", encoding="utf-8") as handle:
            talkers = json.load(handle)
        updated = os.path.getmtime(ZELLO_TALKERS)
    except (OSError, ValueError) as exc:
        raise RuntimeError("Zello current-talker state is unavailable.") from exc
    now = int(time.time())
    target = callsign.strip().upper()
    current = str(talkers.get("current_user") or "").strip().upper()
    current_active = bool(talkers.get("active")) and abs(now - updated) <= 30 and current == target
    recent = False
    for row in talkers.get("recent_users", []) or []:
        if isinstance(row, dict):
            name = str(row.get("callsign") or row.get("name") or row.get("username") or "").strip().upper()
            try:
                epoch = int(row.get("last_tx_epoch") or row.get("last_seen_epoch") or row.get("timestamp") or 0)
            except (TypeError, ValueError):
                epoch = 0
        else:
            name = str(row).strip().upper()
            epoch = 0
        if name != target:
            continue
        recent = (epoch <= 0 and abs(now - updated) <= 43200) or (epoch > 0 and 0 <= now - epoch <= 43200)
        if recent:
            break
    if not current_active and not recent:
        raise RuntimeError("Zello identity is not in recent activity.")

    directory = os.path.dirname(ZELLO_KICKS)
    if not os.path.isdir(directory):
        raise RuntimeError("ASR Zello administration storage is unavailable.")
    lock = os.open(ZELLO_KICKS + ".lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        payload = {"schema": 1, "sessions": {}}
        try:
            with open(ZELLO_KICKS, "r", encoding="utf-8") as handle:
                prior = json.load(handle)
            if isinstance(prior, dict) and prior.get("schema") == 1 and isinstance(prior.get("sessions"), dict):
                payload = prior
        except (OSError, ValueError):
            pass
        payload["sessions"] = {
            str(name).upper(): value for name, value in payload["sessions"].items()
            if isinstance(value, dict) and now - int(value.get("createdAt") or 0) <= 300
        }
        payload["sessions"][target] = {"createdAt": now, "actor": str(actor)[:80]}
        fd, tmp = tempfile.mkstemp(prefix=".asr-zello-kicks.", dir=directory, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o644)
            os.replace(tmp, ZELLO_KICKS)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)

    # Clear the exported active flag only when the selected identity owns the
    # live stream. An idle Kick remains armed for that identity's next stream.
    if current_active:
        talkers["active"] = False
        talkers["current_user"] = ""
        fd, tmp = tempfile.mkstemp(prefix=".zello-talkers.", dir=os.path.dirname(ZELLO_TALKERS), text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(talkers, handle, separators=(",", ":"))
            os.chmod(tmp, 0o644)
            os.replace(tmp, ZELLO_TALKERS)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    append_audit_event("kick", actor, callsign=target, protocol="ZELLO", removed=1)
    emit(ok=True, action="kick", callsign=target, protocol="ZELLO",
         audit=read_audit_events(), verified=True, removed=1,
         enforcement="zello-mute-hold-v1", expiresSeconds=60, quietSeconds=0)


def emit(**payload):
    print(json.dumps(payload, separators=(",", ":")))


def expire_timed_bans(rules, timed):
    now = int(time.time())
    expired = sorted(
        rule for rule, record in timed.items()
        if int(record.get("expiresAt") or 0) <= now
    )
    for rule in expired:
        rules = [item for item in rules if item != rule]
        timed.pop(rule, None)
        write_rules(rules)
        write_timed_bans(timed)
        append_audit_event("expire", "system", rule=rule)

    # Metadata for a manually removed rule must never recreate that rule.
    stale = [rule for rule in timed if rule not in rules]
    if stale:
        for rule in stale:
            timed.pop(rule, None)
        write_timed_bans(timed)
    return rules, timed, expired


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["list", "ban", "unban", "kick", "expire"])
    parser.add_argument("rule", nargs="?")
    parser.add_argument("protocol", nargs="?")
    parser.add_argument("--actor", default="system")
    args = parser.parse_args()

    if args.action == "kick":
        # Native bridge Kick is per bridge (standalone administration helper).
        # Only Zello, which has no remote disconnect, is kicked here.
        if not args.rule or (args.protocol or "").strip().upper() != "ZELLO":
            raise ValueError("Kick requires CALLSIGN ZELLO; other bridges use their own Kick.")
        callsign = normalize(args.rule)
        if callsign.endswith("*"):
            raise ValueError("Kick requires one exact callsign, not a prefix.")
        write_zello_kick(callsign, args.actor)
        return
    rules = read_rules()
    timed = read_timed_bans()
    rules, timed, expired = expire_timed_bans(rules, timed)
    if args.action in {"expire", "list"}:
        standalone = reconcile_standalone_bans(args.actor)
        asl = reconcile_asl_bans()
        if args.action == "expire":
            emit(ok=True, asl=asl, action="expire", expired=expired, rules=sorted(set(rules)),
                 bans=ban_payload(rules, timed), standalone=standalone, tgif=reconcile_tgif_bans(),
                 serverEpoch=int(time.time()))
        else:
            emit(ok=True, asl=asl, rules=sorted(set(rules)), bans=ban_payload(rules, timed),
                 audit=read_audit_events(), standalone=standalone, serverEpoch=int(time.time()), reloadSeconds=30)
        return
    if not args.rule:
        raise ValueError("A Global Ban rule is required.")
    rule = normalize(args.rule)
    now = int(time.time())
    if args.action == "ban":
        assert_ban_allowed(rule)
        duration = (args.protocol or "permanent").strip().lower()
        if rule not in rules:
            rules.append(rule)
        timed = set_ban_expiration(timed, rule, duration, now)
    else:
        duration = ""
        rules = [item for item in rules if item != rule]
        timed.pop(rule, None)
    write_rules(rules)
    write_timed_bans(timed)
    expected = sorted(set(rules))
    if read_rules() != expected:
        raise RuntimeError("Global Ban list write could not be verified.")
    standalone = reconcile_standalone_bans(args.actor)
    asl = reconcile_asl_bans()
    verified = global_enforcement_verified(asl, standalone)
    refresh_connected_clients()
    tgif = reconcile_tgif_bans(force=True)
    common = dict(ok=True, asl=asl, tgif=tgif, action=args.action, rule=rule, rules=expected,
                  bans=ban_payload(expected, timed, now), audit=None, standalone=standalone,
                  serverEpoch=now, verified=verified,
                  enforcement="immediate_global" if verified else "partial_pending_enforcement",
                  reloadSeconds=30)
    if args.action == "ban":
        removed = int(asl.get("applied", {}).get("removed", 0)) if isinstance(asl.get("applied"), dict) else 0
        expires_at = int(timed.get(rule, {}).get("expiresAt") or 0)
        append_audit_event("ban", args.actor, rule=rule, duration=duration, expiresAt=expires_at, removed=removed)
        common.update(duration=duration, expiresAt=expires_at, removed=removed)
    else:
        append_audit_event("unban", args.actor, rule=rule)
    common["audit"] = read_audit_events()
    emit(**common)


if __name__ == "__main__":
    try:
        with admin_lock():
            main()
    except Exception as exc:
        emit(ok=False, error=str(exc))
        raise SystemExit(1)
