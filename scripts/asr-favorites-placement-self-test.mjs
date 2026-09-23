#!/usr/bin/env node

import { readFileSync } from 'node:fs'

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const css = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8')
const api = readFileSync(new URL('../asr-api.php', import.meta.url), 'utf8')
const adminFavorites = readFileSync(new URL('../compat/allscan-v1.01/js/asr-favorites-config.js', import.meta.url), 'utf8')
const common = readFileSync(new URL('../compat/allscan-v1.01/include/common.php', import.meta.url), 'utf8')

assert(
  app.includes("const DASHBOARD_MODULE_ORDER_KEY = 'asrDashboardModuleOrder.v1'")
    && app.includes("const DASHBOARD_MODULES: DashboardModuleKey[] = ['controls', 'favorites', 'connections', 'bridges']")
    && app.includes('useState<DashboardModuleKey[]>(readDashboardModuleOrder)'),
  'Dashboard module order is not initialized from a browser-persistent model',
)
assert(
  app.includes('writeDashboardModuleOrder(next)')
    && app.includes("data-dashboard-module=\"favorites\"")
    && app.includes("data-dashboard-module=\"controls\"")
    && app.includes("data-dashboard-module=\"connections\"")
    && app.includes("data-dashboard-module=\"bridges\""),
  'Whole dashboard modules are not represented in persistent order',
)
assert(
  app.includes('onPointerDown={(event) => beginDashboardModuleDrag(key, event)}')
    && app.includes('onPointerMove={updateDashboardModuleDrag}')
    && app.includes('dashboardDragPreviewRef.current.style.transform')
    && app.includes('scheduleDashboardDragAutoScroll()')
    && app.includes('window.scrollBy(0, delta)')
    && app.includes('if (source && target) moveDashboardModule(source, target)')
    && app.includes("event.key !== 'ArrowUp' && event.key !== 'ArrowDown'")
    && css.includes('touch-action: none;')
    && css.includes('.allscan-module-drag-preview')
    && css.includes('.allscan-section-title > .allscan-module-drag-handle'),
  'Stable compact pointer, touch, or keyboard module dragging is missing',
)
assert(
  app.includes('aria-controls="allscan-favorites-panel"')
    && app.includes('aria-expanded={favoritesOpen ? \'true\' : \'false\'}')
    && app.includes('setFavoritesOpen((open) => !open)')
    && app.includes('setFavoritesOpen(isAddDeleteFavoriteAction)'),
  'Favorites toggle or selection behavior changed',
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
    && css.includes('min-height: 44px;')
    && css.includes('width: min(900px, calc(100vw - 40px));'),
  'Favorites does not use compact, narrowed single-column rows',
)
assert(
  !app.includes('id="allscan-favorite-add"')
    && app.includes("runCommandForNode('delfav', favorite.node)"),
  'Favorites module still contains Add Favorite or lost confirmed row removal',
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
    && app.includes('<span className="allscan-favorite-node-number">{favorite.node}</span>')
    && css.includes('width: fit-content;'),
  'Favorite Rx indicator is not compact beneath the node number',
)
assert(
  app.includes("allscan-favorites-pin")
    && app.includes("favoritesPinned ? ' is-pinned' : ''")
    && app.includes('aria-pressed={favoritesPinned}')
    && css.includes('width: 24px;')
    && css.includes('height: 24px;'),
  'Favorites pin is not a compact icon-only stateful control',
)
assert(
  css.includes('.allscan-favorites-pin.is-pinned')
    && css.includes('border-color: #ff6464;')
    && app.includes('window.localStorage.setItem(FAVORITES_PINNED_KEY')
    && app.includes("const [favoritesOpen, setFavoritesOpen] = useState(() => window.localStorage.getItem(FAVORITES_PINNED_KEY) === '1')"),
  'Pinned Favorites is not red and browser-persistent',
)
assert(
  !app.includes('Import Supermon Favorites…')
    && adminFavorites.includes("heading.textContent = 'Import Supermon Favorites'")
    && adminFavorites.includes("section.querySelector(':scope > h1')?.textContent.trim() === 'Manage Favorites'")
    && adminFavorites.includes("operation: 'preview-import'")
    && adminFavorites.includes("operation: 'import'")
    && adminFavorites.includes('file.modifiable === true')
    && adminFavorites.includes('Existing order, descriptions, and colors will not be replaced')
    && api.includes("'modifiable' => is_string($real) && str_starts_with($real, '/etc/allscan/favorites')")
    && common.includes('/js/asr-favorites-config.js'),
  'Supermon import is not safely contained in Cfgs → Manage Favorites',
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
