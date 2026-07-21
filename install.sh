#!/bin/bash
set -Eeuo pipefail

ASR_VERSION="1.0.0-beta.5.9-Rollup-1"
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

fail() { printf 'ERROR: %s\n' "$*" >&2; return 1; }
restore_runtime_backup() {
  [ -d "$BACKUP_DIR/runtime" ] || return 0
  mkdir -p "$ALLSCAN_DIR"
  for runtime_file in bridge-live.json connected-clients.json zello-status-data.json; do
    [ -f "$BACKUP_DIR/runtime/$runtime_file" ] && cp -p "$BACKUP_DIR/runtime/$runtime_file" "$ALLSCAN_DIR/$runtime_file"
  done
  [ -d "$BACKUP_DIR/runtime/img" ] && cp -a "$BACKUP_DIR/runtime/img" "$ALLSCAN_DIR/img"
  if [ -f "$BACKUP_DIR/runtime/etc-favorites.ini" ]; then
    mkdir -p /etc/allscan
    install -o root -g "$WEB_GROUP" -m 664 "$BACKUP_DIR/runtime/etc-favorites.ini" /etc/allscan/favorites.ini
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
  systemctl daemon-reload 2>/dev/null || true
}

rollback_on_error() {
  status=$?
  set +e
  [ -n "$RELEASE_STAGE" ] && rm -rf "$RELEASE_STAGE"
  if [ "$RELEASE_REPLACED" -eq 1 ]; then
    rm -rf "$RELEASE_DIR"
    [ -d "$RELEASE_PREVIOUS" ] && mv "$RELEASE_PREVIOUS" "$RELEASE_DIR"
  fi
  if [ "$CHANGES_STARTED" -eq 1 ] && [ -f "$BACKUP_DIR/allscan-webroot.tar.gz" ]; then
    echo
    echo "Installation failed. Restoring the previous AllScan installation..." >&2
    [ -d "$ALLSCAN_DIR" ] && mv "$ALLSCAN_DIR" "$BACKUP_DIR/failed-allscan"
    tar -xzf "$BACKUP_DIR/allscan-webroot.tar.gz" -C "$WEB_ROOT"
    restore_runtime_backup
    if [ -f "$BACKUP_DIR/allscan.db" ]; then
      install -o "$WEB_GROUP" -g "$WEB_GROUP" -m 660 "$BACKUP_DIR/allscan.db" /etc/allscan/allscan.db
    fi
    systemctl reload apache2 2>/dev/null || true
    echo "Previous AllScan installation restored." >&2
  fi
  exit "$status"
}
trap rollback_on_error ERR
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

for command in curl php tar install find systemctl; do
  command -v "$command" >/dev/null 2>&1 || fail "Required command not found: $command"
done

if [ -d /var/www/html ]; then
  WEB_ROOT="/var/www/html"
elif [ -d /srv/http ]; then
  WEB_ROOT="/srv/http"
else
  fail "A supported web root was not found."
fi
ALLSCAN_DIR="$WEB_ROOT/allscan"
WEB_GROUP="www-data"
getent group "$WEB_GROUP" >/dev/null 2>&1 || WEB_GROUP="apache"
getent group "$WEB_GROUP" >/dev/null 2>&1 || WEB_GROUP="http"
getent group "$WEB_GROUP" >/dev/null 2>&1 || fail "A supported web-server group was not found."

current_version="not installed"
if [ -r "$ALLSCAN_DIR/include/common.php" ]; then
  current_version=$(sed -n 's/^\$AllScanVersion = "\([^"]*\)";.*/\1/p' "$ALLSCAN_DIR/include/common.php" | head -1)
  current_version="${current_version:-unknown}"
fi
latest_version=$(curl -fsSL https://raw.githubusercontent.com/davidgsd/AllScan/main/include/common.php \
  | sed -n 's/^\$AllScanVersion = "\([^"]*\)";.*/\1/p' | head -1)
latest_version="${latest_version:-unknown}"
[ "$latest_version" != "unknown" ] || fail "The latest official AllScan version could not be verified."
if [ "$current_version" != "$latest_version" ] && [ ! -t 0 ]; then
  fail "The official AllScan update requires an interactive terminal. Run 'bash ./install.sh' directly in an interactive shell; do not run it through a heredoc or wrapper."
fi

echo
echo "============================================================"
echo " AllScan Reimagined Installer"
echo "============================================================"
echo "Existing AllScan backend: $current_version"
echo "Latest official backend:  $latest_version"
echo "Reimagined release:        v1.0.0 Beta 5.9 Rollup 1"
echo
echo "Existing AllScan users, passwords, permissions, Favorites,"
echo "database, and node settings will be preserved."
echo "A complete rollback backup will be created before changes."
echo

