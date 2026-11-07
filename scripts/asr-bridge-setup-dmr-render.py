#!/usr/bin/env python3
"""Pure renderer for an ASR-managed direct DVSwitch DMR instance."""
from __future__ import annotations

from typing import Any


def analog_ini(plan: dict[str, Any]) -> str:
    s, p, g = plan["settings"], plan["ports"], plan["gains"]
    return f"""[GENERAL]
logLevel=2
exportMetadata=true
decoderFallBack=true
useEmulator=true
emulatorAddress=127.0.0.1:{p['emulator']}
pcmPort=0

[AMBE_AUDIO]
address=127.0.0.1
txPort={p['analog_tx']}
rxPort={p['analog_rx']}
ambeMode=DMR
minTxTimeMS=2500
gatewayDmrId={s['dmr_id']}
repeaterID={plan['dmrNetworkId']}
txTg={s['talkgroup']}
txTs={s['timeslot']}
colorCode={s['color_code']}

[USRP]
address=127.0.0.1
txPort={p['usrp_tx']}
rxPort={p['usrp_rx']}
usrpAudio=AUDIO_USE_GAIN
usrpGain={g['dmrToAsl']:.4f}
tlvAudio=AUDIO_USE_GAIN
tlvGain={g['aslToDmr']:.4f}

[DV3000]
address=127.0.0.1
rxPort={p['emulator']}
"""


def dvswitch_ini(plan: dict[str, Any]) -> str:
    s, p = plan["settings"], plan["ports"]
    return f"""[DMR]
address=127.0.0.1
txPort={p['analog_rx']}
rxPort={p['analog_tx']}
slot={s['timeslot']}
exportTG={s['talkgroup']}
hangTimerInFrames=0
talkerAlias=%callsign %location %description
"""


def mmdvm_template(plan: dict[str, Any]) -> str:
    s, p = plan["settings"], plan["ports"]
    slot1, slot2 = (1, 0) if s["timeslot"] == 1 else (0, 1)
    return f"""[General]
Callsign={s['callsign']}
Id={plan['dmrNetworkId']}
Timeout=180
Duplex=0

[Info]
RXFrequency=0
TXFrequency=0
Power=1
Latitude=0
Longitude=0
Height=0
Location=ASR
Description={s['title']}
URL=https://github.com/DVSwitch/MMDVM_Bridge

[Log]
DisplayLevel=1
FileLevel=2
FilePath=/var/log/mmdvm
FileRoot=MMDVM_Bridge_{s['bridge_id']}

[DMR Id Lookup]
File={plan['resources']['root']}/mmdvm/DMRIds.dat
Time=24

[Modem]
Port=/dev/null
RSSIMappingFile=/dev/null
Trace=0
Debug=0

[D-Star]
Enable=0
[DMR]
Enable=1
ColorCode={s['color_code']}
EmbeddedLCOnly=1
DumpTAData=0
[System Fusion]
Enable=0
[P25]
Enable=0
[NXDN]
Enable=0

[D-Star Network]
Enable=0
[DMR Network]
Enable=1
Address=127.0.0.1
Port={p['mmdvm_local']}
Jitter=720
Local={p['mmdvm_remote']}
Password=__DMR_MASTER_PASSWORD__
Slot1={slot1}
Slot2={slot2}
Debug=0
[System Fusion Network]
Enable=0
[P25 Network]
Enable=0
[NXDN Network]
Enable=0
"""


