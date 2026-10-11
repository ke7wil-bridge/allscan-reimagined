#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
python3 scripts/asr-net-bridge-plumbing-acceptance.py --self-test
python3 scripts/asr-net-bridge-mode-control-self-test.py
python3 scripts/asr-net-bridge-mode-self-test.py
python3 scripts/asr-container-provisioning-self-test.py
python3 scripts/asr-bridge-setup-digital-self-test.py
python3 scripts/asr-bridge-setup-m17-self-test.py
python3 scripts/asr-bridge-stale-status-self-test.py
python3 scripts/asr-bridge-control.py --self-test
python3 scripts/asr-ysf-bridge-control.py --self-test
python3 scripts/asr-p25-bridge-control.py self-test
python3 scripts/asr-nxdn-bridge-control.py self-test
python3 scripts/asr-m17-bridge-control.py --self-test
python3 scripts/asr-m17-usrp-connector.py --self-test
node scripts/asr-bridge-dashboard-self-test.mjs
python3 scripts/asr-bridge-ui-regression-self-test.py
python3 scripts/asr-zello-client-management-self-test.py
python3 scripts/asr-deployment-source-consistency-self-test.py
# Always test repository Talker code. Never substitute the deployed copy,
# because doing so would allow precisely the source/live divergence this gate
# is intended to catch.
if command -v php >/dev/null 2>&1; then
  php scripts/asr-talker-identity-self-test.php
elif command -v docker >/dev/null 2>&1 && docker inspect allscan-reimagined-layered-allscan-1 >/dev/null 2>&1; then
  image=$(docker inspect -f '{{.Config.Image}}' allscan-reimagined-layered-allscan-1)
  docker run --rm --entrypoint php -v "$ROOT:/src:ro" -w /src "$image" scripts/asr-talker-identity-self-test.php
else
  echo 'FAIL: PHP unavailable for Talker identity regression test' >&2
  exit 1
fi
bash scripts/asr-side-by-side-self-test.sh
if command -v docker >/dev/null 2>&1 && docker inspect allscan-reimagined-layered-allscan-1 >/dev/null 2>&1; then
  python3 scripts/asr-deployment-source-consistency-self-test.py --live
fi
git diff --check
echo 'PASS: Beta 8 bridge + Talker regression gate'
