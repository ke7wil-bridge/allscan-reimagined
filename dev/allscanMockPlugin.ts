import fs from 'node:fs'
import path from 'node:path'
import type { Plugin } from 'vite'

type MockRoute = 'login' | 'users' | 'settings' | 'cfg'

const runtimeConfig = {
  ok: true,
  node: '641890',
  callsign: 'KE7WIL',
  headerTitle: 'KE7WIL - 641890',
  browserTitle: 'KE7WIL - 641890',
  brandByline: 'by KE7WIL',
  footerByline: 'customized by KE7WIL',
  headerLogo: '/asr-logo-bright-r-tight.png',
  footerLogo: '/asr-logo-bright-r-tight.png',
  versionLabel: 'v1.0.0 Beta 4',
  bridges: [
    { id: 'dmr', node: '641891', title: 'DMR Bridge', detailTitle: 'DMR Connected Clients' },
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

function selectedAttr(isSelected: boolean) {
  return isSelected ? ' selected' : ''
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
          ${item('/allscan/', 'Return to Main Page')}
          ${item('/allscan/user/settings/', 'Settings', 'settings')}
          <span class="asr-admin-menu-current">Reimagined Settings</span>
          ${item('/allscan/performance/', 'Performance Stats')}
          ${item('/allscan/user/', 'Users', 'users')}
          ${item('/allscan/cfg/', 'Configs', 'cfg')}
          <a role="menuitem" href="http://stats.allstarlink.org/stats/641890">Node Status</a>
          ${item('/allscan/lookup/', 'Lookup')}
          ${item('/allscan/?reportBug=1', 'Report a Bug')}
          <a role="menuitem" href="/allscan/user/?logout=1">Logout</a>
        </div>
      </div>
    </details>
  `
}

function adminShell(route: MockRoute, title: string, body: string) {
  const bodyClass =
    route === 'cfg' ? 'asr-admin-page asr-admin-page-cfg'
      : route === 'users' || route === 'login' ? 'asr-admin-page asr-admin-page-users'
        : 'asr-admin-page asr-admin-page-settings'

  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${escapeHtml(title)} | ASR Mock</title>
  <link rel="icon" type="image/png" href="/favicon-bolt-r-c.png">
  <link rel="stylesheet" href="/src/index.css">
  <link rel="stylesheet" href="/allscan/css/asr-admin.css">
  <script src="/allscan/js/main.js" defer></script>
</head>
<body class="${bodyClass}">
  <header class="allscan-header asr-admin-header">
    <div class="allscan-brand">
      <a class="allscan-brand-main" href="/allscan/" aria-label="Return to main AllScan page">
        <div class="allscan-wordmark">
          <strong class="allscan-wordmark-mark">
            <span class="allscan-wordmark-silver allscan-wordmark-all">All</span>
            <span class="allscan-wordmark-bolt-wrap" aria-hidden="true"><img class="allscan-wordmark-bolt" src="/bolt-test-tight.png" alt=""></span>
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

  <main class="asr-admin-mock-main">
    ${body}
  </main>
</body>
</html>`
}

function loginPage(editId?: number) {
  const selectedUser = mockUsers.find((user) => user.id === editId)

  return adminShell('login', 'AllScan Users', `
    <h2>Users</h2>
    <div class="greenborder">
      <h3>Mock Admin Session</h3>
      <p class="w800">Local mock mode treats you as an administrator so the admin layout can be tested without a node database.</p>
      ${usersTable(selectedUser)}
    </div>
  `)
}

function usersTable(selectedUser?: MockUser) {
  const formUser = selectedUser || {
    id: 0,
    name: 'new-user',
    email: 'new@example.test',
    nodes: '641890',
    permission: 'Admin',
    timezone: 'America/Phoenix',
    location: 'Arizona',
  } satisfies MockUser

  const userRows = mockUsers.map((user) => {
    const selectedClass = selectedUser?.id === user.id ? ' class="asr-admin-selected-row"' : ''
    return `<tr${selectedClass}><td>${user.id}</td><td>${escapeHtml(user.name)}</td><td>${escapeHtml(user.email)}</td><td>${escapeHtml(user.nodes)}</td><td>${escapeHtml(user.permission)}</td><td>${escapeHtml(user.timezone)}</td><td>${escapeHtml(user.location)}</td><td><a href="/allscan/user/?edit=${user.id}#user-edit">Edit</a></td></tr>`
  }).join('\n')

  return `
    <table class="grid">
      <thead>
        <tr>
          <th>ID</th>
          <th>Name</th>
          <th>Email</th>
          <th>Nodes</th>
          <th>Permission</th>
          <th>Timezone</th>
          <th>Location</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>
        ${userRows}
      </tbody>
    </table>
    <form id="user-edit" method="post" action="/allscan/user/">
      <fieldset>
        <legend>${selectedUser ? `Edit User #${selectedUser.id}` : 'Add User'}</legend>
        <input type="hidden" name="user_id" value="${selectedUser ? selectedUser.id : ''}">
        <label>Name <input name="name" type="text" value="${escapeHtml(formUser.name)}"></label>
        <label>Email <input name="email" type="text" value="${escapeHtml(formUser.email)}"></label>
        <label>Nodes <input name="nodenums" type="text" value="${escapeHtml(formUser.nodes)}"></label>
        <label>Permission
          <select name="permission">
            <option${selectedAttr(formUser.permission === 'Read Only')}>Read Only</option>
            <option${selectedAttr(formUser.permission === 'Write')}>Write</option>
            <option${selectedAttr(formUser.permission === 'Admin')}>Admin</option>
          </select>
        </label>
        <label>Timezone
          <select name="timezone_id">
            <option${selectedAttr(formUser.timezone === 'America/Phoenix')}>America/Phoenix</option>
            <option${selectedAttr(formUser.timezone === 'America/Denver')}>America/Denver</option>
            <option${selectedAttr(formUser.timezone === 'UTC')}>UTC</option>
          </select>
        </label>
        <label>Location <input name="location" type="text" value="${escapeHtml(formUser.location)}"></label>
        <p><input type="submit" value="${selectedUser ? 'Save User' : 'Add User'}">${selectedUser ? ' <a href="/allscan/user/#user-edit">Cancel Edit</a>' : ''}</p>
      </fieldset>
    </form>
  `
}

function settingsPage() {
  return adminShell('settings', 'AllScan Settings', `
    <h2>User Account Settings</h2>
    <form class="asr-user-settings-form" id="editUserForm" method="post" action="/allscan/user/settings/">
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
    <form class="asr-user-settings-form" id="changePassForm" method="post" action="/allscan/user/settings/">
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
      <h2>Manage Cfgs</h2>
      <h3>Configuration Parameters</h3>
      <table class="grid">
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
      <form class="asr-cfg-edit-form" method="post" action="/allscan/cfg/">
        <fieldset>
          <legend>Edit Cfg</legend>
          <label>Cfg Name
            <select class="asr-cfg-name-select">
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
            </select>
          </label>
          <p><input type="submit" value="Edit Cfg"> <input type="button" value="Set to Default Value"></p>
        </fieldset>
      </form>
    </div>
    <div class="greenborder">
      <h2>Manage Favorites</h2>
      <h3>View/Download/Delete Favorites Files</h3>
      <table class="grid">
        <thead><tr><th>File</th><th>Size (Bytes)</th><th>Last Modified</th><th>Options</th></tr></thead>
        <tbody>
          <tr><td>/var/www/html/allscan/favorites-Sample.ini</td><td>3804</td><td>2025-09-09 21:47</td><td>Delete</td></tr>
          <tr><td>/var/www/html/allscan/favorites-allscan-format.ini</td><td>2196</td><td>2026-05-26 20:44</td><td>Delete</td></tr>
          <tr><td>/var/www/html/allscan/favorites-local-clubs.ini</td><td>609</td><td>2026-01-06 14:18</td><td>Delete</td></tr>
          <tr><td>/var/www/html/allscan/favorites.ini [Default]</td><td>2197</td><td>2026-05-26 21:01</td><td>Delete</td></tr>
        </tbody>
      </table>
      <h3>Copy/Backup Favorites Files</h3>
      <form method="post" action="/allscan/cfg/">
        <fieldset>
          <legend>Copy File</legend>
          <label>File to Copy
            <select><option selected>/var/www/html/allscan/favorites-Sample.ini</option><option>/var/www/html/allscan/favorites.ini</option></select>
          </label>
          <label>Destination Dir
            <select><option selected>/var/www/html/allscan/</option><option>/etc/allscan/</option></select>
          </label>
          <label>Name Suffix (optional) <input type="text" value=""></label>
          <p><input type="submit" value="Copy File"></p>
        </fieldset>
      </form>
      <p class="w800">The Favorites file select control on the main page enables easy switching between files, supporting grouping of favorites by location, type, interests, etc. A new favorites file can be created by copying and then editing an existing file, or uploading a new file.</p>
      <h3>Upload Favorites File</h3>
      <form method="post" action="/allscan/cfg/">
        <fieldset>
          <legend>Upload File</legend>
          <label>Favorites File <input type="file"></label>
          <label>Destination Dir
            <select><option selected>/var/www/html/allscan/</option><option>/etc/allscan/</option></select>
          </label>
          <p><input type="submit" value="Upload"></p>
        </fieldset>
      </form>
      <p class="w800">Favorites file names must be in the format favorites[*].ini, ie. with an optional suffix before the .ini extension. Example valid filenames: favorites.ini, favorites-WestCoast.ini, favorites-UK.ini, favorites-nets.ini, etc.</p>
    </div>
  `)
}

function bridgeLive() {
  return jsonResponse({
    updated: '2026-06-25T17:15:30Z',
    updated_epoch: 1782417330,
    dmr: { active: true, role: 'source', state: 'Source/TX', caller: 'N7MOCK', recent_users: [{ name: 'N7MOCK' }, { name: 'W7TEST' }] },
    ysf: { active: true, role: 'relay', state: 'Relay', caller: 'W7TEST', recent_users: [{ name: 'W7TEST' }] },
    zello: { active: false, role: 'idle', state: 'Idle', caller: '', recent_users: [] },
  })
}

function connectedClients() {
  return jsonResponse({
    dmr: [{ callsign: 'N7MOCK', room: 'TG 3100', connected: '00:12:04' }],
    ysf: [{ callsign: 'W7TEST', room: 'America-Link', connected: '00:03:41' }],
    zello: [],
  })
}

function serveText(res: import('node:http').ServerResponse, body: string, contentType: string) {
  res.statusCode = 200
  res.setHeader('Content-Type', contentType)
  res.end(body)
}

function serveStatic(projectRoot: string, res: import('node:http').ServerResponse, requestPath: string) {
  const normalized = requestPath.replace(/^\/allscan\//, '')
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

      server.middlewares.use((req, res, next) => {
        if (!req.url) return next()
        const url = new URL(req.url, 'http://localhost')
        const requestPath = url.pathname

        if (requestPath === '/allscan/asr-api.php') {
          const action = url.searchParams.get('action')
          if (action === 'runtime-config') return serveText(res, jsonResponse(runtimeConfig), 'application/json; charset=utf-8')
          if (action === 'auth-status') return serveText(res, jsonResponse(authStatus), 'application/json; charset=utf-8')
          if (action === 'drop-clients') return serveText(res, jsonResponse({ ok: true, clients: [] }), 'application/json; charset=utf-8')
          return serveText(res, jsonResponse({ ok: true, message: `Mock ${action || 'request'} handled.` }), 'application/json; charset=utf-8')
        }

        if (requestPath === '/allscan/api/') {
          return serveText(res, jsonResponse({ cpu_temp: '105.2', cpu_temp_f: '105.2', bg_color: '#4caf50' }), 'application/json; charset=utf-8')
        }

        if (requestPath === '/allscan/bridge-live.json') {
          return serveText(res, bridgeLive(), 'application/json; charset=utf-8')
        }

        if (requestPath === '/allscan/connected-clients.json') {
          return serveText(res, connectedClients(), 'application/json; charset=utf-8')
        }

        if (requestPath === '/allscan/css/asr-admin.css') {
          const css = fs.readFileSync(path.join(projectRoot, 'compat/allscan-v1.01/css/asr-admin.css'), 'utf8')
          return serveText(res, css, 'text/css; charset=utf-8')
        }

        if (requestPath === '/allscan/user/settings/' || requestPath === '/allscan/user/settings/index.php') {
          return serveText(res, settingsPage(), 'text/html; charset=utf-8')
        }

        if (requestPath === '/allscan/user/' || requestPath === '/allscan/user/index.php') {
          const editId = Number(url.searchParams.get('edit') || 0)
          return serveText(res, loginPage(Number.isFinite(editId) ? editId : undefined), 'text/html; charset=utf-8')
        }

        if (requestPath === '/allscan/cfg/' || requestPath === '/allscan/cfg/index.php') {
          return serveText(res, cfgPage(), 'text/html; charset=utf-8')
        }

        if (requestPath.startsWith('/allscan/') && serveStatic(projectRoot, res, requestPath)) return

        next()
      })
    },
  }
}
