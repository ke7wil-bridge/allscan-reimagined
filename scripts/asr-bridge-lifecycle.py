#!/usr/bin/env python3
"""Own and retire dedicated AllScan Reimagined bridge resources safely.

ASR never infers ownership from a Settings card. A future bridge installer must
create a preflight intent before any resource, then use ``register-created``
after it has created and marked the exact dedicated resources. ``reconcile``
retires only an absent bridge carrying an exact, unexpired deletion intent. An
incomplete cleanup keeps the manifest and intent so a later reapply can retry.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


CONFIG_PATH = Path("/etc/allscan-reimagined/config.json")
STATE_ROOT = Path("/var/lib/allscan-reimagined")
MANIFEST_ROOT = STATE_ROOT / "bridge-ownership"
TOMBSTONE_ROOT = STATE_ROOT / "bridge-tombstones"
PENDING_ROOT = STATE_ROOT / "bridge-deletion-queue"
CREATION_INTENT_ROOT = STATE_ROOT / "bridge-creation-intents"
STATUS_PATH = Path("/run/allscan-reimagined/bridge-lifecycle.json")
LOCK_PATH = Path("/run/lock/allscan-reimagined-bridge-lifecycle.lock")
MQTT_SECRETS_PATH = Path("/etc/allscan-reimagined/bridge-mqtt-secrets.json")
RPT_PATH = Path("/etc/asterisk/rpt.conf")

ID_RE = re.compile(r"[a-z][a-z0-9_-]{1,31}")
UNIT_RE = re.compile(r"[a-zA-Z0-9_.@-]+\.(?:service|timer|socket|path)")
SHA_RE = re.compile(r"[a-f0-9]{64}")
TOPIC_RE = re.compile(r"[A-Za-z0-9_./-]{1,160}")
CREATION_RE = re.compile(r"[a-f0-9]{32}")
CREATED_BY = "allscan-reimagined-bridge-installer"
PROTECTED_UNITS = {
    "apache2.service",
    "asterisk.service",
    "mosquitto.service",
    "allscan-reimagined-reapply.service",
    "allscan-reimagined-fixed-bridge-recovery.service",
    "allscan-reimagined-fixed-bridge-recovery.timer",
    "allscan-reimagined-bridge-clients.service",
    "allscan-reimagined-bridge-clients.timer",
}
ALLOWED_MODES = {"dmr", "ysf", "zello", "p25", "nxdn", "m17"}
ALLOWED_ROLES = {"standard", "net"}
MAX_STDIN_BYTES = 64 * 1024
DELETION_INTENT_TTL = 10 * 60
CLEANUP_RETRY_TTL = 30 * 24 * 60 * 60
CREATION_INTENT_TTL = 30 * 60


class LifecycleError(RuntimeError):
    pass


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_digest(manifest: dict[str, Any]) -> str:
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def deletion_token(manifest: dict[str, Any]) -> str:
    bound = "\0".join(
        (manifest["bridgeId"], manifest["creationId"], manifest_digest(manifest))
    ).encode("utf-8")
    return hashlib.sha256(bound).hexdigest()


def read_bounded_stdin(stream: Any = sys.stdin) -> Any:
    raw = stream.buffer.read(MAX_STDIN_BYTES + 1) if hasattr(stream, "buffer") else stream.read(MAX_STDIN_BYTES + 1)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > MAX_STDIN_BYTES:
        raise LifecycleError("Lifecycle request is too large.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise LifecycleError("Lifecycle request must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise LifecycleError("Lifecycle request must be a JSON object.")
    return payload


def atomic_json(path: Path, payload: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def ensure_safe_state_root(path: Path, *, create: bool = False) -> None:
    parent = path.parent
    parent_details = parent.lstat()
    if not stat.S_ISDIR(parent_details.st_mode) or parent_details.st_uid != 0 or parent_details.st_mode & 0o022:
        raise LifecycleError(f"Lifecycle state parent is unsafe: {parent}")
    if not path.exists() and not path.is_symlink():
        if not create:
            raise LifecycleError(f"Required lifecycle state directory is missing: {path}")
        path.mkdir(mode=0o700)
        os.chmod(path, 0o700)
    details = path.lstat()
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != 0 or details.st_mode & 0o077:
        raise LifecycleError(f"Lifecycle state directory is not root-owned mode 0700: {path}")


def read_json_regular(path: Path, *, root_only: bool = False) -> Any:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise LifecycleError(f"Unsafe regular file: {path}")
        if root_only and (details.st_uid != 0 or details.st_mode & 0o022):
            raise LifecycleError(f"Unsafe owner or mode: {path}")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            return json.load(handle)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def require_keys_only(item: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(item) - allowed)
    if unknown:
        raise LifecycleError(f"Unknown {label} field(s): {', '.join(unknown)}")


def require_bridge_id(value: Any) -> str:
    bridge_id = str(value)
    if not ID_RE.fullmatch(bridge_id):
        raise LifecycleError("Invalid bridge ID in ownership manifest.")
    return bridge_id


def require_node(value: Any, label: str) -> str:
    node = str(value)
    if not re.fullmatch(r"[0-9]{1,10}", node) or int(node) <= 0:
        raise LifecycleError(f"Invalid {label} in ownership manifest.")
    return node


def require_absolute(value: Any, label: str) -> Path:
    path = Path(str(value))
    if not path.is_absolute() or ".." in path.parts:
        raise LifecycleError(f"Invalid {label} path.")
    return path


def ownership_payload(bridge_id: str, creation_id: str) -> dict[str, Any]:
    return {
        "bridgeId": bridge_id,
        "createdBy": CREATED_BY,
        "creationId": creation_id,
        "ownership": "asr",
        "schema": 1,
    }


def validate_manifest(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise LifecycleError("Ownership manifest must be a JSON object.")
    allowed = {
        "schema", "ownership", "createdBy", "creationId", "bridgeId", "mode", "role", "mainNode",
        "bridgeNode", "asteriskBlocks", "units", "ownedPaths",
        "runtimePaths", "credentialEntries", "firewallFiles", "listeners",
        "topics", "createdAt",
    }
    require_keys_only(payload, allowed, "manifest")
    if payload.get("schema") != 1 or payload.get("ownership") != "asr":
        raise LifecycleError("Unsupported bridge ownership manifest.")
    if payload.get("createdBy") != CREATED_BY or not CREATION_RE.fullmatch(str(payload.get("creationId", ""))):
        raise LifecycleError("Bridge creation provenance is missing or invalid.")
    if not isinstance(payload.get("createdAt"), int) or payload["createdAt"] <= 0:
        raise LifecycleError("Bridge creation time is missing or invalid.")
    creation_id = str(payload["creationId"])
    bridge_id = require_bridge_id(payload.get("bridgeId", ""))
    if payload.get("mode") not in ALLOWED_MODES or payload.get("role") not in ALLOWED_ROLES:
        raise LifecycleError("Invalid bridge mode or role in ownership manifest.")
    require_node(payload.get("mainNode", ""), "main node")
    require_node(payload.get("bridgeNode", ""), "bridge node")

    for block in payload.get("asteriskBlocks", []):
        if not isinstance(block, dict):
            raise LifecycleError("Invalid Asterisk block entry.")
        require_keys_only(block, {"path", "marker", "creationId", "sha256"}, "Asterisk block")
        if require_absolute(block.get("path", ""), "Asterisk") != RPT_PATH:
            raise LifecycleError("Only the marked ASR block in rpt.conf may be owned.")
        if block.get("marker") != bridge_id or block.get("creationId") != creation_id:
            raise LifecycleError("Asterisk block marker must match the bridge ID.")
        if not SHA_RE.fullmatch(str(block.get("sha256", ""))):
            raise LifecycleError("Asterisk ownership block lacks an immutable checksum.")
    if len(payload.get("asteriskBlocks", [])) != 1:
        raise LifecycleError("An ASR-owned managed bridge requires one marked Asterisk doorway block.")

    seen_units: set[str] = set()
    for unit in payload.get("units", []):
        if not isinstance(unit, dict):
            raise LifecycleError("Invalid unit entry.")
        require_keys_only(unit, {"name", "unitFile", "sha256", "ownerMarker"}, "unit")
        name = str(unit.get("name", ""))
        path = require_absolute(unit.get("unitFile", ""), "unit")
        checksum = str(unit.get("sha256", ""))
        if not UNIT_RE.fullmatch(name) or name in PROTECTED_UNITS:
            raise LifecycleError(f"Unsafe or shared systemd unit: {name}")
        if name in seen_units:
            raise LifecycleError(f"Duplicate owned unit: {name}")
        seen_units.add(name)
        if bridge_id not in name.lower().replace("@", "-"):
            raise LifecycleError(f"Unit is not bound to bridge {bridge_id}: {name}")
        if path != Path("/etc/systemd/system") / name or not SHA_RE.fullmatch(checksum):
            raise LifecycleError(f"Invalid owned unit metadata: {name}")
        if unit.get("ownerMarker") != f"# ASR-BRIDGE-OWNER: {bridge_id} {creation_id}":
            raise LifecycleError(f"Unit ownership marker is invalid: {name}")

    owned_root = Path("/opt/allscan-reimagined-bridges") / bridge_id
    owned_root_entries = 0
    seen_owned_paths: set[Path] = set()
    for entry in payload.get("ownedPaths", []):
        if not isinstance(entry, dict):
            raise LifecycleError("Invalid owned path entry.")
        require_keys_only(entry, {"path", "kind", "sha256", "marker"}, "owned path")
        path = require_absolute(entry.get("path", ""), "owned")
        kind = entry.get("kind")
        if path in seen_owned_paths:
            raise LifecycleError(f"Duplicate owned path: {path}")
        seen_owned_paths.add(path)
        if path != owned_root and owned_root not in path.parents:
            raise LifecycleError(f"Owned path is outside the dedicated bridge root: {path}")
        if kind == "file":
            if not SHA_RE.fullmatch(str(entry.get("sha256", ""))):
                raise LifecycleError(f"Owned file lacks a valid checksum: {path}")
        elif kind == "directory":
            marker = require_absolute(entry.get("marker", ""), "ownership marker")
            if marker != path / ".asr-bridge-owner.json":
                raise LifecycleError(f"Owned directory marker is invalid: {path}")
            if path == owned_root:
                owned_root_entries += 1
        else:
            raise LifecycleError(f"Invalid owned path kind: {kind}")
    if owned_root_entries != 1:
        raise LifecycleError("Every ASR-owned managed bridge needs one marked dedicated root directory.")

    seen_runtime_paths: set[Path] = set()
    for entry in payload.get("runtimePaths", []):
        if not isinstance(entry, dict):
            raise LifecycleError("Invalid runtime artifact entry.")
        require_keys_only(entry, {"path", "marker"}, "runtime artifact")
        path = require_absolute(entry.get("path", ""), "runtime")
        if not str(path).startswith("/run/allscan-reimagined") or bridge_id not in str(path):
            raise LifecycleError(f"Unsafe runtime path: {path}")
        if path in seen_runtime_paths:
            raise LifecycleError(f"Duplicate runtime path: {path}")
        seen_runtime_paths.add(path)
        marker = require_absolute(entry.get("marker", ""), "runtime marker")
        if marker.parent != path.parent or marker.name != ".asr-bridge-owner.json":
            raise LifecycleError(f"Runtime ownership marker is invalid: {path}")

    if len(payload.get("credentialEntries", [])) > 1:
        raise LifecycleError("A bridge manifest may own at most one credential entry.")
    for entry in payload.get("credentialEntries", []):
        if not isinstance(entry, dict):
            raise LifecycleError("Invalid credential entry.")
        require_keys_only(entry, {"path", "rootKey", "key", "createdBy", "creationId"}, "credential")
        if require_absolute(entry.get("path", ""), "credential") != MQTT_SECRETS_PATH:
            raise LifecycleError("Only the ASR bridge MQTT credential store is supported.")
        if entry.get("rootKey") != "bridges" or entry.get("key") != bridge_id:
            raise LifecycleError("Credential key must match the bridge ID.")
        if entry.get("createdBy") != CREATED_BY or entry.get("creationId") != creation_id:
            raise LifecycleError("Credential creation provenance does not match the bridge.")

    if len(payload.get("firewallFiles", [])) > 1:
        raise LifecycleError("A bridge manifest may own at most one firewall include.")
    for entry in payload.get("firewallFiles", []):
        if not isinstance(entry, dict):
            raise LifecycleError("Invalid firewall entry.")
        require_keys_only(
            entry,
            {"path", "sha256", "ownerMarker", "backend", "family", "table", "chain", "comment"},
            "firewall",
        )
        path = require_absolute(entry.get("path", ""), "firewall")
        expected = {Path(f"/etc/nftables.d/allscan-reimagined-bridge-{bridge_id}.nft")}
        if path not in expected or not SHA_RE.fullmatch(str(entry.get("sha256", ""))):
            raise LifecycleError(f"Unsafe firewall ownership entry: {path}")
        if entry.get("ownerMarker") != f"# ASR-BRIDGE-OWNER: {bridge_id} {creation_id}":
            raise LifecycleError(f"Firewall ownership marker is invalid: {path}")
        if entry.get("backend") != "nftables":
            raise LifecycleError("Only individually verifiable nftables rules may be ASR-owned.")
        for field in ("family", "table", "chain"):
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", str(entry.get(field, ""))):
                raise LifecycleError(f"Invalid nftables {field} in ownership manifest.")
        if entry.get("comment") != f"asr-bridge:{bridge_id}:{creation_id}":
            raise LifecycleError("The nftables rule comment does not match bridge creation provenance.")

    ports: set[tuple[str, int]] = set()
    for entry in payload.get("listeners", []):
        if not isinstance(entry, dict):
            raise LifecycleError("Invalid listener entry.")
        require_keys_only(entry, {"protocol", "port"}, "listener")
        protocol = entry.get("protocol")
        port = entry.get("port")
        if protocol not in {"tcp", "udp"} or not isinstance(port, int) or not 1 <= port <= 65535:
            raise LifecycleError("Invalid listener protocol or port.")
        if (protocol, port) in ports:
            raise LifecycleError("Duplicate listener in ownership manifest.")
        ports.add((protocol, port))

    for topic in payload.get("topics", []):
        if not TOPIC_RE.fullmatch(str(topic)):
            raise LifecycleError("Invalid MQTT topic in ownership manifest.")
    return payload


@dataclass
class RootMap:
    root: Path = Path("/")

    def path(self, original: Path) -> Path:
        return original if self.root == Path("/") else self.root / original.relative_to("/")


Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def default_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False, timeout=30)


def manifest_preview(manifest: dict[str, Any]) -> dict[str, Any]:
    resources: list[str] = []
    resources.extend(
        f"AllStar doorway {manifest['bridgeNode']}" for _ in manifest.get("asteriskBlocks", [])
    )
    resources.extend(f"Service {entry['name']}" for entry in manifest.get("units", []))
    resources.extend(
        f"Managed {entry['kind']} {Path(entry['path']).name}"
        for entry in manifest.get("ownedPaths", [])
    )
    resources.extend(
        f"Runtime record {Path(entry['path']).name}" for entry in manifest.get("runtimePaths", [])
    )
    resources.extend(
        f"Credential entry {entry['key']}" for entry in manifest.get("credentialEntries", [])
    )
    resources.extend(
        f"Firewall rule {entry['comment']}" for entry in manifest.get("firewallFiles", [])
    )
    return {
        "bridgeId": manifest["bridgeId"],
        "creationId": manifest["creationId"],
        "manifestDigest": manifest_digest(manifest),
        "owned": True,
        "role": manifest["role"],
        "mode": manifest["mode"],
        "deletionToken": deletion_token(manifest),
        "message": "ASR owns this managed bridge and will remove only the dedicated resources listed below.",
        "categories": [
            {"label": "AllStar doorway", "count": len(manifest.get("asteriskBlocks", []))},
            {"label": "Dedicated services", "count": len(manifest.get("units", []))},
            {"label": "Dedicated configuration", "count": len(manifest.get("ownedPaths", []))},
            {"label": "Runtime and status data", "count": len(manifest.get("runtimePaths", []))},
            {"label": "Credential entries", "count": len(manifest.get("credentialEntries", []))},
            {"label": "Firewall rules", "count": len(manifest.get("firewallFiles", []))},
        ],
        "resources": resources,
        "willNotTouch": [
            "Pre-existing or manually installed services and files",
            "Unmarked Asterisk configuration",
            "Shared bridge software and system packages",
            "Unregistered firewall rules and listeners",
        ],
    }


def marked_block(
    path: Path, bridge_id: str, creation_id: str, *, require_root: bool = True,
) -> tuple[list[str], list[str]] | None:
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise LifecycleError(f"Asterisk configuration is not a safe regular file: {path}")
    details = path.stat()
    if (require_root and details.st_uid != 0) or details.st_mode & 0o002:
        raise LifecycleError(f"Asterisk configuration ownership or mode is unsafe: {path}")
    begin = f"; BEGIN ASR BRIDGE {bridge_id} {creation_id}"
    end = f"; END ASR BRIDGE {bridge_id} {creation_id}"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    output: list[str] = []
    owned: list[str] = []
    inside = False
    found = False
    for line in lines:
        stripped = line.rstrip("\r\n")
        if stripped == begin:
            if inside:
                raise LifecycleError(f"Nested ASR ownership block in {path}")
            inside = True
            found = True
            owned.append(line)
            continue
        if stripped == end:
            if not inside:
                raise LifecycleError(f"Unmatched ASR ownership marker in {path}")
            inside = False
            owned.append(line)
            continue
        if inside:
            owned.append(line)
        else:
            output.append(line)
    if inside:
        raise LifecycleError(f"Unclosed ASR ownership block in {path}")
    return (output, owned) if found else None


def remove_marked_block(
    path: Path, bridge_id: str, creation_id: str, expected_sha256: str,
    *, require_root: bool = True,
) -> bool:
    found = marked_block(path, bridge_id, creation_id, require_root=require_root)
    if found is None:
        return False
    output, owned = found
    actual = hashlib.sha256("".join(owned).encode("utf-8")).hexdigest()
    if actual != expected_sha256:
        raise LifecycleError("The ASR-owned Asterisk block changed and was preserved.")
    details = path.stat()
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.asr-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("".join(output))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(details.st_mode))
        os.chown(temporary, details.st_uid, details.st_gid)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def exact_file_remove(
    path: Path, checksum: str, label: str, errors: list[str], required_marker: str = ""
) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_file():
        errors.append(f"{label} is no longer a safe regular file: {path}")
        return
    if sha256_path(path) != checksum:
        errors.append(f"{label} changed after registration and was preserved: {path}")
        return
    if required_marker:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{label} ownership marker could not be verified: {path}: {exc}")
            return
        if required_marker not in content.splitlines():
            errors.append(f"{label} lacks its immutable ownership marker and was preserved: {path}")
            return
    path.unlink()


def exact_file_is_owned(path: Path, checksum: str, required_marker: str = "") -> bool:
    if path.is_symlink() or not path.is_file() or sha256_path(path) != checksum:
        return False
    if not required_marker:
        return True
    try:
        return required_marker in path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return False


def remove_owned_directory(
    path: Path, marker: Path, bridge_id: str, creation_id: str, errors: list[str]
) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_dir():
        errors.append(f"Owned directory is unsafe and was preserved: {path}")
        return
    try:
        marker_payload = read_json_regular(marker, root_only=False)
    except (OSError, ValueError, LifecycleError) as exc:
        errors.append(f"Owned directory marker is unavailable; preserved {path}: {exc}")
        return
    if marker_payload != {
        "bridgeId": bridge_id,
        "createdBy": CREATED_BY,
        "creationId": creation_id,
        "ownership": "asr",
        "schema": 1,
    }:
        errors.append(f"Owned directory marker does not match; preserved {path}")
        return
    remaining = [item for item in path.iterdir() if item != marker]
    if remaining:
        errors.append(
            f"Owned directory contains unregistered content and was preserved: {path}"
        )
        return
    marker.unlink()
    path.rmdir()


def update_credentials(
    path: Path, bridge_id: str, creation_id: str, errors: list[str]
) -> None:
    if not path.exists():
        return
    try:
        payload = read_json_regular(path, root_only=path == MQTT_SECRETS_PATH and os.geteuid() == 0)
        if not isinstance(payload, dict) or not isinstance(payload.get("bridges", {}), dict):
            raise LifecycleError("Credential store schema is invalid.")
        if bridge_id not in payload["bridges"]:
            return
        entry = payload["bridges"][bridge_id]
        expected = {
            "bridgeId": bridge_id,
            "createdBy": CREATED_BY,
            "creationId": creation_id,
            "ownership": "asr",
            "schema": 1,
        }
        if not isinstance(entry, dict) or entry.get("asrOwnership") != expected:
            errors.append("Bridge credential entry does not prove ASR ownership and was preserved.")
            return
        del payload["bridges"][bridge_id]
        atomic_json(path, payload, 0o600)
    except (OSError, ValueError, LifecycleError) as exc:
        errors.append(f"Credential entry remains: {exc}")


def configured_bridge_ids(config_path: Path) -> set[str]:
    payload = read_json_regular(config_path, root_only=False)
    if not isinstance(payload, dict) or not isinstance(payload.get("bridges", []), list):
        raise LifecycleError("ASR bridge configuration is invalid.")
    ids: set[str] = set()
    for bridge in payload["bridges"]:
        if isinstance(bridge, dict) and ID_RE.fullmatch(str(bridge.get("id", ""))):
            ids.add(str(bridge["id"]))
    return ids


def established_nodes(output: str) -> set[str]:
    nodes: set[str] = set()
    for line in output.splitlines():
        fields = line.split()
        if fields and fields[0].isdigit() and fields[-1] == "ESTABLISHED":
            nodes.add(fields[0])
    return nodes


def listener_is_present(protocol: str, port: int, runner: Runner) -> bool:
    result = runner(["/usr/bin/ss", "-H", "-lntup"])
    if result.returncode != 0:
        raise LifecycleError("Could not inspect active listeners.")
    pattern = re.compile(rf"(?:\[.*\]|[0-9a-fA-F:.]+):{port}(?:\s|$)")
    for line in result.stdout.splitlines():
        fields = line.split()
        if not fields or fields[0].lower() != protocol:
            continue
        if pattern.search(line):
            return True
    return False


def nftables_rule_handles(entry: dict[str, Any], runner: Runner) -> list[int]:
    list_command = [
        "/usr/sbin/nft", "-a", "-j", "list", "chain",
        str(entry["family"]), str(entry["table"]), str(entry["chain"]),
    ]
    result = runner(list_command)
    if result.returncode != 0:
        raise LifecycleError("The ASR-owned nftables chain could not be inspected.")
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise LifecycleError("The nftables rule listing was invalid.")
    handles: list[int] = []
    for item in payload.get("nftables", []) if isinstance(payload, dict) else []:
        rule = item.get("rule") if isinstance(item, dict) else None
        if (
            not isinstance(rule, dict)
            or rule.get("comment") != entry["comment"]
            or rule.get("family") != entry["family"]
            or rule.get("table") != entry["table"]
            or rule.get("chain") != entry["chain"]
        ):
            continue
        handle = rule.get("handle")
        if isinstance(handle, int) and handle > 0:
            handles.append(handle)
    return handles


def remove_nftables_rule(entry: dict[str, Any], runner: Runner, errors: list[str]) -> bool:
    try:
        handles = nftables_rule_handles(entry, runner)
    except LifecycleError as exc:
        errors.append(f"{exc} Its rule was preserved.")
        return False
    if len(handles) > 1:
        errors.append("Multiple nftables rules claim the bridge creation ID; none were removed.")
        return False
    if not handles:
        return True
    delete = runner([
        "/usr/sbin/nft", "delete", "rule", str(entry["family"]),
        str(entry["table"]), str(entry["chain"]), "handle", str(handles[0]),
    ])
    if delete.returncode != 0:
        errors.append("The proven ASR-owned nftables rule could not be removed.")
        return False
    try:
        remaining = nftables_rule_handles(entry, runner)
    except LifecycleError as exc:
        errors.append(f"The nftables rule removal could not be verified: {exc}")
        return False
    if remaining:
        errors.append("The proven ASR-owned nftables rule remains active.")
        return False
    return True


def cleanup_manifest(
    manifest: dict[str, Any], *, root_map: RootMap, runner: Runner
) -> dict[str, Any]:
    bridge_id = manifest["bridgeId"]
    errors: list[str] = []
    removed: list[str] = []
    changed_asterisk = False

    block_entry = manifest["asteriskBlocks"][0]
    block_path = root_map.path(Path(block_entry["path"]))
    try:
        block_present = marked_block(
            block_path, bridge_id, manifest["creationId"], require_root=root_map.root == Path("/")
        ) is not None
    except (OSError, LifecycleError) as exc:
        block_present = False
        errors.append(f"Asterisk doorway ownership cannot be proven: {exc}")
    link_result = runner(["/usr/sbin/asterisk", "-rx", f"rpt lstats {manifest['mainNode']}"])
    if link_result.returncode != 0:
        errors.append("The main AllStar link table could not be read; unlink was not attempted.")
        link_present = False
        unlink_ok = False
    else:
        link_present = manifest["bridgeNode"] in established_nodes(link_result.stdout)
        unlink_ok = not link_present
    if link_present:
        if not block_present:
            errors.append("The bridge is linked, but its Asterisk doorway ownership cannot be proven; link was preserved.")
        else:
            unlink = runner(["/usr/sbin/asterisk", "-rx", f"rpt fun {manifest['mainNode']} *1{manifest['bridgeNode']}"])
            unlink_ok = unlink.returncode == 0
            if not unlink_ok:
                errors.append("The AllStar bridge link could not be disconnected.")

    for unit in manifest.get("units", []):
        name = unit["name"]
        unit_path = root_map.path(Path(unit["unitFile"]))
        if not unit_path.exists() and not unit_path.is_symlink():
            removed.append(f"unit:{name}")
            continue
        if not exact_file_is_owned(unit_path, unit["sha256"], str(unit["ownerMarker"])):
            errors.append(f"Dedicated unit ownership cannot be proven; unit was not touched: {name}")
            continue
        stop = runner(["/usr/bin/systemctl", "disable", "--now", name])
        if stop.returncode != 0:
            errors.append(f"Dedicated unit could not be stopped: {name}")
            continue
        runner(["/usr/bin/systemctl", "reset-failed", name])
        exact_file_remove(unit_path, unit["sha256"], "Unit file", errors, str(unit["ownerMarker"]))
        if not unit_path.exists():
            removed.append(f"unit:{name}")

    if block_present and unlink_ok:
        try:
            if remove_marked_block(
                block_path, bridge_id, manifest["creationId"], str(block_entry["sha256"]),
                require_root=root_map.root == Path("/"),
            ):
                changed_asterisk = True
                removed.append("asterisk-doorway")
        except (OSError, LifecycleError) as exc:
            errors.append(f"Asterisk doorway remains: {exc}")

    for entry in manifest.get("credentialEntries", []):
        before_errors = len(errors)
        update_credentials(root_map.path(Path(entry["path"])), bridge_id, manifest["creationId"], errors)
        if len(errors) == before_errors:
            removed.append("credential-entry")

    for entry in manifest.get("firewallFiles", []):
        path = root_map.path(Path(entry["path"]))
        if path.exists() or path.is_symlink():
            if not exact_file_is_owned(path, entry["sha256"], str(entry["ownerMarker"])):
                errors.append(f"Firewall include ownership cannot be proven; rule and file were preserved: {path}")
                continue
        rule_removed = remove_nftables_rule(entry, runner, errors)
        if rule_removed:
            exact_file_remove(path, entry["sha256"], "Firewall include", errors, str(entry["ownerMarker"]))
        if not path.exists():
            removed.append("firewall-rule")

    runtime_groups: dict[Path, list[Path]] = {}
    for entry in manifest.get("runtimePaths", []):
        marker = root_map.path(Path(entry["marker"]))
        runtime_groups.setdefault(marker, []).append(root_map.path(Path(entry["path"])))
    for marker, paths in runtime_groups.items():
        any_runtime_state = marker.exists() or marker.is_symlink() or any(
            path.exists() or path.is_symlink() for path in paths
        )
        if not any_runtime_state:
            removed.append("runtime-status")
            continue
        try:
            marker_payload = read_json_regular(marker, root_only=False)
            if marker_payload != ownership_payload(bridge_id, manifest["creationId"]):
                errors.append(f"Runtime/status ownership cannot be proven; preserved {marker.parent}")
                continue
            unsafe = [path for path in paths if path.is_symlink() or (path.exists() and not path.is_file())]
            if unsafe:
                errors.extend(f"Runtime/status artifact is unsafe and was preserved: {path}" for path in unsafe)
                continue
            for path in paths:
                path.unlink(missing_ok=True)
            remaining = [item for item in marker.parent.iterdir() if item != marker]
            if remaining:
                errors.append(f"Runtime directory contains unregistered content and was preserved: {marker.parent}")
                continue
            marker.unlink()
            marker.parent.rmdir()
            removed.append("runtime-status")
        except (OSError, ValueError, LifecycleError) as exc:
            errors.append(f"Runtime/status artifact remains in {marker.parent}: {exc}")

    files = [entry for entry in manifest.get("ownedPaths", []) if entry["kind"] == "file"]
    directories = sorted(
        [entry for entry in manifest.get("ownedPaths", []) if entry["kind"] == "directory"],
        key=lambda entry: len(Path(entry["path"]).parts), reverse=True,
    )
    dedicated_root = next(entry for entry in directories if Path(entry["path"]) == Path("/opt/allscan-reimagined-bridges") / bridge_id)
    any_owned_path_remains = any(
        root_map.path(Path(entry["path"])).exists() or root_map.path(Path(entry["path"])).is_symlink()
        for entry in manifest.get("ownedPaths", [])
    )
    root_marker = root_map.path(Path(dedicated_root["marker"]))
    try:
        root_marker_payload = read_json_regular(root_marker, root_only=False)
    except (OSError, ValueError, LifecycleError):
        root_marker_payload = None
    expected_marker = {
        "bridgeId": bridge_id, "createdBy": CREATED_BY,
        "creationId": manifest["creationId"], "ownership": "asr", "schema": 1,
    }
    if not any_owned_path_remains:
        removed.extend(["owned-config", "owned-directory"])
    elif root_marker_payload != expected_marker:
        errors.append("The dedicated bridge root no longer proves ASR ownership; all bridge files were preserved.")
    else:
        for entry in files:
            path = root_map.path(Path(entry["path"]))
            exact_file_remove(path, entry["sha256"], "Owned file", errors)
            if not path.exists():
                removed.append("owned-config")
        for entry in directories:
            path = root_map.path(Path(entry["path"]))
            marker = root_map.path(Path(entry["marker"]))
            remove_owned_directory(path, marker, bridge_id, manifest["creationId"], errors)
            if not path.exists():
                removed.append("owned-directory")

    daemon_reload = runner(["/usr/bin/systemctl", "daemon-reload"])
    if daemon_reload.returncode != 0:
        errors.append("systemd daemon-reload failed; unit removal is not verified.")
    if changed_asterisk:
        reload_result = runner(["/usr/sbin/asterisk", "-rx", "module reload app_rpt.so"])
        if reload_result.returncode != 0:
            errors.append("Asterisk could not reload the updated doorway configuration.")

    verify_link = runner(["/usr/sbin/asterisk", "-rx", f"rpt lstats {manifest['mainNode']}"])
    if verify_link.returncode != 0:
        errors.append("The final AllStar link state could not be verified.")
    elif manifest["bridgeNode"] in established_nodes(verify_link.stdout):
        errors.append("The deleted bridge is still linked to the main node.")
    try:
        if marked_block(
            block_path, bridge_id, manifest["creationId"], require_root=root_map.root == Path("/")
        ) is not None:
            errors.append("The exact ASR-owned Asterisk doorway block remains.")
    except (OSError, LifecycleError) as exc:
        errors.append(f"The final Asterisk doorway state could not be verified: {exc}")
    for unit in manifest.get("units", []):
        unit_path = root_map.path(Path(unit["unitFile"]))
        check = runner(["/usr/bin/systemctl", "show", unit["name"], "-p", "LoadState", "-p", "ActiveState", "-p", "SubState", "-p", "Result"])
        if check.returncode != 0 and "LoadState=not-found" not in check.stdout:
            errors.append(f"Dedicated unit state could not be verified: {unit['name']}")
        elif "LoadState=not-found" not in check.stdout:
            errors.append(f"Dedicated unit definition remains loaded: {unit['name']}")
        if any(value in check.stdout for value in ("ActiveState=active", "SubState=failed", "Result=failed")):
            errors.append(f"Dedicated unit is still active or failed: {unit['name']}")
        if unit_path.exists() or unit_path.is_symlink():
            errors.append(f"Dedicated unit file remains: {unit_path}")
    if root_map.root == Path("/"):
        for listener in manifest.get("listeners", []):
            try:
                if listener_is_present(listener["protocol"], listener["port"], runner):
                    errors.append(f"Listener remains on {listener['protocol'].upper()} port {listener['port']}.")
            except LifecycleError as exc:
                errors.append(str(exc))

    return {
        "bridgeId": bridge_id,
        "ok": not errors,
        "state": "removed" if not errors else "incomplete",
        "removedCategories": sorted(set(removed)),
        "remaining": errors,
    }


def load_manifests(root: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    loaded: dict[str, tuple[Path, dict[str, Any]]] = {}
    if root == MANIFEST_ROOT:
        ensure_safe_state_root(root)
    if not root.exists():
        return loaded
    for path in sorted(root.glob("*.json")):
        manifest = validate_manifest(read_json_regular(path, root_only=os.geteuid() == 0))
        bridge_id = manifest["bridgeId"]
        if path.name != f"{bridge_id}.json" or bridge_id in loaded:
            raise LifecycleError(f"Ambiguous ownership manifest: {path}")
        loaded[bridge_id] = (path, manifest)
    return loaded


def preview_all(manifest_root: Path = MANIFEST_ROOT) -> dict[str, Any]:
    previews: dict[str, Any] = {}
    for bridge_id, (_, manifest) in load_manifests(manifest_root).items():
        previews[bridge_id] = manifest_preview(manifest)
    return {"ok": True, "bridges": previews}


def lifecycle_status(status_path: Path = STATUS_PATH) -> dict[str, Any]:
    if not status_path.exists():
        return {"ok": True, "updatedEpoch": 0, "pending": 0, "results": []}
    payload = read_json_regular(status_path, root_only=False)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise LifecycleError("Bridge lifecycle status is invalid.")
    return payload


def queue_deletion(
    request: dict[str, Any], *, manifest_root: Path = MANIFEST_ROOT,
    pending_root: Path = PENDING_ROOT, now: int | None = None,
) -> dict[str, Any]:
    require_keys_only(
        request,
        {"bridgeId", "creationId", "manifestDigest", "deletionToken"},
        "deletion request",
    )
    bridge_id = require_bridge_id(request.get("bridgeId", ""))
    manifests = load_manifests(manifest_root)
    if bridge_id not in manifests:
        raise LifecycleError("No current ASR ownership manifest exists for this bridge.")
    _, manifest = manifests[bridge_id]
    digest = manifest_digest(manifest)
    expected = {
        "bridgeId": bridge_id,
        "creationId": manifest["creationId"],
        "manifestDigest": digest,
        "deletionToken": deletion_token(manifest),
    }
    if request != expected:
        raise LifecycleError("Deletion confirmation does not match the current ownership manifest.")
    created_at = int(time.time()) if now is None else now
    pending = {
        "schema": 1,
        **expected,
        "state": "authorized",
        "createdAt": created_at,
        "expiresAt": created_at + DELETION_INTENT_TTL,
    }
    if pending_root == PENDING_ROOT:
        ensure_safe_state_root(pending_root, create=True)
    else:
        pending_root.mkdir(parents=True, exist_ok=True)
    atomic_json(pending_root / f"{bridge_id}.json", pending, 0o600)
    return {
        "ok": True,
        "bridgeId": bridge_id,
        "creationId": manifest["creationId"],
        "manifestDigest": digest,
        "expiresAt": pending["expiresAt"],
        "queued": True,
    }


def validate_pending_deletion(
    path: Path, manifest: dict[str, Any], *, now: int | None = None,
) -> dict[str, Any]:
    payload = read_json_regular(path, root_only=os.geteuid() == 0)
    if not isinstance(payload, dict):
        raise LifecycleError("Pending deletion record is invalid.")
    require_keys_only(
        payload,
        {
            "schema", "bridgeId", "creationId", "manifestDigest", "deletionToken",
            "state", "createdAt", "lastAttemptAt", "expiresAt",
        },
        "pending deletion",
    )
    current_time = int(time.time()) if now is None else now
    expected = {
        "schema": 1,
        "bridgeId": manifest["bridgeId"],
        "creationId": manifest["creationId"],
        "manifestDigest": manifest_digest(manifest),
        "deletionToken": deletion_token(manifest),
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise LifecycleError(f"Pending deletion {key} does not match the ownership manifest.")
    if not isinstance(payload.get("createdAt"), int) or not isinstance(payload.get("expiresAt"), int):
        raise LifecycleError("Pending deletion time is invalid.")
    if payload.get("state") not in {"authorized", "incomplete"}:
        raise LifecycleError("Pending deletion state is invalid.")
    if payload.get("state") == "authorized" and "lastAttemptAt" in payload:
        raise LifecycleError("Pending deletion authorization has unexpected retry state.")
    if payload.get("state") == "incomplete" and not isinstance(payload.get("lastAttemptAt"), int):
        raise LifecycleError("Pending deletion retry time is invalid.")
    if payload["expiresAt"] <= payload["createdAt"] or payload["expiresAt"] <= current_time:
        raise LifecycleError("Pending deletion confirmation expired; the bridge was preserved.")
    return payload


def preflight_creation(
    request: dict[str, Any], *, intent_root: Path = CREATION_INTENT_ROOT,
    manifest_root: Path = MANIFEST_ROOT, now: int | None = None,
    root_map: RootMap = RootMap(),
) -> dict[str, Any]:
    require_keys_only(
        request,
        {
            "bridgeId", "mode", "role", "mainNode", "bridgeNode", "units",
            "runtimePaths", "firewallFiles", "credentialEntry",
        },
        "creation preflight",
    )
    bridge_id = require_bridge_id(request.get("bridgeId", ""))
    if request.get("mode") not in ALLOWED_MODES or request.get("role") not in ALLOWED_ROLES:
        raise LifecycleError("Invalid bridge mode or role in creation preflight.")
    main_node = require_node(request.get("mainNode", ""), "main node")
    bridge_node = require_node(request.get("bridgeNode", ""), "bridge node")
    if (manifest_root / f"{bridge_id}.json").exists():
        raise LifecycleError("An ownership manifest already exists for this bridge.")
    dedicated_root = root_map.path(Path("/opt/allscan-reimagined-bridges") / bridge_id)
    if dedicated_root.exists() or dedicated_root.is_symlink():
        raise LifecycleError("Pre-existing bridge resources cannot be adopted by an ASR creation transaction.")
    planned_units = request.get("units")
    planned_runtime = request.get("runtimePaths")
    planned_firewall = request.get("firewallFiles")
    if not all(isinstance(value, list) and len(value) <= 64 for value in (planned_units, planned_runtime, planned_firewall)):
        raise LifecycleError("Creation preflight requires bounded exact resource lists.")
    unit_names: list[str] = []
    for value in planned_units:
        name = str(value)
        if not UNIT_RE.fullmatch(name) or name in PROTECTED_UNITS or bridge_id not in name.lower().replace("@", "-"):
            raise LifecycleError(f"Unsafe planned unit: {name}")
        path = root_map.path(Path("/etc/systemd/system") / name)
        if path.exists() or path.is_symlink():
            raise LifecycleError(f"Pre-existing unit cannot be adopted: {name}")
        unit_names.append(name)
    if len(set(unit_names)) != len(unit_names):
        raise LifecycleError("Creation preflight contains a duplicate unit.")
    runtime_paths: list[str] = []
    for value in planned_runtime:
        path = require_absolute(value, "planned runtime")
        if not str(path).startswith("/run/allscan-reimagined") or bridge_id not in str(path):
            raise LifecycleError(f"Unsafe planned runtime path: {path}")
        mapped = root_map.path(path)
        marker = mapped.parent / ".asr-bridge-owner.json"
        if mapped.exists() or mapped.is_symlink() or marker.exists() or marker.is_symlink():
            raise LifecycleError(f"Pre-existing runtime resource cannot be adopted: {path}")
        runtime_paths.append(str(path))
    if len(set(runtime_paths)) != len(runtime_paths):
        raise LifecycleError("Creation preflight contains a duplicate runtime path.")
    firewall_paths: list[str] = []
    for value in planned_firewall:
        path = require_absolute(value, "planned firewall")
        if path != Path(f"/etc/nftables.d/allscan-reimagined-bridge-{bridge_id}.nft"):
            raise LifecycleError(f"Unsafe planned firewall file: {path}")
        mapped = root_map.path(path)
        if mapped.exists() or mapped.is_symlink():
            raise LifecycleError(f"Pre-existing firewall resource cannot be adopted: {path}")
        firewall_paths.append(str(path))
    if len(set(firewall_paths)) != len(firewall_paths) or len(firewall_paths) > 1:
        raise LifecycleError("Creation preflight contains duplicate or unsupported firewall files.")
    credential_entry = request.get("credentialEntry")
    if not isinstance(credential_entry, bool):
        raise LifecycleError("Creation preflight credential reservation must be true or false.")
    if credential_entry:
        credential_path = root_map.path(MQTT_SECRETS_PATH)
        if credential_path.exists() or credential_path.is_symlink():
            credentials = read_json_regular(credential_path, root_only=False)
            entries = credentials.get("bridges") if isinstance(credentials, dict) else None
            if not isinstance(entries, dict):
                raise LifecycleError("The bridge credential store is invalid.")
            if bridge_id in entries:
                raise LifecycleError("A pre-existing credential entry cannot be adopted.")
    created_at = int(time.time()) if now is None else now
    intent = {
        "schema": 1,
        "bridgeId": bridge_id,
        "mode": request["mode"],
        "role": request["role"],
        "mainNode": main_node,
        "bridgeNode": bridge_node,
        "creationId": secrets.token_hex(16),
        "units": sorted(unit_names),
        "runtimePaths": sorted(runtime_paths),
        "firewallFiles": sorted(firewall_paths),
        "credentialEntry": credential_entry,
        "createdAt": created_at,
        "expiresAt": created_at + CREATION_INTENT_TTL,
    }
    if intent_root == CREATION_INTENT_ROOT:
        ensure_safe_state_root(intent_root, create=True)
    else:
        intent_root.mkdir(parents=True, exist_ok=True)
    destination = intent_root / f"{bridge_id}.json"
    if destination.exists():
        raise LifecycleError("A bridge creation preflight is already pending.")
    atomic_json(destination, intent, 0o600)
    return {"ok": True, **intent}


def verify_created_resources(
    manifest: dict[str, Any], root_map: RootMap = RootMap(), runner: Runner = default_runner,
) -> None:
    """Prove creation markers before accepting a new ownership manifest."""
    bridge_id = manifest["bridgeId"]
    creation_id = manifest["creationId"]
    for unit in manifest.get("units", []):
        path = root_map.path(Path(unit["unitFile"]))
        if path.is_symlink() or not path.is_file() or sha256_path(path) != unit["sha256"]:
            raise LifecycleError(f"Created unit cannot be proven: {unit['name']}")
        if unit["ownerMarker"] not in path.read_text(encoding="utf-8").splitlines():
            raise LifecycleError(f"Created unit lacks immutable provenance: {unit['name']}")
    for entry in manifest.get("firewallFiles", []):
        path = root_map.path(Path(entry["path"]))
        if path.is_symlink() or not path.is_file() or sha256_path(path) != entry["sha256"]:
            raise LifecycleError(f"Created firewall include cannot be proven: {path}")
        if entry["ownerMarker"] not in path.read_text(encoding="utf-8").splitlines():
            raise LifecycleError(f"Created firewall include lacks immutable provenance: {path}")
        if root_map.root == Path("/"):
            handles = nftables_rule_handles(entry, runner)
            if len(handles) != 1:
                raise LifecycleError("Created firewall rule is not uniquely active with its immutable provenance.")
    for entry in manifest.get("ownedPaths", []):
        path = root_map.path(Path(entry["path"]))
        if entry["kind"] == "file":
            if path.is_symlink() or not path.is_file() or sha256_path(path) != entry["sha256"]:
                raise LifecycleError(f"Created bridge file cannot be proven: {path}")
        else:
            marker = root_map.path(Path(entry["marker"]))
            if path.is_symlink() or not path.is_dir():
                raise LifecycleError(f"Created bridge directory cannot be proven: {path}")
            payload = read_json_regular(marker, root_only=root_map.root == Path("/") and os.geteuid() == 0)
            expected = {
                "bridgeId": bridge_id, "createdBy": CREATED_BY,
                "creationId": creation_id, "ownership": "asr", "schema": 1,
            }
            if payload != expected:
                raise LifecycleError(f"Created bridge directory lacks immutable provenance: {path}")
    for block in manifest.get("asteriskBlocks", []):
        path = root_map.path(Path(block["path"]))
        found = marked_block(path, bridge_id, creation_id, require_root=root_map.root == Path("/"))
        if found is None:
            raise LifecycleError("Created Asterisk doorway lacks immutable provenance.")
        _, owned = found
        if hashlib.sha256("".join(owned).encode("utf-8")).hexdigest() != block["sha256"]:
            raise LifecycleError("Created Asterisk doorway checksum does not match its manifest.")
    for entry in manifest.get("runtimePaths", []):
        path = root_map.path(Path(entry["path"]))
        marker = root_map.path(Path(entry["marker"]))
        if path.is_symlink() or not path.is_file():
            raise LifecycleError(f"Created runtime record cannot be proven: {path}")
        marker_payload = read_json_regular(marker, root_only=False)
        if marker_payload != ownership_payload(bridge_id, creation_id):
            raise LifecycleError(f"Created runtime record lacks immutable provenance: {path}")
    for entry in manifest.get("credentialEntries", []):
        path = root_map.path(Path(entry["path"]))
        payload = read_json_regular(path, root_only=False)
        bridges = payload.get("bridges") if isinstance(payload, dict) else None
        credential = bridges.get(bridge_id) if isinstance(bridges, dict) else None
        if not isinstance(credential, dict) or credential.get("asrOwnership") != ownership_payload(bridge_id, creation_id):
            raise LifecycleError("Created credential entry lacks immutable provenance.")


def reconcile(
    *, config_path: Path = CONFIG_PATH, manifest_root: Path = MANIFEST_ROOT,
    tombstone_root: Path = TOMBSTONE_ROOT, pending_root: Path = PENDING_ROOT,
    status_path: Path = STATUS_PATH, now: int | None = None,
    root_map: RootMap = RootMap(), runner: Runner = default_runner,
) -> dict[str, Any]:
    active = configured_bridge_ids(config_path)
    manifests = load_manifests(manifest_root)
    if pending_root == PENDING_ROOT:
        ensure_safe_state_root(pending_root)
    results: list[dict[str, Any]] = []
    for bridge_id, (path, manifest) in manifests.items():
        if bridge_id in active:
            continue
        pending_path = pending_root / f"{bridge_id}.json"
        if not pending_path.exists() and not pending_path.is_symlink():
            results.append({
                "bridgeId": bridge_id,
                "ok": False,
                "state": "intent-required",
                "removedCategories": [],
                "remaining": [
                    "Ownership manifest is orphaned without an exact pending deletion intent; all resources were preserved."
                ],
            })
            continue
        try:
            pending = validate_pending_deletion(pending_path, manifest, now=now)
        except (OSError, ValueError, LifecycleError) as exc:
            results.append({
                "bridgeId": bridge_id,
                "ok": False,
                "state": "intent-invalid",
                "removedCategories": [],
                "remaining": [str(exc)],
            })
            continue
        retry_time = int(time.time()) if now is None else now
        pending["state"] = "incomplete"
        pending["lastAttemptAt"] = retry_time
        pending["expiresAt"] = retry_time + CLEANUP_RETRY_TTL
        atomic_json(pending_path, pending, 0o600)
        result = cleanup_manifest(manifest, root_map=root_map, runner=runner)
        results.append(result)
        if result["ok"]:
            if tombstone_root == TOMBSTONE_ROOT:
                ensure_safe_state_root(tombstone_root, create=True)
            else:
                tombstone_root.mkdir(parents=True, exist_ok=True)
            atomic_json(
                tombstone_root / f"{bridge_id}.json",
                {
                    "schema": 1, "bridgeId": bridge_id,
                    "creationId": manifest["creationId"],
                    "manifestDigest": manifest_digest(manifest),
                    "deletedAt": int(time.time()) if now is None else now,
                },
                0o600,
            )
            path.unlink()
            pending_path.unlink()
    payload = {
        "ok": all(result["ok"] for result in results),
        "updatedEpoch": int(time.time()),
        "pending": sum(1 for result in results if not result["ok"]),
        "results": results,
    }
    atomic_json(status_path, payload, 0o640)
    return payload


def register_created_manifest(
    manifest: dict[str, Any], *, manifest_root: Path = MANIFEST_ROOT,
    tombstone_root: Path = TOMBSTONE_ROOT, intent_root: Path = CREATION_INTENT_ROOT,
    now: int | None = None, runner: Runner = default_runner,
    root_map: RootMap = RootMap(),
) -> dict[str, Any]:
    manifest = validate_manifest(manifest)
    bridge_id = manifest["bridgeId"]
    if intent_root == CREATION_INTENT_ROOT:
        ensure_safe_state_root(intent_root)
    intent_path = intent_root / f"{bridge_id}.json"
    intent = read_json_regular(intent_path, root_only=root_map.root == Path("/") and os.geteuid() == 0)
    if not isinstance(intent, dict):
        raise LifecycleError("Bridge creation preflight is invalid.")
    require_keys_only(
        intent,
        {
            "schema", "bridgeId", "mode", "role", "mainNode", "bridgeNode",
            "creationId", "units", "runtimePaths", "firewallFiles", "credentialEntry",
            "createdAt", "expiresAt",
        },
        "creation intent",
    )
    current_time = int(time.time()) if now is None else now
    for key in ("bridgeId", "mode", "role", "mainNode", "bridgeNode", "creationId"):
        if intent.get(key) != manifest.get(key):
            raise LifecycleError(f"Created bridge does not match its preflight {key}.")
    if intent.get("schema") != 1 or not isinstance(intent.get("expiresAt"), int) or intent["expiresAt"] < current_time:
        raise LifecycleError("Bridge creation preflight is invalid or expired.")
    expected_plan = {
        "units": sorted(str(entry["name"]) for entry in manifest.get("units", [])),
        "runtimePaths": sorted(str(entry["path"]) for entry in manifest.get("runtimePaths", [])),
        "firewallFiles": sorted(str(entry["path"]) for entry in manifest.get("firewallFiles", [])),
        "credentialEntry": len(manifest.get("credentialEntries", [])) == 1,
    }
    for key, value in expected_plan.items():
        if intent.get(key) != value:
            raise LifecycleError(f"Created bridge resources do not match the preflight {key}.")
    if (tombstone_root / f"{bridge_id}.json").exists():
        raise LifecycleError("This bridge was deleted. Clear its tombstone only during an explicit reinstall.")
    if manifest_root == MANIFEST_ROOT:
        ensure_safe_state_root(manifest_root, create=True)
    else:
        manifest_root.mkdir(parents=True, exist_ok=True)
    destination = manifest_root / f"{bridge_id}.json"
    if destination.exists():
        raise LifecycleError("An ownership manifest already exists for this bridge.")
    verify_created_resources(manifest, root_map=root_map, runner=runner)
    atomic_json(destination, manifest, 0o600)
    intent_path.unlink()
    return manifest_preview(manifest)


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="asr-bridge-lifecycle-") as temporary:
        root = Path(temporary)
        config = root / "config.json"
        manifests = root / "manifests"
        tombstones = root / "tombstones"
        pending = root / "pending"
        status = root / "status.json"
        manifests.mkdir()
        config.write_text('{"bridges":[]}\n', encoding="utf-8")
        creation_intents = root / "creation-intents"
        preflight = preflight_creation(
            {
                "bridgeId": "p25_net", "mode": "p25", "role": "net",
                "mainNode": "123456", "bridgeNode": "1885",
                "units": ["p25gateway-p25_net.service"],
                "runtimePaths": ["/run/allscan-reimagined-p25-bridge-control/p25_net.json"],
                "firewallFiles": [], "credentialEntry": True,
            },
            intent_root=creation_intents, manifest_root=manifests,
            root_map=RootMap(root), now=1786500000,
        )
        creation_id = preflight["creationId"]
        bridge_root = root / "opt/allscan-reimagined-bridges/p25_net"
        bridge_root.mkdir(parents=True)
        marker = bridge_root / ".asr-bridge-owner.json"
        marker.write_text(json.dumps({
            "bridgeId": "p25_net", "createdBy": CREATED_BY,
            "creationId": creation_id, "ownership": "asr", "schema": 1,
        }), encoding="utf-8")
        owned_file = bridge_root / "P25Gateway.ini"
        owned_file.write_text("test\n", encoding="utf-8")
        unit_path = root / "etc/systemd/system/p25gateway-p25_net.service"
        unit_path.parent.mkdir(parents=True)
        unit_marker = f"# ASR-BRIDGE-OWNER: p25_net {creation_id}"
        unit_path.write_text(unit_marker + "\n# ASR bridge p25_net\n", encoding="utf-8")
        rpt = root / "etc/asterisk/rpt.conf"
        rpt.parent.mkdir(parents=True)
        rpt.write_text(
            f"[general]\n; BEGIN ASR BRIDGE p25_net {creation_id}\n[p25_net]\ntest=yes\n"
            f"; END ASR BRIDGE p25_net {creation_id}\n[other]\nkeep=yes\n",
            encoding="utf-8",
        )
        secrets = root / "etc/allscan-reimagined/bridge-mqtt-secrets.json"
        secrets.parent.mkdir(parents=True)
        secrets.write_text(json.dumps({"bridges": {
            "p25_net": {
                "password": "secret",
                "asrOwnership": ownership_payload("p25_net", creation_id),
            },
            "keep": {},
        }}), encoding="utf-8")
        runtime = root / "run/allscan-reimagined-p25-bridge-control/p25_net.json"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("{}\n", encoding="utf-8")
        runtime_marker = runtime.parent / ".asr-bridge-owner.json"
        runtime_marker.write_text(
            json.dumps(ownership_payload("p25_net", creation_id)), encoding="utf-8"
        )
        block = (
            f"; BEGIN ASR BRIDGE p25_net {creation_id}\n[p25_net]\ntest=yes\n"
            f"; END ASR BRIDGE p25_net {creation_id}\n"
        )
        manifest = {
            "schema": 1, "ownership": "asr", "createdBy": CREATED_BY,
            "creationId": creation_id, "createdAt": 1786500000, "bridgeId": "p25_net",
            "mode": "p25", "role": "net", "mainNode": "123456",
            "bridgeNode": "1885",
            "asteriskBlocks": [{
                "path": "/etc/asterisk/rpt.conf", "marker": "p25_net",
                "creationId": creation_id,
                "sha256": hashlib.sha256(block.encode("utf-8")).hexdigest(),
            }],
            "units": [{"name": "p25gateway-p25_net.service", "unitFile": "/etc/systemd/system/p25gateway-p25_net.service", "sha256": sha256_path(unit_path), "ownerMarker": unit_marker}],
            "ownedPaths": [
                {"path": "/opt/allscan-reimagined-bridges/p25_net/P25Gateway.ini", "kind": "file", "sha256": sha256_path(owned_file)},
                {"path": "/opt/allscan-reimagined-bridges/p25_net", "kind": "directory", "marker": "/opt/allscan-reimagined-bridges/p25_net/.asr-bridge-owner.json"},
            ],
            "runtimePaths": [{
                "path": "/run/allscan-reimagined-p25-bridge-control/p25_net.json",
                "marker": "/run/allscan-reimagined-p25-bridge-control/.asr-bridge-owner.json",
            }],
            "credentialEntries": [{
                "path": str(MQTT_SECRETS_PATH), "rootKey": "bridges", "key": "p25_net",
                "createdBy": CREATED_BY, "creationId": creation_id,
            }],
            "firewallFiles": [], "listeners": [], "topics": ["p25_net/json"],
        }
        validate_manifest(manifest)
        verify_created_resources(manifest, RootMap(root))
        standard_manifest = dict(manifest)
        standard_manifest["role"] = "standard"
        assert validate_manifest(standard_manifest)["role"] == "standard"
        assert manifest_preview(standard_manifest)["role"] == "standard"
        try:
            preflight_creation(
                {
                    "bridgeId": "p25_net", "mode": "p25", "role": "net",
                    "mainNode": "123456", "bridgeNode": "1885",
                    "units": ["p25gateway-p25_net.service"],
                    "runtimePaths": ["/run/allscan-reimagined-p25-bridge-control/p25_net.json"],
                    "firewallFiles": [], "credentialEntry": True,
                },
                intent_root=root / "forged-intents", manifest_root=manifests,
                root_map=RootMap(root), now=1786500001,
            )
        except LifecycleError as exc:
            assert "Pre-existing" in str(exc)
        else:
            raise AssertionError("pre-existing resources were accepted for adoption")
        register_created_manifest(
            manifest, manifest_root=manifests, tombstone_root=tombstones,
            intent_root=creation_intents, root_map=RootMap(root), now=1786500001,
        )
        assert not (creation_intents / "p25_net.json").exists()

        preview = manifest_preview(manifest)
        forged = {
            "bridgeId": "p25_net", "creationId": creation_id,
            "manifestDigest": preview["manifestDigest"], "deletionToken": "0" * 64,
        }
        try:
            queue_deletion(forged, manifest_root=manifests, pending_root=pending, now=1786500100)
        except LifecycleError:
            pass
        else:
            raise AssertionError("forged deletion confirmation was accepted")
        queue_deletion(
            {key: preview[key] for key in ("bridgeId", "creationId", "manifestDigest", "deletionToken")},
            manifest_root=manifests, pending_root=pending, now=1786500100,
        )

        calls: list[list[str]] = []
        state = {"linked": True, "stopFail": True}

        def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            if command[-1:] == ["rpt lstats 123456"]:
                output = "1885 127.0.0.1 0 OUT 00:00:01 ESTABLISHED\n" if state["linked"] else ""
                return subprocess.CompletedProcess(command, 0, output, "")
            if command[-1:] == ["rpt fun 123456 *11885"]:
                state["linked"] = False
            if command[:3] == ["/usr/bin/systemctl", "disable", "--now"] and state["stopFail"]:
                return subprocess.CompletedProcess(command, 1, "", "stop failed")
            if command[:3] == ["/usr/bin/systemctl", "show", "p25gateway-p25_net.service"]:
                if unit_path.exists():
                    return subprocess.CompletedProcess(command, 0, "LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\n", "")
                return subprocess.CompletedProcess(command, 1, "LoadState=not-found\nActiveState=inactive\nSubState=dead\nResult=success\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        result = reconcile(
            config_path=config, manifest_root=manifests, tombstone_root=tombstones,
            pending_root=pending, status_path=status, now=1786500101,
            root_map=RootMap(root), runner=fake_runner,
        )
        assert not result["ok"] and result["pending"] == 1
        assert (manifests / "p25_net.json").exists() and unit_path.exists()
        assert any("could not be stopped" in item for item in result["results"][0]["remaining"])
        retry_record = read_json_regular(pending / "p25_net.json")
        assert retry_record["state"] == "incomplete" and retry_record["expiresAt"] > 1786500101
        state["stopFail"] = False
        result = reconcile(
            config_path=config, manifest_root=manifests, tombstone_root=tombstones,
            pending_root=pending, status_path=status, now=1786500102,
            root_map=RootMap(root), runner=fake_runner,
        )
        assert result["ok"] and result["pending"] == 0
        assert not (manifests / "p25_net.json").exists()
        assert (tombstones / "p25_net.json").exists()
        assert "BEGIN ASR BRIDGE" not in rpt.read_text(encoding="utf-8")
        assert "[other]" in rpt.read_text(encoding="utf-8")
        assert not bridge_root.exists() and not unit_path.exists() and not runtime.exists()
        assert "p25_net" not in read_json_regular(secrets)["bridges"]
        assert "keep" in read_json_regular(secrets)["bridges"]
        assert any(command[-1:] == ["rpt fun 123456 *11885"] for command in calls)
        calls_after_delete = len(calls)
        repeated = reconcile(
            config_path=config, manifest_root=manifests, tombstone_root=tombstones,
            pending_root=pending, status_path=status, now=1786500101,
            root_map=RootMap(root), runner=fake_runner,
        )
        assert repeated["ok"] and repeated["results"] == [] and len(calls) == calls_after_delete

        orphan_manifests = root / "orphan-manifests"
        orphan_manifests.mkdir()
        (orphan_manifests / "p25_net.json").write_text(json.dumps(manifest), encoding="utf-8")
        calls_before_orphan = len(calls)
        orphaned = reconcile(
            config_path=config, manifest_root=orphan_manifests,
            tombstone_root=root / "orphan-tombstones", pending_root=root / "orphan-pending",
            status_path=root / "orphan-status.json", root_map=RootMap(root), runner=fake_runner,
        )
        assert not orphaned["ok"] and orphaned["results"][0]["state"] == "intent-required"
        assert (orphan_manifests / "p25_net.json").exists() and len(calls) == calls_before_orphan

        def listening_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, "udp UNCONN 0 0 127.0.0.1:17000 0.0.0.0:*\n", "")

        assert listener_is_present("udp", 17000, listening_runner)
        assert not listener_is_present("tcp", 17000, listening_runner)

        nft_entry = {
            "family": "inet", "table": "filter", "chain": "input",
            "comment": f"asr-bridge:p25_net:{creation_id}",
        }
        nft_state = {"active": True}

        def nft_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            if command[1:3] == ["delete", "rule"]:
                nft_state["active"] = False
                return subprocess.CompletedProcess(command, 0, "", "")
            rules = []
            if nft_state["active"]:
                rules.append({"rule": {
                    "family": "inet", "table": "filter", "chain": "input",
                    "comment": nft_entry["comment"], "handle": 42,
                }})
            return subprocess.CompletedProcess(command, 0, json.dumps({"nftables": rules}), "")

        nft_errors: list[str] = []
        assert remove_nftables_rule(nft_entry, nft_runner, nft_errors)
        assert not nft_state["active"] and nft_errors == []
        inspect_errors: list[str] = []
        assert not remove_nftables_rule(
            nft_entry,
            lambda command: subprocess.CompletedProcess(command, 1, "", "unavailable"),
            inspect_errors,
        )
        assert any("could not be inspected" in error for error in inspect_errors)

        external_config = root / "external.json"
        external_config.write_text('{"bridges":[]}\n', encoding="utf-8")
        external_root = root / "pre-asr-user-stack"
        external_root.mkdir()
        external_files = {
            external_root / "manual.service": "enabled and running\n",
            external_root / "rpt.conf": "[manual-bridge]\nnode=9001\n",
            external_root / "firewall.rules": "allow udp 17000\n",
            external_root / "package-list": "manual-bridge-package\n",
        }
        before = {}
        for path, content in external_files.items():
            path.write_text(content, encoding="utf-8")
            os.chmod(path, 0o640)
            before[path] = (sha256_path(path), path.stat().st_mode, path.stat().st_mtime_ns)
        calls_before = len(calls)
        external_result = reconcile(
            config_path=external_config, manifest_root=root / "no-manifests",
            tombstone_root=root / "external-tombstones", pending_root=root / "external-pending",
            status_path=root / "external-status.json",
            root_map=RootMap(root), runner=fake_runner,
        )
        assert external_result["ok"] and external_result["results"] == []
        assert len(calls) == calls_before
        for path, metadata in before.items():
            assert path.exists()
            assert (sha256_path(path), path.stat().st_mode, path.stat().st_mtime_ns) == metadata

        bad = dict(manifest)
        bad["units"] = [{"name": "asterisk.service", "unitFile": "/etc/systemd/system/asterisk.service", "sha256": "0" * 64, "ownerMarker": unit_marker}]
        try:
            validate_manifest(bad)
        except LifecycleError:
            pass
        else:
            raise AssertionError("shared unit ownership was accepted")
    print("ASR bridge lifecycle self-test: ok")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preview-all")
    subparsers.add_parser("status")
    subparsers.add_parser("reconcile")
    subparsers.add_parser("queue-deletion")
    subparsers.add_parser("preflight")
    subparsers.add_parser("register-created")
    subparsers.add_parser("self-test")
    return result


def main() -> int:
    arguments = parser().parse_args()
    try:
        if arguments.command == "self-test":
            self_test()
            return 0
        if arguments.command == "preview-all":
            print(json.dumps(preview_all(), sort_keys=True))
            return 0
        if arguments.command == "status":
            payload = lifecycle_status()
            print(json.dumps(payload, sort_keys=True))
            return 0 if payload.get("ok", False) else 1
        if os.geteuid() != 0:
            raise LifecycleError("Bridge lifecycle changes require root.")
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOCK_PATH.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if arguments.command == "queue-deletion":
                payload = queue_deletion(read_bounded_stdin())
            elif arguments.command == "preflight":
                payload = preflight_creation(read_bounded_stdin())
            elif arguments.command == "register-created":
                payload = register_created_manifest(read_bounded_stdin())
            elif os.environ.get("ASR_INSTALL_LOCK_HELD") == "1" or os.environ.get("ASR_ROLLBACK_MODE") == "1":
                payload = {
                    "ok": True,
                    "skipped": True,
                    "reason": "Bridge cleanup is disabled during install and rollback.",
                }
            else:
                payload = reconcile()
        print(json.dumps(payload, sort_keys=True))
        return 0 if payload.get("ok", True) else 1
    except (OSError, ValueError, LifecycleError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
