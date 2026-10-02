#!/usr/bin/env python3
"""Bridge Setup Wizard Phase 1 provisioning primitives.

This module is deliberately UI-free. It detects the host, builds immutable
plans, validates them, and provides transaction semantics for later adapters.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import importlib.util
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

HERE = Path(__file__).resolve().parent


def provisioning_backend():
    spec = importlib.util.spec_from_file_location(
        "asr_provisioning_backend_core", HERE / "asr-provisioning-backend.py"
    )
    if not spec or not spec.loader:
        raise ProvisioningError("provisioning backend module is unavailable")
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ProvisioningError(RuntimeError):
    pass


@dataclass(frozen=True)
class HostFacts:
    platform: str
    asterisk_config_dir: str
    rpt_config: str
    node_numbers: tuple[int, ...]
    systemd: bool
    docker: bool
    docker_compose: bool
    hostname: str
    listening_tcp_ports: tuple[int, ...] = ()
    listening_udp_ports: tuple[int, ...] = ()


@dataclass(frozen=True)
class PlanAction:
    kind: str
    target: str
    description: str
    owned: bool = True


@dataclass(frozen=True)
class ProvisioningPlan:
    schema: int
    platform: str
    bridge_type: str
    bridge_id: str
    actions: tuple[PlanAction, ...]
    protected_paths: tuple[str, ...]
    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


class PlatformAdapter:
    name = "unknown"

    def detect(self) -> HostFacts:
        raise NotImplementedError

    def validate_plan(self, plan: ProvisioningPlan) -> None:
        if plan.platform != self.name:
            raise ProvisioningError(
                f"plan targets {plan.platform}, adapter is {self.name}"
            )
        if not plan.bridge_id or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", plan.bridge_id):
            raise ProvisioningError("invalid bridge id")
        if plan.bridge_type not in {"m17", "p25", "nxdn", "ysf", "dmr", "dstar", "zello"}:
            raise ProvisioningError("unsupported bridge type")
        targets: set[tuple[str, str]] = set()
        protected = {Path(p) for p in plan.protected_paths}
        for action in plan.actions:
            key = (action.kind, action.target)
            if key in targets:
                raise ProvisioningError(f"duplicate action target: {action.target}")
            targets.add(key)
            target = Path(action.target)
            if action.owned and any(target == p for p in protected):
                raise ProvisioningError(f"owned action targets protected path: {target}")


class ASL3Adapter(PlatformAdapter):
    name = "asl3"

    def __init__(self, root: Path = Path("/")) -> None:
        self.root = root

    def _path(self, absolute: str) -> Path:
        return provisioning_backend().map_path(self.root, absolute)
    def _nodes(self, rpt: Path) -> tuple[int, ...]:
        if not rpt.is_file():
            return ()
        nodes: list[int] = []
        section = re.compile(r"^\s*\[([0-9]{3,10})\]\s*$")
        for line in rpt.read_text(encoding="utf-8", errors="replace").splitlines():
            match = section.match(line)
            if match:
                nodes.append(int(match.group(1)))
        return tuple(dict.fromkeys(nodes))

    def _listening_ports(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        if self.root != Path("/"):
            return (), ()
        backend = provisioning_backend()
        profile = backend.load_profile()
        if profile is not None:
            command = ["docker", "exec", profile.asterisk_container, "ss", "-H", "-lntu"]
        elif shutil.which("ss"):
            command = ["ss", "-H", "-lntu"]
        else:
            return (), ()
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        tcp: set[int] = set()
        udp: set[int] = set()
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 5:
                continue
            match = re.search(r":([0-9]{1,5})$", fields[4])
            if match:
                (tcp if fields[0].startswith("tcp") else udp).add(int(match.group(1)))
        return tuple(sorted(tcp)), tuple(sorted(udp))

    def detect(self) -> HostFacts:
        rpt = self._path("/etc/asterisk/rpt.conf")
        os_release = self._path("/etc/os-release")
        text = os_release.read_text(errors="replace") if os_release.is_file() else ""
        asl3_marker = self._path("/etc/asterisk").is_dir() and (
            "debian" in text.lower() or rpt.is_file()
        )
        if not asl3_marker:
            raise ProvisioningError("ASL3 host not detected")
        profile = provisioning_backend().load_profile() if self.root == Path("/") else None
        systemctl = shutil.which("systemctl") if self.root == Path("/") else None
        docker = shutil.which("docker") if self.root == Path("/") else None
        compose = False
        if docker:
            probe = subprocess.run(
                [docker, "compose", "version"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
            )
            compose = probe.returncode == 0
        tcp_ports, udp_ports = self._listening_ports()
        return HostFacts(
            platform=self.name,
            asterisk_config_dir="/etc/asterisk",
            rpt_config="/etc/asterisk/rpt.conf",
            node_numbers=self._nodes(rpt),
            systemd=bool(systemctl) or profile is not None,
            docker=bool(docker),
            docker_compose=compose,
            hostname=socket.gethostname(),
            listening_tcp_ports=tcp_ports,
            listening_udp_ports=udp_ports,
        )


def allocate_node(existing: Iterable[int], start: int = 1001, end: int = 1999) -> int:
    used = set(existing)
    for candidate in range(start, end + 1):
        if candidate not in used:
            return candidate
    raise ProvisioningError(f"no free node in {start}-{end}")


def allocate_ports(existing: Iterable[int], count: int, start: int, end: int) -> tuple[int, ...]:
    if count < 1 or start < 1 or end > 65535 or start > end:
        raise ProvisioningError("invalid port allocation range")
    used = set(existing)
    available = tuple(port for port in range(start, end + 1) if port not in used)
    if len(available) < count:
        raise ProvisioningError(f"not enough free ports in {start}-{end}")
    return available[:count]


def derived_m17_ports(bridge_id: str) -> tuple[int, int, int]:
    import zlib
    base = 17100 + (zlib.crc32(bridge_id.encode()) & 0xFFFFFFFF) % 190 * 10
    return base, base + 1, base + 2


def m17_plan(
    facts: HostFacts, bridge_id: str, callsign: str, *,
    card_type: str = "standard",
) -> ProvisioningPlan:
    callsign = callsign.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9./-]{2,8}", callsign) or not re.search(r"[A-Z]", callsign) or not re.search(r"[0-9]", callsign):
        raise ProvisioningError("invalid M17 callsign")
    if card_type not in {"standard", "m17_net"}:
        raise ProvisioningError("invalid M17 card type")
    allocate_node(facts.node_numbers)
    bind, usrp_rx, _usrp_tx = derived_m17_ports(bridge_id)
    occupied = set(facts.listening_udp_ports)
    if bind in occupied or usrp_rx in occupied:
        raise ProvisioningError("derived M17 receive port is already in use")
    bridge_root = f"/opt/allscan-reimagined-bridges/{bridge_id}"
    unit = f"/etc/systemd/system/allscan-reimagined-m17-bridge@{bridge_id}.service"
    actions = (
        PlanAction("create", bridge_root, "create isolated M17 bridge root"),
        PlanAction("create", f"{bridge_root}/bridge.json", "write M17 bridge configuration"),
        PlanAction("create", unit, "install M17 bridge systemd unit"),
    )
    plan = make_plan(facts, "m17", bridge_id, actions)
    return plan


class Transaction:
    """In-process transaction journal used by the future privileged helper."""

    def __init__(self) -> None:
        self._undo: list[Callable[[], None]] = []
        self.committed = False

    def step(self, apply: Callable[[], Any], undo: Callable[[], None]) -> Any:
        if self.committed:
            raise ProvisioningError("transaction already committed")
        result = apply()
        self._undo.append(undo)
        return result

    def commit(self) -> None:
        self.committed = True
        self._undo.clear()

    def rollback(self) -> list[str]:
        failures: list[str] = []
        while self._undo:
            undo = self._undo.pop()
            try:
                undo()
            except Exception as exc:
                failures.append(str(exc))
        return failures


def make_plan(
    facts: HostFacts, bridge_type: str, bridge_id: str, actions: Sequence[PlanAction]
) -> ProvisioningPlan:
    return ProvisioningPlan(
        schema=1,
        platform=facts.platform,
        bridge_type=bridge_type,
        bridge_id=bridge_id,
        actions=tuple(actions),
        protected_paths=(facts.rpt_config,),
    )


def preflight(plan: ProvisioningPlan, facts: HostFacts) -> tuple[str, ...]:
    errors: list[str] = []
    if plan.platform != facts.platform:
        errors.append("platform changed since plan creation")
    if not facts.systemd:
        errors.append("systemd is required for managed bridge provisioning")
    targets = [Path(action.target) for action in plan.actions]
    if any(not target.is_absolute() for target in targets):
        errors.append("all action targets must be absolute paths")
    if any(target.is_symlink() for target in targets if target.exists() or target.is_symlink()):
        errors.append("action targets may not be symbolic links")
    return tuple(errors)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detect", action="store_true")
    args = parser.parse_args()
    if args.detect:
        facts = ASL3Adapter().detect()
        print(json.dumps(asdict(facts), indent=2))
        return 0
    parser.error("choose an operation")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
