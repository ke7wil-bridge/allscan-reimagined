#!/bin/sh
set -eu

SOURCE_DIR=/opt/asr-source
CONFIG_DIR=/etc/allscan-reimagined
WEB_GROUP=www-data
LOCK_FILE=/run/lock/allscan-reimagined-docker-reapply.lock
UPDATE_MODE="${ASR_UPDATE_MODE:-native}"

case "$UPDATE_MODE" in
  native|external|container) ;;
  *) echo "Invalid ASR_UPDATE_MODE." >&2; exit 1 ;;
esac

mkdir -p /run/lock
exec 9>"$LOCK_FILE"
flock -n 9 || {
  echo "Another Docker ASR reapply is already running." >&2
  exit 1
}

CONFIG_FILE="$CONFIG_DIR/config.json"
test -f "$CONFIG_FILE" && test ! -L "$CONFIG_FILE" || {
  echo "ASR config.json is missing or unsafe." >&2
  exit 1
}
php -r '
  $data = json_decode((string) file_get_contents($argv[1]), true, 512, JSON_THROW_ON_ERROR);
  if (!is_array($data) || !preg_match("/^[0-9]{3,10}$/D", (string)($data["node"] ?? "")) || !is_array($data["bridges"] ?? null)) exit(1);
' "$CONFIG_FILE" || {
  echo "ASR config.json is invalid." >&2
  exit 1
}

# Reassert the authoritative ASL node-database links in case an upstream
# AllScan refresh or runtime helper replaced a web-tree link.
install -o root -g root -m 755 "$SOURCE_DIR/scripts/asr-node-db-link.sh" /usr/local/sbin/allscan-reimagined-node-db-link
/usr/local/sbin/allscan-reimagined-node-db-link

install_helper() {
  source_path="$SOURCE_DIR/scripts/$1"
  target_path="/usr/local/sbin/$2"
  test -f "$source_path" || {
    echo "Required ASR helper is missing: $1" >&2
    exit 1
  }
  install -o root -g root -m 755 "$source_path" "$target_path"
}

install_helper asr-asterisk-read.sh allscan-reimagined-asterisk-read
install_helper asr-friendly-names.php allscan-reimagined-friendly-names
install_helper asr-bridge-clients.php allscan-reimagined-bridge-clients
install_helper asr-bridge-lifecycle.py allscan-reimagined-bridge-lifecycle
install_helper asr-favorites-update.py allscan-reimagined-favorites-update
install_helper asr-favorites-manager.py allscan-reimagined-favorites-manager
install_helper asr-bridge-control.py allscan-reimagined-bridge-control
install_helper asr-managed-dmr-net-control.py allscan-reimagined-managed-dmr-net-control
install_helper asr-ysf-bridge-control.py allscan-reimagined-ysf-bridge-control
install_helper asr-p25-bridge-control.py allscan-reimagined-p25-bridge-control
install_helper asr-nxdn-bridge-control.py allscan-reimagined-nxdn-bridge-control
install_helper asr-m17-bridge-control.py allscan-reimagined-m17-bridge-control
install_helper asr-m17-usrp-connector.py allscan-reimagined-m17-usrp-connector
install_helper asr-global-ban.py allscan-reimagined-global-ban
rm -f /usr/local/sbin/allscan-reimagined-urf-admin
# Global Ban policy on the persistent ASR state volume. Bans saved by older
# releases under the retired URF name migrate once.
GLOBAL_BAN_DIR=/var/lib/allscan-reimagined/admin
install -d -o root -g root -m 755 "$GLOBAL_BAN_DIR"
for legacy in /run/urf-wil-config/urfd.blacklist "$GLOBAL_BAN_DIR/urfd.blacklist"; do
  if [ -f "$legacy" ] && [ ! -L "$legacy" ] && [ ! -e "$GLOBAL_BAN_DIR/global-bans.list" ]; then
    cp -p "$legacy" "$GLOBAL_BAN_DIR/global-bans.list"
  fi
done
[ -e "$GLOBAL_BAN_DIR/global-bans.list" ] || printf '%s\n' '# Managed by AllScan Reimagined Global Ban' > "$GLOBAL_BAN_DIR/global-bans.list"
[ -e "$GLOBAL_BAN_DIR/asr-timed-bans.json" ] || printf '%s\n' '{}' > "$GLOBAL_BAN_DIR/asr-timed-bans.json"
[ -e "$GLOBAL_BAN_DIR/asr-admin-audit.jsonl" ] || : > "$GLOBAL_BAN_DIR/asr-admin-audit.jsonl"
chmod 644 "$GLOBAL_BAN_DIR/global-bans.list" "$GLOBAL_BAN_DIR/asr-timed-bans.json"
chmod 640 "$GLOBAL_BAN_DIR/asr-admin-audit.jsonl"
install_helper asr-asl-ban.py allscan-reimagined-asl-ban
install_helper asr-standalone-admin.py allscan-reimagined-standalone-admin
install_helper asr-tgif-user-session.py allscan-reimagined-tgif-user-session
install_helper asr_bridge_status.py allscan-reimagined-standard-bridge-status
install -o root -g root -m 644 "$SOURCE_DIR/scripts/asr_bridge_status.py" /usr/local/sbin/asr_bridge_status.py
/usr/local/sbin/allscan-reimagined-friendly-names --once

