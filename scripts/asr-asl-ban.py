#!/usr/bin/env python3
"""ASR-owned AllStar/EchoLink restrictions, enforced through the existing ASL3 AMI."""
import argparse
import configparser
import fcntl
import json
import os
import re
import socket
import tempfile
import time
from pathlib import Path

STATE = Path("/run/urf-wil-config/asr-asl-bans.json")
LOCK = Path("/run/urf-wil-config/.asr-asl-bans.lock")
GLOBAL = Path("/run/urf-wil-config/urfd.blacklist")
MANAGER = Path("/etc/asterisk/manager.conf")
CONFIG = Path("/etc/allscan-reimagined/config.json")
DURATIONS = {"15m": 900, "1h": 3600, "1w": 604800, "30d": 2592000, "permanent": 0}
NODE = re.compile(r"^[0-9]{3,10}$")
CALL = re.compile(r"^[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}(?:-[LR])?$")
KEY = re.compile(r"^[A-Z0-9]{3,15}$")
PROTECTED_CALLS = {"RFCKRD0", "YSF-LIVE", "KF0WSS"}


def check_node(value):
    if not NODE.fullmatch(value):
        raise ValueError("Enter a valid AllStar node number.")
    return value


def check_call(value):
    value = value.strip().upper()
    if not CALL.fullmatch(value) or not any(char.isalpha() for char in value):
        raise ValueError("Enter a valid callsign.")
    if value in PROTECTED_CALLS:
        raise ValueError("ASR service and probe callsigns cannot be banned.")
    return value


def base_call(value):
    return re.sub(r"-(?:L|R)$", "", check_call(value))


def local_node():
    data = json.loads(CONFIG.read_text())
    return check_node(str(data.get("node", "")))


def bridge_nodes():
    data = json.loads(CONFIG.read_text())
    return {str(row.get("node", "")) for row in data.get("bridges", []) if isinstance(row, dict)}


def read_state():
    try:
        data = json.loads(STATE.read_text())
    except FileNotFoundError:
        return {"version": 1, "bans": [], "ownedAst": [], "ownedEcho": [], "audit": []}
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("bans"), list):
        raise ValueError("ASL ban ledger is invalid.")
    if not isinstance(data.get("audit", []), list):
        raise ValueError("ASL ban audit is invalid.")
    data.setdefault("audit", [])
    return data


def save_state(data):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".asr-asl-bans.", dir=STATE.parent, text=True)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, STATE)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def global_calls():
    if not GLOBAL.exists():
        return set(), []
    result, unsupported = set(), []
    local_call = str(json.loads(CONFIG.read_text()).get("callsign", "")).strip().upper()
    local_base = base_call(local_call) if CALL.fullmatch(local_call) else ""
    for raw in GLOBAL.read_text().splitlines():
        raw = raw.strip().upper()
        if not raw or raw.startswith("#"):
            continue
        if CALL.fullmatch(raw) and any(char.isalpha() for char in raw):
            canonical = base_call(raw)
            if canonical in PROTECTED_CALLS or (local_base and canonical == local_base):
                unsupported.append(raw)
            else:
                result.add(canonical)
        else:
            unsupported.append(raw)
    return result, unsupported


class AMI:
    def __init__(self):
        cfg = configparser.ConfigParser(
            interpolation=None, inline_comment_prefixes=(";", "#")
        )
        cfg.read(MANAGER)
        user = next((section for section in cfg.sections() if section != "general"), "")
        if not user or not cfg.has_option(user, "secret"):
            raise RuntimeError("Asterisk manager credentials are unavailable.")
        self.sock = socket.create_connection((cfg.get("general", "bindaddr"), cfg.getint("general", "port")), timeout=5)
        self.sock.settimeout(6)
        self.file = self.sock.makefile("rb")
        self.file.readline()
        result = self.action("Login", Username=user, Secret=cfg.get(user, "secret"), Events="off")
        if result.get("Response") != "Success":
            raise RuntimeError("Asterisk manager login failed.")

    def action(self, name, **fields):
        payload = {"Action": name, **fields}
        if any("\r" in str(v) or "\n" in str(v) for v in payload.values()):
            raise ValueError("Invalid Asterisk manager field.")
        self.sock.sendall(("".join(f"{k}: {v}\r\n" for k, v in payload.items()) + "\r\n").encode())
        response = {}
        outputs = []
        for _ in range(8192):
            line = self.file.readline()
            if not line:
                raise RuntimeError("Asterisk manager disconnected.")
            if line in (b"\r\n", b"\n"):
                break
            key, separator, value = line.decode("utf-8", "replace").rstrip("\r\n").partition(": ")
            if separator:
                if key == "Output":
                    outputs.append(value)
                else:
                    response[key] = value
        response["outputs"] = outputs
        if not response.get("Response"):
            raise RuntimeError("Asterisk manager returned no response.")
        return response

    def command(self, command):
        response = self.action("Command", Command=command)
        if response.get("Response") not in ("Success", "Follows"):
            raise RuntimeError("Asterisk rejected the requested operation.")
        return "\n".join(response["outputs"])

    def close(self):
        self.file.close()
        self.sock.close()


