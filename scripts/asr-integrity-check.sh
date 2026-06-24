#!/bin/bash
set -Eeuo pipefail

MASTER_DIR="/opt/allscan-reimagined/current"
if [ -d /var/www/html/allscan ]; then
  ALLSCAN_DIR="/var/www/html/allscan"
elif [ -d /srv/http/allscan ]; then
  ALLSCAN_DIR="/srv/http/allscan"
else
  exit 0
fi

needs_reapply=0
[ -f "$ALLSCAN_DIR/index.html" ] || needs_reapply=1
[ -f "$ALLSCAN_DIR/asr-api.php" ] || needs_reapply=1
if [ "$needs_reapply" -eq 0 ]; then
  cmp -s "$MASTER_DIR/server/asr-api.php" "$ALLSCAN_DIR/asr-api.php" || needs_reapply=1
fi
backend_version=$(sed -n 's/^\$AllScanVersion = "\([^"]*\)";.*/\1/p' "$ALLSCAN_DIR/include/common.php" 2>/dev/null | head -1)
if [ -d "$MASTER_DIR/compat/allscan-${backend_version:-unknown}" ]; then
  grep -q 'asrAdminRuntimeConfig' "$ALLSCAN_DIR/include/common.php" 2>/dev/null || needs_reapply=1
  grep -q 'loginThrottleMaxFailures' "$ALLSCAN_DIR/include/UserModel.php" 2>/dev/null || needs_reapply=1
fi

if [ "$needs_reapply" -eq 1 ]; then
  logger -t allscan-reimagined "Official AllScan replacement detected; restoring Reimagined overlay"
  /usr/local/sbin/allscan-reimagined-reapply
fi
