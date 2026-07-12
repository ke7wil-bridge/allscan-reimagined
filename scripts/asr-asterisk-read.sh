#!/bin/sh
set -eu

cmd="${1:-}"
arg="${2:-}"

case "$cmd" in
  nodes|lstats)
    case "$arg" in
      *[!0-9]*|'') echo "Invalid node." >&2; exit 2 ;;
    esac
    exec /usr/sbin/asterisk -rx "rpt $cmd $arg"
    ;;
  file-status)
    case "$arg" in
      /root/tgif-login.env|/etc/systemd/system/connected-clients-daemon.service.d/tgif-token.conf|/usr/local/sbin/tgif-refresh-token.py|/usr/local/sbin/connected-clients-daemon.py) ;;
      *) echo "Invalid file." >&2; exit 2 ;;
    esac
    if [ ! -e "$arg" ]; then
      printf 'missing|%s\n' "$arg"
      exit 0
    fi
    perms=$(stat -c '%a' "$arg" 2>/dev/null || stat -f '%Lp' "$arg")
    mtime=$(stat -c '%Y' "$arg" 2>/dev/null || stat -f '%m' "$arg")
    size=$(stat -c '%s' "$arg" 2>/dev/null || stat -f '%z' "$arg")
    printf 'present|%s|%s|%s|%s\n' "$arg" "$perms" "$mtime" "$size"
    exit 0
    ;;
  journal)
    case "$arg" in
      apache)
        /usr/bin/journalctl --quiet --no-pager --output=short-iso -n 80 -u apache2.service \
          | /usr/bin/awk '!/ sudo\[/ && !/pam_unix\(sudo:session\)/'
        exit 0
        ;;
      asterisk)
        exec /usr/bin/journalctl --quiet --no-pager --output=short-iso -n 40 -u asterisk.service
        ;;
      asr)
        exec /usr/bin/journalctl --quiet --no-pager --output=short-iso -n 40 \
          -u allscan-reimagined-reapply.service \
          -u allscan-reimagined-bridge-clients.service
        ;;
      bridge-clients)
        exec /usr/bin/journalctl --quiet --no-pager --output=short-iso -n 40 \
          -u connected-clients-daemon.service \
          -u tgif-refresh-token.service
        ;;
      *) echo "Invalid journal scope." >&2; exit 2 ;;
    esac
    ;;
  apache-access)
    exec /usr/bin/tail -n 500 /var/log/apache2/access.log
    ;;
  astapi-viewers)
    set -- /run/allscan-reimagined/astapi-*.lock
    [ -e "$1" ] || { echo 0; exit 0; }
    /usr/bin/lsof -t "$@" 2>/dev/null | /usr/bin/sort -u | /usr/bin/wc -l
    ;;
  *) echo "Invalid command." >&2; exit 2 ;;
esac
