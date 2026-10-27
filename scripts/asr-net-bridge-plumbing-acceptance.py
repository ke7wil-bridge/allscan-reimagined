#!/usr/bin/env python3
"""End-to-end production-path acceptance for Net Bridge modes and Zello."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

MODES = {
    "dmr": {"id": "dmr_tgif_net", "destination": "86753",
        "connect": ["/usr/local/sbin/allscan-reimagined-managed-dmr-net-control", "--bridge", "dmr_tgif_net", "--connect", "86753"],
        "disconnect": ["/usr/local/sbin/allscan-reimagined-managed-dmr-net-control", "--bridge", "dmr_tgif_net", "--disconnect"]},
    "ysf": {"id": "ysf_net", "destination": "67498",
        "connect": ["/usr/local/sbin/allscan-reimagined-ysf-bridge-control", "--connect", "ysf_net", "67498", "--user", "automated-qa"],
        "disconnect": ["/usr/local/sbin/allscan-reimagined-ysf-bridge-control", "--disconnect", "ysf_net", "--user", "automated-qa"]},
    "p25": {"id": "p25_net", "destination": "6749",
        "connect": ["/usr/local/sbin/allscan-reimagined-p25-bridge-control", "connect", "p25_net", "6749", "--user", "automated-qa"],
        "disconnect": ["/usr/local/sbin/allscan-reimagined-p25-bridge-control", "disconnect", "p25_net", "--user", "automated-qa"]},
    "nxdn": {"id": "nxdn_net", "destination": "6749",
        "connect": ["/usr/local/sbin/allscan-reimagined-nxdn-bridge-control", "connect", "nxdn_net", "6749", "--user", "automated-qa"],
        "disconnect": ["/usr/local/sbin/allscan-reimagined-nxdn-bridge-control", "disconnect", "nxdn_net", "--user", "automated-qa"]},
    "m17": {"id": "m17_net", "destination": "M17-NET A",
        "connect": ["/usr/local/sbin/allscan-reimagined-m17-bridge-control", "--bridge", "m17_net", "--user", "automated-qa", "connect", "--reflector", "M17-NET", "--module", "A"],
        "disconnect": ["/usr/local/sbin/allscan-reimagined-m17-bridge-control", "--bridge", "m17_net", "--user", "automated-qa", "disconnect"]},
}
REMOTE_EVIDENCE = {
    "ysf": ("ysfreflector.service", "Received data from     KE7WIL", "Received end of transmission"),
    "p25": ("netoholics-p25-reflector.service", "Transmission from KE7WIL", "Received end of transmission"),
    "nxdn": ("netoholics-nxdn-reflector.service", "Transmission from 15846 at KE7WIL", "Received end of transmission"),
    "m17": ("m17-reflector.service", "Opening stream on module A from client KE7WIL", "Closing stream on module A"),
}


def run(command: list[str], *, stdin: str | None = None, timeout: float = 90) -> str:
    result = subprocess.run(command, input=stdin, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[-1000:])
    return result.stdout.strip()


def run_combined(command: list[str], *, timeout: float = 90) -> str:
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stdout.strip()[-1000:])
    return result.stdout


def parse_json(output: str, label: str) -> dict:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError(f"{label}: no JSON response")


def web_command(args, command: list[str]) -> dict:
    payload = parse_json(run(["docker", "exec", "-u", "www-data", args.web_container,
                              *command]), command[0])
    if payload.get("ok") is not True:
        raise RuntimeError(str(payload.get("error") or payload))
    return payload


def api_read(args, action: str) -> dict:
    php = 'parse_str($argv[1], $_GET); include "/var/www/html/asr/asr-api.php";'
    payload = parse_json(run([
        "docker", "exec", "-u", "www-data", "-e", "ASR_ACCEPTANCE_READ_ONLY=1",
        args.web_container, "php", "-r", php, f"action={action}"]), action)
    if payload.get("ok") is not True:
        raise RuntimeError(f"{action}: {payload.get('error', 'read failed')}")
    return payload


def linked(args, node: str) -> bool:
    output = run(["docker", "exec", args.asterisk_container, "asterisk", "-rx",
                  "rpt lstats 641890"])
    return any(row.split()[:1] == [node] and "ESTABLISHED" in row
               for row in output.splitlines())


def ui_card(payload: dict, bridge_id: str, *, require_control: bool = True) -> tuple[dict, dict]:
    bridge = payload.get("bridge", {}).get(bridge_id)
    control = payload.get("controls", {}).get(bridge_id)
    if not isinstance(bridge, dict) or (require_control and not isinstance(control, dict)):
        raise RuntimeError(f"{bridge_id}: production bridge-status omitted the card")
    return bridge, control if isinstance(control, dict) else {}


def wait_control(args, spec: dict, wanted: bool, timeout: float = 25) -> tuple[dict, dict]:
    deadline = time.monotonic() + timeout
    last = ({}, {})
    while time.monotonic() < deadline:
        last = ui_card(api_read(args, "bridge-status"), spec["id"])
        destination = str(last[1].get("currentDestination")
                          or last[1].get("currentTg") or "").strip()
        complete_disconnect = (last[1].get("linked") is False
                               and last[1].get("digitalLinked") is False
                               and last[1].get("allstarLinked") is False
                               and destination == "")
        if ((wanted and last[1].get("linked") is True
             and destination == spec["destination"])
                or (not wanted and complete_disconnect)):
            return last
        time.sleep(.25)
    raise RuntimeError(f"{spec['id']}: UI API did not confirm linked={wanted}; {last[1]}")


def injector_program(source: int, destination: int, duration: float, markers: bool) -> str:
    # Duplicate Asterisk's bound socket. DMR/M17 therefore retain their strict
    # 127.0.0.1:52001 source validation during this test.
    return f"""import ctypes,json,math,os,socket,struct,time
