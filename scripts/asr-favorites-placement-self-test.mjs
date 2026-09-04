#!/usr/bin/env node

import { readFileSync } from 'node:fs'

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const css = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8')

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
  !app.includes('draggable=') && !app.includes('onPointerMove=') && !css.includes('.allscan-favorites-drag'),
  'Favorites unexpectedly uses an unbounded draggable overlay',
)

console.log('favorites placement self-test: ok')
