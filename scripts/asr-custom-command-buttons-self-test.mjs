#!/usr/bin/env node

import { readFileSync } from 'node:fs'

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const live = readFileSync(new URL('../src/lib/allscanLive.ts', import.meta.url), 'utf8')
const api = readFileSync(new URL('../asr-api.php', import.meta.url), 'utf8')
const serverApi = readFileSync(new URL('../server/asr-api.php', import.meta.url), 'utf8')
const cfg = readFileSync(new URL('../compat/allscan-v1.01/include/CfgModel.php', import.meta.url), 'utf8')
const adminJs = readFileSync(new URL('../compat/allscan-v1.01/js/asr-cmd-buttons-config.js', import.meta.url), 'utf8')
const common = readFileSync(new URL('../compat/allscan-v1.01/include/common.php', import.meta.url), 'utf8')

assert(api === serverApi, 'Duplicated ASR API files differ')
assert(
  cfg.includes("define('showcmdbuttons', 14)")
    && cfg.includes("showcmdbuttons => 'Show Custom Cmd Buttons in Node Controls'")
    && cfg.includes('showcmdbuttons => $checkboxVals'),
  'Cfg model does not provide the Node Controls visibility setting',
)
assert(
  adminJs.includes('enhanceCommandEditor')
    && adminJs.includes('enhanceVisibilityToggle')
    && adminJs.includes('window.confirm')
    && common.includes('/js/asr-cmd-buttons-config.js'),
  'Admin Cfgs does not provide structured Custom Cmd management',
)
assert(
  api.includes("if ($action === 'custom-commands')")
    && api.includes("if ($action === 'custom-commands-save')")
    && api.includes('asr_require_admin();')
    && api.includes('A maximum of 24 custom commands is supported.')
    && api.includes('$cfgModel->saveCfgs();'),
  'Custom command API is missing protected persistence or validation',
)
assert(
  live.includes('fetchCustomCommands')
    && live.includes('saveCustomCommands')
    && live.includes("'X-ASR-Requested-With': 'custom-command-control'"),
  'Frontend Custom Cmd transport is missing',
)
assert(
  app.includes('Cmds <ChevronDown')
    && app.includes('Manage Custom Cmd Buttons')
    && app.includes('Manage Commands…')
    && app.includes("runCommandForNode('dtmf', item.command")
    && app.includes('disabled={busy || !authStatus.canWrite}'),
  'Node Controls Custom Cmd dropdown or permission guard is missing',
)
assert(
  app.includes('Delete ${name}? The change takes effect when you save.')
    && app.includes('moveCustomCommand(index, -1)')
    && app.includes('moveCustomCommand(index, 1)'),
  'Custom command delete confirmation or ordering controls are missing',
)
assert(
  !app.includes('id="allscan-favorite-add"')
    && live.includes("{ value: 'addfav', label: 'Add Favorite' }")
    && live.includes("{ value: 'delfav', label: 'Delete Favorite' }"),
  'Add/Remove Favorite was not correctly retained in Node Controls only',
)

console.log('custom command buttons self-test: ok')
