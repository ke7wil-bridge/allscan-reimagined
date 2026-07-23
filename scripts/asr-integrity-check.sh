#!/bin/bash
set -Eeuo pipefail

MASTER_DIR="${ASR_MASTER_DIR:-/opt/allscan-reimagined/current}"
if [ "${ASR_INSTALL_LOCK_HELD:-0}" != "1" ]; then
  LOCK_PATH="${ASR_LOCK_PATH:-/run/lock/allscan-reimagined-rollback.lock}"
  mkdir -p "$(dirname "$LOCK_PATH")"
  exec 9>"$LOCK_PATH"
  flock -n 9 || exit 0
fi
if [ -n "${ASR_WEB_ROOT:-}" ]; then
  WEB_ROOT="$ASR_WEB_ROOT"
elif [ -d /var/www/html/allscan ]; then
  WEB_ROOT="/var/www/html"
elif [ -d /srv/http/allscan ]; then
  WEB_ROOT="/srv/http"
else
  exit 0
fi
STOCK_ALLSCAN_DIR="${STOCK_ALLSCAN_DIR:-$WEB_ROOT/allscan}"
ASR_WEB_DIR="${ASR_WEB_DIR:-$WEB_ROOT/asr}"
[ -d "$STOCK_ALLSCAN_DIR" ] || exit 0

needs_reapply=0
files_match() {
  local master="$1" installed="$2" master_size installed_size
  [ -f "$installed" ] || return 1
  master_size=$(stat -c %s "$master" 2>/dev/null) || return 1
  installed_size=$(stat -c %s "$installed" 2>/dev/null) || return 1
  [ "$master_size" = "$installed_size" ] || return 1
  # Large images are immutable build assets. Avoid rereading them every check.
  [ "$master_size" -gt 1048576 ] && return 0
  cmp -s "$master" "$installed"
}

[ -f "$ASR_WEB_DIR/index.html" ] || needs_reapply=1
[ -f "$ASR_WEB_DIR/asr-api.php" ] || needs_reapply=1
if [ "${ASR_INTEGRITY_WEB_ONLY:-0}" != "1" ]; then
[ -x /usr/local/sbin/allscan-reimagined-rollback ] || needs_reapply=1
[ -x /usr/local/sbin/allscan-reimagined-bridge-control ] || needs_reapply=1
[ -x /usr/local/sbin/allscan-reimagined-favorites-update ] || needs_reapply=1
[ -d /run/allscan-reimagined-bridge-control ] || needs_reapply=1
[ "$(stat -c '%U:%G:%a' /run/allscan-reimagined-bridge-control 2>/dev/null)" = "root:root:755" ] || needs_reapply=1
[ -f /etc/systemd/system/allscan-reimagined-rollback@.service ] || needs_reapply=1
if python3 - /etc/allscan-reimagined/config.json <<'PY'
import json
import sys
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(1)
raise SystemExit(
    0 if any(
        isinstance(item, dict) and item.get("cardType") == "dmr_net"
        for item in payload.get("bridges", [])
    ) else 1
)
PY
then
  [ -f /etc/systemd/system/allscan-reimagined-dmr-net-live.service ] || needs_reapply=1
  systemctl is-enabled --quiet allscan-reimagined-dmr-net-live.service || needs_reapply=1
fi
if [ -f "$MASTER_DIR/scripts/asr-release-check.py" ]; then
  [ -x /usr/local/sbin/allscan-reimagined-release-check ] || needs_reapply=1
  [ -f /etc/systemd/system/allscan-reimagined-release-check.service ] || needs_reapply=1
  [ -f /etc/systemd/system/allscan-reimagined-release-check.timer ] || needs_reapply=1
  systemctl is-enabled --quiet allscan-reimagined-release-check.timer || needs_reapply=1
  systemctl is-active --quiet allscan-reimagined-release-check.timer || needs_reapply=1
fi
fi
if [ "$needs_reapply" -eq 0 ]; then
  files_match "$MASTER_DIR/server/asr-api.php" "$ASR_WEB_DIR/asr-api.php" || needs_reapply=1
