#!/bin/bash
set -Eeuo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }
helper=/usr/local/libexec/allscan-reimagined/asr-bridge-setup-helper.py
[ -f "$helper" ]
systemctl is-active --quiet mrefd-073.service
ss -lunH | awk '{print $4}' | grep -Eq '(^|:)17000$'

python3 - <<'PY'
import json
c=json.load(open('/etc/allscan-reimagined/config.json'))
assert not any(b.get('id') == 'm17_johnny2' for b in c.get('bridges', []))
assert not any(str(b.get('node')) == '1006' for b in c.get('bridges', []))
PY
! grep -Eq '^[[:space:]]*1006[[:space:]]*=|^[[:space:]]*\[1006\]' /etc/asterisk/rpt.conf

payload='{"bridgeId":"m17_johnny2","callsign":"N1YKT","reflector":"M17-073","host":"127.0.0.1","port":17000,"module":"A","title":"M17-073 Bridge","mainNode":623123,"bridgeNode":1006,"bridgeRole":"standard"}'
plan=$(printf '%s' "$payload" | python3 "$helper" m17-plan)
digest=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["digest"])')
request=$(printf '%s' "$payload" | python3 -c 'import json,sys; p=json.load(sys.stdin); p["planDigest"]=sys.argv[1]; print(json.dumps(p,separators=(",",":")))' "$digest")
result=$(printf '%s' "$request" | python3 "$helper" m17-install)
printf '%s\n' "$result"

systemctl is-active --quiet allscan-reimagined-m17-bridge@m17_johnny2.service
asterisk -rx 'rpt stats 1006' | grep -q 'NODE 1006 STATISTICS'
python3 - <<'PY'
import json
c=json.load(open('/etc/allscan-reimagined/config.json'))
b=next(b for b in c.get('bridges', []) if b.get('id') == 'm17_johnny2')
assert b.get('mode') == 'm17' and str(b.get('node')) == '1006'
assert b.get('m17Reflector') == 'M17-073' and b.get('m17Module') == 'A'
print(json.dumps(b, sort_keys=True))
PY
echo 'M17-073 installed as ASR-managed node 1006.'
