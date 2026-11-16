#!/bin/bash
set -Eeuo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this cutover as root." >&2
  exit 1
fi

helper=/usr/local/libexec/allscan-reimagined/asr-bridge-setup-helper.py
config=/etc/allscan-reimagined/config.json
rpt=/etc/asterisk/rpt.conf
modules=/etc/asterisk/modules.conf
key_file=/opt/allscan-reimagined-bridges/urf/urf/secrets/tgif-password
hoip_file=/etc/asterisk/custom/rpt/hoip-1002.conf
hoip_reserved=/etc/asterisk/custom/rpt/hoip-1002.conf.reserved
legacy_units=(
  allscan-reimagined-urf-tgif-urf.service
  allscan-reimagined-tgif-urf.service
  allscan-reimagined-tcd-urf.service
  allscan-reimagined-urfd-urf.service
)
bridge_ids=(dmr_johnny2 ysf_johnny2 p25_johnny2 nxdn_johnny2)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup=/var/backups/allscan-reimagined/johnny2-production-cutover-$stamp
work=$(mktemp -d /run/johnny2-cutover.XXXXXX)
success=0
cutover_started=0

cleanup_work() {
  find "$work" -type f -exec shred -u -- {} + 2>/dev/null || true
  rmdir "$work" 2>/dev/null || true
}

