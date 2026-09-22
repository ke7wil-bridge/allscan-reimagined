#!/usr/bin/env node

import { readFileSync } from 'node:fs'

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const css = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8')
const api = readFileSync(new URL('../asr-api.php', import.meta.url), 'utf8')

assert(
  app.includes("const FAVORITES_PLACEMENT_KEY = 'asrFavoritesPlacement.v1'"),
  'Favorites placement does not have its own browser preference key',
)
assert(
  app.includes("return value === 'below' ? 'below' : 'above'"),
  'Favorites placement does not fail closed to the existing above position',
)
assert(
  app.includes('useState<FavoritesPlacement>(readFavoritesPlacement)'),
  'Favorites placement is not initialized from the saved browser preference',
)
assert(
  app.includes('window.localStorage.setItem(FAVORITES_PLACEMENT_KEY, placement)'),
  'Favorites placement changes are not saved per browser',
)

const abovePosition = app.indexOf("{favoritesPlacement === 'above' ? favoritesPanel : null}")
const connectionPosition = app.indexOf('<section className="allscan-main-section allscan-connection-section">')
const belowPosition = app.indexOf("{favoritesPlacement === 'below' ? favoritesPanel : null}")
assert(
  abovePosition > 0 && abovePosition < connectionPosition && connectionPosition < belowPosition,
  'Favorites does not render in document order above or below Connection Status',
)

assert(
  app.includes('aria-controls="allscan-favorites-panel"')
    && app.includes('aria-expanded={favoritesOpen ? \'true\' : \'false\'}'),
  'Favorites toggle lost its accessible expanded relationship',
)
assert(
  app.includes('Keep below Connection Status on this browser')
    && app.includes("checked={favoritesPlacement === 'below'}"),
  'Favorites placement checkbox is missing or not controlled',
)
assert(
  app.includes('restoreFavoritesPlacementFocus.current = true')
    && app.includes('favoritesPlacementRef.current?.focus()'),
  'Keyboard focus is not restored after moving the Favorites panel',
)
assert(
  app.includes('setFavoritesOpen((open) => !open)')
    && app.includes('setFavoritesOpen(isAddDeleteFavoriteAction)'),
  'Favorites open/close or selection behavior changed',
)
assert(
  css.includes('html[data-asr-theme] .allscan-favorites-placement')
    && css.includes('.allscan-favorites-placement input:focus-visible')
    && css.includes('@media (max-width: 900px)'),
  'Favorites placement control is missing theme, focus, or responsive styling',
)
assert(
  app.includes('draggable={authStatus.canModify}')
    && app.includes('onDrop={() =>')
    && app.includes("favoriteOperation('reorder'"),
  'Favorites does not provide bounded, persistent row reordering',
)
assert(
  app.includes("const FAVORITES_PINNED_KEY = 'asrFavoritesPinned.v1'")
    && app.includes('onDoubleClick={() =>')
    && app.includes("runCommandForNode('connect', favorite.node)"),
  'Pinned Favorites or its double-click connection behavior is missing',
)
assert(
  css.includes('.allscan-favorites-table tbody {')
    && css.includes('grid-template-columns: minmax(0, 1fr);')
    && css.includes('min-height: 54px;'),
  'Favorites does not use compact, single-column rows',
)
assert(
  !app.includes('Number(favorite.index)')
    && !app.includes('moveFavorite(')
    && app.includes('Drag to reorder'),
  'Favorites retains redundant order numbers or one-step movement controls',
)
assert(
  app.includes("'Linked: —'")
    && app.includes('Linked: '),
  'Favorite linked counts are not clearly labeled',
)
assert(
  app.includes('favorite.description || favorite.name')
    && app.includes('favorite.frequency || favorite.referenceDesc')
    && css.includes('.allscan-favorite-frequency'),
  'Favorite description and frequency are not presented as separate stacked fields',
)
assert(
  api.includes("'description' => $customDescription !== '' ? $customDescription : (string) $display['name']")
    && api.includes("'frequency' => (string) $display['desc']"),
  'Favorites API does not preserve separate description and frequency fields',
)
assert(
  app.includes("'allscan-favorite-rx allscan-fav-cell-rx'")
    && css.includes('width: fit-content;'),
  'Favorite Rx indicator is not compact',
)
assert(
  app.includes("allscan-favorites-pin")
    && app.includes("favoritesPinned ? ' is-pinned' : ''")
    && app.includes('aria-pressed={favoritesPinned}'),
  'Favorites pin is not an icon-only stateful control',
)
assert(
  css.includes('.allscan-favorites-pin.is-pinned')
    && css.includes('border-color: #ff6464;')
    && app.includes('window.localStorage.setItem(FAVORITES_PINNED_KEY'),
  'Pinned Favorites is not red and browser-persistent',
)
assert(
  app.includes('Import Supermon Favorites…')
    && app.indexOf('Import Supermon Favorites…') > app.indexOf('allscan-submenu-admin')
    && !app.includes('>Import Supermon…</button>'),
  'Supermon import was not moved to the Admin menu',
)
assert(
  app.includes('Reset the custom description for node')
    && app.includes('Reset the custom description and color for node')
    && app.includes('Reset the saved custom order')
    && app.includes('Reset all Favorite colors')
    && app.includes('Remove node')
    && app.includes('from Favorites?'),
  'A Favorites reset or delete action is missing confirmation',
)

console.log('favorites placement self-test: ok')