def ast_entries(ami, local):
    output = ami.command(f"database show denylist/{local}")
    found = set()
    for match in re.finditer(r"(?m)^/denylist/" + re.escape(local) + r"/([A-Za-z0-9/-]+)\s*:", output):
        found.add(match.group(1).upper())
    return found


def echo_denies(ami):
    response = ami.action("GetConfig", Filename="echolink.conf")
    if response.get("Response") != "Success":
        raise RuntimeError("Cannot read EchoLink configuration.")
    tokens = set()
    found = False
    # ASL3 GetConfig returns one Line-<category>-<index>: name=value per line.
    for key, value in response.items():
        if key.startswith("Line-"):
            name, separator, entry = value.partition("=")
            if separator and name.strip().lower() == "deny":
                found = True
                tokens.update(part.strip().upper() for part in entry.split(",") if part.strip())
    return tokens, found


def restart_echo_module(ami):
    channels = ami.command("core show channels concise")
    if any(line.split("!", 1)[0].upper().startswith("ECHOLINK/") for line in channels.splitlines()):
        raise RuntimeError("EchoLink configuration was saved; an active EchoLink session prevents applying it. Retry when the session ends.")
    modules = ami.command("module show like chan_echolink.so")
    match = re.search(r"(?m)^chan_echolink\.so\s+.*?\s+(\d+)\s+Running\b", modules)
    if not match or int(match.group(1)) != 0:
        raise RuntimeError("EchoLink configuration was saved; its module is busy or unavailable. Retry when idle.")
    result = ami.action("ModuleLoad", Module="chan_echolink.so", LoadType="refresh")
    if result.get("Response") != "Success" or "unloaded and loaded" not in result.get("Message", "").lower():
        raise RuntimeError("EchoLink configuration was saved; Asterisk did not confirm its module refresh.")
    check = ami.action("ModuleCheck", Module="chan_echolink.so")
    if check.get("Response") != "Success":
        raise RuntimeError("EchoLink module is not running after refresh.")


def apply_echo(ami, desired, previous, force_restart=False):
    current, has_deny = echo_denies(ami)
    manual = current - previous
    target = manual | desired
    newly_owned = desired - current
    owned = (previous & desired) | newly_owned
    if target == current and not force_restart:
        return owned
    if target != current:
        update_echo_config(ami, target, has_deny)
    restart_echo_module(ami)
    observed, _ = echo_denies(ami)
    if observed != target:
        raise RuntimeError("EchoLink deny configuration did not verify.")
    return owned


def update_echo_config(ami, target, has_deny):
    fields = {
        "SrcFilename": "echolink.conf", "DstFilename": "echolink.conf",
        "Action-000000": "Update" if has_deny else "Append",
        "Cat-000000": "el0", "Var-000000": "deny",
        "Value-000000": ",".join(sorted(target)),
    }
    if not target and has_deny:
        fields["Action-000000"] = "Delete"
        fields.pop("Value-000000")
    response = ami.action("UpdateConfig", **fields)
    if response.get("Response") != "Success":
        raise RuntimeError("Asterisk rejected EchoLink deny configuration.")


def disconnect_active(ami, desired_ast, desired_echo, local):
    output = ami.command("core show channels concise")
    removed = 0
    for line in output.splitlines():
        parts = line.split("!")
        if len(parts) < 9:
            continue
        channel, context, cid_number, cid_name = parts[0], parts[1], parts[7], parts[8]
        if not re.fullmatch(r"[A-Za-z0-9/_.:@+\-]{1,130}", channel):
            continue
        is_iax = channel.upper().startswith("IAX2/")
        is_echo = channel.upper().startswith("ECHOLINK/")
        match = (is_iax and context == "radio-secure" and cid_number.upper() in desired_ast)
        if is_iax and context == "allstar-public":
            # Asterisk's concise listing often has an empty caller name for a
            # web transceiver even though the authenticated channel has one.
            detail = ami.command("core show channel " + channel)
            caller = re.search(r"(?m)^\s*CALLSIGN=([A-Za-z0-9-]+)\s*$", detail)
            node = re.search(r"(?m)^\s*NODENUM=([0-9]+)\s*$", detail)
            match = match or bool(caller and node and node.group(1) == local
                                  and caller.group(1).upper() in desired_ast)
        match = match or (is_echo and cid_name.upper().removesuffix("-L").removesuffix("-R") in desired_echo)
        if not match:
            continue
        response = ami.command("channel request hangup " + channel)
        if "no such channel" in response.lower():
            continue
        if "hangup" not in response.lower() or "failed" in response.lower():
            raise RuntimeError("A matching active Asterisk channel could not be disconnected.")
        removed += 1
        if removed >= 25:
            break
    return removed


