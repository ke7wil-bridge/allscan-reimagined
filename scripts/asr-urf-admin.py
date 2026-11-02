#!/usr/bin/env python3
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


def global_ban_root(runtime=Path("/run/urf-wil-config"), native=Path("/var/lib/allscan-reimagined/admin")):
    """Use the Docker mount when present and persistent storage on native nodes."""
    return runtime if runtime.is_dir() else native


GLOBAL_BAN_ROOT = global_ban_root()
BLACKLIST = str(GLOBAL_BAN_ROOT / "urfd.blacklist")
TIMED_BANS = str(GLOBAL_BAN_ROOT / "asr-timed-bans.json")
AUDIT_LOG = str(GLOBAL_BAN_ROOT / "asr-admin-audit.jsonl")
ADMIN_LOCK = str(GLOBAL_BAN_ROOT / ".asr-urf-admin.lock")
CONFIG = "/etc/allscan-reimagined/config.json"
RUNTIME_DIR = "/run/urf-wil"
KICK_REQUEST = os.path.join(RUNTIME_DIR, "asr-kick-client.request")
KICK_RESULT = os.path.join(RUNTIME_DIR, "asr-kick-client.result")
def dmr_runtime_dir():
    """Resolve the shared TGIF/URF DMR runtime on native and containerized nodes.

    The bridge writes to /run/tgif-dmr on the host; the ASR web container may
    expose that same directory as /run/dmr-bridge. Prefer a writable host path
    so administrative changes reach the bridge rather than a read-only mount.
    """
    configured = os.environ.get("ASR_DMR_RUNTIME_DIR", "").strip()
    candidates = [configured] if configured else []
    candidates.extend(["/run/tgif-dmr", "/run/dmr-bridge"])
    # Some native URF installs keep the TGIF runtime inside the per-URF
    # bridge directory rather than bind-mounting it into /run.
    candidates.extend(sorted(__import__("glob").glob(
        "/opt/allscan-reimagined-bridges/urf/*/tgif-run"
    )))
    seen = set()
    existing = []
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if os.path.isdir(candidate):
            existing.append(candidate)
            if os.access(candidate, os.W_OK):
                return candidate
    return existing[0] if existing else (configured or "/run/tgif-dmr")


DMR_RUNTIME_DIR = dmr_runtime_dir()
DMR_ROSTER = os.environ.get("ASR_DMR_ROSTER_FILE", os.path.join(DMR_RUNTIME_DIR, "dmr-clients.json"))
DMR_CONTROL = os.environ.get("ASR_DMR_CONTROL_FILE", os.path.join(DMR_RUNTIME_DIR, "dmr-admin.json"))
DMR_DIRECTORY_ROSTER = os.environ.get("ASR_DMR_DIRECTORY_ROSTER_FILE", "/var/www/html/allscan/connected-clients.json")
ZELLO_RUNTIME_DIR = "/var/www/html/asr"
STANDALONE_ADMIN_HELPER = "/usr/local/sbin/allscan-reimagined-standalone-admin"
ASL_BAN_HELPER = "/usr/local/sbin/allscan-reimagined-asl-ban"
PROTOCOLS = {"DMRMMDVM": "DMRMmdvm", "YSF": "YSF", "P25": "P25", "NXDN": "NXDN", "M17": "M17", "ZELLO": "ZELLO"}
BAN_DURATIONS = {"15m": 15 * 60, "1h": 60 * 60, "1w": 7 * 24 * 60 * 60, "30d": 30 * 24 * 60 * 60, "permanent": 0}
RULE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_./-]{0,14}\*?$")
PROTECTED_IDENTITIES = {"RFCKRD0", "YSF-LIVE", "KF0WSS"}