mkdir -p "$BACKUP_DIR"
if [ -d "$ALLSCAN_DIR" ]; then
  echo "[1/8] Backing up the existing AllScan installation..."
  mkdir -p "$BACKUP_DIR/runtime"
  for runtime_file in bridge-live.json connected-clients.json zello-status-data.json; do
    [ -f "$ALLSCAN_DIR/$runtime_file" ] && cp -p "$ALLSCAN_DIR/$runtime_file" "$BACKUP_DIR/runtime/"
  done
  [ -d "$ALLSCAN_DIR/img" ] && cp -a "$ALLSCAN_DIR/img" "$BACKUP_DIR/runtime/img"
  [ -f /etc/allscan/favorites.ini ] && cp -p /etc/allscan/favorites.ini "$BACKUP_DIR/runtime/etc-favorites.ini"
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
  COPYFILE_DISABLE=1 tar --ignore-failed-read --warning=no-file-changed \
    --exclude='allscan/bridge-live.json' \
    --exclude='allscan/connected-clients.json' \
    --exclude='allscan/zello-status-data.json' \
    --exclude='allscan/astdb.txt' \
    --exclude='allscan/backup-*' \
    --exclude='allscan/*.bak' \
    --exclude='allscan/*.bak.*' \
    --exclude='allscan/._*' \
    --exclude='allscan/.DS_Store' \
    -czf "$BACKUP_DIR/allscan-webroot.tar.gz" -C "$WEB_ROOT" allscan || tar_status=$?
  if [ "$tar_status" -ne 0 ]; then
    [ -s "$BACKUP_DIR/allscan-webroot.tar.gz" ] || fail "AllScan webroot backup failed."
    echo "Backup completed with live-file warnings; continuing with preserved runtime files."
  fi
fi
if [ -f /etc/allscan/allscan.db ]; then
  install -o root -g root -m 600 /etc/allscan/allscan.db "$BACKUP_DIR/allscan.db"
fi
CHANGES_STARTED=1

echo "[2/8] Checking the official AllScan backend..."
if [ "$current_version" != "$latest_version" ]; then
  echo "Official AllScan will be installed or upgraded: $current_version -> $latest_version"
  if ask "Run David Gleason's official AllScan installer now? [Y/n]" y; then
    official_installer_dir=$(mktemp -d /tmp/allscan-official-installer.XXXXXX)
    official_installer="$official_installer_dir/AllScanInstallUpdate.php"
    curl -fsSL "$OFFICIAL_INSTALLER_URL" -o "$official_installer"
    chmod 755 "$official_installer"
    echo "The official installer will explain and confirm its own update steps."
    php "$official_installer"
    rm -rf "$official_installer_dir"
  else
    fail "Official AllScan installation/update was declined."
  fi
else
  echo "Official AllScan is already current ($current_version)."
fi

[ -d "$ALLSCAN_DIR" ] || fail "Official AllScan installation was not completed."
installed_version=$(sed -n 's/^\$AllScanVersion = "\([^"]*\)";.*/\1/p' "$ALLSCAN_DIR/include/common.php" | head -1)
[ "$installed_version" = "$latest_version" ] || fail "Official AllScan is still $installed_version; expected $latest_version."

echo "Configuring ASR login requirement..."
if php -r '
  $_SERVER["DOCUMENT_ROOT"] = $argv[1];
  $_SERVER["SCRIPT_NAME"] = "/allscan/asr-api.php";
  chdir($argv[2]);
  require_once "include/common.php";
  $msg = [];
  asInit($msg);
  $db = dbInit();
  checkTables($db, $msg);
  $cfgModel = new CfgModel($db);
  $userModel = new UserModel($db);
  $admins = $userModel->getUsers(null, null, PERMISSION_ADMIN);
  if (!is_array($admins) || count($admins) < 1) exit(2);
  $now = time();
  $current = $db->getRecord("cfg", "cfg_id=" . publicPermission);
  if ($current) {
    $db->updateRow("cfg", ["val", "updated"], [PERMISSION_NONE, $now], "cfg_id=" . publicPermission);
  } else {
    $db->insertRow("cfg", ["cfg_id", "val", "updated"], [publicPermission, PERMISSION_NONE, $now]);
  }
  if (isset($db->error)) {
    fwrite(STDERR, $db->error . PHP_EOL);
    exit(1);
  }
' "$WEB_ROOT" "$ALLSCAN_DIR"; then
  echo "Public ASR access disabled; existing logged-in users will still open /allscan/ normally."
else
  echo "WARNING: No Admin/Superuser account was found. Public ASR access was left unchanged so the owner is not locked out."
  echo "Create an Admin/Superuser in AllScan, then set Public Permission to None in Configs."
fi