# Containerized ASL3 provisioning is deliberately delegated to a narrow host
# socket.  Never install the native root helper in the web container and never
# expose the Docker socket to Apache.
install -d -o root -g root -m 755 /usr/local/libexec/allscan-reimagined
if [ -S /run/allscan-reimagined-host/bridge-setup.sock ]; then
  install -o root -g root -m 755 "$SOURCE_DIR/scripts/asr-container-host-client.py" \
    /usr/local/libexec/allscan-reimagined/asr-bridge-setup-helper.py
  install -o root -g root -m 755 "$SOURCE_DIR/scripts/asr-provisioning-detect.py" \
    /usr/local/libexec/allscan-reimagined/asr-provisioning-detect.py
  install -o root -g root -m 755 "$SOURCE_DIR/scripts/asr-container-host-control-client.py" \
    /usr/local/libexec/allscan-reimagined/asr-container-host-control-client.py
  if [ "$UPDATE_MODE" = "container" ] || [ "$UPDATE_MODE" = "external" ]; then
    install -o root -g root -m 755 "$SOURCE_DIR/scripts/asr-container-update-client.py" \
      /usr/local/sbin/allscan-reimagined-updater
  fi
  for control_helper in \
    allscan-reimagined-managed-dmr-net-control \
    allscan-reimagined-service-status \
    allscan-reimagined-ysf-bridge-control \
    allscan-reimagined-p25-bridge-control \
    allscan-reimagined-nxdn-bridge-control \
    allscan-reimagined-m17-bridge-control \
    allscan-reimagined-net-bridge-mode-control \
    allscan-reimagined-fixed-bridge-recovery; do
    install -o root -g root -m 755 "$SOURCE_DIR/scripts/asr-container-host-control-client.py" \
      "/usr/local/sbin/$control_helper"
  done
else
  rm -f /usr/local/sbin/allscan-reimagined-fixed-bridge-recovery
  rm -f /usr/local/libexec/allscan-reimagined/asr-bridge-setup-helper.py \
    /usr/local/libexec/allscan-reimagined/asr-provisioning-detect.py \
    /usr/local/libexec/allscan-reimagined/asr-container-host-control-client.py
fi
if [ "$UPDATE_MODE" = "native" ]; then
  rm -f /usr/local/sbin/allscan-reimagined-updater
fi

install -d -o root -g "$WEB_GROUP" -m 1775 /run/allscan-reimagined
install -d -o root -g root -m 700 \
  /run/allscan-reimagined/tgif-users \
  /run/allscan-reimagined/tgif-users/tokens \
  /run/allscan-reimagined/tgif-users/snapshots \
  /run/allscan-reimagined/tgif-users/challenges
install -d -o root -g root -m 755 \
  /run/allscan-reimagined-bridge-control \
  /run/allscan-reimagined-standard-bridge-status
if [ ! -S /run/allscan-reimagined-host/bridge-setup.sock ]; then
  install -d -o root -g root -m 755 /run/allscan-reimagined-ysf-bridge-control
  install -d -o root -g "$WEB_GROUP" -m 2750 \
    /run/allscan-reimagined-p25-bridge-control \
    /run/allscan-reimagined-nxdn-bridge-control
	install -d -o root -g root -m 755 /run/allscan-reimagined-m17
fi
install -d -o root -g root -m 755 /run/asr-standalone-admin
install -d -o root -g root -m 750 /var/log/allscan-reimagined
install -d -o root -g root -m 755 /var/lib/allscan-reimagined
install -d -o root -g root -m 700 /var/lib/allscan-reimagined/bridge-ownership /var/lib/allscan-reimagined/bridge-tombstones /var/lib/allscan-reimagined/bridge-deletion-queue /var/lib/allscan-reimagined/bridge-creation-intents

chown "root:$WEB_GROUP" "$CONFIG_DIR" "$CONFIG_FILE"
chmod 775 "$CONFIG_DIR"
chmod 664 "$CONFIG_FILE"
if [ -e "$CONFIG_DIR/secrets.json" ]; then
  test -f "$CONFIG_DIR/secrets.json" && test ! -L "$CONFIG_DIR/secrets.json" || {
    echo "ASR secrets.json is unsafe." >&2
    exit 1
  }
  php -r 'json_decode((string) file_get_contents($argv[1]), true, 512, JSON_THROW_ON_ERROR);' \
    "$CONFIG_DIR/secrets.json" || {
      echo "ASR secrets.json is invalid." >&2
      exit 1
    }
  chown "root:$WEB_GROUP" "$CONFIG_DIR/secrets.json"
  chmod 640 "$CONFIG_DIR/secrets.json"
fi

