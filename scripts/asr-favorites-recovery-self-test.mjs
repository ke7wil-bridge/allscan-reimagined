#!/usr/bin/env node
import assert from 'node:assert/strict'
import { canPreviewFavoriteBackup } from '../src/lib/favoritesRecovery.ts'

const backup = { path: '/etc/allscan/favorites-test.ini.asr-backup-20261107T190000Z', exists: true, symlink: false, bytes: 120, readable: false }
assert.equal(canPreviewFavoriteBackup(backup), true, 'Root-owned helper backup must offer privileged preview')
for (const name of ['favorites.ini.bak', 'favorites-stock-before-canonical-20261107.ini']) {
  assert.equal(canPreviewFavoriteBackup({ ...backup, path: '/etc/allscan/' + name }), true)
}
for (const change of [{ exists: false }, { symlink: true }, { bytes: null }, { bytes: 0 },
  { path: '/etc/allscan/favorites.ini' }, { path: '/etc/allscan/not-a-backup.txt' }]) {
  assert.equal(canPreviewFavoriteBackup({ ...backup, ...change }), false)
}
console.log('Favorites protected-backup recovery controls: ok')
