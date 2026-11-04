#!/usr/bin/env node

import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { spawnSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const source = await readFile(join(root, 'compat/allscan-v1.05/asr-settings/settings-controller.php'), 'utf8')
const match = source.match(/<script>\s*(\(function \(\) \{[\s\S]*?\}\)\(\);)\s*<\/script>/)
if (!match) throw new Error('Settings browser controller was not found.')
const controller = match[1]
  .replace(/'<\?php[\s\S]*?\?>'/g, "'ROLLBACK'")
  .replace(/<\?php[\s\S]*?\?>/g, '"N1YKT"')

const html = `<!doctype html><html><body>
<form class="asr-reimagined-settings-form" data-max-bridges="16">
  <input name="settingsSaveCsrf" value="test-csrf">
  <div data-urf-panel data-urf-bridge-id="urf" data-urf-runtime-name="URF073">
    <input name="urfModes[]" value="dmr" type="checkbox" checked>
  </div>
  <input data-urf-enabled value="1">
  <div data-bridge-setup-panel>
    <select data-bridge-setup-mode><option value="dmr" selected>DMR</option></select>
    <select data-bridge-setup-path><option value="urf" selected>URF</option></select>
    <input data-bridge-setup-id value="urf">
    <input data-bridge-setup-title value="URF073">
    <input data-bridge-setup-node value="1001">
    <input data-bridge-setup-callsign value="N1YKT">
    <input data-bridge-setup-reflector value="URF073">
    <input data-bridge-setup-digital-id value="3205161">
    <input data-bridge-setup-destination value="42450">
    <select data-bridge-setup-tgif-auth-mode><option value="secured" selected>Secured</option></select>
    <input data-bridge-setup-tgif-essid value="">
    <input data-bridge-setup-dmr-username value="N1YKT">
    <input data-bridge-setup-tgif-password type="password" value="">
    <select data-bridge-setup-dmr-network><option value="tgif" selected>TGIF</option></select>
    <input data-bridge-setup-host value="">
    <input data-bridge-setup-port value="">
    <input data-bridge-setup-module value="A">
    <button type="button" data-bridge-setup-preview>Check Setup</button>
    <button type="button" data-bridge-setup-install disabled hidden>Install Bridge</button>
    <button type="button" data-bridge-setup-cancel>Cancel</button>
    <div data-bridge-setup-output hidden></div>
  </div>
</form>
<pre id="result">RUNNING</pre>
<script>
window.__requests = [];
window.confirm = function() { return true; };
window.fetch = function(url, options) {
  var body = new URLSearchParams(options && options.body ? options.body : '');
  var action = body.get('asrAction') || '';
  window.__requests.push({action: action, body: body.toString()});
  var response = action === 'dmr-plan'
    ? {ok:true, result:{digest:'a'.repeat(64), bridgeNode:1001, dmrNetworkId:'320516101', ports:{usrp_rx:37920, usrp_tx:37921, dmr:37923, tgif_local:37929, tgif_mmdvm:37930}}}
    : action === 'dmr-install'
      ? {ok:false, error:'simulated privileged helper failure'}
      : action === 'dmr-diagnose'
        ? {ok:true, result:{classification:'missing_runtime_files', runtimePresent:false, services:[]}}
      : {ok:true, result:{}};
  return Promise.resolve({ok:response.ok, json:function(){ return Promise.resolve(response); }});
};
${controller}
</script>
<script>
(async function() {
  function check(value, message) { if(!value) throw new Error(message); }
  function settle() { return new Promise(function(resolve) { setTimeout(resolve, 25); }); }
  try {
    var preview = document.querySelector('[data-bridge-setup-preview]');
    var install = document.querySelector('[data-bridge-setup-install]');
    var key = document.querySelector('[data-bridge-setup-tgif-password]');
    var output = document.querySelector('[data-bridge-setup-output]');
    preview.click();
    await settle();
    check(window.__requests.some(function(item) { return item.action === 'dmr-plan'; }), 'Check Setup did not dispatch dmr-plan.');
    check(!install.hidden && !install.disabled, 'Validated plan did not enable Install Bridge.');
    key.value = 'browser-test-key';
    key.dispatchEvent(new Event('input', {bubbles:true}));
    check(!install.hidden && !install.disabled, 'Entering the install-only key invalidated the plan.');
    install.click();
    await settle();
    var request = window.__requests.find(function(item) { return item.action === 'dmr-install'; });
    check(request, 'Install Bridge did not dispatch dmr-install.');
    check(new URLSearchParams(request.body).get('setupTgifPassword') === 'browser-test-key', 'dmr-install omitted the hotspot key.');
    check(window.__requests.some(function(item) { return item.action === 'dmr-diagnose'; }), 'Failed install did not collect read-only runtime diagnostics.');
    check(output.getAttribute('role') === 'alert' && output.textContent.indexOf('simulated privileged helper failure') !== -1, 'Backend failure was not displayed as a persistent alert.');
    check(output.textContent.indexOf('missing runtime files') !== -1, 'Runtime failure classification was not displayed.');
    document.getElementById('result').textContent = 'PASS';
  } catch (error) {
    document.getElementById('result').textContent = 'FAIL: ' + (error && error.message ? error.message : String(error));
  }
})();
</script></body></html>`

const temporary = await mkdtemp(join(tmpdir(), 'asr-browser-install-'))
try {
  const fixture = join(temporary, 'fixture.html')
  await writeFile(fixture, html, { mode: 0o600 })
  const run = spawnSync('/usr/bin/google-chrome', [
    '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-network',
    '--virtual-time-budget=2000', '--dump-dom', `file://${fixture}`,
  ], { encoding: 'utf8', timeout: 15000 })
  if (run.error) throw run.error
  if (run.status !== 0) throw new Error(run.stderr.trim() || `Chrome exited ${run.status}`)
  const result = run.stdout.match(/<pre id="result">([^<]+)<\/pre>/)?.[1] || 'missing result'
  if (result !== 'PASS') throw new Error(result)
  console.log('URF Install Bridge browser self-test: PASS')
} finally {
  await rm(temporary, { recursive: true, force: true })
}
