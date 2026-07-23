import fs from 'node:fs'
import path from 'node:path'
import type { Plugin } from 'vite'

const ASR_BASE = '/asr'

type MockRoute = 'login' | 'users' | 'settings' | 'cfg' | 'reimagined' | 'instructions'

const runtimeConfig = {
  ok: true,
  node: '641890',
  callsign: 'KE7WIL',
  headerTitle: 'KE7WIL - 641890',
  browserTitle: 'KE7WIL - 641890',
  brandByline: 'by KE7WIL',
  footerByline: 'customized by KE7WIL',
  headerLogo: `${ASR_BASE}/asr-logo-bright-r-tight.png`,
  footerLogo: `${ASR_BASE}/asr-logo-bright-r-tight.png`,
  versionLabel: 'v1.0.0 Beta 6',
  bridges: [
    { id: 'dmr', node: '1883', title: 'DMR Bridge', detailTitle: 'Connected Clients', friendlyName: 'DMR Home Bridge', cardType: 'standard' },
    { id: 'dmr_net', node: '1884', title: 'DMR Net Bridge', detailTitle: 'Connected Clients', friendlyName: 'DMR Net Bridge', cardType: 'dmr_net' },
    { id: 'ysf', node: '641892', title: 'YSF Bridge', detailTitle: 'YSF Rooms' },
    { id: 'zello', node: '641893', title: 'Zello Bridge', detailTitle: 'Zello Clients' },
  ],
}

const authStatus = {
  ok: true,
  loggedIn: true,
  username: 'mock-admin',
  permission: 4,
  publicPermission: 1,
  canRead: true,
  canModify: true,
  canWrite: true,
  isAdmin: true,
}

const releaseStatus = {
  ok: true,
  status: 'update_available',
  updateAvailable: true,
  checkedAt: '2026-07-23T12:00:00Z',
  installedVersion: '1.0.0-beta.6',
  installedLabel: 'v1.0.0 Beta 6',
  availableVersion: '1.0.0-beta.6.1',
  availableLabel: 'v1.0.0 Beta 6.1',
  releaseUrl: 'https://github.com/ke7wil-bridge/allscan-reimagined/releases/tag/v1.0.0-beta.6.1',
  publishedAt: '2026-07-23T12:00:00Z',
  package: {
    name: 'allscan-reimagined-1.0.0-beta.6.1.tar.gz',
    url: 'https://github.com/ke7wil-bridge/allscan-reimagined/releases/download/v1.0.0-beta.6.1/allscan-reimagined-1.0.0-beta.6.1.tar.gz',
    size: 6200000,
    sha256: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
  },
}

type MockUser = {
  id: number
  name: string
  email: string
  nodes: string
  permission: 'Read Only' | 'Write' | 'Admin'
  timezone: 'America/Phoenix' | 'America/Denver' | 'UTC'
  location: string
}

const mockUsers: MockUser[] = [
  {
    id: 1,
    name: 'mock-admin-ke7wil',
    email: 'admin.operator@example.test',
    nodes: '641890',
    permission: 'Admin',
    timezone: 'America/Phoenix',
    location: 'Phoenix, Arizona',
  },
  {
    id: 2,
    name: 'control-operator-west-valley',
    email: 'control.operator.west.valley@example.test',
    nodes: '641890, 641891',
    permission: 'Write',
    timezone: 'America/Denver',
    location: 'West Valley Bridge Desk, Arizona',
  },
  {
    id: 3,
    name: 'readonly-monitoring-station',
    email: 'readonly.monitoring.station@example.test',
    nodes: '641890',
    permission: 'Read Only',
    timezone: 'UTC',
    location: 'Remote monitoring location',
  },
]

function jsonResponse(payload: unknown) {
  return JSON.stringify(payload, null, 2)
}

function escapeHtml(value: string) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
}

function adminMenu(current: MockRoute) {
  const item = (href: string, label: string, route?: MockRoute) => {
    if (route && route === current) return `<span class="asr-admin-menu-current">${escapeHtml(label)}</span>`
    return `<a role="menuitem" href="${href}">${escapeHtml(label)}</a>`
  }

  return `
    <details class="allscan-menu-slot asr-admin-menu">
      <summary class="allscan-menu-button">
        <span class="allscan-menu-desktop">Menu</span>
        <span class="allscan-menu-mobile">☰</span>
      </summary>
      <div class="allscan-menu-panel asr-admin-menu-panel" role="menu">
        <div class="allscan-submenu is-open asr-admin-menu-list">
          ${item(`${ASR_BASE}/`, 'Return to Main Page')}
          ${item(`${ASR_BASE}/user/settings/`, 'Settings', 'settings')}
          ${item(`${ASR_BASE}/asr-settings/`, 'Reimagined Settings', 'reimagined')}
          ${item(`${ASR_BASE}/asr-instructions/`, 'Help & Instructions', 'instructions')}
          ${item(`${ASR_BASE}/performance/`, 'Performance Stats')}
          ${item(`${ASR_BASE}/user/`, 'Users', 'users')}
          ${item(`${ASR_BASE}/cfg/`, 'Configs', 'cfg')}
          <a role="menuitem" href="http://stats.allstarlink.org/stats/641890">Node Status</a>
          ${item(`${ASR_BASE}/lookup/`, 'Lookup')}
          ${item(`${ASR_BASE}/?reportBug=1`, 'Report a Bug')}
          <a role="menuitem" href="${ASR_BASE}/user/?logout=1">Logout</a>
        </div>
      </div>
    </details>
  `
}