source={source}; destination={destination}; duration={duration!r}; markers={markers!r}
inode=''
for path in ('/proc/1/net/udp','/proc/1/net/udp6'):
 try: lines=open(path).read().splitlines()[1:]
 except OSError: continue
 for line in lines:
  f=line.split()
  if int(f[1].rsplit(':',1)[1],16)==source: inode=f[9]; break
 if inode: break
if not inode: raise SystemExit('Asterisk USRP source socket not found')
fdnum=None
for name in os.listdir('/proc/1/fd'):
 try: target=os.readlink('/proc/1/fd/'+name)
 except OSError: continue
 if target=='socket:['+inode+']': fdnum=int(name); break
if fdnum is None: raise SystemExit('Asterisk USRP socket fd not found')
libc=ctypes.CDLL(None,use_errno=True); pidfd=libc.syscall(434,1,0)
if pidfd<0: raise OSError(ctypes.get_errno(),'pidfd_open')
dupfd=libc.syscall(438,pidfd,fdnum,0)
if dupfd<0: raise OSError(ctypes.get_errno(),'pidfd_getfd')
s=socket.socket(fileno=dupfd); target=('127.0.0.1',destination)
def frame(seq,ptt,voice=True):
 h=b'USRP'+struct.pack('>7I',seq,0,ptt,0,0,0,0)
 if not voice: return h
 return h+b''.join(struct.pack('<h',int(3500*math.sin(2*math.pi*800*(seq*160+i)/8000))) for i in range(160))
frames=max(75,int(duration/.02)); started=time.time()
for seq in range(frames):
 s.sendto(frame(seq,1),target)
 if markers and seq>0 and seq%5==0:
  s.sendto(frame(seq,0,False),target); time.sleep(.04)
 time.sleep(.02)
