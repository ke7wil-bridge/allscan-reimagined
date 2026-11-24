#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
for test in \
  asrplus-agent-mode-self-test.py \
  asrplus-ban-result-self-test.py \
  asrplus-command-integrity-self-test.py \
  asrplus-directory-permissions-self-test.py \
  asrplus-import-layout-self-test.py \
  asrplus-ky2-identity-migration-self-test.py \
  asrplus-ky2-watcher-self-test.py \
  asrplus-post-command-status-self-test.py \
  asrplus-required-mode-readiness-self-test.py \
  asrplus-staging-config-self-test.py \
  asrplus-texas-agent-config-self-test.py \
  asrplus-token-self-test.py
do
  python3 "$ROOT/scripts/$test"
done
python3 "$ROOT/scripts/asrplus-texas-isolated-qualification.py" --soak-seconds 1
echo "ASR Plus security and Texas isolated regression gate: PASS"
