#!/bin/bash
set -Eeuo pipefail

MANAGER_CONF="/etc/asterisk/manager.conf"

[ -f "$MANAGER_CONF" ] || exit 0

WEB_GROUP="www-data"
getent group "$WEB_GROUP" >/dev/null 2>&1 || WEB_GROUP="apache"
getent group "$WEB_GROUP" >/dev/null 2>&1 || WEB_GROUP="http"
getent group "$WEB_GROUP" >/dev/null 2>&1 || exit 0

if command -v setfacl >/dev/null 2>&1; then
  setfacl -m "g:${WEB_GROUP}:rx" /etc/asterisk 2>/dev/null || true
  setfacl -m "g:${WEB_GROUP}:r" "$MANAGER_CONF" 2>/dev/null || true
  exit 0
fi

chgrp "$WEB_GROUP" "$MANAGER_CONF" 2>/dev/null || true
chmod g+r "$MANAGER_CONF" 2>/dev/null || true
