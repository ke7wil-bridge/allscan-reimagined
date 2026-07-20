#!/bin/bash
set -Eeuo pipefail

detect_allscan_dir() {
  if [ -n "${ASR_ALLSCAN_DIR:-}" ]; then
    printf '%s\n' "$ASR_ALLSCAN_DIR"
  elif [ -d /var/www/html/allscan ]; then
    printf '%s\n' /var/www/html/allscan
  elif [ -d /srv/http/allscan ]; then
    printf '%s\n' /srv/http/allscan
  else
    return 1
  fi
}

apply_favorites_permissions() {
  local allscan_dir etc_dir data_dir owner owner_user owner_group web_favorite etc_favorite
  local migration_dir migration_backup favorite backup
  allscan_dir=$(detect_allscan_dir) || { echo "AllScan installation not found." >&2; return 1; }
  etc_dir="${ASR_ETC_ALLSCAN_DIR:-/etc/allscan}"
  data_dir="${ASR_DATA_DIR:-/var/lib/allscan-reimagined}"
  owner="${ASR_OWNER:-root:${ASR_WEB_GROUP:-www-data}}"
  owner_user=${owner%%:*}
  owner_group=${owner#*:}
  web_favorite="$allscan_dir/favorites.ini"
  etc_favorite="$etc_dir/favorites.ini"

  install -d -o "$owner_user" -g "$owner_group" -m 775 "$etc_dir"
  install -d -o "$owner_user" -g "$owner_group" -m 755 "$allscan_dir"

  if [ ! -e "$etc_favorite" ] && [ -f "$web_favorite" ] && [ ! -L "$web_favorite" ]; then
    install -o "$owner_user" -g "$owner_group" -m 664 "$web_favorite" "$etc_favorite"
  fi

  if [ -e "$etc_favorite" ]; then
    if [ -f "$web_favorite" ] && [ ! -L "$web_favorite" ] && ! cmp -s "$web_favorite" "$etc_favorite"; then
      migration_dir="$data_dir/migrations"
      install -d -o "$owner_user" -g "$owner_group" -m 700 "$migration_dir"
      migration_backup="$migration_dir/favorites-web-before-link-$(date +%Y%m%d-%H%M%S)-$$.ini"
      install -o "$owner_user" -g "$owner_group" -m 600 "$web_favorite" "$migration_backup"
      echo "Preserved a differing web-root Favorites copy at $migration_backup"
    fi
    rm -f "$web_favorite"
    ln -s "$etc_favorite" "$web_favorite"
  fi

  shopt -s nullglob
  for favorite in "$etc_dir"/favorites*.ini "$allscan_dir"/favorites*.ini; do
    [ -f "$favorite" ] || continue
    [ ! -L "$favorite" ] || continue
    backup="${favorite}.bak"
    if [ ! -e "$backup" ]; then
      install -o "$owner_user" -g "$owner_group" -m 664 "$favorite" "$backup"
    fi
    chown "$owner" "$favorite" "$backup"
    chmod 664 "$favorite" "$backup"
  done
  shopt -u nullglob
}

self_test() {
  local tmp owner target
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/asr-favorites-test.XXXXXX")
  trap 'rm -rf "$tmp"' RETURN
  owner="$(id -un):$(id -gn)"
  mkdir -p "$tmp/web" "$tmp/etc" "$tmp/data"
  printf '%s\n' 'label[] = "Web Favorite 12345"' > "$tmp/web/favorites.ini"

  ASR_ALLSCAN_DIR="$tmp/web" \
  ASR_ETC_ALLSCAN_DIR="$tmp/etc" \
  ASR_DATA_DIR="$tmp/data" \
  ASR_OWNER="$owner" \
    apply_favorites_permissions

  [ -L "$tmp/web/favorites.ini" ] || { echo "self-test did not create the web Favorites symlink" >&2; return 1; }
  target=$(readlink "$tmp/web/favorites.ini")
  [ "$target" = "$tmp/etc/favorites.ini" ] || { echo "self-test symlink target is incorrect" >&2; return 1; }
  [ -f "$tmp/etc/favorites.ini.bak" ] || { echo "self-test did not create the stock AllScan backup file" >&2; return 1; }
  cmp -s "$tmp/etc/favorites.ini" "$tmp/etc/favorites.ini.bak" || { echo "self-test backup contents differ" >&2; return 1; }

  rm -f "$tmp/web/favorites.ini"
  printf '%s\n' 'label[] = "Different Web Favorite 54321"' > "$tmp/web/favorites.ini"
  ASR_ALLSCAN_DIR="$tmp/web" \
  ASR_ETC_ALLSCAN_DIR="$tmp/etc" \
  ASR_DATA_DIR="$tmp/data" \
  ASR_OWNER="$owner" \
    apply_favorites_permissions >/dev/null

  [ -L "$tmp/web/favorites.ini" ] || { echo "self-test did not restore the Favorites symlink" >&2; return 1; }
  find "$tmp/data/migrations" -type f -name 'favorites-web-before-link-*.ini' -exec grep -q 'Different Web Favorite' {} \; -print -quit \
    | grep -q . || { echo "self-test did not preserve a differing web Favorites copy" >&2; return 1; }
  echo "favorites permissions and single-source self-test: ok"
}

case "${1:---apply}" in
  --apply) apply_favorites_permissions ;;
  --self-test) self_test ;;
  *) echo "Usage: $0 [--apply|--self-test]" >&2; exit 2 ;;
esac
