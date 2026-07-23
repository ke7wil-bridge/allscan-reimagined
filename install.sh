#!/bin/bash
set -Eeuo pipefail

ASR_VERSION="1.0.0-beta.6"
ASR_BACKUP_RETENTION="${ASR_BACKUP_RETENTION:-10}"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PAYLOAD_DIR="$SCRIPT_DIR/payload"
RELEASE_DIR="/opt/allscan-reimagined/releases/$ASR_VERSION"
BACKUP_DIR="/root/allscan-reimagined-backups/$(date +%Y%m%d-%H%M%S)"
OFFICIAL_INSTALLER_URL="https://raw.githubusercontent.com/davidgsd/AllScan/main/AllScanInstallUpdate.php"
CHANGES_STARTED=0
RELEASE_STAGE=""
RELEASE_PREVIOUS=""
RELEASE_REPLACED=0
RELEASE_PREVIOUS_ARMED=0
CURRENT_LINK_PREVIOUS=""
CURRENT_LINK_HAD_TARGET=0
CURRENT_LINK_CHANGED=0
MIGRATED_STOCK_ARMED=0
MIGRATED_STOCK_DIR=""
REAPPLY_PATH_WAS_ENABLED=0
REAPPLY_PATH_WAS_ACTIVE=0
REAPPLY_TIMER_WAS_ENABLED=0
REAPPLY_TIMER_WAS_ACTIVE=0
REAPPLY_STATES_CAPTURED=0
ALLSCAN_OLD_ARMED=0
ALLSCAN_OLD_BACKUP=""
STOCK_LOGIN_OPTED_IN=0
ASR_WEB_WAS_PRESENT=0

fail() { printf 'ERROR: %s\n' "$*" >&2; return 1; }
validate_command() {
  local description="$1"
  shift
  if ! "$@"; then
    fail "Validation failed: $description"
  fi
}
restore_runtime_backup() {
  [ -d "$BACKUP_DIR/runtime" ] || return 0
  mkdir -p "$ASR_WEB_DIR"
  for runtime_file in bridge-live.json connected-clients.json asr-connected-clients.json zello-status-data.json; do
    [ -f "$BACKUP_DIR/runtime/$runtime_file" ] && cp -p "$BACKUP_DIR/runtime/$runtime_file" "$ASR_WEB_DIR/$runtime_file"
  done
  for runtime_dir in img asr-user-content; do
    [ -d "$BACKUP_DIR/runtime/$runtime_dir" ] && cp -a "$BACKUP_DIR/runtime/$runtime_dir" "$ASR_WEB_DIR/$runtime_dir"
  done
}

restore_shared_backup() {
  [ -d "$BACKUP_DIR/runtime" ] || return 0
  if [ -f "$BACKUP_DIR/runtime/etc-favorites.ini" ]; then
    mkdir -p /etc/allscan
    install -o root -g "$WEB_GROUP" -m 664 "$BACKUP_DIR/runtime/etc-favorites.ini" /etc/allscan/favorites.ini
  elif [ -f "$BACKUP_DIR/runtime/etc-favorites.ini.absent" ]; then
    rm -f /etc/allscan/favorites.ini
  fi
  if [ -f "$BACKUP_DIR/runtime/tgif-daemon-environment" ]; then
    mkdir -p /etc/allscan-reimagined
    install -o root -g root -m 600 "$BACKUP_DIR/runtime/tgif-daemon-environment" /etc/allscan-reimagined/connected-clients-daemon.env
  elif [ -f "$BACKUP_DIR/runtime/tgif-daemon-environment.absent" ]; then
    rm -f /etc/allscan-reimagined/connected-clients-daemon.env
  fi
  if [ -f "$BACKUP_DIR/runtime/tgif-token-dropin" ]; then
    mkdir -p /etc/systemd/system/connected-clients-daemon.service.d
    install -o root -g root -m 644 "$BACKUP_DIR/runtime/tgif-token-dropin" /etc/systemd/system/connected-clients-daemon.service.d/tgif-token.conf
  elif [ -f "$BACKUP_DIR/runtime/tgif-token-dropin.absent" ]; then
    rm -f /etc/systemd/system/connected-clients-daemon.service.d/tgif-token.conf
  fi
  if [ -f "$BACKUP_DIR/allscan.db" ]; then
    install -d -o root -g "$WEB_GROUP" -m 775 /etc/allscan
    install -o "$WEB_GROUP" -g "$WEB_GROUP" -m 660 \
      "$BACKUP_DIR/allscan.db" /etc/allscan/allscan.db
  elif [ -f "$BACKUP_DIR/allscan.db.absent" ]; then
    rm -f /etc/allscan/allscan.db
  fi
  install -d -o root -g root -m 755 /etc/allscan-reimagined
  for protected in config.json secrets.json station-map-cache.json; do
    if [ -f "$BACKUP_DIR/runtime/protected/$protected" ]; then
      cp -p "$BACKUP_DIR/runtime/protected/$protected" "/etc/allscan-reimagined/$protected"
    elif [ -f "$BACKUP_DIR/runtime/protected/$protected.absent" ]; then
      rm -f "/etc/allscan-reimagined/$protected"
    fi
  done
  systemctl daemon-reload 2>/dev/null || true
}

restore_prior_reapply_units() {
  local unit backup
  for unit in \
    allscan-reimagined-reapply.service \
    allscan-reimagined-reapply.path \
    allscan-reimagined-reapply.timer; do
    backup="$BACKUP_DIR/runtime/systemd/$unit"
    if [ -f "$backup" ]; then
      install -o root -g root -m 644 "$backup" "/etc/systemd/system/$unit"
    elif [ -f "$backup.absent" ]; then
      rm -f "/etc/systemd/system/$unit"
    fi
  done
}