s.sendto(frame(frames,0,False),target)
print(json.dumps({{'sourcePort':source,'destinationPort':destination,'frames':frames,'seconds':time.time()-started}}))
"""


def inject_and_observe(args, bridge_id: str, source: int, destination: int,
                       *, zello: bool = False) -> None:
    traffic_epoch = int(time.time())
    local_eot = False
    process = subprocess.Popen(
        ["docker", "exec", "-i", args.asterisk_container, "python3", "-"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdin is not None
    process.stdin.write(injector_program(source, destination, args.duration, zello))
    process.stdin.close()
    process.stdin = None
    seen = False
    dropped = False
    active = False
    deadline = time.monotonic() + args.duration + 12
    while time.monotonic() < deadline:
        if zello:
            active = api_read(args, "zello-fast-relay").get("active") is True
            if seen and process.poll() is None and not active:
                dropped = True
        else:
            if bridge_id in {"p25_net", "nxdn_net"}:
                bridge = api_read(args, "fast-next-mode-activity").get("cards", {}).get(bridge_id, {})
            elif bridge_id == "ysf_net":
                bridge = api_read(args, "fast-ysf-net-activity").get("cards", {}).get(bridge_id, {})
            else:
                bridge, _ = ui_card(api_read(args, "bridge-status"), bridge_id)
            active = str(bridge.get("role", "")).lower() == "relay"
        seen |= active
        if process.poll() is not None and seen and not active:
            break
        time.sleep(.04 if zello else .12)
    stdout, stderr = process.communicate(timeout=3)
    if process.returncode:
        raise RuntimeError(f"{bridge_id}: legitimate injector failed: {stderr.strip()}")
    parse_json(stdout, f"{bridge_id} injector")
    if dropped:
        raise RuntimeError("zello: RELAY dropped on an in-stream chan_usrp false marker")
    if not seen:
        raise RuntimeError(f"{bridge_id}: production API never reported RELAY")
    # A live reflector may begin forwarding an unrelated inbound stream after
    # the quiet precondition and while our outbound stream is in flight.  That
    # is genuine RELAY activity, not a stuck local PTT.  Allow that traffic to
    # finish, but still require the production state to become Idle so a real
    # missing EOT remains a hard failure.
    idle_wait = 3 if zello else (8 if bridge_id == "m17_net" else 120)
    idle_deadline = time.monotonic() + idle_wait
    final = {}
    while True:
        if zello:
            active = api_read(args, "zello-fast-relay").get("active") is True
        else:
            if bridge_id in {"p25_net", "nxdn_net"}:
                final = api_read(args, "fast-next-mode-activity").get("cards", {}).get(bridge_id, {})
            elif bridge_id == "ysf_net":
                final = api_read(args, "fast-ysf-net-activity").get("cards", {}).get(bridge_id, {})
            else:
                final, _ = ui_card(api_read(args, "bridge-status"), bridge_id)
            active = str(final.get("role", "")).lower() != "idle"
        if not active or time.monotonic() >= idle_deadline:
            break
        time.sleep(.1 if zello else .25)
    if not zello:
        stale = str(final.get("current_user", final.get("caller", ""))).strip()
        if stale in {"9999", "0"}:
            raise RuntimeError(f"{bridge_id}: stale Talking identity after EOT: {stale}")
    if active and bridge_id == "m17_net":
        status = web_command(args, [
            "/usr/local/sbin/allscan-reimagined-m17-bridge-control",
            "--bridge", "m17_net", "status",
        ])
        local_eot = (
            status.get("outboundActive") is False
            and int(status.get("outboundActivityEpoch", 0) or 0) >= traffic_epoch - 1
            and int(status.get("m17PacketsSent", 0) or 0) > 0
        )
        active = not local_eot
    if active:
        raise RuntimeError(f"{bridge_id}: production state did not return to Idle")
    # The dashboard's Last Activity label reads the production bridge-status
    # payload after EOT. A transient fast-path RELAY alone is not sufficient.
    deadline = time.monotonic() + 8
    persisted = 0
    final_bridge = {}
    while time.monotonic() < deadline:
        final_bridge, _ = ui_card(api_read(args, "bridge-status"), bridge_id,
                                  require_control=not zello)
        persisted = max(int(final_bridge.get(field, 0) or 0)
                        for field in ("activity_epoch", "last_time_epoch",
                                      "last_source_epoch", "active_start_epoch"))
        if persisted >= traffic_epoch - 1 and str(final_bridge.get("role", "")).lower() == "idle":
            break
        time.sleep(.25)
    if persisted < traffic_epoch - 1:
        raise RuntimeError(f"{bridge_id}: production API lost completed activity; {final_bridge}")
    if str(final_bridge.get("role", "")).lower() != "idle" and not local_eot:
        raise RuntimeError(f"{bridge_id}: production bridge-status remained active after EOT")


def validate_exclusive(args, selected: str) -> None:
    config = json.loads(run(["docker", "exec", args.web_container, "cat",
                             "/etc/allscan-reimagined/config.json"]))
    active = []
    for bridge in config.get("bridges", []):
        mode = str(bridge.get("mode", "")).lower()
        if bridge.get("cardType") != f"{mode}_net" or mode not in MODES:
            continue
        if mode == "dmr":
            output = run(["docker", "ps", "--filter",
                          f"label=com.docker.compose.project={bridge['id']}",
                          "--format", "{{.Names}}"])
            if output.strip(): active.append(mode)
            continue
        units = ([f"allscan-reimagined-m17-bridge@{bridge['id']}.service"] if mode == "m17"
                 else [str(bridge.get(key)) for key in ("gatewayService", "mmdvmService",
                                                        "analogBridgeService", "emulatorService")
                       if bridge.get(key)])
        if units and all(subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0
                         for unit in units):
            active.append(mode)
    if active != [selected]:
        raise RuntimeError(f"{selected}: backend exclusivity failed; active={active}")


def verify_protocol_receipt(args, mode: str, since_epoch: int) -> None:
    if mode == "dmr":
        output = run_combined(["docker", "logs", "--since", str(since_epoch),
                               f"asr-urf-reflector-{MODES[mode]['id']}"])
        required = ("Opening stream on module A", "by user KE7WIL", "Closing stream of module A")
    else:
        if args.skip_remote_evidence:
            return
        unit, *required = REMOTE_EVIDENCE[mode]
        output = run([
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            args.remote_host, "journalctl", "-u", unit, "--since",
            f"@{since_epoch}", "--no-pager", "-o", "cat",
        ], timeout=15)
    missing = [pattern for pattern in required if pattern not in output]
    if missing:
        raise RuntimeError(f"{mode}: protocol receipt/EOT evidence missing: {missing}")


def wait_remote_quiet(args, mode: str, timeout: float = 120) -> None:
    """Wait for a stable idle window on single-stream test reflectors."""
    markers = {
        "ysf": ("Received data from", "Received end of transmission"),
        "p25": ("Transmission started", "Received end of transmission"),
        "nxdn": ("Transmission from", "Received end of transmission"),
        "m17": ("Opening stream on module", "Closing stream on module"),
    }
    if mode not in markers or args.skip_remote_evidence:
        return
    unit = REMOTE_EVIDENCE[mode][0]
    deadline = time.monotonic() + timeout
    last = ""
    idle_samples = 0
    start_marker, end_marker = markers[mode]
    while time.monotonic() < deadline:
        last = run([
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            args.remote_host, "journalctl", "-u", unit, "-n", "80",
            "--no-pager", "-o", "cat",
        ], timeout=15)
        data_at = last.rfind(start_marker)
        eot_at = last.rfind(end_marker)
        idle_samples = idle_samples + 1 if data_at < 0 or eot_at > data_at else 0
        if idle_samples >= 2:
            return
        time.sleep(.75)
    raise RuntimeError(f"{mode}: remote reflector remained busy; test traffic was not injected")


def qualify_mode(args, mode: str) -> None:
    spec = MODES[mode]
    selected = web_command(args, ["/usr/local/sbin/allscan-reimagined-net-bridge-mode-control",
                                  "--mode", mode])
    if selected.get("ready") is not True:
        raise RuntimeError(f"{mode}: selected backend is not ready")
    validate_exclusive(args, mode)
    web_command(args, spec["connect"])
    _, control = wait_control(args, spec, True)
    if control.get("digitalLinked") is not True or control.get("allstarLinked") is not True:
        raise RuntimeError(f"{mode}: API lacks both digital and AllStar evidence")
    if not linked(args, "1999"):
        raise RuntimeError(f"{mode}: node1999 is not linked")
    if mode == "m17":
        command = ["/usr/local/sbin/allscan-reimagined-m17-bridge-control", "--bridge", "m17_net", "status"]
        first = web_command(args, command)
        epoch = float(first.get("lastKeepaliveEpoch", 0))
        if first.get("linkState") != "linked" or epoch <= 0:
            raise RuntimeError("m17: no reflector ACK/keepalive evidence")
        # M17 reflector keepalives are periodic, not guaranteed to advance
        # inside a 1.2-second sample.  Allow one normal keepalive interval
        # before declaring a live ACK-confirmed connection stale.
        deadline = time.monotonic() + 20.0
        advanced = False
        while time.monotonic() < deadline:
            time.sleep(.5)
            current = web_command(args, command)
            if current.get("linkState") != "linked" or current.get("digitalLinked") is not True:
                raise RuntimeError("m17: ACK-confirmed link dropped while awaiting keepalive")
            current_epoch = float(current.get("lastKeepaliveEpoch", 0))
            if current_epoch > epoch:
                if time.time() - current_epoch > 30:
                    raise RuntimeError("m17: advancing keepalive evidence is stale")
                advanced = True
                break
        if not advanced:
            raise RuntimeError("m17: reflector keepalive did not advance")
    wait_remote_quiet(args, mode)
    evidence_since = int(time.time()) - 1
    inject_and_observe(args, spec["id"], 52001, 52000)
    verify_protocol_receipt(args, mode, evidence_since)
    web_command(args, spec["disconnect"])
    wait_control(args, spec, False)
    if linked(args, "1999"):
        raise RuntimeError(f"{mode}: node1999 remained linked after disconnect")
    print(f"PASS {mode}: exclusive backend, API connect, node1999, RELAY, EOT, Idle, disconnect")


def qualify_zello(args) -> None:
    if not linked(args, args.zello_node):
        raise RuntimeError(f"zello: node {args.zello_node} is not linked")
    inject_and_observe(args, "zello", 5679, 5678, zello=True)
    print("PASS zello: legitimate chan_usrp cadence, production RELAY, EOT, Idle")


def self_test() -> None:
    program = injector_program(52001, 52000, 1.5, True)
    assert "pidfd_getfd" in program and "seq%5" in program
    assert "idle_deadline" in Path(__file__).read_text()
    assert set(MODES) == {"dmr", "ysf", "p25", "nxdn", "m17"}
    assert set(REMOTE_EVIDENCE) == {"ysf", "p25", "nxdn", "m17"}
    installer = Path(__file__).with_name("asr-bridge-setup-zello-install.py").read_text()
    assert "def prepare_runtime_source" in installer and "_commit_usrp_off" in installer
    api = Path(__file__).parents[1].joinpath("server/asr-api.php").read_text()
    assert "ASR_ACCEPTANCE_READ_ONLY" in api and "persistedActivityEpoch" in api
    assert "zelloConfigured" in api and "activity_epoch' => $activityEpoch" in api
    print("PASS: production-path plumbing acceptance self-test")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--modes", nargs="+", choices=(*MODES, "zello"), default=[*MODES, "zello"])
    parser.add_argument("--web-container", default="allscan-reimagined-layered-allscan-1")
    parser.add_argument("--asterisk-container", default="allstarlink3")
    parser.add_argument("--zello-node", default="1002")
    parser.add_argument("--remote-host", default="root@45.76.255.39")
    parser.add_argument("--skip-remote-evidence", action="store_true")
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--final-mode", choices=tuple(MODES), default="p25")
    args = parser.parse_args()
    if args.self_test: self_test()
    if not args.run:
        if not args.self_test: parser.error("use --run or --self-test")
        return 0
    if not 1.5 <= args.duration <= 10: parser.error("duration must be 1.5-10 seconds")
    try:
        for mode in args.modes:
            qualify_zello(args) if mode == "zello" else qualify_mode(args, mode)
    finally:
        web_command(args, ["/usr/local/sbin/allscan-reimagined-net-bridge-mode-control",
                           "--mode", args.final_mode])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
