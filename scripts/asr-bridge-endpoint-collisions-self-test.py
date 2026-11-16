#!/usr/bin/env python3
"""Deterministic native ASL3 and Docker endpoint collision contract tests."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("endpoint_guard_test", HERE / "asr-bridge-endpoint-collisions.py")
guard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = guard
spec.loader.exec_module(guard)
E = guard.Endpoint


def ep(owner, protocol="udp", address="127.0.0.1", port=41000, namespace="host", group=""):
    return E(owner, "receiver", protocol, address, port, namespace, "fixture", group)


def conflict(*rows):
    return guard.collisions(rows)


# Same UDP bind or wildcard collides; action identifies both owners and endpoint.
item = conflict(ep("legacy"), ep("asr:dmr"))
assert len(item) == 1 and {item[0]["left"]["owner"], item[0]["right"]["owner"]} == {"legacy", "asr:dmr"}
assert "before activation" in item[0]["action"]
assert conflict(ep("legacy", address="0.0.0.0"), ep("asr", address="127.0.0.1"))
assert not conflict(ep("legacy", protocol="tcp"), ep("asr", protocol="udp"))
assert not conflict(ep("legacy", address="127.0.0.2"), ep("asr", address="127.0.0.1"))
assert not conflict(ep("legacy", port=41001), ep("asr", port=41000))
assert not conflict(ep("legacy", namespace="docker:one"), ep("asr", namespace="docker:two"))
assert not conflict(ep("legacy", namespace="docker:one"), ep("asr", namespace="host"))
# A Docker port mapped to host namespace *does* conflict with host listeners.
assert conflict(ep("docker:published", namespace="host"), ep("asr", namespace="host"))
assert conflict(ep("first", address="::"), ep("second", address="127.0.0.1"))
assert not conflict(ep("first", address="::1"), ep("second", address="127.0.0.1"))
# Shared USRP is only safe for mutually exclusive managed net backends.
assert not conflict(ep("asr:p25-net", port=52000, group="net-usrp"),
                    ep("asr:ysf-net", port=52000, group="net-usrp"))
assert conflict(ep("asr:p25-net", port=52000, group="net-usrp"),
                ep("legacy", port=52000))
# Repeated external destinations must not appear as local listeners.
assert not conflict(E("asr:dmr", "master", "udp", "10.0.0.1", 62031, kind="destination"),
                    E("asr:ysf", "master", "udp", "10.0.0.1", 62031, kind="destination"))

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    rpt = root / "etc/asterisk/rpt.conf"
    rpt.parent.mkdir(parents=True)
    rpt.write_text("[1900]\nrxchannel=USRP/127.0.0.1:41000:41001\n")
    config = {"bridges": [{"id": "dmr_legacy", "mode": "dmr", "node": "1751",
                            "setupPorts": {"usrp_rx": 41022, "usrp_tx": 41023}}]}
    guard.check_candidate(root, config, "new", "p25", {"usrp_rx": 41012})
    try:
        guard.check_candidate(root, config, "new", "p25", {"usrp_rx": 41000})
        raise AssertionError("unmanaged AllStar USRP collision accepted")
    except ValueError as exc:
        assert "asterisk:node-1900" in str(exc) and "41000" in str(exc)
    try:
        guard.check_candidate(root, config, "new", "p25", {"usrp_rx": 41022})
        raise AssertionError("managed USRP collision accepted")
    except ValueError as exc:
        assert "asr:dmr_legacy" in str(exc)
    # An owned reconfigure may reuse its own endpoints, but not other owners'.
    guard.check_candidate(root, config, "dmr_legacy", "dmr", {"usrp_rx": 41022})
    # Existing ASR app_rpt sections should not be mistaken for manual bridges.
    rpt.write_text("[1751]\nrxchannel = USRP/127.0.0.1:41022:41023\n")
    assert not guard.rpt_endpoints(rpt.read_text(), config)
    # If a manual change takes over the node with new ports, inventory it.
    rpt.write_text("[1751]\nrxchannel = USRP/127.0.0.1:41030:41031\n")
    assert len(guard.rpt_endpoints(rpt.read_text(), config)) == 2
    # Managed net bridge mode backups explicitly share node 1999.
    config = {"bridges": [{"id": "p25_net", "mode": "p25", "node": "1999",
                            "cardType": "p25_net",
                            "setupPorts": {"usrp_rx": 52000, "usrp_tx": 52001}}]}
    rpt.write_text("[1999]\nrxchannel=USRP/127.0.0.1:52000:52001\n")
    guard.check_candidate(root, config, "ysf_net", "ysf",
                          {"usrp_rx": 52000, "usrp_tx": 52001}, role="net")
    try:
        guard.check_candidate(root, config, "standard", "ysf", {"usrp_rx": 52000})
        raise AssertionError("standard instance reused net audio")
    except ValueError:
        pass
    # Read-only diagnosis does not mutate fixtures and reports manual links.
    original = rpt.read_bytes()
    report = guard.diagnostic(config, rpt.read_text())
    assert report["readOnly"] and report["schema"] == 1
    assert report["coverage"]["asteriskChannels"] and rpt.read_bytes() == original
    assert len(report["endpoints"]) >= 2
    assert report["conflicts"] == []

# Inactive installation records block accidental reuse even if no card is live.
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    state = root / "var/lib/allscan-reimagined/bridge-setup/installations"
    state.mkdir(parents=True)
    (state / "dmr_retired.json").write_text(json.dumps({
        "bridgeType": "dmr", "bridgeNode": 1501,
        "ports": {"usrp_rx": 48000, "usrp_tx": 48001},
    }))
    try:
        guard.check_candidate(root, {"bridges": []}, "new_dmr", "dmr", {"usrp_rx": 48000})
        raise AssertionError("inactive ASR ownership reservation ignored")
    except ValueError as exc:
        assert "conflicts with managed bridge" in str(exc)
    legacy = root / "opt/Analog_Bridge_old_timer/Analog_Bridge.ini"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("[USRP]\naddress=127.0.0.1\nrxPort=48090\ntxPort=48091\n")
    try:
        guard.check_candidate(root, {"bridges": []}, "new_dmr", "dmr", {"usrp_rx": 48090})
        raise AssertionError("manual Analog Bridge port ignored")
    except ValueError as exc:
        assert "legacy:" in str(exc) and "48090" in str(exc)
    managed = root / "opt/Analog_Bridge_dmr_my_bridge/Analog_Bridge.ini"
    managed.parent.mkdir(parents=True)
    managed.write_text("[USRP]\naddress=127.0.0.1\nrxPort=48190\n")
    cfg = {"bridges": [{"id": "dmr_my_bridge", "mode": "dmr",
                        "setupPorts": {"usrp_rx": 48190}}]}
    guard.check_candidate(root, cfg, "dmr_my_bridge", "dmr", {"usrp_rx": 48190})
    rows = guard.legacy_service_endpoints(root, cfg)
    assert any(e.owner == "asr:dmr_my_bridge" for e in rows)

docker = guard.docker_published_endpoints([{
    "Name": "/mmdvm", "HostConfig": {"NetworkMode": "bridge",
    "PortBindings": {"41300/udp": [{"HostIp": "0.0.0.0", "HostPort": "41300"}],
                     "41400/tcp": [{"HostIp": "127.0.0.1", "HostPort": "41400"}]}}
}])
assert len(docker) == 2
assert guard.collisions(docker + [ep("asr", port=41300)])
assert not guard.collisions(docker + [ep("asr", protocol="udp", port=41400)])
assert not guard.docker_published_endpoints([{
    "Name": "/host", "HostConfig": {"NetworkMode": "host",
    "PortBindings": {"41300/udp": [{"HostPort": "41300"}]}}
}])
dest_cfg = {"bridges": [
    {"id": "p25_one", "mode": "p25", "reflectorHost": "example.net",
     "reflectorPort": 41400, "setupPorts": {"usrp_rx": 41500, "mqtt": 41400}},
    {"id": "p25_two", "mode": "p25", "reflectorHost": "example.net",
     "reflectorPort": 41400, "setupPorts": {"usrp_rx": 41501}},
]}
endpoints = guard.configured(dest_cfg)
assert len([e for e in endpoints if e.kind == "destination"]) == 2
assert any(e.protocol == "tcp" and e.label == "mqtt" for e in endpoints)
assert not guard.collisions(endpoints)

# ss sockets report UDP/TCP separately and preserve IPv4/IPv6 bind addresses.
sockets = guard.socket_endpoints(
    "udp UNCONN 0 0 0.0.0.0:44111 0.0.0.0:*\n"
    "tcp LISTEN 0 128 127.0.0.1:44111 0.0.0.0:*\n"
    "udp UNCONN 0 0 [::1]:52022 [::]:*\n")
assert {(e.protocol, e.address, e.port) for e in sockets} == {
    ("udp", "0.0.0.0", 44111), ("tcp", "127.0.0.1", 44111),
    ("udp", "::1", 52022),
}
print("bridge endpoint collisions (native, Docker, manual, shared USRP): PASS")
