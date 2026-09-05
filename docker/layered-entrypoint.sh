#!/bin/sh
set -eu

ALLSCAN_REPO_URL="${ALLSCAN_REPO_URL:-https://github.com/davidgsd/AllScan.git}"
ALLSCAN_REPO_REF="${ALLSCAN_REPO_REF:-main}"
ASR_NODE_NUMBER="${ASR_NODE_NUMBER:-668390}"
ASR_CALLSIGN="${ASR_CALLSIGN:-ASL3}"
ASR_AMI_HOST="${ASR_AMI_HOST:-allstarlink3}"
ASR_AMI_PORT="${ASR_AMI_PORT:-5038}"
ASR_AMI_USER="${ASR_AMI_USER:-admin}"
ASR_AMI_PASS="${ASR_AMI_PASS:-}"
ASR_REQUIRE_LOGIN="${ASR_REQUIRE_LOGIN:-0}"

ASR_AMI_HOST_CFG="$ASR_AMI_HOST"
if ! printf '%s' "$ASR_AMI_HOST_CFG" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
  RESOLVED_AMI_HOST="$(getent hosts "$ASR_AMI_HOST_CFG" 2>/dev/null | awk 'NR==1 {print $1}')"
  if [ -n "$RESOLVED_AMI_HOST" ]; then
    ASR_AMI_HOST_CFG="$RESOLVED_AMI_HOST"
  fi
fi

WEB_ROOT=/var/www/html
STOCK_DIR="$WEB_ROOT/allscan"
ASR_DIR="$WEB_ROOT/asr"
UPSTREAM_CACHE=/opt/upstream-allscan

if [ -z "$ASR_AMI_PASS" ]; then
  echo "ASR_AMI_PASS must be set in .env" >&2
  exit 1
fi

if [ ! -d "$UPSTREAM_CACHE/.git" ]; then
  rm -rf "$UPSTREAM_CACHE"
  git clone --depth 1 --branch "$ALLSCAN_REPO_REF" "$ALLSCAN_REPO_URL" "$UPSTREAM_CACHE"
else
  git -C "$UPSTREAM_CACHE" fetch --depth 1 origin "$ALLSCAN_REPO_REF"
  git -C "$UPSTREAM_CACHE" checkout --force FETCH_HEAD
fi

mkdir -p "$STOCK_DIR" "$ASR_DIR"
rsync -a --delete --exclude=.git "$UPSTREAM_CACHE/" "$STOCK_DIR/"
rsync -a --delete "$STOCK_DIR/" "$ASR_DIR/"

# Route bare host requests to ASR UI instead of Apache's empty web-root 403.
cat > "$WEB_ROOT/index.php" <<'EOF'
<?php
header('Location: /asr/');
exit;
EOF

# Place built frontend assets without deleting backend runtime files.
rsync -a /opt/asr-ui-dist/ "$ASR_DIR/"
rsync -a /opt/asr-source/compat/allscan-v1.01/ "$ASR_DIR/"
cp /opt/asr-source/asr-api.php "$ASR_DIR/asr-api.php"
if [ -f "$STOCK_DIR/js/main.js" ]; then
  mkdir -p "$ASR_DIR/js"
  cp "$STOCK_DIR/js/main.js" "$ASR_DIR/js/main.js"
fi
if [ -f "$STOCK_DIR/include/apiInit.php" ]; then
  cp "$STOCK_DIR/include/apiInit.php" "$ASR_DIR/include/apiInit.php"
fi

if [ -f "$ASR_DIR/astapi/server.php" ]; then
  sed -i "s#require_once('../include/apiInit.php');#require_once(__DIR__ . '/../include/apiInit.php');#" "$ASR_DIR/astapi/server.php"
  sed -i "s#require_once('AMI.php');#require_once(__DIR__ . '/AMI.php');#" "$ASR_DIR/astapi/server.php"
  sed -i "s#require_once('nodeInfo.php');#require_once(__DIR__ . '/nodeInfo.php');#" "$ASR_DIR/astapi/server.php"
  sed -i "s#require_once(__DIR__ . '/asrEchoLink.php');#require_once(__DIR__ . '/asrEchoLink.php');#" "$ASR_DIR/astapi/server.php"
fi

# Upstream v1.01 user form unsets $newUser and later returns it, which emits
# warnings on successful add/edit and breaks subsequent header redirects.
for USER_INDEX in "$STOCK_DIR/user/index.php" "$ASR_DIR/user/index.php"; do
  if [ -f "$USER_INDEX" ]; then
    sed -i 's/unset(\$newUser);/\$newUser = null;/g' "$USER_INDEX"
  fi
done