fi
if [ "$needs_reapply" -eq 0 ]; then
  while IFS= read -r master_file; do
    relative=${master_file#"$MASTER_DIR/web/"}
    files_match "$master_file" "$ASR_WEB_DIR/$relative" || { needs_reapply=1; break; }
  done < <(find "$MASTER_DIR/web" -type f | sort)
fi
backend_version=$(sed -n 's/^\$AllScanVersion = "\([^"]*\)";.*/\1/p' "$STOCK_ALLSCAN_DIR/include/common.php" 2>/dev/null | head -1)
compat_dir="$MASTER_DIR/compat/allscan-${backend_version:-unknown}"
if [ ! -d "$compat_dir" ]; then
  logger -t allscan-reimagined \
    "No exact compatibility layer for stock AllScan ${backend_version:-unknown}; keeping current /asr"
  exit 1
fi
if [ "$needs_reapply" -eq 0 ]; then
  for relative in \
    include/common.php \
    include/dbUtils.php \
    include/CfgModel.php \
    include/UserModel.php \
    user/settings/index.php \
    asr-settings/index.php \
    asr-settings/rollback-status.php \
    asr-instructions/index.php \
    lookup/index.php \
    echolink-lookup/index.php \
    performance/index.php \
    css/asr-admin.css \
    astapi/server.php \
    astapi/AMI.php; do
    [ -f "$compat_dir/$relative" ] || continue
    files_match "$compat_dir/$relative" "$ASR_WEB_DIR/$relative" || { needs_reapply=1; break; }
  done
fi

if [ "$needs_reapply" -eq 1 ]; then
  logger -t allscan-reimagined "ASR tree is stale; rebuilding /asr from untouched stock AllScan"
  if [ "${ASR_INTEGRITY_WEB_ONLY:-0}" = "1" ]; then
    ASR_INSTALL_LOCK_HELD=1 ASR_REAPPLY_WEB_ONLY=1 \
      bash "${ASR_REAPPLY_COMMAND:-$MASTER_DIR/scripts/asr-reapply.sh}"
  else
    ASR_INSTALL_LOCK_HELD=1 /usr/local/sbin/allscan-reimagined-reapply
  fi
fi

if [ "${ASR_INTEGRITY_WEB_ONLY:-0}" = "1" ]; then
  exit 0
fi

if [ -x /usr/local/sbin/allscan-reimagined-patch-connected-clients ]; then
  reconnect_patch_status=0
  /usr/local/sbin/allscan-reimagined-patch-connected-clients >/dev/null || reconnect_patch_status=$?
  case "$reconnect_patch_status" in
    0)
      logger -t allscan-reimagined "Repaired connected-clients TGIF reconnect cleanup"
      systemctl try-restart connected-clients-daemon.service >/dev/null 2>&1 || true
      ;;
    3)
      # The companion daemon is absent or its reconnect cleanup is already complete.
      ;;
    *)
      logger -t allscan-reimagined \
        "Connected-clients reconnect repair failed with status $reconnect_patch_status"
      exit "$reconnect_patch_status"
      ;;
  esac
fi

if [ -x /usr/local/sbin/allscan-reimagined-migrate-tgif-environment ]; then
  if /usr/local/sbin/allscan-reimagined-migrate-tgif-environment >/dev/null; then
    logger -t allscan-reimagined "Migrated TGIF daemon credentials to protected environment storage"
    systemctl daemon-reload
    systemctl try-restart connected-clients-daemon.service >/dev/null 2>&1 || true
  else
    migration_status=$?
    [ "$migration_status" -eq 3 ] || exit "$migration_status"
  fi
fi

if [ -x /usr/local/sbin/allscan-reimagined-favorites-permissions ]; then
  ASR_ALLSCAN_DIR="$ASR_WEB_DIR" \
    /usr/local/sbin/allscan-reimagined-favorites-permissions --apply >/dev/null
fi