remove_asr_managed_wiring() {
  systemctl disable --now \
    allscan-reimagined-reapply.path \
    allscan-reimagined-reapply.timer \
    allscan-reimagined-release-check.timer \
    allscan-reimagined-dmr-net-live.service \
    allscan-reimagined-bridge-clients.timer \
    allscan-reimagined-connected-clients-maintenance.timer >/dev/null 2>&1 || true
  command -v a2disconf >/dev/null 2>&1 \
    && a2disconf allscan-reimagined >/dev/null 2>&1 || true
  rm -f \
    /usr/local/bin/allscan_wt_clients.sh \
    /usr/local/sbin/allscan-reimagined-* \
    /etc/systemd/system/allscan-reimagined-*.service \
    /etc/systemd/system/allscan-reimagined-*.path \
    /etc/systemd/system/allscan-reimagined-*.timer \
    /etc/cron.d/allscan-reimagined-* \
    /etc/sudoers.d/allscan-reimagined \
    /etc/apache2/conf-available/allscan-reimagined.conf
  systemctl daemon-reload >/dev/null 2>&1 || true
}

restore_reapply_unit_states() {
  local unit enabled active
  for unit in path timer; do
    if [ "$unit" = "path" ]; then
      enabled=$REAPPLY_PATH_WAS_ENABLED
      active=$REAPPLY_PATH_WAS_ACTIVE
    else
      enabled=$REAPPLY_TIMER_WAS_ENABLED
      active=$REAPPLY_TIMER_WAS_ACTIVE
    fi
    if [ "$enabled" -eq 1 ]; then
      systemctl enable "allscan-reimagined-reapply.$unit" >/dev/null 2>&1 || true
    else
      systemctl disable "allscan-reimagined-reapply.$unit" >/dev/null 2>&1 || true
    fi
    if [ "$active" -eq 1 ]; then
      systemctl start "allscan-reimagined-reapply.$unit" >/dev/null 2>&1 || true
    else
      systemctl stop "allscan-reimagined-reapply.$unit" >/dev/null 2>&1 || true
    fi
  done
}