if [ -d "$BACKUP_DIR/runtime" ]; then
  for runtime_file in bridge-live.json connected-clients.json zello-status-data.json; do
    if [ -s "$BACKUP_DIR/runtime/$runtime_file" ] && [ ! -s "$ALLSCAN_DIR/$runtime_file" ]; then
      cp -p "$BACKUP_DIR/runtime/$runtime_file" "$ALLSCAN_DIR/$runtime_file"
    fi
  done
  if [ -d "$BACKUP_DIR/runtime/img" ] && [ ! -d "$ALLSCAN_DIR/img" ]; then
    cp -a "$BACKUP_DIR/runtime/img" "$ALLSCAN_DIR/img"
  fi
fi

echo "[3/8] Installing the Reimagined master files outside the web root..."
RELEASE_STAGE="${RELEASE_DIR}.new.$$"
rm -rf "$RELEASE_STAGE"
mkdir -p "$RELEASE_STAGE"
cp -a "$PAYLOAD_DIR/." "$RELEASE_STAGE/"
chown -R root:root "$RELEASE_STAGE"
find "$RELEASE_STAGE" -type d -exec chmod 755 {} +
find "$RELEASE_STAGE" -type f -exec chmod 644 {} +
chmod 755 "$RELEASE_STAGE/bin/"*.sh "$RELEASE_STAGE/scripts/"*.sh "$RELEASE_STAGE/scripts/asr-friendly-names.php" "$RELEASE_STAGE/scripts/asr-bridge-clients.php" "$RELEASE_STAGE/scripts/asr-manager-perms.sh" "$RELEASE_STAGE/scripts/asr-patch-connected-clients.py" "$RELEASE_STAGE/scripts/asr-migrate-tgif-environment.py" "$RELEASE_STAGE/scripts/asr-patch-allscan-index.py"
RELEASE_PREVIOUS="${RELEASE_DIR}.previous.$$"
rm -rf "$RELEASE_PREVIOUS"
[ -d "$RELEASE_DIR" ] && mv "$RELEASE_DIR" "$RELEASE_PREVIOUS"
mv "$RELEASE_STAGE" "$RELEASE_DIR"
RELEASE_STAGE=""
RELEASE_REPLACED=1
ln -sfn "$RELEASE_DIR" /opt/allscan-reimagined/current

echo "[4/8] Detecting node identity, branding, and bridges..."
if [ -r /dev/tty ] && [ -w /dev/tty ]; then
  "$RELEASE_DIR/scripts/asr-configure.sh" < /dev/tty
else
  "$RELEASE_DIR/scripts/asr-configure.sh"
fi

echo "[5/8] Applying the Reimagined interface and security protections..."
"$RELEASE_DIR/scripts/asr-reapply.sh"

echo "[6/8] Installing automatic update-survival services..."
install -o root -g root -m 755 "$RELEASE_DIR/scripts/asr-reapply.sh" /usr/local/sbin/allscan-reimagined-reapply
install -o root -g root -m 755 "$RELEASE_DIR/scripts/asr-integrity-check.sh" /usr/local/sbin/allscan-reimagined-integrity-check
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
PathChanged=$ALLSCAN_DIR/include/common.php
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
systemctl enable --now allscan-reimagined-reapply.path allscan-reimagined-reapply.timer

echo "[7/8] Validating the installed application..."
php -l "$ALLSCAN_DIR/asr-api.php" >/dev/null
php -l "$ALLSCAN_DIR/index.php" >/dev/null
bash -n /usr/local/bin/allscan_wt_clients.sh
runtime_json=$(curl -fsS "http://127.0.0.1/allscan/asr-api.php?action=runtime-config")
printf '%s' "$runtime_json" | php -r '
  $data = json_decode(stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
  if (($data["ok"] ?? false) !== true || empty($data["node"])) exit(1);
'
auth_json=$(curl -fsS "http://127.0.0.1/allscan/asr-api.php?action=auth-status")
printf '%s' "$auth_json" | php -r '
  $data = json_decode(stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
  if (($data["publicPermission"] ?? 2) > 2) {
      fwrite(STDERR, "Public Permission must be Read Only or lower before internet exposure.\n");
      exit(1);
  }
'
curl -fsS http://127.0.0.1/allscan/ | grep -q 'assets/index-'

rm -rf "$RELEASE_PREVIOUS"
RELEASE_PREVIOUS=""
RELEASE_REPLACED=0
CHANGES_STARTED=0
trap - ERR
if ! prune_old_backups; then
  echo "WARNING: Old rollback backups could not be pruned automatically." >&2
fi

echo "[8/8] Installation complete."
echo
echo "AllScan backend:       $latest_version"
echo "AllScan Reimagined:    v1.0.0 Beta 5.9 Rollup 1"
echo "Personal configuration: /etc/allscan-reimagined/config.json"
echo "Rollback backup:        $BACKUP_DIR"
echo "Open:                    http://$(hostname -I | awk '{print $1}')/allscan/"
echo
echo "Existing user accounts and passwords were not changed."