def run_mmdvm(plan: dict[str, Any]) -> str:
    root, s, p = plan["resources"]["root"], plan["settings"], plan["ports"]
    runtime = f"/run/asr-direct-dmr-{s['bridge_id']}"
    return f"""#!/usr/bin/env bash
set -euo pipefail
umask 077
runtime={runtime}
install -d -m 0750 "$runtime"
python3 - {root}/mmdvm/MMDVM_Bridge.ini.template "$runtime/MMDVM_Bridge.ini" {root}/secrets/master-password <<'PY'
import pathlib, sys
source, target, secret_path = map(pathlib.Path, sys.argv[1:])
secret = secret_path.read_text(encoding="utf-8").rstrip("\\n")
if not secret or len(secret) > 128 or any(ord(ch) < 33 or ord(ch) == 127 for ch in secret):
    raise SystemExit("DMR master credential is empty or invalid")
text = source.read_text(encoding="utf-8")
if text.count("__DMR_MASTER_PASSWORD__") != 1:
    raise SystemExit("DMR credential marker is invalid")
target.write_text(text.replace("__DMR_MASTER_PASSWORD__", secret), encoding="utf-8")
target.chmod(0o600)
PY
export DMR_ID={s['dmr_id']} DMR_NETWORK_ID={plan['dmrNetworkId']}
export CALLSIGN={s['callsign']} TGIF_HOST={s['master_host']} TGIF_PORT={s['master_port']} TGIF_TG={s['talkgroup']}
export LOCAL_PORT={p['mmdvm_local']} MMDVM_PORT={p['mmdvm_remote']} URF_ENABLED=0
export HEALTH_DIR="$runtime" DMR_ROSTER_FILE="$runtime/dmr-clients.json" DMR_CONTROL_FILE="$runtime/dmr-admin.json"
export DMR_ID_LOOKUP_FILE={root}/mmdvm/DMRIds.dat
python3 {root}/mmdvm/asr-tgif-dmr-proxy.py &
proxy_pid=$!
trap 'kill "$proxy_pid" 2>/dev/null || true; wait "$proxy_pid" 2>/dev/null || true' EXIT INT TERM
export DVSWITCH={root}/mmdvm/DVSwitch.ini
{root}/mmdvm/MMDVM_Bridge "$runtime/MMDVM_Bridge.ini"
"""


def unit_texts(plan: dict[str, Any], owner_marker: str = "") -> dict[str, str]:
    bridge_id, root, p = plan["settings"]["bridge_id"], plan["resources"]["root"], plan["ports"]
    marker = f"# ASR-BRIDGE-OWNER: {bridge_id} {owner_marker}\n" if owner_marker else ""
    vocoder, mmdvm, analog, admin = plan["services"]
    common = """Restart=on-failure
RestartSec=2
User=asr-bridge
Group=asr-bridge
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
"""
    return {
        f"/etc/systemd/system/{vocoder}": marker + f"""[Unit]
Description=ASR direct DMR software vocoder ({bridge_id})
After=network.target

[Service]
Type=simple
WorkingDirectory={root}/vocoder
ExecStart={root}/vocoder/qemu-arm-static {root}/vocoder/md380-emu -S {p['emulator']}
{common}
[Install]
WantedBy=multi-user.target
""",
        f"/etc/systemd/system/{mmdvm}": marker + f"""[Unit]
Description=ASR direct TGIF MMDVM bridge ({bridge_id})
After=network-online.target {vocoder}
Requires={vocoder}
Wants=network-online.target

[Service]
Type=simple
RuntimeDirectory=asr-direct-dmr-{bridge_id}
RuntimeDirectoryMode=0750
WorkingDirectory={root}/mmdvm
ExecStart={root}/mmdvm/run-mmdvm.sh
{common}ReadOnlyPaths={root}/secrets/master-password
ReadWritePaths=/var/log/mmdvm /run/asr-direct-dmr-{bridge_id}

[Install]
WantedBy=multi-user.target
""",
        f"/etc/systemd/system/{analog}": marker + f"""[Unit]
Description=ASR direct DMR Analog Bridge ({bridge_id})
After={mmdvm} {vocoder}
Requires={mmdvm} {vocoder}

[Service]
Type=simple
WorkingDirectory={root}/analog
Environment=AnalogBridgeLogDir=/var/log/dvswitch
ExecStart={root}/analog/Analog_Bridge {root}/analog/Analog_Bridge.ini
{common}ReadWritePaths=/var/log/dvswitch

[Install]
WantedBy=multi-user.target
""",
        f"/etc/systemd/system/{admin}": marker + f"""[Unit]
Description=ASR direct DMR client administration ({bridge_id})
After={mmdvm}
Requires={mmdvm}

[Service]
Type=simple
User=root
Group=root
RuntimeDirectory=asr-standalone-admin/{bridge_id}
RuntimeDirectoryMode=0750
ExecStart=/usr/bin/python3 {root}/admin/asr-dmr-standalone-admin-adapter.py {bridge_id} /run/asr-direct-dmr-{bridge_id}/dmr-clients.json /run/asr-direct-dmr-{bridge_id}/dmr-admin.json {owner_marker}
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadOnlyPaths={root}/admin/asr-dmr-standalone-admin-adapter.py
ReadWritePaths=/run/asr-standalone-admin/{bridge_id} /run/asr-direct-dmr-{bridge_id}

[Install]
WantedBy=multi-user.target
""",
    }
