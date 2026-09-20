#!/bin/sh
set -eu

SOURCE_DIR=/opt/asr-source
CONFIG_DIR=/etc/allscan-reimagined
WEB_GROUP=www-data
LOCK_FILE=/run/lock/allscan-reimagined-docker-reapply.lock

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
install_helper asr-bridge-control.py allscan-reimagined-bridge-control
install_helper asr-ysf-bridge-control.py allscan-reimagined-ysf-bridge-control
install_helper asr-p25-bridge-control.py allscan-reimagined-p25-bridge-control
install_helper asr-nxdn-bridge-control.py allscan-reimagined-nxdn-bridge-control
install_helper asr-m17-bridge-control.py allscan-reimagined-m17-bridge-control
install_helper asr-m17-usrp-connector.py allscan-reimagined-m17-usrp-connector
install_helper asr-urf-admin.py allscan-reimagined-urf-admin
install_helper asr-asl-ban.py allscan-reimagined-asl-ban
install_helper asr-standalone-admin.py allscan-reimagined-standalone-admin
install_helper asr-tgif-user-session.py allscan-reimagined-tgif-user-session
install_helper asr_bridge_status.py allscan-reimagined-standard-bridge-status
install -o root -g root -m 644 "$SOURCE_DIR/scripts/asr_bridge_status.py" /usr/local/sbin/asr_bridge_status.py
/usr/local/sbin/allscan-reimagined-friendly-names --once

install -d -o root -g "$WEB_GROUP" -m 1775 /run/allscan-reimagined
install -d -o root -g root -m 700 \
  /run/allscan-reimagined/tgif-users \
  /run/allscan-reimagined/tgif-users/tokens \
  /run/allscan-reimagined/tgif-users/snapshots \
  /run/allscan-reimagined/tgif-users/challenges
install -d -o root -g root -m 755 \
  /run/allscan-reimagined-bridge-control \
  /run/allscan-reimagined-standard-bridge-status \
  /run/allscan-reimagined-ysf-bridge-control
install -d -o root -g "$WEB_GROUP" -m 2750 \
  /run/allscan-reimagined-p25-bridge-control \
  /run/allscan-reimagined-nxdn-bridge-control
install -d -o root -g root -m 755 /run/allscan-reimagined-m17
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
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-asterisk-read
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-friendly-names
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-clients
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-lifecycle preview-all
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-lifecycle queue-deletion
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-lifecycle status
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-favorites-update add --file /etc/allscan/favorites*.ini --node * --label *
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-favorites-update delete --file /etc/allscan/favorites*.ini --node *
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-control --connect [a-zA-Z0-9_-]* [0-9]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-control --disconnect [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-ysf-bridge-control --connect [a-zA-Z0-9_-]* [0-9][0-9][0-9][0-9][0-9] --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-ysf-bridge-control --disconnect [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-p25-bridge-control connect [a-zA-Z0-9_-]* [0-9]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-p25-bridge-control disconnect [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-nxdn-bridge-control connect [a-zA-Z0-9_-]* [0-9]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-nxdn-bridge-control disconnect [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-m17-bridge-control --bridge [a-zA-Z0-9_-]* status
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-m17-bridge-control --bridge [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]* connect --reflector M17-[A-Z0-9]* --module [A-Z]
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-m17-bridge-control --bridge [a-zA-Z0-9_-]* --user [a-zA-Z0-9_.@+-]* disconnect
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-asl-ban list
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-asl-ban sync
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-asl-ban ban *
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-asl-ban unban *
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-urf-admin list
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-urf-admin ban [A-Za-z0-9./*-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-urf-admin ban [A-Za-z0-9./*-]* [A-Za-z0-9]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-urf-admin unban [A-Za-z0-9./*-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-urf-admin kick [A-Z0-9./-]* [A-Za-z0-9]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-urf-admin list --actor [A-Za-z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-urf-admin ban [A-Za-z0-9./*-]* [A-Za-z0-9]* --actor [A-Za-z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-urf-admin unban [A-Za-z0-9./*-]* --actor [A-Za-z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-urf-admin kick [A-Z0-9./-]* [A-Za-z0-9]* --actor [A-Za-z0-9_.@+-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-urf-admin dmr-list
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-urf-admin dmr-ban [A-Z0-9./-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-urf-admin dmr-unban [A-Z0-9./-]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-tgif-user-session status [0-9]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-tgif-user-session login [0-9]* [A-Z0-9]* --talkgroup [0-9]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-tgif-user-session login [0-9]* [A-Z0-9]* --talkgroup [0-9]* --captcha [a-z0-9]*
$WEB_GROUP ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-tgif-user-session logout [0-9]*
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