rollback() {
  status=$?
  trap - ERR INT TERM
  if [ "$cutover_started" -eq 1 ] && [ "$success" -eq 0 ]; then
    echo "Cutover failed; restoring the pre-cutover URF state." >&2
    python3 - "${bridge_ids[@]}" <<'PY' || true
import json
import pathlib
import shutil
import subprocess
import sys

state = pathlib.Path("/var/lib/allscan-reimagined/bridge-setup/installations")
ids = sys.argv[1:]
services = set()
owned_files = set()
for bridge_id in ids:
    record = state / f"{bridge_id}.json"
    try:
        payload = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    for service in payload.get("services", []):
        if isinstance(service, str) and bridge_id in service:
            services.add(service)
    for raw in payload.get("ownedFiles", []):
        if not isinstance(raw, str):
            continue
        path = pathlib.Path(raw)
        # Records are trusted only for bridge-specific absolute files.  Shared
        # configuration is restored from the archive below and is never
        # deleted from a failed transaction.
        if (path.is_absolute() and bridge_id in str(path)
                and str(path) not in {"/etc/allscan-reimagined/config.json",
                                      "/etc/asterisk/rpt.conf",
                                      "/etc/asterisk/modules.conf"}):
            owned_files.add(path)
for service in sorted(services):
    subprocess.run(["systemctl", "disable", "--now", service], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for path in sorted(owned_files, key=lambda item: len(item.parts), reverse=True):
    if path.is_file() or path.is_symlink():
        path.unlink(missing_ok=True)

allowed_roots = []
for bridge_id in ids:
    allowed_roots.extend([
        pathlib.Path("/opt/allscan-reimagined-bridges") / bridge_id,
        pathlib.Path(f"/opt/P25Gateway_{bridge_id}"),
        pathlib.Path(f"/opt/NXDNGateway_{bridge_id}"),
        pathlib.Path(f"/opt/YSFGateway_{bridge_id}"),
        pathlib.Path(f"/opt/Analog_Bridge_{bridge_id}"),
        pathlib.Path(f"/opt/MMDVM_Bridge_{bridge_id}"),
        pathlib.Path(f"/opt/md380-emu_{bridge_id}"),
    ])
for root in allowed_roots:
    if root.exists() and not root.is_symlink():
        shutil.rmtree(root)
for bridge_id in ids:
    (pathlib.Path("/var/lib/allscan-reimagined/bridge-lifecycle/manifests") / f"{bridge_id}.json").unlink(missing_ok=True)
    (pathlib.Path("/run/allscan-reimagined-bridge-lifecycle/intents") / f"{bridge_id}.json").unlink(missing_ok=True)
    admin = pathlib.Path("/run/asr-standalone-admin") / bridge_id
    if admin.exists() and not admin.is_symlink():
        shutil.rmtree(admin)
    (state / f"{bridge_id}.json").unlink(missing_ok=True)
PY
    tar -xzf "$backup/pre-cutover-root.tar.gz" -C / || true
    [ ! -f "$hoip_reserved" ] || rm -f -- "$hoip_reserved"
    systemctl daemon-reload || true
    systemctl restart asterisk.service || true
    for unit in "${legacy_units[@]}"; do
      if systemctl cat "$unit" >/dev/null 2>&1; then
        systemctl enable "$unit" >/dev/null 2>&1 || true
        systemctl restart "$unit" >/dev/null 2>&1 || true
      fi
    done
    echo "Rollback attempted from: $backup" >&2
  fi
  cleanup_work
  exit "$status"
}
trap rollback ERR INT TERM
trap cleanup_work EXIT

for required in "$helper" "$config" "$rpt" "$modules" "$key_file" "$hoip_file"; do
  [ -f "$required" ] && [ ! -L "$required" ] || { echo "Required file is missing or unsafe: $required" >&2; exit 1; }
done
grep -Fq '"architecture": "direct-dvswitch"' /usr/local/libexec/allscan-reimagined/asr-bridge-setup-digital-install.py
[ -s "$key_file" ] || { echo "The stored TGIF hotspot key is unavailable." >&2; exit 1; }

python3 - "$config" "$rpt" "$hoip_file" "$hoip_reserved" <<'PY'
import json, pathlib, re, sys
config = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if str(config.get("node")) != "623123":
    raise SystemExit("Refusing cutover: primary ASL node is not 623123")
bridges = [b for b in config.get("bridges", []) if isinstance(b, dict)]
legacy = [b for b in bridges if b.get("id") == "urf" and b.get("mode") == "urf" and str(b.get("node")) == "1001"]
if len(legacy) != 1:
    raise SystemExit("Refusing cutover: expected legacy URF bridge was not found exactly once")
targets = {"dmr_johnny2", "ysf_johnny2", "p25_johnny2", "nxdn_johnny2"}
if any(b.get("id") in targets for b in bridges):
    raise SystemExit("Refusing cutover: a replacement bridge already exists")
text = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
mappings_1001 = re.findall(r"(?m)^[ \t]*1001[ \t]*=.*$", text)
sections_1001 = re.findall(r"(?m)^[ \t]*\[1001\][^\n]*$", text)
if len(mappings_1001) != 1 or len(sections_1001) != 1:
    raise SystemExit("Refusing cutover: node 1001 is not an exact single legacy mapping and section")
if len(re.findall(r"(?m)^[ \t]*1002[ \t]*=.*$", text)) != 1:
    raise SystemExit("Refusing cutover: node 1002 does not have its exact single legacy mapping")
if re.search(r"(?m)^[ \t]*\[1002\]", text):
    raise SystemExit("Refusing cutover: node 1002 unexpectedly has a section in the main rpt.conf")
hoip = pathlib.Path(sys.argv[3])
reserved = pathlib.Path(sys.argv[4])
if reserved.exists() or reserved.is_symlink():
    raise SystemExit("Refusing cutover: the HOIP reservation target already exists")
hoip_text = hoip.read_text(encoding="utf-8")
if len(re.findall(r"(?m)^[ \t]*\[1002\][^\n]*$", hoip_text)) != 1 or "rxchannel = Local/pseudo" not in hoip_text:
    raise SystemExit("Refusing cutover: the dormant HOIP node file is not the audited Local/pseudo stanza")
if '#tryinclude "custom/rpt/*.conf"' not in text:
    raise SystemExit("Refusing cutover: the custom node include method changed")
for reserved in ("1006", "1007"):
    if re.search(rf"(?m)^[ \t]*{reserved}[ \t]*=|^[ \t]*\[{reserved}\]", text):
        raise SystemExit(f"Refusing cutover: reserved node {reserved} is active")
asterisk_root = pathlib.Path("/etc/asterisk")
protected_hits = []
for path in asterisk_root.rglob("*.conf"):
    if path.is_file() and not path.is_symlink() and "623122" in path.read_text(encoding="utf-8", errors="replace"):
        protected_hits.append(str(path))
if protected_hits:
    raise SystemExit("Refusing cutover: protected node 623122 unexpectedly exists in " + ", ".join(protected_hits))
print("PASS: exact legacy topology; protected node 623122 is absent")
PY

for unit in allscan-reimagined-urfd-urf.service allscan-reimagined-tcd-urf.service allscan-reimagined-tgif-urf.service; do
  systemctl is-active --quiet "$unit" || { echo "Legacy service is not active: $unit" >&2; exit 1; }
done

install -d -o root -g root -m 700 "$backup"
tar -czf "$backup/pre-cutover-root.tar.gz" -C / \
  etc/allscan-reimagined etc/asterisk \
  var/lib/allscan-reimagined/bridge-setup \
  opt/allscan-reimagined-bridges/urf \
  etc/systemd/system/allscan-reimagined-urfd-urf.service \
  etc/systemd/system/allscan-reimagined-tcd-urf.service \
  etc/systemd/system/allscan-reimagined-tgif-urf.service
sha256sum "$backup/pre-cutover-root.tar.gz" > "$backup/SHA256SUMS"
chmod 600 "$backup/pre-cutover-root.tar.gz" "$backup/SHA256SUMS"
tar -tzf "$backup/pre-cutover-root.tar.gz" >/dev/null
echo "PASS: fresh cutover backup: $backup"

printf '%s\n' absent > "$work/protected-before.txt"

cutover_started=1
for unit in "${legacy_units[@]}"; do
  systemctl disable --now "$unit" >/dev/null 2>&1 || true
done

python3 - "$config" "$rpt" "$hoip_file" "$hoip_reserved" <<'PY'
import json, os, pathlib, re, sys, tempfile

def atomic(path, data):
    p=pathlib.Path(path); fd,tmp=tempfile.mkstemp(prefix=f".{p.name}.",dir=p.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as h:
            h.write(data); h.flush(); os.fchmod(h.fileno(),p.stat().st_mode & 0o777); os.fsync(h.fileno())
        os.replace(tmp,p)
    finally: pathlib.Path(tmp).unlink(missing_ok=True)

cp=pathlib.Path(sys.argv[1]); c=json.loads(cp.read_text(encoding="utf-8"))
old=c.get("bridges",[])
new=[b for b in old if not (isinstance(b,dict) and b.get("id")=="urf" and b.get("mode")=="urf" and str(b.get("node"))=="1001")]
if len(old)-len(new)!=1: raise SystemExit("legacy config removal was not exact")
c["bridges"]=new; atomic(cp,json.dumps(c,indent=2,sort_keys=True)+"\n")

rp=pathlib.Path(sys.argv[2]); text=rp.read_text(encoding="utf-8")
for node in ("1001","1002"):
    text,count=re.subn(rf"(?m)^[ \t]*{node}[ \t]*=.*\n?","",text,count=1)
    if count!=1: raise SystemExit(f"legacy mapping removal failed for {node}")
text,count=re.subn(r"(?ms)^[ \t]*\[1001\][^\n]*\n.*?(?=^[ \t]*\[|\Z)","",text,count=1)
if count!=1: raise SystemExit("legacy section removal failed for 1001")
atomic(rp,text)
hoip=pathlib.Path(sys.argv[3]); reserved=pathlib.Path(sys.argv[4])
if reserved.exists() or not hoip.is_file() or hoip.is_symlink():
    raise SystemExit("dormant HOIP reservation file changed during cutover")
os.replace(hoip,reserved)
PY

run_bridge() {
  mode=$1
  request=$2
  plan="$work/$mode-plan.json"
  result="$work/$mode-result.json"
  "$helper" "$mode-plan" < "$request" > "$plan"
  python3 - "$request" "$plan" "$mode" "$key_file" > "$work/$mode-install.json" <<'PY'
import json, pathlib, sys
request=json.loads(pathlib.Path(sys.argv[1]).read_text())
plan=json.loads(pathlib.Path(sys.argv[2]).read_text())
request["planDigest"]=plan["digest"]
if sys.argv[3]=="dmr":
    request["tgifPassword"]=pathlib.Path(sys.argv[4]).read_text(encoding="utf-8").rstrip("\n")
print(json.dumps(request))
PY
  chmod 600 "$work/$mode-install.json"
  "$helper" "$mode-install" < "$work/$mode-install.json" > "$result"
  python3 - "$result" "$mode" <<'PY'
import json, pathlib, sys
p=json.loads(pathlib.Path(sys.argv[1]).read_text())
if not p.get("ok") or not p.get("provisioningVerified"):
    raise SystemExit(f"{sys.argv[2].upper()} provisioning did not verify")
print(f"PASS: {sys.argv[2].upper()} bridge provisioned on node {p['bridgeNode']}")
PY
}

python3 - > "$work/dmr-request.json" <<'PY'
import json
print(json.dumps({"bridgeId":"dmr_johnny2","bridgePath":"standard","bridgeRole":"standard","callsign":"N1YKT","digitalId":3205161,"hotspotEssid":3,"destination":42450,"host":"tgif.network","port":62031,"timeslot":2,"colorCode":1,"dmrToAslGainDb":5,"aslToDmrGainDb":0,"title":"TGIF DMR Bridge","bridgeNode":1001,"network":"tgif"}))
PY
python3 - > "$work/ysf-request.json" <<'PY'
import json
print(json.dumps({"bridgeId":"ysf_johnny2","bridgeRole":"standard","callsign":"N1YKT","digitalId":3205161,"destination":42450,"host":"137.184.28.141","port":42020,"name":"US-N1YKT","title":"YSF Bridge","bridgeNode":1003}))
PY
python3 - > "$work/p25-request.json" <<'PY'
import json
print(json.dumps({"bridgeId":"p25_johnny2","bridgeRole":"standard","callsign":"N1YKT","digitalId":3205161,"destination":73,"host":"162.213.130.93","port":41000,"title":"P25 Bridge","bridgeNode":1004}))
PY
python3 - > "$work/nxdn-request.json" <<'PY'
import json
print(json.dumps({"bridgeId":"nxdn_johnny2","bridgeRole":"standard","callsign":"N1YKT","digitalId":3205161,"destination":7486,"identity":2312,"host":"162.213.130.93","port":41400,"title":"NXDN Bridge","bridgeNode":1005}))
PY
chmod 600 "$work"/*-request.json

run_bridge dmr "$work/dmr-request.json"
run_bridge ysf "$work/ysf-request.json"
run_bridge p25 "$work/p25-request.json"
run_bridge nxdn "$work/nxdn-request.json"

python3 - "$config" "$rpt" "$work/protected-before.txt" "$hoip_file" "$hoip_reserved" <<'PY'
import json,pathlib,re,sys
c=json.loads(pathlib.Path(sys.argv[1]).read_text())
expected={"dmr_johnny2":"1001","ysf_johnny2":"1003","p25_johnny2":"1004","nxdn_johnny2":"1005"}
found={str(b.get("id")):str(b.get("node")) for b in c.get("bridges",[]) if isinstance(b,dict)}
if any(found.get(k)!=v for k,v in expected.items()) or "urf" in found:
    raise SystemExit("final ASR bridge map is incorrect")
t=pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
for node in ("1002","1006","1007"):
    if re.search(rf"(?m)^[ \t]*{node}[ \t]*=|^[ \t]*\[{node}\]",t):
        raise SystemExit(f"reserved node {node} became active")
if pathlib.Path(sys.argv[3]).read_text().strip() != "absent":
    raise SystemExit("protected-node baseline marker is invalid")
protected_hits=[]
for path in pathlib.Path("/etc/asterisk").rglob("*.conf"):
    if path.is_file() and not path.is_symlink() and "623122" in path.read_text(encoding="utf-8",errors="replace"):
        protected_hits.append(str(path))
if protected_hits: raise SystemExit("protected node 623122 was introduced during cutover")
if pathlib.Path(sys.argv[4]).exists() or not pathlib.Path(sys.argv[5]).is_file():
    raise SystemExit("HOIP node 1002 was not preserved as a disabled reservation")
print("PASS: final node map, reservations, and protected node 623122")
PY

for node in 1001 1003 1004 1005; do
  asterisk -rx "rpt stats $node" | grep -Fq "NODE $node STATISTICS"
done
for unit in allscan-reimagined-urfd-urf.service allscan-reimagined-tcd-urf.service allscan-reimagined-tgif-urf.service; do
  ! systemctl is-active --quiet "$unit" || { echo "Legacy URF unit remained active: $unit" >&2; false; }
done

success=1
trap - ERR INT TERM
cleanup_work
trap - EXIT
echo "Johnny2 production cutover provisioned successfully."
echo "Active nodes: DMR 1001, YSF 1003, P25 1004, NXDN 1005."
echo "Reserved only: FreeDMR 1002, M17 1006, HOIP 1007."
echo "Protected node 623122 remains absent and untouched."
echo "Rollback backup: $backup"
echo "Live bidirectional audio and reboot recovery still require operator verification."
