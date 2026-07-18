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

[ -f "$ALLSCAN_DIR/index.html" ] || needs_reapply=1
[ -f "$ALLSCAN_DIR/asr-api.php" ] || needs_reapply=1
if [ "$needs_reapply" -eq 0 ]; then
  files_match "$MASTER_DIR/server/asr-api.php" "$ALLSCAN_DIR/asr-api.php" || needs_reapply=1
fi
if [ "$needs_reapply" -eq 0 ]; then
  while IFS= read -r master_file; do
    relative=${master_file#"$MASTER_DIR/web/"}
    files_match "$master_file" "$ALLSCAN_DIR/$relative" || { needs_reapply=1; break; }
  done < <(find "$MASTER_DIR/web" -type f | sort)
fi
backend_version=$(sed -n 's/^\$AllScanVersion = "\([^"]*\)";.*/\1/p' "$ALLSCAN_DIR/include/common.php" 2>/dev/null | head -1)
compat_dir="$MASTER_DIR/compat/allscan-${backend_version:-unknown}"
if [ "$needs_reapply" -eq 0 ] && [ -d "$compat_dir" ]; then
  for relative in \
    include/common.php \
    include/UserModel.php \
    user/settings/index.php \
    asr-settings/index.php \
    lookup/index.php \
    echolink-lookup/index.php \
    performance/index.php \
    css/asr-admin.css \
    astapi/server.php \
    astapi/AMI.php; do
    [ -f "$compat_dir/$relative" ] || continue
    files_match "$compat_dir/$relative" "$ALLSCAN_DIR/$relative" || { needs_reapply=1; break; }
  done
fi

if [ "$needs_reapply" -eq 1 ]; then
  logger -t allscan-reimagined "Official AllScan replacement detected; restoring Reimagined overlay"
  /usr/local/sbin/allscan-reimagined-reapply
fi

if [ -x /usr/local/sbin/allscan-reimagined-patch-connected-clients ]; then
  if /usr/local/sbin/allscan-reimagined-patch-connected-clients >/dev/null; then
    logger -t allscan-reimagined "Repaired connected-clients TGIF reconnect cleanup"
    systemctl try-restart connected-clients-daemon.service >/dev/null 2>&1 || true
  fi
fi