cat > /etc/sudoers.d/allscan-reimagined-docker <<EOF
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-docker-reapply
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-updater --check-json
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-updater --preflight-json
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-updater --queue-update
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-updater --recover-json
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-updater --status-json [0-9]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-updater --diagnostic-json [0-9]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-asterisk-read
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-friendly-names
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-clients
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-lifecycle preview-all
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-lifecycle queue-deletion
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-lifecycle status
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-favorites-update add --file /etc/allscan/favorites*.ini --node * --label *
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-favorites-update delete --file /etc/allscan/favorites*.ini --node *
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-favorites-manager *
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-control --connect [a-zA-Z0-9_-]* [0-9]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-control --disconnect [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-managed-dmr-net-control --bridge [a-zA-Z0-9_-]* --connect [0-9]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-managed-dmr-net-control --bridge [a-zA-Z0-9_-]* --disconnect
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-managed-dmr-net-control --bridge [a-zA-Z0-9_-]* --status
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-ysf-bridge-control --connect [a-zA-Z0-9_-]* [0-9][0-9][0-9][0-9][0-9] --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-ysf-bridge-control --disconnect [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-p25-bridge-control connect [a-zA-Z0-9_-]* [0-9]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-p25-bridge-control disconnect [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-nxdn-bridge-control connect [a-zA-Z0-9_-]* [0-9]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-nxdn-bridge-control disconnect [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-m17-bridge-control --bridge [a-zA-Z0-9_-]* status
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-m17-bridge-control --bridge [a-zA-Z0-9_-]* qualification-start
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-m17-bridge-control --bridge [a-zA-Z0-9_-]* qualification-complete
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-m17-bridge-control --bridge [a-zA-Z0-9_-]* qualification-cancel
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-m17-bridge-control --bridge [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]* connect --reflector M17-[A-Z0-9]* --module [A-Z]
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-m17-bridge-control --bridge [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]* disconnect
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-net-bridge-mode-control --mode dmr
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-net-bridge-mode-control --mode ysf
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-net-bridge-mode-control --mode p25
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-net-bridge-mode-control --mode nxdn
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-net-bridge-mode-control --mode m17
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-asl-ban list
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-asl-ban sync
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-asl-ban ban *
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-asl-ban unban *
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-global-ban list --actor [A-Za-z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-global-ban ban [A-Za-z0-9_./*-]* [A-Za-z0-9]* --actor [A-Za-z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-global-ban unban [A-Za-z0-9_./*-]* --actor [A-Za-z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-global-ban kick [A-Z0-9_./-]* ZELLO --actor [A-Za-z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-global-ban zello-ban-list --actor [A-Za-z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-global-ban zello-ban ZELLO_EXACT_HEX_[A-F0-9]* [A-Za-z0-9]* --actor [A-Za-z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-global-ban zello-unban ZELLO_EXACT_HEX_[A-F0-9]* --actor [A-Za-z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-tgif-user-session status node
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-tgif-user-session login node [A-Z0-9]* --talkgroup [0-9]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-tgif-user-session login node [A-Z0-9]* --talkgroup [0-9]* --captcha [a-z0-9]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-tgif-user-session logout node
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/libexec/allscan-reimagined/asr-bridge-setup-helper.py m17-plan
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/libexec/allscan-reimagined/asr-bridge-setup-helper.py m17-install
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/libexec/allscan-reimagined/asr-bridge-setup-helper.py p25-plan
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/libexec/allscan-reimagined/asr-bridge-setup-helper.py p25-install
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/libexec/allscan-reimagined/asr-bridge-setup-helper.py nxdn-plan
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/libexec/allscan-reimagined/asr-bridge-setup-helper.py nxdn-install
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/libexec/allscan-reimagined/asr-bridge-setup-helper.py ysf-plan
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/libexec/allscan-reimagined/asr-bridge-setup-helper.py ysf-install
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/libexec/allscan-reimagined/asr-bridge-setup-helper.py dmr-plan
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/libexec/allscan-reimagined/asr-bridge-setup-helper.py dmr-install
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/libexec/allscan-reimagined/asr-bridge-setup-helper.py dmr-security-update
EOF
chmod 440 /etc/sudoers.d/allscan-reimagined-docker
visudo -cf /etc/sudoers.d/allscan-reimagined-docker >/dev/null

# Exercise the same configuration loader used by bridge controls.  This is a
# real apply validation, not a success marker: malformed or unreadable saved
# bridge configuration makes reapply fail.
PYTHONPATH=/usr/local/sbin python3 -c \
  'import runpy; data=runpy.run_path("/usr/local/sbin/allscan-reimagined-bridge-control")["load_config"](); assert isinstance(data.get("bridges"), list)'

tmp_marker=$(mktemp /run/allscan-reimagined/.docker-reapply.XXXXXX)
config_hash=$(sha256sum "$CONFIG_FILE" | awk '{print $1}')
printf '{"ok":true,"configSha256":"%s"}\n' "$config_hash" > "$tmp_marker"
chown "root:$WEB_GROUP" "$tmp_marker"
chmod 640 "$tmp_marker"
mv "$tmp_marker" /run/allscan-reimagined/docker-reapply.json

echo "Docker ASR configuration reapplied successfully."