def configured_backends():
    try:
        with open(CONFIG, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise RuntimeError("ASR bridge configuration is unavailable.") from exc
    backends = set()
    for bridge in payload.get("bridges", []):
        if not isinstance(bridge, dict):
            continue
        mode = str(bridge.get("mode") or bridge.get("type") or bridge.get("id") or "").lower()
        if bridge.get("urfReflector") or mode.startswith("urf"):
            backends.add("urf")
    return backends


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
    for value in payload.get("filteredStations", []):
        identity = str(value).strip().upper()
        if re.fullmatch(r"[A-Z0-9][A-Z0-9_./-]{0,14}", identity):
            identities.add(identity)
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
        return {"verified": True, "applied": [], "skipped": [], "unavailable": True}
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


def native_admin_modes():
    root = Path("/run/asr-standalone-admin")
    return {
        mode for mode in ("dmr", "ysf", "p25", "nxdn", "m17")
        if (root / mode / "capabilities.json").is_file()
    }


def configured_urf_modes():
    try:
        payload = json.loads(Path(CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    modes = set()
    for bridge in payload.get("bridges", []):
        if not isinstance(bridge, dict) or not bridge.get("urfReflector"):
            continue
        enabled = bridge.get("modes", bridge.get("urfModes", []))
        if isinstance(enabled, list):
            modes.update(str(value).strip().lower() for value in enabled)
    return modes & {"dmr", "ysf", "p25", "nxdn", "m17"}


def native_admin_kick(mode, callsign, actor):
    try:
        completed = subprocess.run(
            [STANDALONE_ADMIN_HELPER, "kick", mode, callsign, "--actor", actor],
            check=False, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Native URF Kick did not complete.") from exc
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
        raise RuntimeError(str((payload or {}).get("error") or "Native URF Kick failed."))
    return payload


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
    return {"status": "applied", **payload}


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
    fd, tmp = tempfile.mkstemp(prefix=".urfd.blacklist.", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("# Managed by AllScan Reimagined URF Administration\n")
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


def dmr_client_id(callsign):
    target = callsign.strip().upper()
    if target.isdigit():
        return int(target)
    readable = False
    for path in (DMR_ROSTER, DMR_DIRECTORY_ROSTER):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        readable = True
        rows = payload.get("urf_dmr", payload.get("dmr", [])) if isinstance(payload, dict) else []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            dmrid = str(row.get("dmrid") or row.get("id") or "").strip()
            row_call = str(row.get("callsign") or "").strip().upper()
            if dmrid.isdigit() and (target == row_call or target == dmrid):
                return int(dmrid)
    if not readable:
        raise RuntimeError("DMR client roster is unavailable.")
    raise RuntimeError("DMR client is no longer in the current TGIF roster.")


def remove_dmr_roster_client(dmrid, reason):
    try:
        with open(DMR_ROSTER, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return
    rows = payload.get("urf_dmr", []) if isinstance(payload, dict) else []
    kept = [row for row in rows if not isinstance(row, dict) or str(row.get("dmrid") or row.get("id") or "") != str(dmrid)]
    if len(kept) == len(rows):
        return
    events = payload.get("events", []) if isinstance(payload.get("events"), list) else []
    callsign = next((str(row.get("callsign") or "") for row in rows if isinstance(row, dict) and str(row.get("dmrid") or row.get("id") or "") == str(dmrid)), "")
    events.append({"dmrid": str(dmrid), "callsign": callsign, "event": reason, "epoch": int(time.time()), "reason": reason})
    payload["urf_dmr"] = kept
    payload["events"] = events[-100:]
    directory = os.path.dirname(DMR_ROSTER)
    fd, tmp = tempfile.mkstemp(prefix=".dmr-clients.", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
        os.chmod(tmp, 0o664)
        os.replace(tmp, DMR_ROSTER)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def write_dmr_kick(callsign, actor="system"):
    dmrid = dmr_client_id(callsign)
    try:
        with open(DMR_CONTROL, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        payload = {}
    banned = sorted({int(value) for value in payload.get("banned_ids", []) if str(value).isdigit()})
    kicked = {str(key): int(value) for key, value in payload.get("kicked_ids", {}).items() if str(key).isdigit()}
    clients = {str(key): str(value).upper() for key, value in payload.get("banned_clients", {}).items() if str(key).isdigit()}
    # The TGIF master owns the remote session, so local Disconnect is enforced
    # at the inbound adapter. Its rolling quiet-gap contract blocks the current
    # stream and treats later traffic after the disconnect gap as a reconnect.
    kicked[str(dmrid)] = int(time.time())
    remove_dmr_roster_client(dmrid, "kick")
    directory = os.path.dirname(DMR_CONTROL)
    if not os.path.isdir(directory) or not os.access(directory, os.W_OK):
        raise RuntimeError("DMR administration runtime is unavailable.")
    fd, tmp = tempfile.mkstemp(prefix=".dmr-admin.", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"banned_ids": banned, "banned_clients": clients, "kicked_ids": kicked}, handle, separators=(",", ":"))
        os.chmod(tmp, 0o664)
        os.replace(tmp, DMR_CONTROL)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    append_audit_event("kick", actor, callsign=callsign, protocol="DMRMMDVM", dmrid=str(dmrid))
    emit(ok=True, action="kick", callsign=callsign, protocol="DMRMMDVM", audit=read_audit_events(), verified=True, dmrid=str(dmrid), enforcement="disconnect_only", quietSeconds=0)


def sync_dmr_ban(rule, banned_state):
    """Mirror an exact URFWIL callsign ban into the DMR-ID enforcement store."""
    if rule.endswith("*") or not os.path.isdir(os.path.dirname(DMR_CONTROL)):
        return
    try:
        with open(DMR_CONTROL, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        payload = {}
    banned = {int(value) for value in payload.get("banned_ids", []) if str(value).isdigit()}
    kicked = {str(key): int(value) for key, value in payload.get("kicked_ids", {}).items() if str(key).isdigit()}
    clients = {str(key): str(value).upper() for key, value in payload.get("banned_clients", {}).items() if str(key).isdigit()}
    target = rule.upper()
    ids = {int(key) for key, call in clients.items() if call == target}
    if banned_state:
        try:
            ids.add(dmr_client_id(target))
        except RuntimeError:
            if not ids:
                return
        for dmrid in ids:
            banned.add(dmrid); kicked.pop(str(dmrid), None); clients[str(dmrid)] = target
            remove_dmr_roster_client(dmrid, "ban")
    else:
        if not ids:
            # Use only an authoritative current roster mapping; never infer a
            # DMR ID from a callsign or from unrelated banned numeric IDs.
            try:
                current_id = dmr_client_id(target)
                if current_id in banned:
                    ids.add(current_id)
            except RuntimeError:
                pass
        for dmrid in ids:
            banned.discard(dmrid); clients.pop(str(dmrid), None)
    directory = os.path.dirname(DMR_CONTROL)
    fd, tmp = tempfile.mkstemp(prefix=".dmr-admin.", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"banned_ids": sorted(banned), "banned_clients": clients, "kicked_ids": kicked}, handle, separators=(",", ":"))
        os.chmod(tmp, 0o664); os.replace(tmp, DMR_CONTROL)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)



def emit(**payload):
    print(json.dumps(payload, separators=(",", ":")))


def callsign_matches(rule, value):
    target = re.sub(r"\s+[A-Z]$", "", value.strip(), flags=re.I).upper()
    return target.startswith(rule[:-1]) if rule.endswith("*") else target == rule


def roster_contains(xml, callsign, protocol):
    for node in re.findall(r"<NODE>(.*?)</NODE>", xml, re.S):
        cs = re.search(r"<Callsign>(.*?)</Callsign>", node, re.S)
        pr = re.search(r"<Protocol>(.*?)</Protocol>", node, re.S)
        if not cs or not pr:
            continue
        node_protocol = pr.group(1).strip().upper()
        if callsign_matches(callsign, cs.group(1)) and (protocol == "*" or node_protocol == protocol.upper()):
            return True
    return False


def request_urf_disconnect(callsign, protocol, event, require_connected, actor="system"):
    if event not in {"kick", "ban", "unban"}:
        raise ValueError("Unsupported URF administrative event.")
    protocol_mode = {
        "DMRMMDVM": "dmr", "YSF": "ysf", "P25": "p25",
        "NXDN": "nxdn", "M17": "m17",
    }.get(protocol.upper(), "")
    native_modes = native_admin_modes()
    if event == "kick" and protocol_mode in native_modes and protocol_mode != "dmr":
        return int(native_admin_kick(protocol_mode, callsign, actor).get("removed") or 0)
    if protocol == "*":
        enabled = configured_urf_modes()
        native_required = enabled - {"dmr"}
        dmr_ready = "dmr" not in enabled or os.path.isfile(DMR_ROSTER)
        if native_required and native_required <= native_modes and dmr_ready:
            payload = reconcile_standalone_bans(actor)
            applied = {
                str(item.get("mode")) for item in payload.get("applied", [])
                if isinstance(item, dict)
            }
            if not native_required <= applied:
                raise RuntimeError("Native URF administration did not acknowledge every enabled protocol.")
            return sum(
                int(item.get("removed") or 0) for item in payload.get("applied", [])
                if isinstance(item, dict)
            )
    if not os.path.isdir(RUNTIME_DIR) or not os.access(RUNTIME_DIR, os.W_OK):
        if "urf" not in configured_backends() and not require_connected:
            return 0
        raise RuntimeError("URF runtime does not support administrative client removal.")
    try:
        os.unlink(KICK_RESULT)
    except FileNotFoundError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=".asr-kick-client.", dir=RUNTIME_DIR, text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"{callsign}|{protocol}|{event}\n")
    os.replace(tmp, KICK_REQUEST)

    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        try:
            result = open(KICK_RESULT, "r", encoding="utf-8").read().strip().split("|")
        except FileNotFoundError:
            result = []
        result_matches = (
            len(result) == 4
            and result[0] == callsign
            and result[1].upper() == protocol.upper()
            and result[2] == event
        )
        if result_matches:
            try:
                count = int(result[3])
            except ValueError:
                count = 0
            if require_connected and count < 1:
                raise RuntimeError("Client was no longer connected or could not be kicked.")
            if event == "unban":
                return count
            if count < 1:
                return 0
            if event == "kick":
                # Kick is deliberately disconnect-only. A client that reconnects
                # immediately must not turn a successful removal into an error.
                return count

            # For Ban, URFD object removal alone is not proof of enforcement.
            # Require the matching roster entry to remain absent for four seconds.
            xml_path = os.path.join(RUNTIME_DIR, "urfd.xml")
            verify_deadline = time.monotonic() + 15.0
            while time.monotonic() < verify_deadline:
                try:
                    xml = open(xml_path, "r", encoding="utf-8", errors="replace").read()
                except OSError:
                    xml = ""
                if not roster_contains(xml, callsign, protocol):
                    absent_since = time.monotonic()
                    while time.monotonic() - absent_since < 4.0:
                        time.sleep(0.5)
                        try:
                            verify_xml = open(xml_path, "r", encoding="utf-8", errors="replace").read()
                        except OSError:
                            verify_xml = ""
                        if roster_contains(verify_xml, callsign, protocol):
                            raise RuntimeError(
                                "Kick did not terminate the active client session."
                                if event == "kick"
                                else "Ban did not remove the active client session."
                            )
                    return count
                time.sleep(0.5)
            raise RuntimeError(
                "Kick was requested, but the client is still connected."
                if event == "kick"
                else "Ban was saved, but the active client session is still connected."
            )
        time.sleep(0.2)
    raise RuntimeError("URF reflector did not confirm the administrative client action.")


def expire_timed_bans(rules, timed):
    now = int(time.time())
    expired = sorted(
        rule for rule, record in timed.items()
        if int(record.get("expiresAt") or 0) <= now
    )
    for rule in expired:
        # Keep the visible rule authoritative until the active backends have
        # accepted removal of the expired ban.
        request_urf_disconnect(rule, "*", "unban", False)
        sync_dmr_ban(rule, False)
        next_rules = [item for item in rules if item != rule]
        rules = next_rules
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
    parser.add_argument("action", choices=["list", "ban", "unban", "kick", "expire", "dmr-ban", "dmr-unban", "dmr-list"])
    parser.add_argument("rule", nargs="?")
    parser.add_argument("protocol", nargs="?")
    parser.add_argument("--actor", default="system")
    args = parser.parse_args()
    if args.action in {"dmr-list", "dmr-ban", "dmr-unban"}:
        try:
            with open(DMR_CONTROL, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            payload = {}
        banned = {int(value) for value in payload.get("banned_ids", []) if str(value).isdigit()}
        kicked = {str(key): int(value) for key, value in payload.get("kicked_ids", {}).items() if str(key).isdigit()}
        banned_clients = {str(key): str(value).upper() for key, value in payload.get("banned_clients", {}).items() if str(key).isdigit()}
        if args.action == "dmr-list":
            emit(ok=True, bannedIds=[str(value) for value in sorted(banned)], bannedRules=[banned_clients.get(str(value), str(value)) for value in sorted(banned)])
            return
        if not args.rule:
            raise ValueError("A current DMR callsign or DMR ID is required.")
        dmrid = dmr_client_id(args.rule)
        if args.action == "dmr-ban":
            banned.add(dmrid)
            kicked.pop(str(dmrid), None)
            banned_clients[str(dmrid)] = args.rule.strip().upper()
            remove_dmr_roster_client(dmrid, "ban")
        else:
            banned.discard(dmrid)
            banned_clients.pop(str(dmrid), None)
        directory = os.path.dirname(DMR_CONTROL)
        if not os.path.isdir(directory) or not os.access(directory, os.W_OK):
            raise RuntimeError("DMR administration runtime is unavailable.")
        fd, tmp = tempfile.mkstemp(prefix=".dmr-admin.", dir=directory, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"banned_ids": sorted(banned), "banned_clients": banned_clients, "kicked_ids": kicked}, handle, separators=(",", ":"))
            os.chmod(tmp, 0o664)
            os.replace(tmp, DMR_CONTROL)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        emit(ok=True, action=args.action, rule=args.rule.strip().upper(), dmrid=str(dmrid), bannedIds=[str(value) for value in sorted(banned)], rules=[banned_clients.get(str(value), str(value)) for value in sorted(banned)], verified=True, enforcement="immediate", reloadSeconds=0)
        return

    if args.action == "kick":
        if not args.rule or not args.protocol:
            raise ValueError("Kick requires CALLSIGN and PROTOCOL.")
        callsign = args.rule.strip().upper()
        protocol = args.protocol.strip().upper()
        callsign = normalize(callsign)
        if callsign.endswith("*"):
            raise ValueError("Kick requires one exact callsign, not a prefix.")
        if protocol not in PROTOCOLS:
            raise ValueError("Unsupported bridge client protocol.")
        runtime_protocol = PROTOCOLS[protocol]
        if protocol == "DMRMMDVM":
            write_dmr_kick(callsign, args.actor)
            return
        if protocol == "ZELLO":
            raise ValueError("Zello does not support disconnecting a channel user.")
        count = request_urf_disconnect(callsign, runtime_protocol, "kick", True, args.actor)
        append_audit_event("kick", args.actor, callsign=callsign, protocol=protocol, removed=count)
        emit(ok=True, action="kick", callsign=callsign, protocol=protocol, audit=read_audit_events(), verified=True, removed=count, enforcement="disconnect_only", quietSeconds=0)
        return
    rules = read_rules()
    timed = read_timed_bans()
    rules, timed, expired = expire_timed_bans(rules, timed)
    if args.action == "expire":
        standalone = reconcile_standalone_bans(args.actor)
        asl = reconcile_asl_bans()
        emit(ok=True, asl=asl, action="expire", expired=expired, rules=sorted(set(rules)), bans=ban_payload(rules, timed), standalone=standalone, serverEpoch=int(time.time()))
        return
    if args.action == "list":
        standalone = reconcile_standalone_bans(args.actor)
        asl = reconcile_asl_bans()
        emit(ok=True, asl=asl, rules=sorted(set(rules)), bans=ban_payload(rules, timed), audit=read_audit_events(), standalone=standalone, serverEpoch=int(time.time()), reloadSeconds=30)
        return
    if not args.rule:
        raise ValueError("A Global Ban rule is required.")
    rule = normalize(args.rule)
    if args.action == "ban":
        assert_ban_allowed(rule)
        duration = (args.protocol or "permanent").strip().lower()
        now = int(time.time())
        if rule not in rules:
            rules.append(rule)
        timed = set_ban_expiration(timed, rule, duration, now)
        write_rules(rules)
        write_timed_bans(timed)
        sync_dmr_ban(rule, True)
        expected = sorted(set(rules))
        if read_rules() != expected:
            raise RuntimeError("URF blacklist write could not be verified.")
        removed = request_urf_disconnect(rule, "*", "ban", False, args.actor)
        standalone = reconcile_standalone_bans(args.actor)
        asl = reconcile_asl_bans()
        removed += int(asl.get("applied", {}).get("removed", 0))
        expires_at = int(timed.get(rule, {}).get("expiresAt") or 0)
        append_audit_event("ban", args.actor, rule=rule, duration=duration, expiresAt=expires_at, removed=removed)
        emit(ok=True, asl=asl, action="ban", rule=rule, duration=duration, rules=expected, bans=ban_payload(expected, timed, now), audit=read_audit_events(), standalone=standalone, expiresAt=expires_at, serverEpoch=now, verified=asl.get("status") == "applied", removed=removed, enforcement="immediate_global" if asl.get("status") == "applied" else "partial_pending_asl", reloadSeconds=30)
        return
    # Clear known backend enforcement before removing the logical entry. If
    # backend synchronization fails, the visible Global Ban List must remain
    # authoritative instead of falsely claiming that the identity is unbanned.
    request_urf_disconnect(rule, "*", "unban", False, args.actor)
    sync_dmr_ban(rule, False)
    next_rules = [item for item in rules if item != rule]
    rules = next_rules
    timed.pop(rule, None)
    write_rules(rules)
    write_timed_bans(timed)
    standalone = reconcile_standalone_bans(args.actor)
    asl = reconcile_asl_bans()
    expected = sorted(set(rules))
    if read_rules() != expected:
        raise RuntimeError("URF blacklist write could not be verified.")
    now = int(time.time())
    append_audit_event("unban", args.actor, rule=rule)
    emit(ok=True, asl=asl, action="unban", rule=rule, rules=expected, bans=ban_payload(expected, timed, now), audit=read_audit_events(), standalone=standalone, serverEpoch=now, verified=asl.get("status") == "applied", enforcement="immediate_global" if asl.get("status") == "applied" else "partial_pending_asl", reloadSeconds=30)


if __name__ == "__main__":
    try:
        with admin_lock():
            main()
    except Exception as exc:
        emit(ok=False, error=str(exc))
        raise SystemExit(1)
