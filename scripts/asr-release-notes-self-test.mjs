#!/usr/bin/env node
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parseReleaseChanges, parseReleaseCategories } from '../src/lib/releaseNotes.js'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
const pkg = JSON.parse(read('../package.json'))
const config = read('../src/config/whatsNew.ts')
const css = read('../src/styles/asr/06-compact-controls-and-talkers.css')
const markdown = read(`../release-notes/v${pkg.version}.md`)

// The notes shown to users must come from the authoritative notes for this exact build.
assert.ok(config.includes(`version: '${pkg.version}'`), 'Update popup version does not match the package')
assert.ok(config.includes(`v${pkg.version}.md?raw`), 'Update popup is not tied to this release file')
assert.ok(config.includes('parseReleaseCategories(releaseNotes)'), 'Full release list is not used')
assert.ok(!config.includes('items: ['), 'Manually maintained highlights must not replace release notes')
const categories = parseReleaseCategories(markdown)
const realChanges = categories.flatMap((category) => category.items)
assert.deepEqual(realChanges, parseReleaseChanges(markdown), 'Every shipped change must be included in its category')
assert.ok(categories.length >= 3, 'Changes must be grouped by feature area')
// Preserve the exact Beta 8.7 regression requirements without falsely repeating old features in a maintenance release.
const beta87 = parseReleaseCategories(read('../release-notes/v1.0.0-beta.8.7.md'))
assert.ok(beta87.length >= 4 && beta87.some(c => /AllStar/.test(c.title)) && beta87.some(c => /Talker/.test(c.title)))
const beta87Changes = beta87.flatMap(c => c.items)
assert.ok(beta87Changes.some(item => /bubble chart/i.test(item)))
assert.ok(beta87Changes.some(item => /M17 talker cards/i.test(item)))
assert.ok(beta87Changes.length >= 15, 'Incomplete Beta 8.7 change list')
if (pkg.version === '1.0.0-beta.8.7.1') {
  for (const required of [/1999/, /manual disconnect/i, /Global Ban/i, /M17.*qualification/i, /Zello.*connected.client/i, /What.s New/i]) {
    assert.ok(realChanges.some(item => required.test(item)), `Maintenance change missing: ${required}`)
  }
  assert.ok(categories.some(c => /AllStar/.test(c.title)))
  assert.ok(categories.some(c => /Talker/.test(c.title)))
  assert.ok(realChanges.length >= 15, 'Incomplete maintenance change list')
}

// Verify short and long lists, continuation lines, nested bullets, and excluded sections.
assert.deepEqual(parseReleaseChanges('## Changes\n- One fix.\n## Known limitations\n- Not shipped.'), ['One fix.'])
const longList = Array.from({ length: 40 }, (_, index) => `- Change ${index + 1} explained plainly.`).join('\n')
const long = `# Update\n## Changes since last release\n${longList}\n- Another improvement\n  with a wrapped description.\n  - A nested fix.\n1. Final numbered fix.\n## Known limitations\n- Not a released feature.\n## Installation\n- Not a fix.\n`
const items = parseReleaseChanges(long)
assert.equal(items.length, 43, 'Long updates must contain every item, including the last')
assert.equal(items.at(-1), 'Final numbered fix.')
assert.equal(items.at(-3), 'Another improvement with a wrapped description.')
assert.ok(!items.includes('Not a released feature.'))
assert.throws(() => parseReleaseChanges('## Known limitations\n- No shipped changes'), /complete bulleted Changes/)

// A bounded notes scroller keeps the close button and footer outside the scrolling area.
assert.match(css, /\.asr-update-dialog--whats-new\s*\{[^}]*height:[^;]+;/s)
assert.match(css, /\.asr-update-dialog--whats-new \.asr-whats-new\s*\{[^}]*overflow-y:\s*scroll;/s)
assert.match(css, /\.asr-update-dialog--whats-new > footer\s*\{[^}]*flex:\s*0 0 auto;/s)
assert.match(css, /\.asr-whats-new::-webkit-scrollbar\s*\{[^}]*width:\s*12px;/s)

console.log(`ASR release notes self-test passed (${realChanges.length} documented changes; 43-item long-list regression)`)
