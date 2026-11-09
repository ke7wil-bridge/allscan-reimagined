#!/usr/bin/env bash
# Read-only qualification: never deploy, restart services, or alter bridge state.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-/tmp/asr-css-qualification-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
{
  echo "ASR CSS qualification $(date -u +%FT%TZ)"
  echo "HEAD $(git rev-parse HEAD)"
  echo "CSS module SHA256:"
  sha256sum src/index.css src/styles/asr/*.css
  echo "CSS lines $(cat src/index.css src/styles/asr/*.css | wc -l)"
  echo '=== Working tree ==='
  git status --short
} > "$OUT/manifest.txt"
python3 - "$OUT" <<'PY'
import sys,re,json,collections,pathlib
out=pathlib.Path(sys.argv[1]); s='\n'.join(p.read_text() for p in [pathlib.Path('src/index.css'), *sorted(pathlib.Path('src/styles/asr').glob('*.css'))])
selectors=collections.Counter(re.findall(r'(?m)^([^{}\n]+)\s*\{',s))
report={'lines':s.count('\n')+1,'important_count':len(re.findall(r'!important\b',s)), 'media_queries':len(re.findall(r'@media\s*\(',s)), 'repeated_selector_heads':[{ 'selector':k.strip(),'count':v} for k,v in selectors.most_common() if v>1][:100]}
(out/'css-audit.json').write_text(json.dumps(report,indent=2)+'\n')
print('CSS audit:',report['lines'],'lines,',report['important_count'],'important flags,',report['media_queries'],'media conditions')
PY
failures=()
run() {
  local label="$1"; shift
  echo "=== $label ==="
  if "$@" > "$OUT/$label.log" 2>&1; then
    echo "PASS: $label"; tail -5 "$OUT/$label.log"
  else
    failures+=("$label")
    echo "FAIL: $label"; tail -25 "$OUT/$label.log"
  fi
}
run build npm run build
run lint npm run lint
run bridge-dashboard node scripts/asr-bridge-dashboard-self-test.mjs
run favorites-placement node scripts/asr-favorites-placement-self-test.mjs
run bridge-admin-controls python3 scripts/asr-bridge-admin-controls-self-test.py
run custom-commands node scripts/asr-custom-command-buttons-self-test.mjs
run maintenance-regression node scripts/asr-maintenance-regression-self-test.mjs
run settings-redesign node scripts/asr-settings-redesign-self-test.mjs
run settings-tabs-unit node scripts/asr-settings-tabs-self-test.mjs
run update-dialog node scripts/asr-update-dialog-self-test.mjs
run release-notes node scripts/asr-release-notes-self-test.mjs
run release-notes-browser node scripts/asr-release-notes-browser-self-test.mjs
run cpu-temperature node scripts/asr-cpu-temperature-consumers-self-test.mjs
run mobile-layout node scripts/asr-mobile-layout-browser-self-test.mjs
run support node scripts/asr-support-self-test.mjs
# The default support test URL (4173) belongs to a separate legacy ASR worktree.
# Start a dedicated mock server so this gate always tests the candidate CSS.
run support-browser bash -c 'set -e; ASR_MOCK=1 ./node_modules/.bin/vite --host 127.0.0.1 --port 4197 --strictPort > "$1/support-vite.log" 2>&1 & server=$!; trap "kill $server 2>/dev/null || true" EXIT; for i in $(seq 1 60); do if curl -fsS http://127.0.0.1:4197/asr/ >/dev/null 2>&1; then break; fi; sleep .1; done; ASR_SUPPORT_TEST_URL=http://127.0.0.1:4197/asr/ ASR_SUPPORT_SCREENSHOT_DIR="$1/support-screenshots" node scripts/asr-support-browser-self-test.mjs' bash "$OUT"
run bridge-install-browser node scripts/asr-bridge-install-browser-self-test.mjs
run settings-tabs-browser bash scripts/asr-settings-tabs-browser-self-test.sh
run urf-client-browser node scripts/asr-urf-client-management-browser-self-test.mjs
run lookup-map-browser node scripts/asr-lookup-map-browser-self-test.mjs
# Visual comparisons use 20 deterministic mock screenshots, five themes x four sizes.
# The baseline directory must be an explicitly approved known-good capture.
if [[ -n "${ASR_CSS_VISUAL_BASELINE:-}" ]]; then
  run visual-regression bash -c 'set -e; ASR_MOCK=1 ./node_modules/.bin/vite --host 127.0.0.1 --port 4197 --strictPort > "$1/visual-vite.log" 2>&1 & server=$!; trap "kill $server 2>/dev/null || true" EXIT; for i in $(seq 1 60); do if curl -fsS http://127.0.0.1:4197/asr/ >/dev/null 2>&1; then break; fi; sleep .1; done; node scripts/asr-css-visual-baseline.mjs "$1/visual-candidate"; python3 scripts/asr-css-visual-compare.py "$2" "$1/visual-candidate"' bash "$OUT" "$ASR_CSS_VISUAL_BASELINE"
else
  failures+=("visual-regression")
  echo 'FAIL visual-regression: set ASR_CSS_VISUAL_BASELINE to an approved known-good capture'
fi
if ((${#failures[@]})); then
  printf 'FAIL %s gate(s): %s; report=%s\n' "${#failures[@]}" "${failures[*]}" "$OUT" | tee "$OUT/result.txt"
  exit 1
fi
printf 'PASS all CSS qualification gates; report=%s\n' "$OUT" | tee "$OUT/result.txt"
