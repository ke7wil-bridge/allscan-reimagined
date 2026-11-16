<?php
require_once '/var/www/html/asr/include/common.php';
$msg = [];
$asrplusScriptName = $_SERVER['SCRIPT_NAME'] ?? '';
$_SERVER['SCRIPT_NAME'] = '/asr/index.php';
asInit($msg);
$_SERVER['SCRIPT_NAME'] = $asrplusScriptName;
$db = dbInit();
checkTables($db, $msg);
$userModel = new UserModel($db);
$user = $userModel->validate();
if (!isset($user->user_id) || !validDbID($user->user_id)) {
    header('Location: /asr/user/');
    exit;
}
?><!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="robots" content="noindex,nofollow">
  <title>Netoholics Connection Monitor · ASR Plus</title>
  <link rel="icon" type="image/png" href="/asr/favicon-bolt-r-c.png">
  <link rel="stylesheet" href="asrplus.css?v=20261112-3">
</head>
<body>
  <header class="topbar">
    <div class="brand"><img src="/asrplus/netoholics-logo.png" alt="Netoholics Network"><div><span class="eyebrow">Private multi-node monitor</span><h1>Netoholics Connection Monitor</h1></div></div>
    <div class="top-actions"><span id="summary">Loading connections…</span><label class="theme-control">Theme <select id="theme"><option value="netoholics">Netoholics</option><option value="dark">Dark Side</option><option value="light">Bright Side</option><option value="ocean">Deep Ocean</option><option value="matrix">Matrix</option></select></label><button id="favorites-toggle" type="button">Favorites</button><button id="refresh" type="button">Refresh</button><a href="/asr/">Back to ASR</a></div>
  </header>
  <main id="modules">
    <p id="error" role="status"></p>
    <section id="talkers" class="dashboard-section movable-module" data-module="talkers" aria-label="Current and recent talkers"><h2 class="section-heading"><button class="module-handle" type="button" title="Drag to move" aria-label="Move Talkers">⋮⋮</button>Talkers</h2><div id="talker-cards" class="talker-cards"></div></section>
    <section id="favorites" class="dashboard-section movable-module" data-module="favorites" aria-label="Favorites">
      <header class="section-heading favorites-heading"><span><button class="module-handle" type="button" title="Drag to move" aria-label="Move Favorites">⋮⋮</button>Favorites</span><label>Connect on <select id="favorites-hub" aria-label="Hub for Favorites connections"></select></label></header>
      <div id="favorites-content" class="favorites-content">Loading Favorites…</div>
    </section>
    <section id="connections" class="movable-module" data-module="connections" aria-label="All hub connections">
      <button class="module-handle connection-module-handle" type="button" title="Drag all hub connections" aria-label="Move all hub connections">⋮⋮</button>
      <div id="hubs" aria-live="polite"></div>
    </section>
    <section id="bridges" class="dashboard-section movable-module" data-module="bridges" aria-label="Digital bridge status">
      <h2 class="section-heading"><button class="module-handle" type="button" title="Drag to move" aria-label="Move Bridges">⋮⋮</button>Digital Bridge Status</h2>
      <div id="bridge-grid" class="bridge-grid"></div>
    </section>
  </main>
  <footer><span id="updated">Waiting for connections…</span><span class="footer-brand"><img src="/asr/asr-logo-bright-r-tight.png" alt="ASR">ASR Plus · KE7WIL</span></footer>
  <script src="asrplus.js?v=20261112-3" defer></script>
</body>
</html>
