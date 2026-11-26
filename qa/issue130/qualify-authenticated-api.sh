#!/bin/bash
set -euo pipefail

base=http://127.0.0.1/asr
cookies=$(mktemp /root/asr130qa/.cookies.XXXXXX)
secret=''
cleanup() {
  secret=''
  rm -f -- "$cookies"
  sqlite3 /etc/allscan/allscan.db "DELETE FROM user WHERE name='ASR130QA';" >/dev/null
}
trap cleanup EXIT
chmod 600 "$cookies"

# The credential exists only in this isolated guest process and is never logged.
secret="Qa$(python3 -c 'import secrets; print(secrets.token_hex(5))')!"
printf 'ASR130QA\n%s\n' "$secret" \
  | php /opt/allscan-reimagined/current/scripts/asr-stock-admin-bootstrap.php \
      /var/www/html /var/www/html/allscan >/dev/null

unauthorized=$(curl -sS -o /tmp/asr130-unauthorized.json -w '%{http_code}' \
  -X POST -H 'Origin: http://127.0.0.1' \
  -H 'X-ASR-Requested-With: asr-standard-link' \
  --data 'action=standard-bridge-link&bridgeId=qa_ysf&verb=connect' \
  "$base/asr-api.php")
test "$unauthorized" = 403

printf 'name=ASR130QA&pass=%s&Submit=Log+In\n' "$secret" \
  | curl -fsS -c "$cookies" -b "$cookies" -L \
      -H 'Content-Type: application/x-www-form-urlencoded' \
      --data-binary @- "$base/user/" >/dev/null

auth=$(curl -fsS -b "$cookies" "$base/asr-api.php?action=auth-status")
python3 -c 'import json,sys; p=json.load(sys.stdin); assert p["loggedIn"] and p["canModify"]' <<<"$auth"

request_link() {
  local verb=$1 output
  output=$(curl -fsS -b "$cookies" -c "$cookies" \
    -X POST -H 'Origin: http://127.0.0.1' \
    -H 'Referer: http://127.0.0.1/asr/' \
    -H 'X-ASR-Requested-With: asr-standard-link' \
    --data "action=standard-bridge-link&bridgeId=qa_ysf&verb=$verb" \
    "$base/asr-api.php")
  python3 -c 'import json,sys; p=json.load(sys.stdin); assert p["ok"] is True' <<<"$output"
}

request_link connect
python3 - <<'PY'
import json
data = json.load(open('/etc/allscan-reimagined/config.json'))
assert next(b for b in data['bridges'] if b['id'] == 'qa_ysf')['manualDisconnect'] is False
PY
request_link disconnect
python3 - <<'PY'
import json
data = json.load(open('/etc/allscan-reimagined/config.json'))
assert next(b for b in data['bridges'] if b['id'] == 'qa_ysf')['manualDisconnect'] is True
PY

echo 'Authenticated native HTTP API connect/disconnect: PASS'