function adminShell(route: MockRoute, title: string, body: string) {
  const bodyClass =
    route === 'cfg' ? 'asr-admin-page asr-admin-page-cfg'
      : route === 'users' || route === 'login' ? 'asr-admin-page asr-admin-page-users'
        : route === 'instructions' ? 'asr-admin-page asr-admin-page-instructions'
          : 'asr-admin-page asr-admin-page-settings'

  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${escapeHtml(title)} | ASR Mock</title>
  <link rel="icon" type="image/png" href="${ASR_BASE}/favicon-bolt-r-c.png">
  <link rel="stylesheet" href="${ASR_BASE}/src/index.css">
  <link rel="stylesheet" href="${ASR_BASE}/css/asr-admin.css">
  <script src="${ASR_BASE}/js/main.js" defer></script>
</head>
<body class="${bodyClass}">
  <header class="allscan-header asr-admin-header">
    <div class="allscan-brand">
      <a class="allscan-brand-main" href="${ASR_BASE}/" aria-label="Return to main AllScan page">
        <div class="allscan-wordmark">
          <strong class="allscan-wordmark-mark">
            <span class="allscan-wordmark-silver allscan-wordmark-all">All</span>
            <span class="allscan-wordmark-bolt-wrap" aria-hidden="true"><img class="allscan-wordmark-bolt" src="${ASR_BASE}/bolt-test-tight.png" alt=""></span>
            <span class="allscan-wordmark-silver allscan-wordmark-can">can</span>
          </strong>
          <span class="allscan-tagline font-georgia">Reimagined</span>
          <small class="allscan-brand-version">${runtimeConfig.versionLabel}</small>
          <span class="allscan-byline">${runtimeConfig.brandByline}</span>
        </div>
      </a>
    </div>
    <div class="allscan-header-center">
      <img class="allscan-header-ke7wil-logo" src="${runtimeConfig.headerLogo}" alt="Header logo">
      <h1 class="allscan-title">${runtimeConfig.headerTitle}</h1>
      <div class="allscan-cpu"><span class="allscan-meta-label">CPU Temp:</span><b class="allscan-cpu-pill" style="background-color:#4caf50;">105.2°F</b></div>
      <div class="allscan-clockline"><span><span class="allscan-meta-label">Local</span> 10:15:30 AM</span><span><span class="allscan-meta-label">UTC</span> 17:15:30</span></div>
    </div>
    ${adminMenu(route)}
  </header>

  ${body}
</body>
</html>`
}

function loginPage() {
  return adminShell('login', 'AllScan Users', `
    <h1>User Administration</h1>
    <h2>User Accounts</h2>
    ${usersTable()}
  `)
}

function usersTable() {
  const userRows = mockUsers.map((user) => {
    return `<tr><td>${escapeHtml(user.name)}</td><td>${escapeHtml(user.email)}</td><td>${escapeHtml(user.location)}</td><td>${escapeHtml(user.nodes)}</td><td>2026-07-${String(23 - user.id).padStart(2, '0')}</td><td>${escapeHtml(user.permission)}</td><td>${escapeHtml(user.timezone)}</td><td>${user.id}</td></tr>`
  }).join('\n')

  return `
    <table class="favs">
      <thead>
        <tr>
          <th>Name</th>
          <th>Email</th>
          <th>Location</th>
          <th>Nodes</th>
          <th>Last Login</th>
          <th>Permission</th>
          <th>Time Zone</th>
          <th>ID</th>
        </tr>
      </thead>
      <tbody>
        ${userRows}
      </tbody>
    </table>
    <br>
    <form id="addUserForm" class="left" method="post" action="${ASR_BASE}/user/">
      <fieldset>
        <legend>Add User</legend>
        <table class="noborder">
          <tbody>
            <tr><td>Name</td><td><input name="name" type="text"></td><td>&nbsp;</td></tr>
            <tr><td>Email</td><td><input name="email" type="text"></td><td>&nbsp;</td></tr>
            <tr><td>Password</td><td><input name="pass" type="password"></td><td>&nbsp;</td></tr>
            <tr><td>Location</td><td><input name="location" type="text"></td><td>&nbsp;</td></tr>
            <tr><td>Node #s</td><td><input name="nodenums" type="text"></td><td>&nbsp;</td></tr>
            <tr><td>Permission</td><td><select name="permission"><option>Read Only</option><option>Read/Modify</option><option>Full</option><option>Admin</option><option>Superuser</option></select></td><td>&nbsp;</td></tr>
            <tr><td>Time Zone</td><td><select name="timezone_id"><option>[Default]</option><option>America/Phoenix</option><option>America/Denver</option><option>UTC</option></select></td><td>&nbsp;</td></tr>
            <tr><td></td><td><span class="floatright"><input type="submit" value="Add User"></span></td><td></td></tr>
          </tbody>
        </table>
      </fieldset>
    </form>
    <br>
    <form id="editUserForm" class="left" method="post" action="${ASR_BASE}/user/">
      <fieldset>
        <legend>Edit User</legend>
        <table class="noborder">
          <tbody>
            <tr><td>Name [ID]</td><td><select name="user_id">${mockUsers.map((user) => `<option>${escapeHtml(user.name)} [${user.id}]</option>`).join('')}</select></td><td>&nbsp;</td></tr>
            <tr><td></td><td><span class="floatright"><input type="submit" value="Edit User"> <input type="submit" value="Delete User"></span></td><td></td></tr>
          </tbody>
        </table>
      </fieldset>
    </form>
    <h3>Form Notes</h3>
    <ul class="left w600">
      <li><b>Name</b>: Login username. Must be 2-24 alphanumeric characters and may contain spaces.</li>
      <li><b>Email</b>: Optional.</li>
      <li><b>Password</b>: Must be 6-16 printable ASCII characters.</li>
      <li><b>Location</b>: Optional user location.</li>
      <li><b>Node #s</b>: Optional space-separated list of managed AllStar nodes.</li>
      <li><b>Permission</b>: Controls which AllScan actions the user may perform.</li>
      <li><b>Time Zone</b>: User's time zone.</li>
    </ul>
  `
}

function settingsPage() {
  return adminShell('settings', 'AllScan Settings', `
    <h2>User Account Settings</h2>
    <form class="asr-user-settings-form" id="editUserForm" method="post" action="${ASR_BASE}/user/settings/">
      <fieldset>
        <legend>Update Settings</legend>
        <table class="grid">
          <tbody>
            <tr><th>Name</th><td><input name="name" type="text" value="Lucas KM7ETV"></td></tr>
            <tr><th>Email</th><td><input name="email" type="text" value="lucasoptura@gmail.com"></td></tr>
            <tr><th>Location</th><td><input name="location" type="text" value="Phoenix, Arizona"></td></tr>
            <tr><th>Node #s</th><td><input name="nodenums" type="text" value=""></td></tr>
            <tr><th>Permission</th><td><select name="permission"><option>Read Only</option><option>Write</option><option>Admin</option><option selected>Superuser</option></select></td></tr>
            <tr><th>Time Zone</th><td><select name="timezone_id"><option selected>America/Phoenix</option><option>America/Denver</option><option>UTC</option></select></td></tr>
          </tbody>
        </table>
        <p><input name="Submit" type="submit" value="Update Settings"></p>
      </fieldset>
    </form>
    <form class="asr-user-settings-form" id="changePassForm" method="post" action="${ASR_BASE}/user/settings/">
      <fieldset>
        <legend>Change Password</legend>
        <table class="grid">
          <tbody>
            <tr><th>New Password</th><td><input name="pass" type="password" value=""></td></tr>
            <tr><th>Confirm New Password</th><td><input name="confirm" type="password" value=""></td></tr>
          </tbody>
        </table>
        <p><input name="Submit" type="submit" value="Change Password"></p>
      </fieldset>
    </form>
    <h3>Form Notes:</h3>
    <ul class="left asr-settings-notes">
      <li><b>Name</b>: Login username. Can be your first name, first &amp; last name/initials, your callsign, or other name. Must be 2-24 alphanumeric characters. May contain spaces</li>
      <li><b>Email</b>: Optional. Not currently used but email features may be supported in the future</li>
      <li><b>Location</b>: Optional. Your location eg. "Chicago, IL" or "Nottingham, UK"</li>
      <li><b>Node #s</b>: Optional. Space separated list of AllStar node numbers you own or manage</li>
      <li><b>Time Zone</b>: Default is UTC</li>
      <li><b>Password</b>: Must be 6-16 printable ASCII characters</li>
    </ul>
  `)
}

function cfgPage() {
  return adminShell('cfg', 'AllScan Cfgs', `
    <div class="greenborder">
      <h1>Manage Configs</h1>
      <h2>Configuration Parameters</h2>
      <table class="favs">
        <thead>
          <tr><th>ID</th><th>Name</th><th>Value</th><th>Default Value</th><th>Last Updated</th></tr>
        </thead>
        <tbody>
          <tr><td>1</td><td>Public Permission</td><td>[Default]</td><td>Read Only</td><td>-</td></tr>
          <tr><td>2</td><td>Favorites.ini Locations</td><td>[Default]</td><td>favorites.ini, ../supermon/favorites.ini, /etc/allscan/favorites.ini</td><td>-</td></tr>
          <tr><td>3</td><td>Call Sign</td><td>KE7WIL</td><td>-</td><td>2025-03-07 12:54</td></tr>
          <tr><td>4</td><td>Location</td><td>Phoenix, Arizona</td><td>-</td><td>2025-03-07 12:54</td></tr>
          <tr><td>5</td><td>Node Title</td><td>KE7WIL</td><td>-</td><td>2025-03-07 12:54</td></tr>
          <tr><td>6</td><td>DiscBeforeConn Default</td><td>Off</td><td>On</td><td>2025-05-16 04:45</td></tr>
          <tr><td>7</td><td>Node Number</td><td>[Default]</td><td>-</td><td>-</td></tr>
          <tr><td>8</td><td>AMI Host</td><td>[Default]</td><td>-</td><td>-</td></tr>
          <tr><td>9</td><td>AMI Port</td><td>[Default]</td><td>-</td><td>-</td></tr>
          <tr><td>10</td><td>AMI User</td><td>[Default]</td><td>-</td><td>-</td></tr>
          <tr><td>11</td><td>AMI Pass</td><td>[Default]</td><td>-</td><td>-</td></tr>
          <tr><td>12</td><td>Custom Cmd Buttons</td><td>[Default]</td><td>-</td><td>-</td></tr>
          <tr><td>13</td><td>Check For Updates</td><td>[Default]</td><td>On</td><td>-</td></tr>
        </tbody>
      </table>
      <p class="w800">Node Number and AMI Cfgs default to values in /etc/asterisk/rpt.conf and manager.conf if not set here.</p>
      <p class="w800">Call Sign, Location, and Node Title default to fields in astdb.txt if not set here.</p>
      <form id="editCfgForm" class="left asr-cfg-edit-form" method="post" action="${ASR_BASE}/cfg/">
        <fieldset>
          <legend>Edit Cfg</legend>
          <table class="noborder">
            <tbody>
              <tr><td>Cfg Name</td><td><select class="asr-cfg-name-select" name="cfg_id">
                <option selected>Public Permission</option>
                <option>Favorites.ini Locations</option>
                <option>Call Sign</option>
                <option>Location</option>
                <option>Node Title</option>
                <option>DiscBeforeConn Default</option>
                <option>Node Number</option>
                <option>AMI Host</option>
                <option>AMI Port</option>
                <option>AMI User</option>
                <option>AMI Pass</option>
                <option>Custom Cmd Buttons</option>
                <option>Check For Updates</option>
              </select></td><td>&nbsp;</td></tr>
              <tr><td></td><td><span class="floatright"><input type="submit" value="Edit Cfg"> <input type="button" value="Set to Default Value"></span></td><td></td></tr>
            </tbody>
          </table>
        </fieldset>
      </form>
    </div>
    <div class="greenborder">
      <h1>Manage Favorites</h1>
      <h2>View/Download/Delete Favorites Files</h2>
      <table class="favs">
        <thead><tr><th>File</th><th>Size (Bytes)</th><th>Last Modified</th><th>Options</th></tr></thead>
        <tbody>
          <tr><td>/var/www/html/allscan/favorites-Sample.ini</td><td>3804</td><td>2025-09-09 21:47</td><td>Delete</td></tr>
          <tr><td>/var/www/html/allscan/favorites-allscan-format.ini</td><td>2196</td><td>2026-05-26 20:44</td><td>Delete</td></tr>
          <tr><td>/var/www/html/allscan/favorites-local-clubs.ini</td><td>609</td><td>2026-01-06 14:18</td><td>Delete</td></tr>
          <tr><td>/var/www/html/allscan/favorites.ini [Default]</td><td>2197</td><td>2026-05-26 21:01</td><td>Delete</td></tr>
        </tbody>
      </table>
      <h2>Copy/Backup Favorites Files</h2>
      <form id="copyFileForm" class="left" method="post" action="${ASR_BASE}/cfg/">
        <fieldset>
          <legend>Copy File</legend>
          <table class="noborder">
            <tbody>
              <tr><td>File to Copy</td><td><select><option selected>/var/www/html/allscan/favorites-Sample.ini</option><option>/var/www/html/allscan/favorites.ini</option></select></td><td>&nbsp;</td></tr>
              <tr><td>Destination Dir</td><td><select><option selected>/var/www/html/allscan/</option><option>/etc/allscan/</option></select></td><td>&nbsp;</td></tr>
              <tr><td>Name Suffix (optional)</td><td><input type="text" value=""></td><td>&nbsp;</td></tr>
              <tr><td></td><td><span class="floatright"><input type="submit" value="Copy File"></span></td><td></td></tr>
            </tbody>
          </table>
        </fieldset>
      </form>
      <p class="w800">The Favorites file select control on the main page enables easy switching between files, supporting grouping of favorites by location, type, interests, etc. A new favorites file can be created by copying and then editing an existing file, or uploading a new file.</p>
      <h2>Upload Favorites File</h2>
      <form class="left" method="post" action="${ASR_BASE}/cfg/">
        <fieldset>
          <legend>Upload File</legend>
          <table class="noborder">
            <tbody>
              <tr><td>Favorites File</td><td><input type="file"></td><td>&nbsp;</td></tr>
              <tr><td>Destination Dir</td><td><select><option selected>/var/www/html/allscan/</option><option>/etc/allscan/</option></select></td><td>&nbsp;</td></tr>
              <tr><td></td><td><span class="floatright"><input type="submit" value="Upload"></span></td><td></td></tr>
            </tbody>
          </table>
        </fieldset>
      </form>
      <p class="w800">Favorites file names must be in the format favorites[*].ini, ie. with an optional suffix before the .ini extension. Example valid filenames: favorites.ini, favorites-WestCoast.ini, favorites-UK.ini, favorites-nets.ini, etc.</p>
    </div>
  `)
}

function reimaginedSettingsPage() {
  return adminShell('reimagined', 'Reimagined Settings', `
    <h2>Reimagined Settings</h2>
    <p class="asr-rollback-warning"><strong>Local preview:</strong> This page uses simulated KE7WIL data. Nothing here can change the node.</p>
    <form class="asr-reimagined-settings-form" action="#" onsubmit="return false">
      <fieldset class="asr-settings-section is-collapsed">
        <legend><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Header &amp; Branding <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></legend>
        <p class="asr-settings-help">Preview placeholder for the existing header and branding controls.</p>
      </fieldset>
      <fieldset class="asr-settings-section is-collapsed" data-settings-section="bridges">
        <legend><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Bridge Cards <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></legend>
        <p class="asr-settings-help">Only active bridge cards are listed here. Add another card only after its bridge is installed and working.</p>
        <label class="asr-settings-check">
          <input name="maintainFriendlyNames" type="checkbox" value="1" checked>
          <span>Maintain bridge friendly names across updates, restarts, and reboots</span>
        </label>
        <p class="asr-settings-inline-note">When enabled, ASR keeps configured bridge node labels matching the Connection Status Name.</p>
        <div class="asr-bridge-settings-row">
          <div class="asr-bridge-panel-body">
            <div class="asr-bridge-panel-section">
              <div class="asr-bridge-section-copy"><strong>Bridge Card</strong><span>Controls the bridge card shown on the ASR home page.</span></div>
              <div class="asr-bridge-fields-grid asr-bridge-card-grid">
                <label><span>Card Type</span><select><option>Standard Bridge</option><option selected>DMR Net Bridge</option></select></label>
                <label><span>ID</span><input value="dmr_net"></label>
                <label><span>Node</span><input value="1884"></label>
                <label><span>Card Title</span><input value="DMR Net Bridge"></label>
                <label><span>Detail Title</span><input value="Connected Clients"></label>
              </div>
            </div>
            <div class="asr-bridge-panel-section">
              <div class="asr-bridge-section-copy"><strong>DMR Net Controls</strong><span>Paths used by the Connect and Disconnect controls.</span></div>
              <div class="asr-bridge-fields-grid">
                <label><span>ABInfo Path</span><input value="/tmp/ABInfo_34004.json"></label>
                <label><span>DVSwitch Script</span><input value="/opt/MMDVM_Bridge_TGIFNet/dvswitch.sh"></label>
                <label><span>Analog Bridge Config</span><input value="/opt/Analog_Bridge_TGIFNet/Analog_Bridge.ini"></label>
              </div>
            </div>
          </div>
        </div>
        <p class="asr-settings-inline-note">After saving bridge changes, refresh the main ASR page. If an old name remains, perform a hard refresh: Ctrl+Shift+R on Windows/Linux or Command+Shift+R on Mac. On a phone, close the ASR tab and reopen it.</p>
        <p class="asr-settings-help-action"><a class="asr-settings-help-button" href="${ASR_BASE}/asr-instructions/#bridge-cards">Open Full Reimagined Help</a></p>
      </fieldset>
      <fieldset class="asr-settings-section is-collapsed" data-settings-section="bridge-help">
        <legend><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Bridge Setup Help <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></legend>
        <div class="asr-setup-help-grid">
          <section><h2>Before Adding a Card</h2><p>The bridge and its private AllStar node must already be installed and working.</p></section>
          <section><h2>Card Basics</h2><p>Choose the card type, bridge ID, node, and display names. Leave the client source disabled unless a real source exists.</p></section>
          <section><h2>DMR Net Bridge</h2><p>This card requires a separately installed, tunable DMR bridge.</p></section>
        </div>
        <p class="asr-settings-help-action"><a class="asr-settings-help-button" href="${ASR_BASE}/asr-instructions/#bridge-setup">Read Detailed Bridge Setup Help</a></p>
      </fieldset>
      <fieldset class="asr-settings-section is-collapsed">
        <legend><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Lookup / Map <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></legend>
        <p class="asr-settings-help">Preview placeholder for the existing lookup and map controls.</p>
      </fieldset>
      <fieldset class="asr-settings-section asr-rollback-section is-collapsed" data-settings-section="rollback">
        <legend><button class="asr-settings-section-toggle" type="button" aria-expanded="false">Roll Back ASR Version <span class="asr-settings-toggle-icon" aria-hidden="true">+</span></button></legend>
        <p class="asr-settings-help">Restore one of the five newest valid previous ASR versions. Users, Favorites, the database, Reimagined settings, bridge settings, map cache, and protected secrets are preserved.</p>
        <div class="asr-rollback-current">
          <span>Currently installed</span>
          <strong>v1.0.0 Beta 6 Preview</strong>
        </div>
        <div class="asr-rollback-controls">
          <label for="asrRollbackSelect">
            <span>Previous Version</span>
            <select id="asrRollbackSelect">
              <option value="">Select a previous version</option>
              <option value="20260723-040034" data-version="v1.0.0 Beta 5.11">v1.0.0 Beta 5.11 — simulated backup</option>
              <option value="20260721-120349" data-version="v1.0.0 Beta 5.10">v1.0.0 Beta 5.10 — simulated backup</option>
            </select>
          </label>
          <button id="asrRollbackReview" class="asr-rollback-button" type="button" disabled>Roll Back to Selected Version</button>
        </div>
        <p id="asrRollbackPreviewStatus" class="asr-rollback-status" hidden></p>
        <p class="asr-rollback-warning"><strong>Important:</strong> Rollback has its own button. Save Reimagined Settings does not perform a rollback, and unsaved settings edits are not saved during rollback.</p>
      </fieldset>
      <p class="asr-reimagined-submit">
        <input type="submit" value="Save Reimagined Settings">
        <span>Preview only—nothing is saved.</span>
      </p>
    </form>
    <div id="asrRollbackDialog" class="asr-rollback-dialog" role="dialog" aria-modal="true" aria-labelledby="asrRollbackDialogTitle" hidden>
      <div class="asr-rollback-dialog-card">
        <h2 id="asrRollbackDialogTitle">Confirm ASR Rollback</h2>
        <div class="asr-rollback-version-change">
          <div><span>Current version</span><strong>v1.0.0 Beta 6 Preview</strong></div>
          <span class="asr-rollback-arrow" aria-hidden="true">→</span>
          <div><span>Restore version</span><strong id="asrRollbackTargetVersion"></strong></div>
        </div>
        <p>ASR will create a fresh safety backup and restore the selected version without restarting Asterisk or bridge services.</p>
        <p class="asr-rollback-dialog-warning"><strong>Unsaved settings edits will not be saved.</strong></p>
        <div class="asr-rollback-dialog-actions">
          <button id="asrRollbackCancel" type="button">Cancel</button>
          <button id="asrRollbackConfirm" class="asr-rollback-button" type="button">Confirm Rollback</button>
        </div>
      </div>
    </div>
    <script>
      document.querySelectorAll('.asr-settings-section-toggle').forEach(function (button) {
        button.addEventListener('click', function () {
          var section = button.closest('.asr-settings-section');
          var expanded = section.classList.contains('is-collapsed');
          section.classList.toggle('is-collapsed', !expanded);
          button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
          button.querySelector('.asr-settings-toggle-icon').textContent = expanded ? '−' : '+';
        });
      });
      var select = document.getElementById('asrRollbackSelect');
      var review = document.getElementById('asrRollbackReview');
      var dialog = document.getElementById('asrRollbackDialog');
      var target = document.getElementById('asrRollbackTargetVersion');
      var status = document.getElementById('asrRollbackPreviewStatus');
      select.addEventListener('change', function () { review.disabled = !select.value; });
      review.addEventListener('click', function () {
        target.textContent = select.options[select.selectedIndex].dataset.version || '';
        dialog.hidden = false;
        document.body.classList.add('asr-rollback-dialog-open');
      });
      document.getElementById('asrRollbackCancel').addEventListener('click', function () {
        dialog.hidden = true;
        document.body.classList.remove('asr-rollback-dialog-open');
      });
      document.getElementById('asrRollbackConfirm').addEventListener('click', function () {
        dialog.hidden = true;
        document.body.classList.remove('asr-rollback-dialog-open');
        status.hidden = false;
        status.textContent = 'Preview confirmed. No rollback was started and no node was changed.';
      });
    </script>
  `)
}

function instructionsPage(projectRoot: string) {
  const source = fs.readFileSync(
    path.join(projectRoot, 'compat/allscan-v1.01/asr-instructions/index.php'),
    'utf8',
  )
  const main = source.match(/<main\b[\s\S]*?<\/main>/i)?.[0]
  return adminShell(
    'instructions',
    'Help & Instructions',
    main || '<main class="asr-instructions-page"><p>Help preview unavailable.</p></main>',
  )
}

function bridgeLive() {
  return jsonResponse({
    updated: '2026-06-25T17:15:30Z',
    updated_epoch: 1782417330,
    dmr: { active: true, role: 'source', state: 'Source/TX', caller: 'N7MOCK', recent_users: [{ name: 'N7MOCK' }, { name: 'W7TEST' }] },
    dmr_net: { active: false, role: 'idle', state: 'Idle', caller: '', recent_users: [] },
    ysf: { active: true, role: 'relay', state: 'Relay', caller: 'W7TEST', recent_users: [{ name: 'W7TEST' }] },
    zello: { active: false, role: 'idle', state: 'Idle', caller: '', recent_users: [] },
  })
}

function connectedClients() {
  return jsonResponse({
    dmr: [{ callsign: 'N7MOCK', room: 'TG 3100', connected: '00:12:04' }],
    dmr_net: [{ callsign: 'N7NET', room: 'TG 86753', connected: '00:03:16' }],
    ysf: [{ callsign: 'W7TEST', room: 'America-Link', connected: '00:03:41' }],
    zello: [],
  })
}

function favoritesPayload() {
  return jsonResponse({
    ok: true,
    selectedFile: '/etc/allscan/favorites.ini',
    files: [
      { value: '/etc/allscan/favorites.ini', label: 'favorites.ini', selected: true },
    ],
    rows: [
      { index: '1', node: '2300', label: 'Public Legacy Node', name: 'Public Legacy Node', desc: 'Four-digit selection test', location: 'Test', rx: '', lcnt: '', href: '' },
      { index: '2', node: '1883', label: 'Private Bridge Node', name: 'Private Bridge Node', desc: 'Private selection test', location: 'Local', rx: '', lcnt: '', href: '' },
      { index: '3', node: '29332', label: 'Standard Public Node', name: 'Standard Public Node', desc: 'Five-digit selection test', location: 'Test', rx: '', lcnt: '', href: '' },
    ],
  })
}

function connectionFeed() {
  return JSON.stringify({
    641890: {
      remote_nodes: [
        { node: '1', info: 'LOCAL', keyed: 'no', mode: 'T', num_alinks: 3, lnodes: ['2300', '1883', '1884'] },
        { node: '2300', info: 'Public Legacy Node', keyed: 'no', mode: 'T', direction: 'OUT', elapsed: '00:02:00', last_keyed: 'Never', lnodes: [] },
        { node: '1883', info: 'Private Bridge Node', keyed: 'no', mode: 'T', direction: 'OUT', elapsed: '00:01:00', last_keyed: 'Never', lnodes: [] },
        { node: '1884', info: 'Node not in database', keyed: 'no', mode: 'T', direction: 'OUT', elapsed: '00:00:45', last_keyed: 'Never', lnodes: [] },
      ],
    },
  })
}

function serveText(res: import('node:http').ServerResponse, body: string, contentType: string) {
  res.statusCode = 200
  res.setHeader('Content-Type', contentType)
  res.end(body)
}

function serveStatic(projectRoot: string, res: import('node:http').ServerResponse, requestPath: string) {
  const normalized = requestPath.startsWith(`${ASR_BASE}/`)
    ? requestPath.slice(ASR_BASE.length + 1)
    : requestPath
  const file = path.join(projectRoot, 'public', normalized)
  if (!file.startsWith(path.join(projectRoot, 'public'))) return false
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) return false
  const ext = path.extname(file).toLowerCase()
  const contentType =
    ext === '.png' ? 'image/png'
      : ext === '.svg' ? 'image/svg+xml'
        : ext === '.gif' ? 'image/gif'
          : 'application/octet-stream'
  res.statusCode = 200
  res.setHeader('Content-Type', contentType)
  fs.createReadStream(file).pipe(res)
  return true
}

export function allscanMockPlugin(): Plugin {
  return {
    name: 'allscan-reimagined-mock',
    configureServer(server) {
      const projectRoot = server.config.root
      let favoritesFailuresRemaining = process.env.ASR_MOCK_FAVORITES_FAIL_ONCE === '1' ? 1 : 0

      server.middlewares.use((req, res, next) => {
        if (!req.url) return next()
        const url = new URL(req.url, 'http://localhost')
        const requestPath = url.pathname

        if (requestPath === `${ASR_BASE}/asr-api.php`) {
          const action = url.searchParams.get('action')
          if (action === 'runtime-config') return serveText(res, jsonResponse(runtimeConfig), 'application/json; charset=utf-8')
          if (action === 'auth-status') return serveText(res, jsonResponse(authStatus), 'application/json; charset=utf-8')
          if (action === 'release-status') return serveText(res, jsonResponse(releaseStatus), 'application/json; charset=utf-8')
          if (action === 'favorites') {
            if (favoritesFailuresRemaining > 0) {
              favoritesFailuresRemaining -= 1
              res.statusCode = 503
              return serveText(
                res,
                jsonResponse({ ok: false, error: 'Favorites list could not be loaded.' }),
                'application/json; charset=utf-8',
              )
            }
            return serveText(res, favoritesPayload(), 'application/json; charset=utf-8')
          }
          if (action === 'drop-clients') return serveText(res, jsonResponse({ ok: true, clients: [] }), 'application/json; charset=utf-8')
          if (action === 'bridge-status') {
            return serveText(res, jsonResponse({
              ok: true,
              bridge: JSON.parse(bridgeLive()),
              clients: JSON.parse(connectedClients()),
              controls: { dmr_net: { currentTg: '86753', ready: true, abinfoAvailable: true } },
            }), 'application/json; charset=utf-8')
          }
          if (action === 'bridge-tune') {
            return serveText(res, jsonResponse({
              ok: true,
              bridgeId: 'dmr_net',
              oldTg: '67498',
              currentTg: '86753',
              message: 'DMR Net Bridge tuned to TG 86753.',
            }), 'application/json; charset=utf-8')
          }
          return serveText(res, jsonResponse({ ok: true, message: `Mock ${action || 'request'} handled.` }), 'application/json; charset=utf-8')
        }

        if (requestPath === `${ASR_BASE}/astapi/connect.php`) {
          return serveText(res, 'Mock bridge link command sent.', 'text/plain; charset=utf-8')
        }

        if (requestPath === `${ASR_BASE}/astapi/server.php`) {
          res.statusCode = 200
          res.setHeader('Content-Type', 'text/event-stream; charset=utf-8')
          res.setHeader('Cache-Control', 'no-cache')
          res.write(`event: nodes\ndata: ${connectionFeed()}\n\n`)
          return
        }

        if (requestPath === `${ASR_BASE}/api/`) {
          return serveText(res, jsonResponse({ cpu_temp: '105.2', cpu_temp_f: '105.2', bg_color: '#4caf50' }), 'application/json; charset=utf-8')
        }

        if (requestPath === `${ASR_BASE}/bridge-live.json`) {
          return serveText(res, bridgeLive(), 'application/json; charset=utf-8')
        }

        if (requestPath === `${ASR_BASE}/connected-clients.json`) {
          return serveText(res, connectedClients(), 'application/json; charset=utf-8')
        }

        if (requestPath === `${ASR_BASE}/css/asr-admin.css`) {
          const css = fs.readFileSync(path.join(projectRoot, 'compat/allscan-v1.01/css/asr-admin.css'), 'utf8')
          return serveText(res, css, 'text/css; charset=utf-8')
        }

        if (requestPath === `${ASR_BASE}/user/settings/` || requestPath === `${ASR_BASE}/user/settings/index.php`) {
          return serveText(res, settingsPage(), 'text/html; charset=utf-8')
        }

        if (requestPath === `${ASR_BASE}/user/` || requestPath === `${ASR_BASE}/user/index.php`) {
          return serveText(res, loginPage(), 'text/html; charset=utf-8')
        }

        if (requestPath === `${ASR_BASE}/cfg/` || requestPath === `${ASR_BASE}/cfg/index.php`) {
          return serveText(res, cfgPage(), 'text/html; charset=utf-8')
        }

        if (requestPath === `${ASR_BASE}/asr-settings/` || requestPath === `${ASR_BASE}/asr-settings/index.php`) {
          return serveText(res, reimaginedSettingsPage(), 'text/html; charset=utf-8')
        }

        if (requestPath === `${ASR_BASE}/asr-instructions/` || requestPath === `${ASR_BASE}/asr-instructions/index.php`) {
          return serveText(res, instructionsPage(projectRoot), 'text/html; charset=utf-8')
        }

        if (requestPath.startsWith(`${ASR_BASE}/`) && serveStatic(projectRoot, res, requestPath)) return

        next()
      })
    },
  }
}
