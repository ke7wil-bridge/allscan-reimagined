#!/usr/bin/env bash
set -u

ENV_FILE="${ASR_NODE_HEALTH_ENV:-/etc/default/asr-node-health}"
[ -r "$ENV_FILE" ] && . "$ENV_FILE"

NODE_NUMBER="${NODE_NUMBER:-}"
MODE="${MODE:-native}"
ASTERISK_SERVICE="${ASTERISK_SERVICE:-asterisk.service}"
ASTERISK_CONTAINER="${ASTERISK_CONTAINER:-allstarlink3}"
FAILURES_BEFORE_RESTART="${FAILURES_BEFORE_RESTART:-3}"
RESTART_COOLDOWN_SECONDS="${RESTART_COOLDOWN_SECONDS:-1800}"
SNAPSHOT_INTERVAL_SECONDS="${SNAPSHOT_INTERVAL_SECONDS:-300}"
STATE_DIR="${STATE_DIR:-/var/lib/asr-node-health}"
LOG_FILE="${LOG_FILE:-/var/log/asr-node-health.log}"

if [ "${1:-}" = "--self-test" ]; then
  [ "$FAILURES_BEFORE_RESTART" -ge 3 ]
  [ "$RESTART_COOLDOWN_SECONDS" -ge 900 ]
  case "$MODE" in native|docker) ;; *) exit 1 ;; esac
  echo "ASR node-health watchdog self-test: ok"
  exit 0
fi
[ "$#" -eq 0 ] || exit 2
[ -n "$NODE_NUMBER" ] || { echo "NODE_NUMBER is required" >&2; exit 1; }

install -d -o root -g root -m 755 "$STATE_DIR"
touch "$LOG_FILE"
chown root:adm "$LOG_FILE" 2>/dev/null || chown root:root "$LOG_FILE"
chmod 640 "$LOG_FILE"
exec 9>"$STATE_DIR/lock"
flock -n 9 || exit 0

log_event() {
  local level="$1" message="$2" line
  line="$(date --iso-8601=seconds) level=$level node=$NODE_NUMBER mode=$MODE $message"
  printf '%s\n' "$line" >>"$LOG_FILE"
  logger -t asr-node-health -- "$line"
}

asterisk_active() {
  if [ "$MODE" = "docker" ]; then
    [ "$(docker inspect -f '{{.State.Running}}' "$ASTERISK_CONTAINER" 2>/dev/null)" = "true" ]
  else
    systemctl is-active --quiet "$ASTERISK_SERVICE"
  fi
}

asterisk_command() {
  if [ "$MODE" = "docker" ]; then
    timeout 8 docker exec "$ASTERISK_CONTAINER" asterisk -rx "$1"
  else
    timeout 8 asterisk -rx "$1"
  fi
}

probe() {
  asterisk_active || return 1
  asterisk_command "core show uptime" >/dev/null 2>&1 || return 1
  asterisk_command "rpt lstats $NODE_NUMBER" >/dev/null 2>&1 || return 1
}

restart_asterisk() {
  if [ "$MODE" = "docker" ]; then
    timeout 120 docker restart -t 90 "$ASTERISK_CONTAINER" >/dev/null
  else
    timeout 120 systemctl restart "$ASTERISK_SERVICE"
  fi
}

read_counter() {
  local value=0
  [ -r "$1" ] && value=$(cat "$1" 2>/dev/null || echo 0)
  case "$value" in ''|*[!0-9]*) value=0 ;; esac
  printf '%s' "$value"
}

write_snapshot() {
  local now="$1" last mem_available swap_total swap_free swap_used root_used load_value asterisk_rss apache_rss
  last=$(read_counter "$STATE_DIR/last-snapshot")
  [ $((now - last)) -ge "$SNAPSHOT_INTERVAL_SECONDS" ] || return 0
  mem_available=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
  swap_total=$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)
  swap_free=$(awk '/^SwapFree:/ {print $2}' /proc/meminfo)
  swap_used=$((swap_total - swap_free))
  root_used=$(df -P / | awk 'NR==2 {gsub(/%/, "", $5); print $5}')
  load_value=$(awk '{print $1","$2","$3}' /proc/loadavg)
  if [ "$MODE" = "docker" ]; then
    asterisk_rss=$(docker stats --no-stream --format '{{.MemUsage}}' "$ASTERISK_CONTAINER" 2>/dev/null | awk '{print $1}' || true)
  else
    local pid
    pid=$(systemctl show "$ASTERISK_SERVICE" -p MainPID --value 2>/dev/null || echo 0)
    asterisk_rss=$([ "$pid" -gt 0 ] 2>/dev/null && awk '/^VmRSS:/ {print $2"kB"}' "/proc/$pid/status" 2>/dev/null || true)
  fi
  apache_rss=$(ps -C apache2 -o rss= 2>/dev/null | awk '{sum+=$1} END {print sum+0}')
  log_event info "status=healthy mem_available_kb=${mem_available:-0} swap_used_kb=$swap_used root_used_pct=${root_used:-0} load=$load_value asterisk_rss=${asterisk_rss:-unknown} apache_rss_kb=$apache_rss"
  printf '%s\n' "$now" >"$STATE_DIR/last-snapshot"
}

now=$(date +%s)
failures_file="$STATE_DIR/consecutive-failures"
restart_file="$STATE_DIR/last-restart"
if probe; then
  failures=$(read_counter "$failures_file")
  if [ "$failures" -gt 0 ]; then
    log_event recovered "status=responsive prior_consecutive_failures=$failures"
  fi
  printf '0\n' >"$failures_file"
  write_snapshot "$now"
  exit 0
fi

failures=$(read_counter "$failures_file")
failures=$((failures + 1))
printf '%s\n' "$failures" >"$failures_file"
active=no
asterisk_active && active=yes
log_event warning "status=unresponsive service_active=$active consecutive_failures=$failures restart_threshold=$FAILURES_BEFORE_RESTART"
[ "$failures" -ge "$FAILURES_BEFORE_RESTART" ] || exit 0

last_restart=$(read_counter "$restart_file")
if [ $((now - last_restart)) -lt "$RESTART_COOLDOWN_SECONDS" ]; then
  log_event warning "status=restart-suppressed cooldown_remaining_seconds=$((RESTART_COOLDOWN_SECONDS - now + last_restart))"
  exit 0
fi

printf '%s\n' "$now" >"$restart_file"
log_event critical "status=restarting-asterisk reason=three-consecutive-failed-core-and-rpt-probes"
if restart_asterisk; then
  sleep 5
  if probe; then
    printf '0\n' >"$failures_file"
    log_event recovered "status=responsive-after-restart"
    write_snapshot "$(date +%s)"
    exit 0
  fi
fi
log_event critical "status=restart-failed-or-still-unresponsive"
exit 1
