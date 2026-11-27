import type { FavoriteNode, LiveConnectionRow } from './allscanLive'

// Only explicit operator customizations, never directory-derived names. If
// duplicate Favorites disagree, fail closed rather than choose by sort order.
export function favoriteConnectionNames(favorites: readonly FavoriteNode[]) {
  const names = new Map<string, string>()
  const conflicts = new Set<string>()
  for (const favorite of favorites) {
    const name = favorite.customDescription?.trim()
    if (!name || conflicts.has(favorite.node)) continue
    const previous = names.get(favorite.node)
    if (previous && previous !== name) {
      names.delete(favorite.node)
      conflicts.add(favorite.node)
    } else names.set(favorite.node, name)
  }
  return names
}

// Presentation only. Never overwrite row.info: moderation, callsign identity,
// routing and talker attribution must continue using authoritative data.
export function connectionDisplayName(row: LiveConnectionRow, localNode: string, bridgeNodes: ReadonlySet<string>, favoriteNames: ReadonlyMap<string, string>) {
  if (row.node === localNode || row.state === 'message' || row.configuredTransport || row.bridgeId || bridgeNodes.has(row.node)) return row.info
  if (!/^\d{4,7}$/.test(row.node) || Number(row.node) < 2000 || /^3\d{6}$/.test(row.node)) return row.info
  if (/echolink|bridge|broadcastify|digital|transport/i.test(`${row.mode} ${row.info}`)) return row.info
  return favoriteNames.get(row.node) || row.info
}