for MAIN_INDEX in "$STOCK_DIR/index.php" "$ASR_DIR/index.php"; do
  if [ -f "$MAIN_INDEX" ]; then
    perl -0777 -i -pe 's/\$msg\[\]\s*=\s*"User: \$user->name, IP: \$user->ip_addr";/\$msg[] = ((isset(\$user) \&\& is_object(\$user)) ? "User: " . (\$user->name ?? "unknown") . ", IP: " . (\$user->ip_addr ?? "unknown") : "User: anonymous");/g' "$MAIN_INDEX"
    sed -i 's/\$nodeNum/\$node/g' "$MAIN_INDEX"
    sed -i 's#list(\$x, \$call, \$desc, \$loc) = \$astdb\[\$node\];#list(\$x, \$call, \$desc, \$loc) = array_key_exists(\$node, \$astdb) ? \$astdb[\$node] : [\$node, "Node", "Not in ASL DB", (string)\$node];#g' "$MAIN_INDEX"
  fi
done

for COMMON_FILE in "$STOCK_DIR/include/common.php" "$ASR_DIR/include/common.php"; do
  if [ -f "$COMMON_FILE" ]; then
    sed -i '/\$html->a("\$urlbase\/user\/settings\//a\
				$html->a("$urlbase/asr-settings/", null, '\''Themes'\''),' "$COMMON_FILE"
    sed -i "s/return '--';/return '--°F \/ --°C';/" "$COMMON_FILE"
  fi
done

if [ -f "$ASR_DIR/index.php" ]; then
  sed -i 's/if((\$ct = cpuTemp()))/if(false \&\& (\$ct = cpuTemp()))/' "$ASR_DIR/index.php"
fi

mkdir -p /etc/allscan /etc/allscan-reimagined /etc/asterisk
mkdir -p /var/log/asterisk

if [ ! -f /etc/allscan/allscan.db ]; then
  sqlite3 /etc/allscan/allscan.db <<'SQL'
CREATE TABLE IF NOT EXISTS user (
  user_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  hash TEXT NOT NULL,
  email TEXT,
  location TEXT,
  nodenums TEXT,
  permission INTEGER NOT NULL DEFAULT 1,
  timezone_id INTEGER NOT NULL DEFAULT 0,
  last_login INTEGER,
  last_ip_addr TEXT
);
CREATE TABLE IF NOT EXISTS cfg (
  cfg_id INTEGER PRIMARY KEY,
  val TEXT NOT NULL,
  updated INTEGER NOT NULL
);
SQL
fi

if [ ! -f /etc/allscan/favorites.ini ]; then
  cat > /etc/allscan/favorites.ini <<'EOF'
; ASR layered install favorites file
[general]
label[] = "AllStarLink"
cmd[] = "*3"
EOF
elif ! grep -q '^\[general\]' /etc/allscan/favorites.ini; then
  cat >> /etc/allscan/favorites.ini <<'EOF'

[general]
label[] = "AllStarLink"
cmd[] = "*3"
EOF
fi

cat > /etc/allscan-reimagined/config.json <<EOF
{
  "node": "$ASR_NODE_NUMBER",
  "callsign": "$ASR_CALLSIGN",
  "requireLogin": $([ "$ASR_REQUIRE_LOGIN" = "1" ] && echo true || echo false),
  "bridges": []
}
EOF

cat > /etc/asterisk/rpt.conf <<EOF
[$ASR_NODE_NUMBER]
(node-main)
EOF

cat > /etc/asterisk/manager.conf <<EOF
[general]
enabled = yes
port = $ASR_AMI_PORT
bindaddr = $ASR_AMI_HOST_CFG

[$ASR_AMI_USER]
secret = $ASR_AMI_PASS
read = all,system,call,log,verbose,command,agent,user,config
write = all,system,call,log,verbose,command,agent,user,config
EOF

cat > /usr/local/etc/php/conf.d/allscan-include-path.ini <<EOF
include_path=".:/usr/local/lib/php:/var/www/html/allscan/include:/var/www/html/asr/include"
EOF

ASR_ASTDB="$ASR_DIR/astdb.txt"
if [ ! -s "$ASR_ASTDB" ] || [ "$(wc -c < "$ASR_ASTDB" 2>/dev/null || echo 0)" -lt 1200 ]; then
  : > "$ASR_ASTDB"
  i=1
  while [ "$i" -le 80 ]; do
    printf '%s|%s|Layered ASR Node %s|Docker\n' "$((700000 + i))" "$ASR_CALLSIGN" "$i" >> "$ASR_ASTDB"
    i=$((i + 1))
  done
  printf '%s|%s|Primary ASL3 Node|Docker\n' "$ASR_NODE_NUMBER" "$ASR_CALLSIGN" >> "$ASR_ASTDB"
fi

chown -R www-data:www-data /etc/allscan /etc/allscan-reimagined "$ASR_DIR" "$STOCK_DIR"
chmod 664 /etc/allscan/favorites.ini /etc/allscan/allscan.db /etc/allscan-reimagined/config.json || true

echo "Layered install ready: stock at /allscan and ASR overlay at /asr"

exec "$@"