def reconcile(data, ami, local):
    data.setdefault("audit", [])
    now = int(time.time())
    active = [row for row in data["bans"] if not row["expiresAt"] or row["expiresAt"] > now]
    expired = [row for row in data["bans"] if row not in active]
    if expired:
        for row in expired:
            data["audit"].append({"action": "expire", "kind": row["kind"], "value": row["value"], "at": now})
        data["audit"] = data["audit"][-250:]
    desired_ast = {row["value"] for row in active if row["kind"] in ("node", "allstar-call")}
    desired_echo = {row["value"] for row in active if row["kind"] == "echolink-call"}
    global_rules, unsupported = global_calls()
    for call in global_rules:
        desired_ast.add(call)
        desired_echo.add(call)
    previous_ast = set(data.get("ownedAst", []))
    previous_echo = set(data.get("ownedEcho", []))
    existing = ast_entries(ami, local)
    # Entries already present before ASR owns them belong to the Linux menu.
    newly_owned = set()
    for target in sorted(desired_ast - existing):
        ami.command(f"database put denylist/{local} {target} ASR-managed")
        newly_owned.add(target)
    for target in sorted(previous_ast - desired_ast):
        ami.command(f"database del denylist/{local} {target}")
    owned_ast = ((previous_ast & desired_ast) | newly_owned)
    if not desired_ast.issubset(ast_entries(ami, local)):
        raise RuntimeError("AllStar native deny entries did not verify.")
    data["ownedAst"] = sorted(owned_ast)
    save_state(data)
    removed = disconnect_active(ami, desired_ast, desired_echo, local)
    # Record intended ownership before the remote write so a reload failure can
    # be retried without mistaking our own directive for an external manual ban.
    existing_echo, _ = echo_denies(ami)
    owned_echo = (previous_echo & desired_echo) | (desired_echo - existing_echo)
    data["ownedEcho"] = sorted(owned_echo)
    save_state(data)
    applied_echo = data.get("appliedEcho")
    refresh_needed = isinstance(applied_echo, list) and applied_echo != sorted((existing_echo - previous_echo) | desired_echo)
    apply_echo(ami, desired_echo, previous_echo, force_restart=refresh_needed)
    data["appliedEcho"] = sorted(echo_denies(ami)[0])
    data["ownedEcho"] = sorted(owned_echo)
    data["bans"] = active
    save_state(data)
    return {"removed": removed, "allstar": sorted(desired_ast), "echolink": sorted(desired_echo),
            "unmappedGlobalRules": unsupported,
            "externalAst": sorted(ast_entries(ami, local) - owned_ast),
            "externalEcho": sorted(echo_denies(ami)[0] - owned_echo)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("list", "ban", "unban", "sync"))
    parser.add_argument("--kind", choices=("node", "allstar-call", "echolink-call"))
    parser.add_argument("--value", default="")
    parser.add_argument("--duration", choices=DURATIONS, default="permanent")
    parser.add_argument("--reason", default="")
    parser.add_argument("--actor", default="system")
    args = parser.parse_args()
    local = local_node()
    with LOCK.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_state()
        if args.action == "ban":
            if not args.kind:
                raise ValueError("Choose a ban target.")
            value = check_node(args.value) if args.kind == "node" else base_call(args.value)
            if value == local or (args.kind == "node" and value in bridge_nodes()):
                raise ValueError("Local and internal bridge nodes cannot be banned.")
            own_call = str(json.loads(CONFIG.read_text()).get("callsign", "")).strip()
            if args.kind != "node" and CALL.fullmatch(own_call.upper()) and value == base_call(own_call):
                raise ValueError("The local callsign cannot be banned.")
            now = int(time.time())
            data["bans"] = [row for row in data["bans"] if (row["kind"], row["value"]) != (args.kind, value)]
            data["bans"].append({"kind": args.kind, "value": value, "createdAt": now,
                                 "expiresAt": now + DURATIONS[args.duration] if DURATIONS[args.duration] else 0,
                                 "reason": args.reason[:160], "actor": args.actor[:80]})
            data["audit"].append({"action": "ban", "kind": args.kind, "value": value, "at": now,
                                  "expiresAt": data["bans"][-1]["expiresAt"], "actor": args.actor[:80],
                                  "reason": args.reason[:160]})
            data["audit"] = data["audit"][-250:]
            save_state(data)
        elif args.action == "unban":
            if not args.kind:
                raise ValueError("Choose a ban target.")
            value = check_node(args.value) if args.kind == "node" else base_call(args.value)
            data["bans"] = [row for row in data["bans"] if (row["kind"], row["value"]) != (args.kind, value)]
            data["audit"].append({"action": "unban", "kind": args.kind, "value": value, "at": int(time.time()), "actor": args.actor[:80]})
            data["audit"] = data["audit"][-250:]
            save_state(data)
        try:
            ami = AMI()
            try:
                applied = reconcile(data, ami, local)
            finally:
                ami.close()
        except Exception as exc:
            if args.action in ("ban", "unban"):
                raise RuntimeError(f"Ban record saved; native enforcement pending: {exc}") from exc
            raise
        print(json.dumps({"ok": True, "bans": data["bans"], "audit": data["audit"], "applied": applied, "localNode": local}))



if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        raise SystemExit(1)
