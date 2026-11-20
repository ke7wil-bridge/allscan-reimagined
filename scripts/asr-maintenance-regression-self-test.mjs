#!/usr/bin/env node

import { readFileSync } from 'node:fs'

const root = new URL('../', import.meta.url)
const read = (path) => readFileSync(new URL(path, root), 'utf8')
const check = (condition, message) => {
  if (!condition) throw new Error(message)
}

const app = read('src/App.tsx')
const live = read('src/lib/allscanLive.ts')
const api = read('asr-api.php')
const serverApi = read('server/asr-api.php')
const installer = read('install.sh')
const dockerfile = read('Dockerfile.layered')
const build = read('build-release.sh')
const stockManifest = JSON.parse(read('stock-allscan.json'))

check(api === serverApi, 'Native and packaged ASR API sources differ.')
check(
  app.includes('const TALKER_HISTORY_MAX_AGE_SECONDS = 24 * 60 * 60')
    && app.includes('.filter((entry) => entry.eventEpoch >= cutoff)')
    && app.includes('Math.max(activeCandidate.eventEpoch, activeCandidate.startedEpoch) >= cutoff')
    && app.includes('[clock, currentTalker, recentTalkers, bridgeState.cards, enrichBridgeTalker]'),
  'Talker cards are not capped and re-evaluated at the 24-hour boundary.',
)
check(
  stockManifest.version === 'v1.06'
    && stockManifest.commit === 'bbfd2a444f05091ea6e66907fca0d804780c65f6'
    && stockManifest.sha256 === 'a06cb8e197f8b461482cdf6aec60740e1bcaf68345f57ca3694b51ba7176cc5b',
  'Pinned stock AllScan v1.06 provenance changed.',
)
check(
  app.includes('className="allscan-action-button allscan-custom-commands-button"'),
  'Commands button does not inherit the shared action-button theme.',
)
check(
  app.includes('config.phpSecurityWarning')
    && app.includes('warnings.forEach((warning) => appendNodeMessage(warning))')
    && live.includes('storageWarning?: string')
    && live.includes('phpSecurityWarning?: string'),
  'Storage and Docker PHP runtime warnings are not surfaced in Node Messages.',
)
check(
  live.includes('const body = await response.text()')
    && live.includes('Favorites request failed (${response.status}). Check node storage and the shared AllScan database.'),
  'Favorites failures do not preserve an actionable storage/database error.',
)
check(
  api.includes("define('ASR_JSON_API', true)")
    && api.includes("'Node storage or the shared AllScan database is unavailable. Check disk space, inodes, and database health.'")
    && api.includes("'storageWarning' => $storageWarning")
    && api.includes("'phpSecurityWarning' => $phpSecurityWarning"),
  'ASR API does not fail closed with JSON or report critical node storage pressure.',
)
for (const version of ['v1.02', 'v1.03', 'v1.04', 'v1.05', 'v1.06']) {
  const database = read(`compat/allscan-${version}/include/dbUtils.php`)
  check(
    database.includes("defined('ASR_JSON_API')")
      && database.includes("throw new RuntimeException('Shared AllScan database validation failed.')"),
    `AllScan ${version} database initialization can still corrupt ASR JSON output.`,
  )
}
check(
  read('compat/allscan-v1.06/include/common.php').includes('$AllScanVersion = "v1.06";')
    && build.includes('COMPAT_VERSIONS="v1.02 v1.03 v1.04 v1.05 v1.06"'),
  'AllScan v1.06 compatibility is not included in the release manifest.',
)
check(
  dockerfile.includes('FROM php:8.3.36-apache')
    && installer.includes('ensure_php_security_updates')
    && installer.includes('apt-get install -y --only-upgrade'),
  'Docker and native installers do not enforce the critical PHP security refresh.',
)

console.log('maintenance regression self-test: ok')
