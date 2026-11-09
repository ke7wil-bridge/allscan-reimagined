#!/usr/bin/env python3
"""Read-only bridge endpoint inventory and collision checks.

A destination is not a listener; TCP and UDP are separate; Docker bridge
namespaces are isolated unless a port is published into the host namespace.
AllStar's single Net Bridge (node 1999) intentionally shares USRP 52000/52001
between mutually-exclusive mode backends. No automatic port rewrites or
unsafe collision overrides are supported.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import shutil
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

NET_PORTS = frozenset({52000, 52001})
NET_TYPES = frozenset({"dmr_net", "ysf_net", "p25_net", "nxdn_net", "m17_net"})
MANAGED_KEY = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
USRP_CHANNEL = re.compile(r"USRP/([^:\s]+):(\d{1,5}):(\d{1,5})", re.IGNORECASE)
ACTIVE_SOCKET = re.compile(r"^(udp|tcp)\S*\s+.*?\s+(\S+):(\d{1,5})\s")
HOST = "host"


@dataclass(frozen=True)
class Endpoint:
    owner: str
    label: str
    protocol: str
    address: str
    port: int
    namespace: str = HOST
    source: str = "config"
    shared_group: str = ""
    kind: str = "listener"

    def __post_init__(self) -> None:
        if self.protocol not in {"udp", "tcp"}:
            raise ValueError("unsupported transport")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("invalid endpoint port")
        if not self.address or not self.namespace:
            raise ValueError("invalid endpoint address/namespace")


def address_overlaps(left: str, right: str) -> bool:
    def parsed(value: str):
        value = value.strip().strip("[]")
        if value == "*":
            value = "0.0.0.0"
        try:
            return ipaddress.ip_address(value)
        except ValueError:
            return None
    a, b = parsed(left), parsed(right)
    if a is not None and b is not None:
        if a.is_unspecified or b.is_unspecified:
            # IPv6 wildcard may be dual-stack (IPV6_V6ONLY varies).
            return a.version == b.version or a.version == 6 or b.version == 6
        return a == b
    # Unknown DNS/interface names: fail closed rather than asserting isolation.
    return left == right or a is None or b is None or a.is_unspecified or b.is_unspecified


def collisions(endpoints: Iterable[Endpoint], focus: str | None = None) -> list[dict[str, Any]]:
    items = list(endpoints)
    found = []
    seen: set[tuple[Any, ...]] = set()
    for i, a in enumerate(items):
        if a.kind != "listener":
            continue
        for b in items[i + 1:]:
            if b.kind != "listener" or a.owner == b.owner:
                continue
            if focus is not None and focus not in (a.owner, b.owner):
                continue
            if (a.namespace, a.protocol, a.port) != (b.namespace, b.protocol, b.port):
                continue
            if not address_overlaps(a.address, b.address):
                continue
            if (a.shared_group == "net-usrp" and b.shared_group == "net-usrp"
                    and a.port in NET_PORTS):
                # Mutually exclusive ASR Net Bridge backends share node 1999.
                continue
            # Socket snapshots can corroborate an already-configured owner.
            if a.source == "socket" or b.source == "socket":
                other = b if a.source == "socket" else a
                if other.source != "socket" and other.owner.startswith("asr:"):
                    # These are ambiguous unless the socket can be attributed.
                    # Keep them visible as warnings, but don't block upgrades
                    # of already-owned active bridges solely on a socket count.
                    continue
            key = tuple(sorted(((a.owner, a.label, a.address), (b.owner, b.label, b.address)))) + (a.protocol, a.port, a.namespace)
            if key in seen:
                continue
            seen.add(key)
            found.append({
                "severity": "unsafe",
                "reason": "competing listeners / USRP receivers",
                "protocol": a.protocol, "port": a.port, "namespace": a.namespace,
                "left": asdict(a), "right": asdict(b),
                "action": "Choose a free receive endpoint or disable/remove the conflicting owner before activation. No automatic rewrite is performed.",
            })
    return found


def make_endpoints(owner: str, kind: str, ports: dict[str, Any], *,
                   role: str = "standard", namespace: str = HOST,
                   address: str = "127.0.0.1", source: str = "config") -> list[Endpoint]:
    result: list[Endpoint] = []
    for key, raw in ports.items():
        if type(raw) is not int or not 1 <= raw <= 65535:
            continue
        # MQTT is an outbound client in gateway configs, but ASR owns a local
        # broker listening on that port for P25/NXDN. YSF has no MQTT broker.
        if key == "mqtt" and kind not in {"p25", "nxdn"}:
            continue
        if key == "emulator" and kind == "p25":
            continue  # the P25 renderer explicitly disables the emulator
        protocol = "tcp" if key in {"mqtt", "transcoder"} else "udp"
        bind_address = address
        if key in {"m17", "m17BindPort"}:
            bind_address = "0.0.0.0"
        if key in {"m17BindPort", "m17", "m17UsrpRxPort", "m17UsrpTxPort"}:
            # M17's remote TX port is the chan_usrp receiver, not outbound
            # network traffic, and must be reserved as an audio endpoint.
            pass
        shared = "net-usrp" if role == "net" and raw in NET_PORTS and key.lower() in {
            "usrp_rx", "usrp_tx", "usrprx", "usrptx", "m17usrprxport", "m17usrptxport"
        } else ""
        result.append(Endpoint(owner, key, protocol, bind_address, raw, namespace, source, shared))
    return result


def _config_owner(item: dict[str, Any]) -> str:
    return f"asr:{item['id']}"


def configured(config: dict[str, Any]) -> list[Endpoint]:
    result = []
    for item in config.get("bridges", []):
        if not isinstance(item, dict) or not MANAGED_KEY.fullmatch(str(item.get("id", ""))):
            continue
        owner = _config_owner(item)
        kind = str(item.get("mode", "")).lower()
        role = "net" if item.get("cardType") in NET_TYPES and str(item.get("node")) == "1999" else "standard"
        ports = item.get("setupPorts")
        namespace = str(item.get("endpointNamespace") or HOST)
        if isinstance(ports, dict):
            result += make_endpoints(owner, kind, ports, role=role, namespace=namespace)
        for property_name, addr in (
                ("setupListenerPorts", "127.0.0.1"),
                ("setupPublicPorts", "0.0.0.0")):
            additional = item.get(property_name, {})
            if isinstance(additional, dict):
                result += make_endpoints(
                    owner, kind,
                    {property_name + "." + k: v for k, v in additional.items()},
                    namespace=namespace, address=addr,
                )
        for address_key, port_key in (
                ("reflectorHost", "reflectorPort"),
                ("dmrMasterHost", "dmrMasterPort"),
                ("m17Host", "m17Port")):
            address, port = item.get(address_key), item.get(port_key)
            if isinstance(address, str):
                try:
                    port = int(port)
                    if 1 <= port <= 65535:
                        result.append(Endpoint(owner, "destination:" + port_key, "udp",
                                               address, port, namespace,
                                               kind="destination"))
                except (ValueError, TypeError):
                    pass
        if kind == "m17" and "m17BindPort" in item:
            fields = {}
            for key in ("m17BindPort", "m17UsrpRxPort", "m17UsrpTxPort"):
                try:
                    fields[key] = int(item[key])
                except (KeyError, ValueError, TypeError):
                    pass
            result += make_endpoints(owner, kind, fields, role=role, namespace=namespace)
    return result


def rpt_endpoints(text: str, config: dict[str, Any]) -> list[Endpoint]:
    """Inventory manual chan_usrp nodes, excluding ASR-managed ones."""
    result = []
    section = ""
    managed: str | None = None
    active = False
    managed_nodes: dict[str, list[dict[str, Any]]] = {}
    for item in config.get("bridges", []):
        if isinstance(item, dict) and MANAGED_KEY.fullmatch(str(item.get("id", ""))):
            try:
                managed_nodes.setdefault(str(int(item.get("node"))), []).append(item)
            except (TypeError, ValueError):
                pass
    for raw in text.splitlines():
        line = raw.strip()
        match = re.match(r";\s*ASR MANAGED BEGIN\s+([a-z0-9_-]+)", line, re.IGNORECASE)
        if match:
            managed = match.group(1)
            continue
        if re.match(r";\s*ASR MANAGED END", line, re.IGNORECASE):
            managed = None
            continue
        if line.startswith((";", "#")):
            continue
        match = re.match(r"\[([0-9]{3,10})\]", line)
        if match:
            section = match.group(1)
            active = True
            continue
        if line.startswith("["):
            active = False
            continue
        if not active or not re.match(r"rxchannel\s*=", line, re.IGNORECASE):
            continue
        match = USRP_CHANNEL.search(line)
        if not match:
            continue
        address, backend_rx, asterisk_rx = match.group(1), int(match.group(2)), int(match.group(3))
        expected = (backend_rx, asterisk_rx)
        def owned_by_asr(item: dict[str, Any]) -> bool:
            ports = item.get("setupPorts", {})
            if isinstance(ports, dict) and (
                    ports.get("usrp_rx"), ports.get("usrp_tx")) == expected:
                return True
            if (item.get("m17UsrpRxPort"), item.get("m17UsrpTxPort")) == expected:
                return True
            return (section == "1999" and item.get("cardType") in NET_TYPES
                    and set(expected) == NET_PORTS)
        if managed or (address == "127.0.0.1" and any(
                owned_by_asr(item) for item in managed_nodes.get(section, []))):
            continue
        owner = f"asterisk:node-{section}"
        result.extend([
            Endpoint(owner, "usrp-backend-rx", "udp", address, backend_rx, source="rpt.conf"),
            Endpoint(owner, "usrp-asterisk-rx", "udp", address, asterisk_rx, source="rpt.conf"),
        ])
    return result


def installation_endpoints(root: Path, config: dict[str, Any]) -> list[Endpoint]:
    """Reserve inactive ASR bridge ports even when their cards were removed."""
    state_dir = root / "var/lib/allscan-reimagined/bridge-setup/installations"
    if not state_dir.is_dir():
        return []
    result = []
    for path in sorted(state_dir.glob("*.json")):
        if path.is_symlink() or not MANAGED_KEY.fullmatch(path.stem):
            raise ValueError(f"unsafe managed bridge ownership record: {path.name}")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"invalid managed bridge ownership record: {path.name}") from exc
        if not isinstance(record, dict) or not isinstance(record.get("ports"), dict):
            continue
        kind = str(record.get("bridgeType") or "").lower()
        record_settings = record.get("settings") if isinstance(record.get("settings"), dict) else {}
        net = (record_settings.get("bridge_role") == "net"
               or (str(record.get("bridgeNode")) == "1999" and kind in {"m17", "p25", "nxdn", "ysf"}))
        result += make_endpoints(f"asr:{path.stem}", kind, record["ports"],
                                 role="net" if net else "standard",
                                 source="installation-record")
    return result


def legacy_service_endpoints(root: Path, config: dict[str, Any]) -> list[Endpoint]:
    """Known DVSwitch/gateway INI layouts, read-only; never edit manual files."""
    patterns = (
        "etc/Analog_Bridge.ini", "etc/MMDVM_Bridge.ini",
        "etc/dvswitch/Analog_Bridge.ini", "etc/dvswitch/MMDVM_Bridge.ini",
        "opt/Analog_Bridge*/Analog_Bridge.ini",
        "opt/MMDVM_Bridge*/MMDVM_Bridge.ini",
        "opt/P25Gateway*/*.ini", "opt/NXDNGateway*/*.ini",
        "opt/YSFGateway*/*.ini",
    )
    result = []
    known = {str(i.get("id")) for i in config.get("bridges", []) if isinstance(i, dict)}
    state_dir = root / "var/lib/allscan-reimagined/bridge-setup/installations"
    if state_dir.is_dir():
        known.update(p.stem for p in state_dir.glob("*.json") if MANAGED_KEY.fullmatch(p.stem))
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if path.is_symlink() or not path.is_file():
                continue
            name = path.parent.name
            matching = [bridge_id for bridge_id in known if name.endswith("_" + bridge_id)]
            owner = (f"asr:{max(matching, key=len)}" if matching
                     else f"legacy:{path.relative_to(root)}")
            section = ""
            values: dict[str, dict[str, str]] = {}
            for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if line.startswith((";", "#")) or not line:
                    continue
                if line.startswith("[") and "]" in line:
                    section = line.split("]", 1)[0].lstrip("[").strip().lower()
                    values.setdefault(section, {})
                elif "=" in line and section:
                    key, value = line.split("=", 1)
                    values[section][key.strip().lower()] = value.split(";", 1)[0].strip()
            for group, fields in values.items():
                # Only known receive/listener keys, never remote destinations.
                if group in {"usrp", "ambe_audio", "dmr", "dv3000"}:
                    keys = ("rxport",)
                elif group in {"general", "remote commands"} and "gateway" in name.lower():
                    keys = ("localport", "port") if group == "remote commands" else ("localport",)
                elif group.endswith(" network") and "mmdvm" in name.lower():
                    keys = ("localport", "local")
                elif group in {"network", "ysf network"} and "gateway" in name.lower():
                    keys = ("port",)
                else:
                    continue
                for key in keys:
                    raw = fields.get(key, "")
                    if not raw.isdecimal() or not 1 <= int(raw) <= 65535:
                        continue
                    address = fields.get("localaddress") or fields.get("address") or "127.0.0.1"
                    if group in {"remote commands", "network", "ysf network"}:
                        address = "0.0.0.0"  # avoid assuming undocumented interfaces
                    protocol = "udp"
                    result.append(Endpoint(owner, f"{name}[{group}].{key}", protocol,
                                           address, int(raw), source="legacy-ini"))
    return result


def docker_published_endpoints(inspect: list[dict[str, Any]]) -> list[Endpoint]:
    """Explicit host port publishes; unpublished bridge-network sockets isolate."""
    endpoints = []
    for item in inspect:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or item.get("Id") or "container").lstrip("/")
        host = item.get("HostConfig", {})
        if not isinstance(host, dict) or str(host.get("NetworkMode")) == "host":
            continue  # host networking already appears in host 'ss'
        ports = host.get("PortBindings") or {}
        if not isinstance(ports, dict):
            continue
        for internal, binds in ports.items():
            match = re.fullmatch(r"\d+/(udp|tcp)", str(internal))
            if not match or not isinstance(binds, list):
                continue
            for bind in binds:
                if not isinstance(bind, dict):
                    continue
                value = str(bind.get("HostPort") or "")
                if not value.isdecimal() or not 1 <= int(value) <= 65535:
                    continue
                address = str(bind.get("HostIp") or "0.0.0.0")
                endpoints.append(Endpoint(f"docker:{name}", f"published:{internal}",
                                          match.group(1), address, int(value),
                                          source="docker-publish"))
    return endpoints


def live_docker() -> tuple[list[Endpoint], bool]:
    if not shutil.which("docker"):
        return [], False
    try:
        running = subprocess.run(["docker", "ps", "-q"], capture_output=True,
                                 text=True, timeout=3, check=False)
        if running.returncode != 0:
            return [], False
        identities = [x for x in running.stdout.splitlines() if re.fullmatch(r"[a-f0-9]{12,64}", x)]
        if not identities:
            return [], True
        inspected = subprocess.run(["docker", "inspect", *identities[:50]],
                                   capture_output=True, text=True, timeout=5, check=False)
        if inspected.returncode != 0:
            return [], False
        return docker_published_endpoints(json.loads(inspected.stdout)), True
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return [], False


def socket_endpoints(text: str, namespace: str = HOST) -> list[Endpoint]:
    result = []
    for line in text.splitlines():
        # ss -H -lntu columns: Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port
        cols = line.split()
        if len(cols) < 5 or not cols[0].startswith(("tcp", "udp")):
            continue
        proto = "tcp" if cols[0].startswith("tcp") else "udp"
        local = cols[4].strip()
        if local.startswith("[") and "]:" in local:
            address, port = local[1:].rsplit("]:", 1)
        elif ":" in local:
            address, port = local.rsplit(":", 1)
        else:
            continue
        try:
            result.append(Endpoint("socket:unattributed", "active-socket", proto,
                                   address, int(port), namespace, "socket"))
        except ValueError:
            continue
    return result


def diagnostic(config: dict[str, Any], rpt_text: str = "", sockets: str = "",
               extra: Iterable[Endpoint] = (), docker_checked: bool = False) -> dict[str, Any]:
    current = configured(config) + rpt_endpoints(rpt_text, config) + list(extra)
    active = socket_endpoints(sockets)
    return {
        "schema": 1, "readOnly": True,
        "endpoints": [asdict(item) for item in current],
        "sockets": [asdict(item) for item in active],
        "conflicts": collisions(current),
        "coverage": {
            "configuredBridges": True, "asteriskChannels": bool(rpt_text),
            "activeSockets": bool(sockets),
            "dockerPortPublishing": docker_checked,
            "legacyServiceFiles": "known DVSwitch/Gateway patterns only; not exhaustive",
        },
    }


def check_candidate(root: Path, config: dict[str, Any], bridge_id: str,
                    kind: str, ports: dict[str, int], *, role: str = "standard",
                    address: str = "127.0.0.1") -> None:
    """Fail closed on configured/manual collisions during a setup preview.

    Must be invoked again inside the privileged install lock (as plan is).
    Own existing endpoints are excluded for upgrades/reconfiguration.
    """
    candidate = make_endpoints(f"candidate:{bridge_id}", kind, ports, role=role,
                               address=address)
    current = [e for e in configured(config) + installation_endpoints(root, config)
               + legacy_service_endpoints(root, config)
               if e.owner != f"asr:{bridge_id}"]
    if root == Path("/"):
        docker, checked = live_docker()
        if not checked and shutil.which("docker"):
            raise ValueError("unable to inventory Docker host port publishing; retry when Docker is inspectable")
        current.extend(docker)
    rpt = root / "etc/asterisk/rpt.conf"
    if rpt.is_file() and not rpt.is_symlink():
        current += rpt_endpoints(rpt.read_text(encoding="utf-8", errors="replace"), config)
    conflicts = collisions(current + candidate, focus=f"candidate:{bridge_id}")
    if root == Path("/"):
        if not shutil.which("ss"):
            raise ValueError("cannot inspect active host sockets (ss is not installed)")
        try:
            probe = subprocess.run(["ss", "-H", "-lntu"], capture_output=True,
                                   text=True, timeout=4, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("unable to inspect active host sockets") from exc
        if probe.returncode != 0:
            raise ValueError("unable to inspect active host sockets")
        if probe.returncode == 0:
            # An already-configured ASR receiver may be active. Do not count
            # its own socket twice; other listeners remain activation blockers.
            for socket in socket_endpoints(probe.stdout):
                if any((e.namespace == socket.namespace
                        and e.protocol == socket.protocol and e.port == socket.port
                        and address_overlaps(e.address, socket.address))
                       for e in configured(config)):
                    continue
                conflicts += collisions(candidate + [socket], focus=f"candidate:{bridge_id}")
    if conflicts:
        item = conflicts[0]
        other = item["left"] if item["left"]["owner"] != f"candidate:{bridge_id}" else item["right"]
        managed_note = " (conflicts with managed bridge)" if other["source"] == "installation-record" else ""
        raise ValueError(
            f"unsafe {item['protocol'].upper()} port block/USRP endpoint collision on "
            f"{item['namespace']}:{item['port']} with {other['owner']}{managed_note} "
            f"({other['label']} at {other['address']}); resolve before activation"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/"))
    args = parser.parse_args()
    config_path = args.root / "etc/allscan-reimagined/config.json"
    rpt_path = args.root / "etc/asterisk/rpt.conf"
    if not config_path.is_file() or config_path.is_symlink():
        parser.error("ASR config is unreadable or unsafe")
    config = json.loads(config_path.read_text())
    if not isinstance(config, dict):
        parser.error("ASR config must be a JSON object")
    rpt = rpt_path.read_text(errors="replace") if rpt_path.is_file() and not rpt_path.is_symlink() else ""
    sockets = ""
    if args.root == Path("/"):
        try:
            probe = subprocess.run(["ss", "-H", "-lntu"], capture_output=True,
                                   text=True, timeout=4, check=False)
            if probe.returncode == 0:
                sockets = probe.stdout
        except (OSError, subprocess.TimeoutExpired):
            pass
    docker, checked = live_docker() if args.root == Path("/") else ([], False)
    extra = installation_endpoints(args.root, config) + legacy_service_endpoints(args.root, config) + docker
    print(json.dumps(diagnostic(config, rpt, sockets, extra=extra,
                                docker_checked=checked), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
