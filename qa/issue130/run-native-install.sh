#!/bin/bash
set -u

if [ "$#" -ne 2 ]; then
  echo "usage: $0 RELEASE_TREE RESULT_PREFIX" >&2
  exit 64
fi

release_tree=$1
result_prefix=$2
stage_dir=/root/asr130qa/stage

case "$release_tree" in
  /root/asr130qa/baseline|/root/asr130qa/candidate) ;;
  *) echo "refusing unexpected release tree: $release_tree" >&2; exit 64 ;;
esac
case "$result_prefix" in
  /root/asr130qa/results/*) ;;
  *) echo "refusing unexpected result prefix: $result_prefix" >&2; exit 64 ;;
esac

install -d -m 700 "$stage_dir" /root/asr130qa/results
rm -f -- "$result_prefix.rc"
cd "$release_tree" || exit 1
ASR_STAGE_DIR="$stage_dir" bash ./install.sh >"$result_prefix.log" 2>&1
status=$?
printf '%s\n' "$status" >"$result_prefix.rc"
exit "$status"