rollback_on_error() {
  status="${1:-$?}"
  trap - ERR INT TERM
  set +e
  [ -n "$RELEASE_STAGE" ] && rm -rf "$RELEASE_STAGE"
  if [ "$RELEASE_REPLACED" -eq 1 ]; then
    rm -rf "$RELEASE_DIR"
  fi
  if [ "$RELEASE_PREVIOUS_ARMED" -eq 1 ] && [ -d "$RELEASE_PREVIOUS" ]; then
    rm -rf "$RELEASE_DIR"
    mv "$RELEASE_PREVIOUS" "$RELEASE_DIR"
  fi
  if [ "$CURRENT_LINK_CHANGED" -eq 1 ]; then
    if [ "$CURRENT_LINK_HAD_TARGET" -eq 1 ] && [ -d "$CURRENT_LINK_PREVIOUS" ]; then
      ln -sfn "$CURRENT_LINK_PREVIOUS" /opt/allscan-reimagined/current
    elif [ "$CURRENT_LINK_HAD_TARGET" -eq 0 ]; then
      rm -f /opt/allscan-reimagined/current
    fi
  fi
  if [ "$MIGRATED_STOCK_ARMED" -eq 1 ] && [ -d "$MIGRATED_STOCK_DIR" ]; then
    rm -rf -- "$STOCK_ALLSCAN_DIR"
    mv "$MIGRATED_STOCK_DIR" "$STOCK_ALLSCAN_DIR"
  fi
  if [ "$ALLSCAN_OLD_ARMED" -eq 1 ] && [ -d "$ALLSCAN_OLD_BACKUP" ]; then
    [ -e "$WEB_ROOT/allscan-old" ] && mv "$WEB_ROOT/allscan-old" "$BACKUP_DIR/failed-allscan-old"
    mv "$ALLSCAN_OLD_BACKUP" "$WEB_ROOT/allscan-old"
  fi
  if [ "$CHANGES_STARTED" -eq 1 ] && [ -f "$BACKUP_DIR/asr-webroot.tar.gz" ]; then
    echo
    echo "Installation failed. Restoring the previous AllScan Reimagined tree..." >&2
    [ -d "$ASR_WEB_DIR" ] && mv "$ASR_WEB_DIR" "$BACKUP_DIR/failed-asr"
    tar -xzf "$BACKUP_DIR/asr-webroot.tar.gz" -C "$WEB_ROOT"
    restore_runtime_backup
    echo "Previous AllScan Reimagined tree restored; stock AllScan was left untouched." >&2
  elif [ "$CHANGES_STARTED" -eq 1 ] && [ -f "$BACKUP_DIR/asr-webroot.absent" ]; then
    rm -rf -- "$ASR_WEB_DIR"
  fi
  restore_shared_backup
  if [ "$CHANGES_STARTED" -eq 1 ]; then
    remove_asr_managed_wiring
    if [ "$CURRENT_LINK_HAD_TARGET" -eq 1 ] && [ -f "$CURRENT_LINK_PREVIOUS/scripts/asr-reapply.sh" ]; then
      install -o root -g root -m 755 "$CURRENT_LINK_PREVIOUS/scripts/asr-reapply.sh" \
        /usr/local/sbin/allscan-reimagined-reapply
      [ -f "$CURRENT_LINK_PREVIOUS/scripts/asr-integrity-check.sh" ] && \
        install -o root -g root -m 755 "$CURRENT_LINK_PREVIOUS/scripts/asr-integrity-check.sh" \
          /usr/local/sbin/allscan-reimagined-integrity-check
      prior_reapply_output=$(
        ASR_INSTALL_LOCK_HELD=1 ASR_ROLLBACK_MODE=1 \
          bash "$CURRENT_LINK_PREVIOUS/scripts/asr-reapply.sh" 2>&1
      )
      prior_reapply_status=$?
      if [ "$prior_reapply_status" -ne 0 ]; then
        echo "WARNING: The previous ASR persistence wiring could not be fully reapplied." >&2
        printf '%s\n' "$prior_reapply_output" >&2
      fi
    fi
    for helper_spec in \
      "scripts/asr-release-check.py:/usr/local/sbin/allscan-reimagined-release-check" \
      "scripts/asr-rollback.py:/usr/local/sbin/allscan-reimagined-rollback" \
      "scripts/asr-bridge-control.py:/usr/local/sbin/allscan-reimagined-bridge-control" \
      "scripts/asr-favorites-update.py:/usr/local/sbin/allscan-reimagined-favorites-update"; do
      helper_source=${helper_spec%%:*}
      helper_target=${helper_spec#*:}
      if [ "$CURRENT_LINK_HAD_TARGET" -eq 1 ] && [ -f "$CURRENT_LINK_PREVIOUS/$helper_source" ]; then
        install -o root -g root -m 755 "$CURRENT_LINK_PREVIOUS/$helper_source" "$helper_target"
      else
        rm -f "$helper_target"
      fi
    done
    restore_prior_reapply_units
    systemctl daemon-reload 2>/dev/null || true
    [ "$REAPPLY_STATES_CAPTURED" -eq 1 ] && restore_reapply_unit_states
  fi
  systemctl reload apache2 2>/dev/null || true
  exit "$status"
}
trap 'rollback_on_error $?' ERR
trap 'rollback_on_error 130' INT TERM
ask() {
  local prompt="$1" default="${2:-y}" answer
  [ -t 0 ] || { [ "$default" = "y" ]; return; }
  read -r -p "$prompt " answer
  answer="${answer:-$default}"
  [[ "$answer" =~ ^[Yy]$ ]]
}

prune_old_backups() {
  local backup_root="/root/allscan-reimagined-backups" removed=0 remove_count index
  local -a backups=()
  [ -d "$backup_root" ] || return 0
  while IFS= read -r backup; do
    backups+=("$backup")
  done < <(find "$backup_root" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' \
    | grep -E '^[0-9]{8}-[0-9]{6}$' | sort -r)
  remove_count=$((${#backups[@]} - ASR_BACKUP_RETENTION))
  [ "$remove_count" -gt 0 ] || return 0
  for ((index=ASR_BACKUP_RETENTION; index<${#backups[@]}; index++)); do
    rm -rf -- "$backup_root/${backups[$index]}"
    removed=$((removed + 1))
  done
  if [ "$removed" -gt 0 ]; then
    echo "Removed $removed old rollback backup(s); kept the newest $ASR_BACKUP_RETENTION."
  fi
}

[ "${EUID:-$(id -u)}" -eq 0 ] || fail "Run this installer with sudo."
[ -d "$PAYLOAD_DIR/web" ] || fail "Installer payload is incomplete."
[[ "$ASR_BACKUP_RETENTION" =~ ^[1-9][0-9]*$ ]] || fail "ASR_BACKUP_RETENTION must be a positive integer."

for command in curl php python3 tar install find systemctl flock; do
  command -v "$command" >/dev/null 2>&1 || fail "Required command not found: $command"
done
install -d -o root -g root -m 755 /run/lock
exec 9>/run/lock/allscan-reimagined-rollback.lock
flock -n 9 || fail "Another ASR installation or rollback is already running."

if [ -d /var/www/html ]; then
  WEB_ROOT="/var/www/html"
elif [ -d /srv/http ]; then
  WEB_ROOT="/srv/http"
else
  fail "A supported web root was not found."
fi
STOCK_ALLSCAN_DIR="$WEB_ROOT/allscan"
ASR_WEB_DIR="$WEB_ROOT/asr"
[ -d "$ASR_WEB_DIR" ] && ASR_WEB_WAS_PRESENT=1
WEB_GROUP="www-data"
getent group "$WEB_GROUP" >/dev/null 2>&1 || WEB_GROUP="apache"
getent group "$WEB_GROUP" >/dev/null 2>&1 || WEB_GROUP="http"
getent group "$WEB_GROUP" >/dev/null 2>&1 || fail "A supported web-server group was not found."

if systemctl is-enabled --quiet allscan-reimagined-reapply.path; then
  REAPPLY_PATH_WAS_ENABLED=1
fi
if systemctl is-active --quiet allscan-reimagined-reapply.path; then
  REAPPLY_PATH_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet allscan-reimagined-reapply.timer; then
  REAPPLY_TIMER_WAS_ENABLED=1
fi
if systemctl is-active --quiet allscan-reimagined-reapply.timer; then
  REAPPLY_TIMER_WAS_ACTIVE=1
fi
REAPPLY_STATES_CAPTURED=1

current_version="not installed"
if [ -r "$STOCK_ALLSCAN_DIR/include/common.php" ]; then
  current_version=$(sed -n 's/^\$AllScanVersion = "\([^"]*\)";.*/\1/p' "$STOCK_ALLSCAN_DIR/include/common.php" | head -1)
  current_version="${current_version:-unknown}"
fi
latest_version=$(curl -fsSL https://raw.githubusercontent.com/davidgsd/AllScan/main/include/common.php \
  | sed -n 's/^\$AllScanVersion = "\([^"]*\)";.*/\1/p' | head -1)
latest_version="${latest_version:-unknown}"
[ "$latest_version" != "unknown" ] || fail "The latest official AllScan version could not be verified."
STOCK_OVERLAY_DETECTED=0
if [ -f "$STOCK_ALLSCAN_DIR/asr-api.php" ] \
  || [ -d "$STOCK_ALLSCAN_DIR/asr-settings" ] \
  || grep -q 'AllScan Reimagined' "$STOCK_ALLSCAN_DIR/index.html" 2>/dev/null; then
  STOCK_OVERLAY_DETECTED=1
fi
if { [ "$current_version" != "$latest_version" ] || [ "$STOCK_OVERLAY_DETECTED" -eq 1 ]; } \
  && [ ! -t 0 ]; then
  fail "The official AllScan update requires an interactive terminal. Run 'bash ./install.sh' directly in an interactive shell; do not run it through a heredoc or wrapper."
fi

echo
echo "============================================================"
echo " AllScan Reimagined Installer"
echo "============================================================"
echo "Existing AllScan backend: $current_version"
echo "Latest official backend:  $latest_version"
echo "Reimagined release:        v1.0.0 Beta 6"
echo
echo "Existing AllScan users, passwords, permissions, Favorites,"
echo "database, and node settings will be preserved."
echo
echo "This installation provides two separate web interfaces:"
echo "  /allscan/  Original stock AllScan"
echo "  /asr/      AllScan Reimagined"
echo "They share the same user accounts and preserved node data. However, each"
echo "interface has its own browser login session, so you may need to sign in"
echo "separately when opening each address."
echo "A complete ASR rollback backup will be created before changes."
echo

install -d -o root -g root -m 700 "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR/runtime/protected" "$BACKUP_DIR/runtime/systemd"
chmod 700 "$BACKUP_DIR/runtime" "$BACKUP_DIR/runtime/protected" "$BACKUP_DIR/runtime/systemd"
for unit in \
  allscan-reimagined-reapply.service \
  allscan-reimagined-reapply.path \
  allscan-reimagined-reapply.timer; do
  if [ -f "/etc/systemd/system/$unit" ]; then
    cp -p "/etc/systemd/system/$unit" "$BACKUP_DIR/runtime/systemd/$unit"
  else
    : > "$BACKUP_DIR/runtime/systemd/$unit.absent"
  fi
done
for protected in config.json secrets.json station-map-cache.json; do
  if [ -f "/etc/allscan-reimagined/$protected" ]; then
    cp -p "/etc/allscan-reimagined/$protected" "$BACKUP_DIR/runtime/protected/$protected"
  else
    : > "$BACKUP_DIR/runtime/protected/$protected.absent"
  fi
done
if [ -f /etc/allscan/favorites.ini ]; then
  cp -p /etc/allscan/favorites.ini "$BACKUP_DIR/runtime/etc-favorites.ini"
else
  : > "$BACKUP_DIR/runtime/etc-favorites.ini.absent"
fi
if [ -d "$WEB_ROOT/allscan-old" ]; then
  ALLSCAN_OLD_BACKUP="$BACKUP_DIR/preserved-allscan-old"
  mv "$WEB_ROOT/allscan-old" "$ALLSCAN_OLD_BACKUP"
  ALLSCAN_OLD_ARMED=1
fi
PRE_UPDATE_ASR_VERSION="not-installed"
PRE_UPDATE_MASTER=""
if [ -e /opt/allscan-reimagined/current ]; then
  CURRENT_LINK_PREVIOUS=$(readlink -f /opt/allscan-reimagined/current 2>/dev/null || true)
  if [ -d "$CURRENT_LINK_PREVIOUS" ]; then
    CURRENT_LINK_HAD_TARGET=1
    PRE_UPDATE_MASTER="$CURRENT_LINK_PREVIOUS"
  fi
  case "$PRE_UPDATE_MASTER" in
	    /opt/allscan-reimagined/releases/*)
	      if [ -f "$PRE_UPDATE_MASTER/server/asr-api.php" ]; then
	        PRE_UPDATE_ASR_VERSION=$(python3 "$PAYLOAD_DIR/scripts/asr-rollback.py" \
	          detect-version "$PRE_UPDATE_MASTER/server/asr-api.php")
	        PRE_UPDATE_ASR_VERSION="${PRE_UPDATE_ASR_VERSION:-unknown}"
	      fi
      ;;
    *)
      PRE_UPDATE_MASTER=""
      PRE_UPDATE_ASR_VERSION="unknown"
      ;;
  esac
fi
BACKUP_WEB_DIR=""
BACKUP_WEB_NAME=""
if [ -d "$ASR_WEB_DIR" ]; then
  BACKUP_WEB_DIR="$ASR_WEB_DIR"
  BACKUP_WEB_NAME="asr"
elif [ "$STOCK_OVERLAY_DETECTED" -eq 1 ]; then
  # Schema-1 compatibility backup: this is the one-time escape hatch back to
  # the old in-place overlay architecture.
  BACKUP_WEB_DIR="$STOCK_ALLSCAN_DIR"
  BACKUP_WEB_NAME="allscan"
fi
if [ -n "$BACKUP_WEB_DIR" ]; then
  echo "[1/8] Backing up the existing AllScan Reimagined installation..."
  mkdir -p "$BACKUP_DIR/runtime"
  for runtime_file in bridge-live.json connected-clients.json asr-connected-clients.json zello-status-data.json; do
    [ -f "$BACKUP_WEB_DIR/$runtime_file" ] && cp -p "$BACKUP_WEB_DIR/$runtime_file" "$BACKUP_DIR/runtime/"
  done
  for runtime_dir in img asr-user-content; do
    [ -d "$BACKUP_WEB_DIR/$runtime_dir" ] && cp -a "$BACKUP_WEB_DIR/$runtime_dir" "$BACKUP_DIR/runtime/$runtime_dir"
  done
  if [ -f /etc/allscan-reimagined/connected-clients-daemon.env ]; then
    install -o root -g root -m 600 /etc/allscan-reimagined/connected-clients-daemon.env "$BACKUP_DIR/runtime/tgif-daemon-environment"
  else
    : > "$BACKUP_DIR/runtime/tgif-daemon-environment.absent"
  fi
  if [ -f /etc/systemd/system/connected-clients-daemon.service.d/tgif-token.conf ]; then
    install -o root -g root -m 600 /etc/systemd/system/connected-clients-daemon.service.d/tgif-token.conf "$BACKUP_DIR/runtime/tgif-token-dropin"
  else
    : > "$BACKUP_DIR/runtime/tgif-token-dropin.absent"
  fi
  tar_status=0
  web_archive="$BACKUP_DIR/${BACKUP_WEB_NAME}-webroot.tar.gz"
  COPYFILE_DISABLE=1 tar --ignore-failed-read --warning=no-file-changed \
    --exclude="$BACKUP_WEB_NAME/bridge-live.json" \
    --exclude="$BACKUP_WEB_NAME/connected-clients.json" \
    --exclude="$BACKUP_WEB_NAME/asr-connected-clients.json" \
    --exclude="$BACKUP_WEB_NAME/zello-status-data.json" \
    --exclude="$BACKUP_WEB_NAME/astdb.txt" \
    --exclude="$BACKUP_WEB_NAME/backup-*" \
    --exclude="$BACKUP_WEB_NAME/*.bak" \
    --exclude="$BACKUP_WEB_NAME/*.bak.*" \
    --exclude="$BACKUP_WEB_NAME/._*" \
    --exclude="$BACKUP_WEB_NAME/.DS_Store" \
    -czf "$web_archive" -C "$WEB_ROOT" "$BACKUP_WEB_NAME" || tar_status=$?
  if [ "$tar_status" -ne 0 ]; then
    [ -s "$web_archive" ] || fail "AllScan Reimagined webroot backup failed."
    echo "Backup completed with live-file warnings; continuing with preserved runtime files."
  fi
  chmod 600 "$web_archive"
fi
if [ "$ASR_WEB_WAS_PRESENT" -eq 0 ]; then
  : > "$BACKUP_DIR/asr-webroot.absent"
fi
if [ -n "$PRE_UPDATE_MASTER" ]; then
  COPYFILE_DISABLE=1 tar --ignore-failed-read --warning=no-file-changed \
    -czf "$BACKUP_DIR/asr-release.tar.gz" -C "$PRE_UPDATE_MASTER" .
  chmod 600 "$BACKUP_DIR/asr-release.tar.gz"
fi
if [ -f /etc/allscan/allscan.db ]; then
  install -o root -g root -m 600 /etc/allscan/allscan.db "$BACKUP_DIR/allscan.db"
else
  : > "$BACKUP_DIR/allscan.db.absent"
fi
python3 "$PAYLOAD_DIR/scripts/asr-rollback.py" \
  finalize-backup "$BACKUP_DIR" "$PRE_UPDATE_ASR_VERSION"
CHANGES_STARTED=1
systemctl stop \
  allscan-reimagined-reapply.path \
  allscan-reimagined-reapply.timer \
  allscan-reimagined-reapply.service >/dev/null 2>&1 || true

echo "[2/8] Checking the official AllScan backend..."
if [ "$STOCK_OVERLAY_DETECTED" -eq 1 ]; then
  echo "The current /allscan contains the older Reimagined overlay."
  echo "A clean official /allscan is required before creating the side-by-side /asr tree."
  if ! ask "Move the old overlay into the rollback backup and reinstall clean stock AllScan? [Y/n]" y; then
    fail "Side-by-side migration was declined."
  fi
  MIGRATED_STOCK_DIR="$BACKUP_DIR/migrated-allscan-overlay"
  mv "$STOCK_ALLSCAN_DIR" "$MIGRATED_STOCK_DIR"
  MIGRATED_STOCK_ARMED=1
fi
if [ "$current_version" != "$latest_version" ] || [ "$STOCK_OVERLAY_DETECTED" -eq 1 ]; then
  echo "Official AllScan will be installed or upgraded: $current_version -> $latest_version"
  if ask "Run David Gleason's official AllScan installer now? [Y/n]" y; then
    official_installer_dir=$(mktemp -d /tmp/allscan-official-installer.XXXXXX)
    official_installer="$official_installer_dir/AllScanInstallUpdate.php"
    curl -fsSL "$OFFICIAL_INSTALLER_URL" -o "$official_installer"
    chmod 755 "$official_installer"
    echo "The official installer will explain and confirm its own update steps."
    php "$official_installer"
    rm -rf "$official_installer_dir"
    [ -d "$STOCK_ALLSCAN_DIR" ] || fail "The official installer did not create clean stock /allscan."
  else
    fail "Official AllScan installation/update was declined."
  fi
else
  echo "Official AllScan is already current ($current_version)."
fi

[ -d "$STOCK_ALLSCAN_DIR" ] || fail "Official AllScan installation was not completed."
installed_version=$(sed -n 's/^\$AllScanVersion = "\([^"]*\)";.*/\1/p' "$STOCK_ALLSCAN_DIR/include/common.php" | head -1)
[ "$installed_version" = "$latest_version" ] || fail "Official AllScan is still $installed_version; expected $latest_version."
[ -f "$PAYLOAD_DIR/compat/allscan-$installed_version/include/common.php" ] \
  || fail "This ASR release has no exact compatibility layer for stock AllScan $installed_version; /asr was not changed."

if [ -d "$BACKUP_DIR/runtime" ]; then
  for runtime_file in bridge-live.json connected-clients.json asr-connected-clients.json zello-status-data.json; do
    if [ -s "$BACKUP_DIR/runtime/$runtime_file" ] && [ ! -s "$ASR_WEB_DIR/$runtime_file" ]; then
      install -d -m 755 "$ASR_WEB_DIR"
      cp -p "$BACKUP_DIR/runtime/$runtime_file" "$ASR_WEB_DIR/$runtime_file"
    fi
  done
  for runtime_dir in img asr-user-content; do
    if [ -d "$BACKUP_DIR/runtime/$runtime_dir" ] && [ ! -d "$ASR_WEB_DIR/$runtime_dir" ]; then
      cp -a "$BACKUP_DIR/runtime/$runtime_dir" "$ASR_WEB_DIR/$runtime_dir"
    fi
  done
fi

echo "[3/8] Installing the Reimagined master files outside the web root..."
RELEASE_STAGE="${RELEASE_DIR}.new.$$"
rm -rf "$RELEASE_STAGE"
mkdir -p "$RELEASE_STAGE"
cp -a "$PAYLOAD_DIR/." "$RELEASE_STAGE/"
chown -R root:root "$RELEASE_STAGE"
find "$RELEASE_STAGE" -type d -exec chmod 755 {} +
find "$RELEASE_STAGE" -type f -exec chmod 644 {} +
chmod 755 "$RELEASE_STAGE/bin/"*.sh "$RELEASE_STAGE/scripts/"*.sh "$RELEASE_STAGE/scripts/asr-friendly-names.php" "$RELEASE_STAGE/scripts/asr-bridge-clients.php" "$RELEASE_STAGE/scripts/asr-manager-perms.sh" "$RELEASE_STAGE/scripts/asr-patch-connected-clients.py" "$RELEASE_STAGE/scripts/asr-migrate-tgif-environment.py" "$RELEASE_STAGE/scripts/asr-patch-allscan-index.py" "$RELEASE_STAGE/scripts/asr-release-check.py" "$RELEASE_STAGE/scripts/asr-rollback.py" "$RELEASE_STAGE/scripts/asr-bridge-control.py" "$RELEASE_STAGE/scripts/asr-favorites-update.py" "$RELEASE_STAGE/scripts/asr-favorites-source.py" "$RELEASE_STAGE/scripts/asr-stock-count-helper.py" "$RELEASE_STAGE/scripts/asr-lookup-map-self-test.php" "$RELEASE_STAGE/scripts/asr-lookup-map-browser-self-test.mjs" "$RELEASE_STAGE/scripts/asr-access-policy-self-test.php"
RELEASE_PREVIOUS="${RELEASE_DIR}.previous.$$"
rm -rf "$RELEASE_PREVIOUS"
if [ -d "$RELEASE_DIR" ]; then
  mv "$RELEASE_DIR" "$RELEASE_PREVIOUS"
  RELEASE_PREVIOUS_ARMED=1
fi
RELEASE_REPLACED=1
mv "$RELEASE_STAGE" "$RELEASE_DIR"
RELEASE_STAGE=""
CURRENT_LINK_CHANGED=1
ln -sfn "$RELEASE_DIR" /opt/allscan-reimagined/current

echo "[4/8] Detecting node identity, branding, and bridges..."
if [ -r /dev/tty ] && [ -w /dev/tty ]; then
  STOCK_ALLSCAN_DIR="$STOCK_ALLSCAN_DIR" "$RELEASE_DIR/scripts/asr-configure.sh" < /dev/tty
else
  STOCK_ALLSCAN_DIR="$STOCK_ALLSCAN_DIR" "$RELEASE_DIR/scripts/asr-configure.sh"
fi

echo "[5/8] Applying the Reimagined interface and security protections..."
ASR_INSTALL_LOCK_HELD=1 STOCK_ALLSCAN_DIR="$STOCK_ALLSCAN_DIR" ASR_WEB_DIR="$ASR_WEB_DIR" \
  "$RELEASE_DIR/scripts/asr-reapply.sh"

if python3 "$RELEASE_DIR/scripts/asr-favorites-source.py" \
  --check \
  --database /etc/allscan/allscan.db \
  --canonical /etc/allscan/favorites.ini >/dev/null; then
  echo "Stock AllScan and ASR already use the preserved canonical Favorites source."
else
  echo
  echo "Stock AllScan and ASR need one preserved Favorites source."
  echo "The stock Favorites file will be backed up if it differs; stock web files will not be modified."
  if ! ask "Use /etc/allscan/favorites.ini for both interfaces? [y/N]" n; then
    fail "Canonical shared Favorites configuration was declined."
  fi
  python3 "$RELEASE_DIR/scripts/asr-favorites-source.py" \
    --apply \
    --database /etc/allscan/allscan.db \
    --canonical /etc/allscan/favorites.ini \
    --stock-favorites "$STOCK_ALLSCAN_DIR/favorites.ini" \
    --migration-dir /var/lib/allscan-reimagined/migrations
fi

echo
echo "Optional stock-interface security:"
echo "If enabled, /allscan will require login for every visit, including"
echo "read-only monitoring and dashboard viewing."
if [ -t 0 ] && ask "Require login for stock /allscan, including read-only monitoring? [y/N]" n; then
  php -r '
    $_SERVER["DOCUMENT_ROOT"] = $argv[1];
    $_SERVER["SCRIPT_NAME"] = "/allscan/index.php";
    chdir($argv[2]);
    require_once "include/common.php";
    $msg = [];
    asInit($msg);
    $db = dbInit();
    checkTables($db, $msg);
    $users = new UserModel($db);
    $admins = $users->getUsers(null, null, PERMISSION_ADMIN);
    if (!is_array($admins) || count($admins) < 1) exit(2);
    $now = time();
    $current = $db->getRecord("cfg", "cfg_id=" . publicPermission);
    if ($current) {
      $db->updateRow("cfg", ["val", "updated"], [PERMISSION_NONE, $now], "cfg_id=" . publicPermission);
    } else {
      $db->insertRow("cfg", ["cfg_id", "val", "updated"], [publicPermission, PERMISSION_NONE, $now]);
    }
    if (isset($db->error)) exit(1);
  ' "$WEB_ROOT" "$STOCK_ALLSCAN_DIR" \
    || fail "Stock /allscan login requirement could not be enabled."
  STOCK_LOGIN_OPTED_IN=1
  echo "Stock /allscan now requires an existing AllScan login."
else
  echo "Stock /allscan access policy was left unchanged."
fi

echo "Validating the ASR-only login policy..."
access_policy_status=0
php -r '
  $_SERVER["DOCUMENT_ROOT"] = $argv[1];
  $_SERVER["SCRIPT_NAME"] = "/asr/asr-api.php";
  chdir($argv[2]);
  require_once "include/common.php";
  $msg = [];
  asInit($msg);
  $db = dbInit();
  checkTables($db, $msg);
  $cfgModel = new CfgModel($db);
  if ((int)($gCfg[publicPermission] ?? PERMISSION_READ_ONLY) !== PERMISSION_NONE) exit(3);
  $userModel = new UserModel($db);
  $admins = $userModel->getUsers(null, null, PERMISSION_ADMIN);
  if (!is_array($admins) || count($admins) < 1) exit(2);
' "$WEB_ROOT" "$ASR_WEB_DIR" || access_policy_status=$?
case "$access_policy_status" in
  0)
    if [ "$STOCK_LOGIN_OPTED_IN" -eq 1 ]; then
      echo "ASR uses its path-scoped login policy; stock /allscan login was also explicitly enabled."
    else
      echo "ASR requires its own path-scoped login; stock AllScan access policy was not changed."
    fi
    ;;
  2)
    echo "WARNING: ASR requires login, but no Admin/Superuser account was found." >&2
    echo "Create an administrator through stock AllScan before attempting to sign in to ASR." >&2
    ;;
  *)
    fail "The ASR-only login policy did not validate."
    ;;
esac

echo "[6/8] Installing automatic update-survival services..."
install -o root -g root -m 755 "$RELEASE_DIR/scripts/asr-reapply.sh" /usr/local/sbin/allscan-reimagined-reapply
install -o root -g root -m 755 "$RELEASE_DIR/scripts/asr-integrity-check.sh" /usr/local/sbin/allscan-reimagined-integrity-check
install -o root -g root -m 755 "$RELEASE_DIR/scripts/asr-rollback.py" /usr/local/sbin/allscan-reimagined-rollback
cat > /etc/systemd/system/allscan-reimagined-reapply.service <<'EOF'
[Unit]
Description=Reapply AllScan Reimagined after an official AllScan update
After=apache2.service

[Service]
Type=oneshot
Nice=10
IOSchedulingClass=idle
ExecStart=/usr/local/sbin/allscan-reimagined-integrity-check
EOF
cat > /etc/systemd/system/allscan-reimagined-reapply.path <<EOF
[Unit]
Description=Watch official AllScan files for replacement

[Path]
PathChanged=$STOCK_ALLSCAN_DIR/include/common.php
Unit=allscan-reimagined-reapply.service

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/allscan-reimagined-reapply.timer <<'EOF'
[Unit]
Description=Periodic AllScan Reimagined integrity check

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
RandomizedDelaySec=45s
Unit=allscan-reimagined-reapply.service

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload

echo "[7/8] Validating the installed application..."
echo "  Checking installed PHP files..."
validate_command "ASR API PHP syntax" php -l "$ASR_WEB_DIR/asr-api.php" >/dev/null
validate_command "stock entry-point PHP syntax under /asr" php -l "$ASR_WEB_DIR/index.php" >/dev/null
validate_command "rollback status PHP syntax" php -l "$ASR_WEB_DIR/asr-settings/rollback-status.php" >/dev/null
validate_command "instructions PHP syntax" php -l "$ASR_WEB_DIR/asr-instructions/index.php" >/dev/null
echo "  Checking release notification, rollback, bridge, Favorites, and access helpers..."
validate_command "release-check helper self-test" \
  python3 "$RELEASE_DIR/scripts/asr-release-check.py" --self-test >/dev/null
validate_command "rollback helper self-test" \
  python3 "$RELEASE_DIR/scripts/asr-rollback.py" self-test >/dev/null
validate_command "bridge-control helper self-test" \
  python3 "$RELEASE_DIR/scripts/asr-bridge-control.py" --self-test >/dev/null
validate_command "Favorites update helper self-test" \
  python3 "$RELEASE_DIR/scripts/asr-favorites-update.py" --self-test >/dev/null
validate_command "canonical Favorites source self-test" \
  python3 "$RELEASE_DIR/scripts/asr-favorites-source.py" --self-test >/dev/null
validate_command "instructions and Settings self-test" \
  python3 "$RELEASE_DIR/scripts/asr-instructions-self-test.py" >/dev/null
validate_command "stock topology formula self-test" \
  python3 "$RELEASE_DIR/scripts/asr-stock-count-helper.py" --self-test >/dev/null
validate_command "lookup and map backend self-test" \
  php "$RELEASE_DIR/scripts/asr-lookup-map-self-test.php" >/dev/null
validate_command "ASR access-policy self-test" \
  php "$RELEASE_DIR/scripts/asr-access-policy-self-test.php" >/dev/null
validate_command "AllScan client helper shell syntax" \
  bash -n /usr/local/bin/allscan_wt_clients.sh
echo "  Enabling the daily release-notification timer..."
validate_command "enable/start release-check timer" \
  systemctl enable --now allscan-reimagined-release-check.timer >/dev/null
validate_command "release-check timer is enabled" \
  systemctl is-enabled --quiet allscan-reimagined-release-check.timer
validate_command "release-check timer is active" \
  systemctl is-active --quiet allscan-reimagined-release-check.timer
validate_command "rollback job unit was installed" \
  test -f /etc/systemd/system/allscan-reimagined-rollback@.service
if python3 - /etc/allscan-reimagined/config.json <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(
    0 if any(
        isinstance(item, dict) and item.get("cardType") == "dmr_net"
        for item in payload.get("bridges", [])
    ) else 1
)
PY
then
  validate_command "configured DMR Net live service is active" \
    systemctl is-active --quiet allscan-reimagined-dmr-net-live.service
fi
if ! rollback_json=$(/usr/local/sbin/allscan-reimagined-rollback list --json); then
  fail "Validation failed: rollback backup listing"
fi
if ! printf '%s' "$rollback_json" | python3 -c '
import json, sys
data = json.load(sys.stdin)
assert data.get("ok") is True
assert isinstance(data.get("backups"), list)
'; then
  fail "Validation failed: rollback listing JSON"
fi
if ! runtime_json=$(curl -fsS "http://127.0.0.1/asr/asr-api.php?action=runtime-config"); then
  fail "Validation failed: /asr runtime-config endpoint"
fi
if ! printf '%s' "$runtime_json" | php -r '
  $data = json_decode(stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
  if (($data["ok"] ?? false) !== true || empty($data["node"])) exit(1);
'; then
  fail "Validation failed: runtime-config response content"
fi
if ! auth_json=$(curl -fsS "http://127.0.0.1/asr/asr-api.php?action=auth-status"); then
  fail "Validation failed: /asr auth-status endpoint"
fi
if ! printf '%s' "$auth_json" | php -r '
  $data = json_decode(stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
  $config = json_decode((string) file_get_contents($argv[1]), true, 512, JSON_THROW_ON_ERROR);
  $requireLogin = !array_key_exists("requireLogin", $config) || !empty($config["requireLogin"]);
  $expected = $requireLogin ? 0 : 2;
  if ((int)($data["publicPermission"] ?? -1) !== $expected) {
      fwrite(STDERR, "ASR effective access policy does not match requireLogin.\n");
      exit(1);
  }
' /etc/allscan-reimagined/config.json; then
  fail "Validation failed: ASR effective login policy"
fi
if [ "$STOCK_LOGIN_OPTED_IN" -eq 1 ]; then
  validate_command "stock /allscan login policy" php -r '
    $_SERVER["DOCUMENT_ROOT"] = $argv[1];
    $_SERVER["SCRIPT_NAME"] = "/allscan/index.php";
    chdir($argv[2]);
    require_once "include/common.php";
    $msg = [];
    asInit($msg);
    $db = dbInit();
    checkTables($db, $msg);
    new CfgModel($db);
    if ((int)($gCfg[publicPermission] ?? PERMISSION_READ_ONLY) !== PERMISSION_NONE) exit(1);
  ' "$WEB_ROOT" "$STOCK_ALLSCAN_DIR"
fi
if ! curl -fsS http://127.0.0.1/asr/ | grep -q 'assets/index-'; then
  fail "Validation failed: /asr page did not serve its built assets"
fi
validate_command "enable/start reapply path and timer" \
  systemctl enable --now allscan-reimagined-reapply.path allscan-reimagined-reapply.timer

trap - ERR INT TERM
rm -rf "$RELEASE_PREVIOUS"
RELEASE_PREVIOUS=""
RELEASE_REPLACED=0
RELEASE_PREVIOUS_ARMED=0
CHANGES_STARTED=0
[ -n "$MIGRATED_STOCK_DIR" ] && rm -rf -- "$MIGRATED_STOCK_DIR"
MIGRATED_STOCK_ARMED=0
ALLSCAN_OLD_ARMED=0
if ! prune_old_backups; then
  echo "WARNING: Old rollback backups could not be pruned automatically." >&2
fi

echo "[8/8] Installation complete."
echo
echo "AllScan backend:       $latest_version"
echo "AllScan Reimagined:    v1.0.0 Beta 6"
echo "Personal configuration: /etc/allscan-reimagined/config.json"
echo "Rollback backup:        $BACKUP_DIR"
echo "Stock AllScan:           http://$(hostname -I | awk '{print $1}')/allscan/"
echo "AllScan Reimagined:      http://$(hostname -I | awk '{print $1}')/asr/"
echo "Open either address in a browser. The interfaces share accounts and node data,"
echo "but each keeps a separate login session."
echo
echo "Existing user accounts and passwords were not changed."
