#!/bin/bash
set -Eeuo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }
config=/etc/allscan-reimagined/config.json
src_a=/opt/allscan-reimagined/current/scripts/asr-bridge-setup-m17.py
src_b=/usr/local/libexec/allscan-reimagined/asr-bridge-setup-m17.py
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup=/var/backups/allscan-reimagined/johnny2-m17-direct-$stamp
install -d -m 700 "$backup"
cp -a "$config" "$backup/config.json"
cp -a "$src_a" "$backup/asr-bridge-setup-m17.current.py"
cp -a "$src_b" "$backup/asr-bridge-setup-m17.libexec.py"

for file in "$src_a" "$src_b"; do
  perl -0pi -e 's/\n    if settings\.card_type == "standard":\n        entry\.update\(\{"urfReflector": True, "urfGroupId": "urf", "urfName": "URFWIL"\}\)//' "$file"
  ! grep -Fq 'entry.update({"urfReflector": True, "urfGroupId": "urf", "urfName": "URFWIL"})' "$file"
done

python3 - <<'PY'
import json, os, pathlib, tempfile
p=pathlib.Path('/etc/allscan-reimagined/config.json')
st=p.stat()
c=json.loads(p.read_text())
matches=[b for b in c.get('bridges',[]) if b.get('id')=='m17_johnny2']
assert len(matches)==1
b=matches[0]
assert b.get('mode')=='m17' and str(b.get('node'))=='1006'
assert b.get('m17Reflector')=='M17-073' and b.get('m17Host')=='127.0.0.1'
for key in ('urfReflector','urfGroupId','urfName'):
    b.pop(key,None)
b['m17AudioQualified']=True
b['m17QualificationState']='operator_authorized_pending_live_test'
b['liveAudioVerified']=False
fd,tmp=tempfile.mkstemp(prefix='.config.json.m17-direct.',dir=p.parent)
try:
    with os.fdopen(fd,'w') as f:
        json.dump(c,f,indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.chown(tmp,st.st_uid,st.st_gid); os.chmod(tmp,st.st_mode & 0o7777)
    os.replace(tmp,p)
finally:
    if os.path.exists(tmp): os.unlink(tmp)
PY

systemctl enable --now allscan-reimagined-m17-bridge@m17_johnny2.service
for _ in $(seq 1 20); do
  state=$(/usr/local/sbin/allscan-reimagined-m17-bridge-control --bridge m17_johnny2 status 2>/dev/null || true)
  if printf '%s' "$state" | grep -q '"digitalLinked": true'; then break; fi
  sleep 1
done
systemctl is-active --quiet allscan-reimagined-m17-bridge@m17_johnny2.service
/usr/local/sbin/allscan-reimagined-m17-usrp-connector --bridge m17_johnny2 --check >/dev/null
state=$(/usr/local/sbin/allscan-reimagined-m17-bridge-control --bridge m17_johnny2 status)
printf '%s\n' "$state"
printf '%s' "$state" | grep -q '"digitalLinked": true'
asterisk -rx 'rpt stats 1006' | grep -q 'NODE 1006 STATISTICS'

echo "Standalone direct M17 node 1006 enabled. Backup: $backup"
