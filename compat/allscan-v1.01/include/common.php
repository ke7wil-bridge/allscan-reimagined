<?php
// AllScan main includes & common functions
// Author: David Gleason - AllScan.info
$AllScanVersion = "v1.01";
define('ASR_REIMAGINED_VERSION_LABEL', 'v1.0.0 Beta 5.8');
require_once('Html.php');
require_once('logUtils.php');
require_once('timeUtils.php');
require_once('viewUtils.php');
require_once('favsUtils.php');
require_once('dbUtils.php');
require_once('DB.php');
require_once('UserModel.php');
require_once('CfgModel.php');
require_once('hwUtils.php');

// API functions
define('GET_CPU_TEMP', 'getCpuTemp');

// Enable files to be written with group writeable permissions
umask(0002);

/*	AllScan can be installed in any top level folder within the www server root folder.
	allscan/ is the default, additional copies can be installed in other test / version-specific /
	backup dirs. This enables servers with multiple nodes to have separate allscan installs for
	each, eg. allscan567890/, ...
	Each install requires its own DB file in /etc/allscan/. Examples:
	Install Dir				DB File name
	wwwroot/allscan/		/etc/allscan/allscan.db			(default)
	wwwroot/allscan-test/	/etc/allscan/allscan-test.db
	wwwroot/				/etc/allscan/.db
	If you copy/move/backup an allscan install, eg. "cp -a allscan allscan-bak", you should also copy the DB file:
	"cp /etc/allscan/allscan.db /etc/allscan/allscan-bak.db"
*/
// File System Cfgs - initialized in asInit() before dbInit() can be called
$wwwroot = '';	// eg. var/www/html
$asdir = '';	// eg. allscan
$subdir = '';	// eg. '' or user
$relpath = '';	// eg. allscan or allscan/user
$urlbase = '';	// eg. /allscan (prepended to url paths eg. <img src=\"$urlbase/AllScan.png\">)
$asdbdir = '/etc/allscan';
$asdbfile = '';
$asdbfile2 = $asdbdir . '/asdb.txt';

// Title cfgs
$title = '';
$title2 = '';
// AMI cfgs
$amicfg = new stdClass();

function asInit(&$msg) {
	global $wwwroot, $asdir, $subdir, $relpath, $urlbase;
	$wwwroot = $_SERVER['DOCUMENT_ROOT'];
	$path = pathinfo($_SERVER['SCRIPT_NAME']);
	$relpath = $asdir = substr($path['dirname'], 1);
	if($asdir && strpos($asdir, '/')) {
		$dirs = explode('/', $asdir);
		$asdir = array_shift($dirs);
		$subdir = implode('/', $dirs);
	}
	$urlbase = $asdir ? "/$asdir" : '';
	$msg[] = "wwwroot=$wwwroot, asdir=$asdir, subdir=$subdir, relpath=$relpath";
	// Default install results: wwwroot=/var/www/html/, asdir=allscan, subdir=, relpath=allscan
	// Or if in an allscan subdir eg. user: same as above but subdir=user, relpath=allscan/user
}

function asrInitAuthenticatedUser(&$msg) {
	global $db, $userCnt, $cfgModel, $userModel, $user;
	$db = dbInit();
	$userCnt = checkTables($db, $msg);
	if(!$userCnt)
		redirect('user/');
	$cfgModel = new CfgModel($db);
	$userModel = new UserModel($db);
	$user = $userModel->validate();
	if(empty($user) || !isset($user->user_id) || !validDbID($user->user_id))
		redirect('user/');
	return $user;
}

function htmlInit($title) {
	global $html, $urlbase;
	echo $html->htmlOpen($title)
		.	'<script>(function(){document.documentElement.dataset.asrTheme="standard";document.documentElement.dataset.asrMode="dark"})();</script>' . NL
			.	"<link href=\"$urlbase/css/main.css\" rel=\"stylesheet\" type=\"text/css\">" . NL
			.	asrAdminAssetCssLink()
			.	asrAdminCssLink()
			.	"<link href=\"$urlbase/favicon-bolt-r-c.png\" rel=\"icon\" type=\"image/png\">" . NL
		.	'<meta name="viewport" content="width=device-width, initial-scale=1">' . NL
		.	"<script src=\"$urlbase/js/main.js\"></script>" . NL
		.	'</head>' . NL;
}

function asrAdminCssLink() {
	global $wwwroot, $asdir, $urlbase;
	$file = "$wwwroot/$asdir/css/asr-admin.css";
	$version = is_readable($file) ? filemtime($file) : time();
	return "<link href=\"$urlbase/css/asr-admin.css?v=$version\" rel=\"stylesheet\" type=\"text/css\">" . NL;
}

function asrAdminAssetCssLink() {
	global $wwwroot, $asdir, $urlbase;
	$assetDir = "$wwwroot/$asdir/assets";
	$files = glob("$assetDir/index-*.css");
	if(!$files)
		return '';
	rsort($files);
	$file = basename($files[0]);
	return "<link href=\"$urlbase/assets/$file\" rel=\"stylesheet\" type=\"text/css\">" . NL;
}

function pageInit($onload='', $showHdrLinks=true, $showUpdateLink=false) {
	global $html, $AllScanVersion, $gCfg, $urlbase, $title, $title2, $userCnt;
	// Return now if not called from an HTML context
	if(!isset($html))
		return;
	// Load Title cfgs
	if(checkTitleCfgs()) {
		$title2 = $title = $gCfg[call] . ' ' . $gCfg[location];
		if($gCfg[title])
			$title2 .= ' - ' . $gCfg[title];
	} else {
		$title = '[Call Sign] [Location]';
		$title2 = '[Node Title] - ' . $title;
	}
	htmlInit(asrAdminDocumentTitle($title));
	$bodyClass = asrAdminBodyClass();
	// Output header
	$menuHtml = asrAdminHeaderMenu($showHdrLinks);
	$cpuTemp = asrAdminCpuTemp();
	$cpuBg = asrAdminCpuBg($cpuTemp);
	$nodeTitle = asrAdminTitle($title);
	$runtime = asrAdminRuntimeConfig();
	$versionLabel = htmlspecial($runtime['versionLabel'] ?? ASR_REIMAGINED_VERSION_LABEL);
	$brandByline = htmlspecial($runtime['brandByline'] ?? '');
	$headerLogo = htmlattr($runtime['headerLogo'] ?? "$urlbase/asr-logo-bright-r-tight.png");
	echo "<body$onload class=\"$bodyClass\">" . NL
		. '<script>(function(){try{document.body.dataset.asrTheme=document.documentElement.dataset.asrTheme||"standard";document.body.dataset.asrMode=document.documentElement.dataset.asrMode||"dark"}catch(e){}})();</script>' . NL
		. '<header class="allscan-header asr-admin-header">' . NL
		. '<div class="allscan-brand"><a class="allscan-brand-main" href="' . $urlbase . '/" aria-label="Return to main AllScan page"><div class="allscan-wordmark">'
		. '<strong class="allscan-wordmark-mark"><span class="allscan-wordmark-silver allscan-wordmark-all">All</span><span class="allscan-wordmark-bolt-wrap" aria-hidden="true"><img class="allscan-wordmark-bolt" src="' . $urlbase . '/bolt-test-tight.png" alt=""></span><span class="allscan-wordmark-silver allscan-wordmark-can">can</span></strong>'
		. '<span class="allscan-tagline">Reimagined</span>'
		. '<small class="allscan-brand-version">' . $versionLabel . '</small>'
		. ($brandByline ? '<span class="allscan-byline">' . $brandByline . '</span>' : '')
		. '</div></a></div>' . NL
		. '<div class="allscan-header-center"><a class="allscan-header-logo-link" href="' . $urlbase . '/" aria-label="Return to main AllScan page"><img class="allscan-header-ke7wil-logo" src="' . $headerLogo . '" alt="Header logo"></a>'
		. '<h1 class="allscan-title">' . htmlspecial($nodeTitle) . '</h1>'
		. '<div class="allscan-cpu"><span class="allscan-meta-label">CPU Temp:</span><b class="allscan-cpu-pill" style="background-color:' . htmlattr($cpuBg) . ';">' . htmlspecial($cpuTemp) . '</b></div>'
		. '<div class="allscan-clockline"><span><span class="allscan-meta-label">Local</span> ' . date('g:i:s A') . '</span>'
		. '<span><span class="allscan-meta-label">UTC</span> ' . gmdate('G:i:s') . '</span></div></div>' . NL
		. $menuHtml . NL
		. '</header>' . NL
		. '<script>function asrAdminCloseMenu(wrap){var panel=wrap&&wrap.querySelector(".asr-admin-menu-panel");var btn=wrap&&wrap.querySelector(".allscan-menu-button");if(panel){panel.setAttribute("hidden","");panel.style.display="none";panel.classList.remove("has-active-submenu")}if(btn)btn.setAttribute("aria-expanded","false");if(wrap)wrap.classList.remove("is-open")}function asrAdminClearSubmenus(wrap){var panel=wrap&&wrap.querySelector(".asr-admin-menu-panel");if(!wrap||!panel)return;wrap.querySelectorAll(".allscan-menu-proxy-row").forEach(function(button){button.classList.remove("is-active");button.setAttribute("aria-expanded","false")});wrap.querySelectorAll(".allscan-submenu").forEach(function(menu){menu.classList.remove("is-open")});panel.classList.remove("has-active-submenu")}function asrAdminToggleMenu(btn){var wrap=btn.closest(".asr-admin-menu");var panel=wrap&&wrap.querySelector(".asr-admin-menu-panel");if(!panel)return;var open=panel.hasAttribute("hidden");if(open){panel.removeAttribute("hidden");panel.style.display="block";wrap.classList.add("is-open");btn.setAttribute("aria-expanded","true")}else{asrAdminCloseMenu(wrap)}}document.addEventListener("click",function(e){var row=e.target.closest&&e.target.closest(".asr-admin-menu .allscan-menu-proxy-row");if(row){var key=row.getAttribute("data-submenu");if(!key)return;e.preventDefault();e.stopPropagation();var wrap=row.closest(".asr-admin-menu");var panel=wrap.querySelector(".asr-admin-menu-panel");var active=row.classList.contains("is-active");asrAdminClearSubmenus(wrap);if(!active){row.classList.add("is-active");row.setAttribute("aria-expanded","true");var submenu=wrap.querySelector(".allscan-submenu-"+key);if(submenu)submenu.classList.add("is-open");if(panel)panel.classList.add("has-active-submenu")}return}var back=e.target.closest&&e.target.closest(".asr-admin-menu .allscan-submenu-back");if(back){e.preventDefault();e.stopPropagation();asrAdminClearSubmenus(back.closest(".asr-admin-menu"));return}document.querySelectorAll(".asr-admin-menu.is-open").forEach(function(wrap){if(wrap.contains(e.target))return;asrAdminCloseMenu(wrap)})});</script>' . NL . BR;
}

function asrAdminHeaderMenu($showHdrLinks=true) {
	global $html, $urlbase, $amicfg, $msg, $subdir;
	if(!isset($amicfg->node))
		getAmiCfg($msg);
	$node = trim($amicfg->node ?? '');
	$resources = [
		'<a role="menuitem" href="https://allscan.info/" target="_blank" rel="noreferrer">AllScan.info</a>',
		'<a role="menuitem" href="https://github.com/ke7wil-bridge/allscan-reimagined#updates" target="_blank" rel="noreferrer">Updates</a>',
		'<a role="menuitem" href="https://github.com/davidgsd/AllScan#allscan" target="_blank" rel="noreferrer">Original AllScan</a>',
		'<a role="menuitem" href="https://www.allstarlink.org/" target="_blank" rel="noreferrer">AllStarLink.org</a>',
		'<a role="menuitem" href="http://stats.allstarlink.org/stats/keyed" target="_blank" rel="noreferrer">Keyed Nodes</a>',
		'<a role="menuitem" href="https://community.allstarlink.org/" target="_blank" rel="noreferrer">ASL Forum</a>',
		'<a role="menuitem" href="https://www.facebook.com/groups/allscan" target="_blank" rel="noreferrer">AllScan FB</a>',
		'<a role="menuitem" href="https://www.eham.net/" target="_blank" rel="noreferrer">eHam.net</a>',
	];
	$loggedIn = isset($GLOBALS['user']->user_id) && validDbID($GLOBALS['user']->user_id);
	$isAdmin = $loggedIn && adminUser();
	$admin = [
		$html->a("$urlbase/user/settings/", null, 'Settings'),
		$html->a("$urlbase/asr-settings/", null, 'Reimagined Settings'),
		$html->a("$urlbase/performance/", null, 'Performance Stats'),
		$html->a("$urlbase/user/", null, 'Users'),
		$html->a("$urlbase/cfg/", null, 'Configs'),
	];
	if($node !== '')
		$admin[] = '<a role="menuitem" href="http://stats.allstarlink.org/stats/' . htmlattr($node) . '" target="_blank" rel="noreferrer">Node Status</a>';
	if(!$loggedIn) {
		$admin[] = $html->a("$urlbase/user/", null, 'Login');
	}
	$returnMainPages = ['cfg', 'user', 'user/settings', 'lookup', 'echolink-lookup', 'asr-settings', 'performance'];
	$showReturnMain = $loggedIn && in_array($subdir, $returnMainPages, true);
	$returnMain = $showReturnMain
		? '<a class="asr-admin-return-main" href="' . $urlbase . '/">Return to Main Page</a>'
		: '';
	$returnMainMenu = $showReturnMain
		? '<a class="allscan-menu-proxy-row allscan-menu-direct-row asr-admin-menu-main-row" role="menuitem" href="' . $urlbase . '/"><span>Main Page</span></a>'
		: '';
	$reportBugMenu = $isAdmin
		? '<a class="allscan-menu-proxy-row asr-admin-report-bug-row" role="menuitem" href="' . $urlbase . '/?reportBug=1"><span>Report a Bug</span></a>'
		: '';
	$logoutMenu = $loggedIn
		? '<a class="allscan-menu-proxy-row asr-admin-logout-row" role="menuitem" href="' . $urlbase . '/user/?logout=1"><span>Logout</span></a>'
		: '';
	return '<div class="allscan-menu-slot asr-admin-menu"><button type="button" class="allscan-menu-button" aria-haspopup="menu" aria-expanded="false" onclick="asrAdminToggleMenu(this)"><span class="allscan-menu-desktop">Menu <span class="asr-admin-menu-caret" aria-hidden="true">⌄</span></span><span class="allscan-menu-mobile">☰</span></button>'
		. '<div class="allscan-menu-panel asr-admin-menu-panel" role="menu" hidden style="display:none">'
		. '<div class="allscan-menu-proxy-list">'
		. $returnMainMenu
		. '<button type="button" class="allscan-menu-proxy-row" data-submenu="admin" aria-expanded="false"><span>Admin</span><span class="allscan-menu-row-icon" aria-hidden="true">⌄</span></button>'
		. '<button type="button" class="allscan-menu-proxy-row" data-submenu="resources" aria-expanded="false"><span>Resources</span><span class="allscan-menu-row-icon" aria-hidden="true">⌄</span></button>'
		. '<a class="allscan-menu-proxy-row allscan-menu-direct-row" role="menuitem" href="' . $urlbase . '/lookup/"><span>Lookup</span></a>'
		. $reportBugMenu
		. $logoutMenu
		. '</div>'
		. '<button type="button" class="allscan-submenu-back"><span class="allscan-submenu-back-icon" aria-hidden="true">‹</span><span>Menu</span></button>'
		. '<div class="allscan-submenu allscan-submenu-resources">' . implode(NL, $resources) . '</div>'
		. '<div class="allscan-submenu allscan-submenu-admin">' . implode(NL, array_map('asrAdminMenuItem', $admin)) . '</div>'
		. '</div>' . $returnMain . '</div>';
}

function asrAdminRuntimeConfig() {
	static $runtime = null;
	if($runtime !== null)
		return $runtime;
	$file = '/etc/allscan-reimagined/config.json';
	$data = is_readable($file) ? json_decode(file_get_contents($file), true) : [];
	$runtime = is_array($data) ? $data : [];
	$runtime['versionLabel'] = ASR_REIMAGINED_VERSION_LABEL;
	$runtime['brandByline'] = 'by KE7WIL';
	if(empty($runtime['footerByline']))
		$runtime['footerByline'] = 'customized by KE7WIL';
	return $runtime;
}

function asrAdminTitle($title) {
	$runtime = asrAdminRuntimeConfig();
	global $gCfg, $amicfg, $msg;
	if(!isset($amicfg->node))
		getAmiCfg($msg);
	$call = trim($gCfg[call] ?? '');
	$node = trim($amicfg->node ?? ($gCfg[nodenum] ?? ''));
	if(!empty($runtime['headerTitle']))
		return str_replace(['{CALLSIGN}', '{NODE}'], [$call ?: 'AllScan', $node], $runtime['headerTitle']);
	if($call && $node)
		return "$call | Node $node";
	return $title;
}

function asrAdminDocumentTitle($title) {
	$runtime = asrAdminRuntimeConfig();
	global $gCfg, $amicfg, $msg;
	if(!isset($amicfg->node))
		getAmiCfg($msg);
	$call = trim($gCfg[call] ?? '');
	$node = trim($amicfg->node ?? ($gCfg[nodenum] ?? ''));
	if(!empty($runtime['browserTitle']))
		return str_replace(['{CALLSIGN}', '{NODE}'], [$call ?: 'AllScan', $node], $runtime['browserTitle']);
	if($call && $node)
		return "$call - $node | ASR";
	return $title;
}

function asrAdminBodyClass() {
	global $subdir;
	$classes = ['asr-admin-page'];
	if($subdir === 'cfg') {
		$classes[] = 'asr-admin-page-cfg';
	} elseif($subdir === 'user') {
		$classes[] = 'asr-admin-page-users';
	} elseif($subdir === 'user/settings') {
		$classes[] = 'asr-admin-page-settings';
	} elseif($subdir === 'asr-settings') {
		$classes[] = 'asr-admin-page-reimagined';
	} elseif($subdir === 'performance') {
		$classes[] = 'asr-admin-page-performance';
	} elseif($subdir === 'lookup') {
		$classes[] = 'asr-admin-page-lookup';
	} elseif($subdir === 'echolink-lookup') {
		$classes[] = 'asr-admin-page-lookup';
		$classes[] = 'asr-admin-page-echolink-lookup';
	}
	return implode(' ', $classes);
}

function asrAdminCpuTemp() {
	$temp = cpuTemp();
	if(preg_match('/([0-9]+&deg;F\\s*\\/\\s*[0-9]+&deg;C)/', $temp, $m))
		return html_entity_decode($m[1], ENT_QUOTES | ENT_HTML5);
	return '--';
}

function asrAdminCpuBg($temp) {
	if(preg_match('/([0-9]+)°F/', $temp, $m)) {
		$ft = (int)$m[1];
		if($ft < 130)
			return 'darkgreen';
		if($ft < 150)
			return '#660';
		return 'red';
	}
	return '#660';
}

function asrAdminMenuItem($item) {
	if(strpos($item, '<a ') === 0)
		return $item;
	return '<span class="asr-admin-menu-current">' . $item . '</span>';
}

function checkTitleCfgs() {
	global $gCfg, $astdb, $amicfg, $msg;
	if(!isset($amicfg->node))
		getAmiCfg($msg);
	$node = $amicfg->node;
	$call = $desc = $loc = '';
	if($node && is_numeric($node)) {
		if(empty($astdb))
			$astdb = readAstDb($msg);
		if(!is_array($astdb))
			$astdb = [];
		if(array_key_exists($node, $astdb))
			list($x, $call, $desc, $loc) = $astdb[$node];
		if(empty($gCfg[call]) && $call)
			$gCfg[call] = $call;
		if(empty($gCfg[location]) && $loc)
			$gCfg[location] = $loc;
		if(empty($gCfg[title]) && $desc)
			$gCfg[title] = $desc;
	}
	return (!empty($gCfg[call]));
}

function getHdrLinks() {
	global $html, $urlbase, $user, $amicfg, $msg;
	$lnk = [];
	if(isset($user->user_id) && validDbID($user->user_id)) {
		$url = "$urlbase/user/settings/";
		$title = 'Settings';
		$lnk[] = ($url === getScriptName()) ? $title : $html->a($url, null, $title);
		// Keep the legacy admin links in the same order as the shared Admin menu.
		if(adminUser()) {
			$url = "$urlbase/asr-settings/";
			$title = 'Reimagined Settings';
			$lnk[] = ($url === getScriptName()) ? $title : $html->a($url, null, $title);
			$url = "$urlbase/performance/";
			$title = 'Performance Stats';
			$lnk[] = ($url === getScriptName()) ? $title : $html->a($url, null, $title);
			$url = "$urlbase/user/";
			$title = 'Users';
			$lnk[] = ($url === getScriptName()) ? $title : $html->a($url, null, $title);
			$url = "$urlbase/cfg/";
			$title = 'Configs';
			$lnk[] = ($url === getScriptName()) ? $title : $html->a($url, null, $title);
			if(!isset($amicfg->node))
				getAmiCfg($msg);
			$node = trim($amicfg->node ?? '');
			if($node !== '')
				$lnk[] = '<a href="http://stats.allstarlink.org/stats/' . htmlattr($node) . '" target="_blank" rel="noreferrer">Node Status</a>';
		}
		$url = "$urlbase/lookup/";
		$title = 'Lookup';
		$lnk[] = ($url === getScriptName()) ? $title : $html->a($url, null, $title);
		if(adminUser())
			$lnk[] = $html->a("$urlbase/", ['reportBug'=>1], 'Report a Bug');
		$lnk[] = $html->a("$urlbase/user/", ['logout'=>1], 'Logout');
	} else {
		// Show Login link
		$lnk[] = $html->a("$urlbase/user/", null, 'Login');
	}
	return $lnk;
}

function msg($txt, $class=null) {
	global $html;
	if(isset($html)) {
		if($class)
			$txt = $html->span($txt, $class);
		$txt .= BR;
	}
	echo $txt . NL;
}

// Read node# and AMI Cfgs from AllScan gCfg or rpt.conf and manager.conf
function getAmiCfg(&$msg) {
	global $amicfg, $gCfg;
	if($gCfg[nodenum] && $gCfg[amihost] && $gCfg[amiport] && $gCfg[amiuser] && $gCfg[amipass]) {
		$amicfg->node = $gCfg[nodenum];
		$amicfg->host = $gCfg[amihost];
		$amicfg->port = $gCfg[amiport];
		$amicfg->user = $gCfg[amiuser];
		$amicfg->pass = $gCfg[amipass];
		$msg[] = "AllScan Cfg node#: $amicfg->node, host: $amicfg->host:$amicfg->port";
		return true;
	}
	if($gCfg[nodenum]) {
		$amicfg->node = $gCfg[nodenum];
		$msg[] = "AllScan Cfg node#: $amicfg->node";
	} else {
		// Read node number(s) from rpt.conf
		$f = '/etc/asterisk/rpt.conf';
		if(!file_exists($f)) {
			$msg[] = "$f not found";
			return false;
		}
		$rptc = file($f, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
		if($rptc === false) {
			$msg[] = "Error reading $f";
			return false;
		}
		$nnums = [];
		foreach($rptc as $s) {
			if(preg_match('/^\[([0-9]{4,6})\]/', $s, $m) == 1)
				$nnums[] = $m[1];
		}
		if(!count($nnums)) {
			$msg[] = "No valid nodes found in $f. Check file or set Node Number parameter on Configs Tab.";
			return false;
		}
		$msg[] = "rpt.conf node #s: " . implode(', ', $nnums);
		$amicfg->node = $nnums[0];
		if(count($nnums) > 1)
			$msg[] = "AllScan uses the first node# found in rpt.conf. To use a different node#<br>"
					."reorder the node stanzas in rpt.conf or set the Node Number parameter on the Configs Tab.";
	}
	// Read AMI info from manager.conf
	$f = '/etc/asterisk/manager.conf';
	if(!file_exists($f)) {
		$msg[] = "$f not found";
		return false;
	}
	$mcfg = parse_ini_file($f, true);
	if($mcfg === false) {
		$msg[] = "Error reading $f";
		return false;
	}
	foreach($mcfg as $k => $v) {
		if($k === 'general' && isset($v['port'])) {
			$amicfg->host = empty($v['bindaddr']) ? '127.0.0.1' : $v['bindaddr'];
			$amicfg->port = $v['port'];
			$msg[] = "manager.conf host: $amicfg->host:$amicfg->port";
		} elseif(!isset($amicfg->user) && isset($v['secret'])) {
			$amicfg->user = $k;
			$amicfg->pass = $v['secret'];
		}
	}
	if( empty($amicfg->host) || empty($amicfg->port) ||
		empty($amicfg->user) || empty($amicfg->pass) ) {
		$msg[] = "Valid Asterisk Manager (AMI) definitions not found in $f.<br>"
				."Run asl-menu to configure AMI credentials, or set AMI parameters on Configs Tab";
		return false;
	}
	return true;
}

$astdbtxt = ['astdb.txt', '../supermon/astdb.txt', '/var/log/asterisk/astdb.txt'];

// Read AstDB file, looking in all commonly used locations
function readAstDb(&$msg) {
	global $astdbtxt, $asdbfile2;
	// Check for file in our directory and in Asterisk/Supermon locations
	// If exists in more than one place use the newest. Download it if not found
	$mtime = [0, 0, 0];
	foreach($astdbtxt as $i => $f) {
		if(file_exists($f)) {
			$msg[] = "$f last updated " . date('Y-m-d', filemtime($f));
			if(filesize($f) < 1024) {
				$msg[] = "$f invalid filesize";
			} else {
				$mtime[$i] = filemtime($f);
			}
		}
	}
	arsort($mtime, SORT_NUMERIC);
	if(!reset($mtime)) {
		$msg[] = "No astdb.txt file found. Check that you have asl3-update-astdb service properly installed, "
				.	"or a cron job or other mechanism set up to periodically update the file";
		if(!downloadAstDb($msg))
			return false;
		$file = 'astdb.txt';
	} else {
		$keys = array_keys($mtime);
		$file = $astdbtxt[$keys[0]];
	}
	$msg[] = "Reading $file...";
	$astdb = [];
	$rows = readFileLines($file, $msg);
	if(!$rows) {
		if(!readAsDb($astdb)) {
			return false;
		}
	} else {
		foreach($rows as $row) {
			$arr = explode('|', trim($row));
			if(_count($arr) < 4 || !is_numeric($arr[0]))
				continue;
			$astdb[$arr[0]] = $arr;
		}
		unset($rows);
	}
	$np = readAsDb($astdb);
	$cnt = count($astdb);
	if(!$cnt) {
		$msg[] = "$file invalid. Check file and permissions";
		return false;
	}
	$msg[] = "$cnt Nodes in ASL DB, $np Nodes in $asdbfile2";
	return $astdb;
}

// Below called by astapi files, which should only happen if controller file eg. index.php already called
// getNodeCfg() above which confirms there is a valid file available
function readAstDb2() {
	global $astdbtxt;
	// Check for file in our directory and if not found look in Asterisk/Supermon locations
	// If exists in more than one place use the newest
	$mtime = [0, 0, 0];
	foreach($astdbtxt as $i => $f) {
		if(file_exists($f) && filesize($f) >= 1024) {
			$mtime[$i] = filemtime($f);
		}
	}
	arsort($mtime, SORT_NUMERIC);
	if(!reset($mtime)) {
		$astdb = [];
		readAsDb($astdb);
		return count($astdb) ? $astdb : false;
	}
	$keys = array_keys($mtime);
	$file = $astdbtxt[$keys[0]];
	$rows = readFileLines($file, $msg);
	if(!$rows) {
		return false;
	}
	$astdb = [];
	foreach($rows as $row) {
		$arr = explode('|', trim($row));
		if(_count($arr) < 4 || !is_numeric($arr[0]))
			continue;
		$astdb[$arr[0]] = $arr;
	}
	unset($rows);
	readAsDb($astdb);
	return $astdb;
}

// Below reads a local database file (same format as astdb.txt) that allows private nodes
// to be defined or for the text of existing nodes in astdb.txt to be overridden
function readAsDb(&$astdb) {
	global $asdbfile2;
	$n = 0;
	if(is_array($astdb) && file_exists($asdbfile2)) {
		$rows = [];
		$rows = readFileLines($asdbfile2, $msg);
		if($rows) {
			foreach($rows as $row) {
				$arr = explode('|', trim($row));
				if(_count($arr) < 4 || !is_numeric($arr[0]))
					continue;
				$astdb[$arr[0]] = $arr;
				$n++;
			}
			unset($rows);
		}
	}
	return $n;
}

function downloadAstDb(&$msg) {
	$url = 'http://allmondb.allstarlink.org/';
	$data = @file_get_contents($url);
	if($data !== false) {
		$file = 'astdb.txt';
		if(file_put_contents($file, $data)) {
			$msg[] = "Retrieved and saved $file OK";
			return true;
		}
		$msg[] = error("Error saving ./$file. Check directory permissions");
	} else {
		$msg[] = error("Error retrieving $url");
	}
	return false;
}

function checkDiskSpace(&$msg, $dir='/') {
	$free = disk_free_space($dir);
	$total = disk_total_space($dir);
	if($free) {
		$free = round($free/1073741824, 2);
		$total = round($total/1073741824, 2);
		$p = round(100 * $free / $total, 1);
		if($dir === '/')
			$msg[] = "File system space free $p% ($free/$total GB)";
		else
			$msg[] = "$pct% space free ($free / $total GB) in '$dir'";
	} else {
		$msg[] = "Error reading '$dir' disk free space";
	}
	// Check for log files > 50MB
	$cwd = getcwd();
	$d1 = '/var/log';
	if(chdir($d1) === false) {
		$msg[] = "Unable to cd to $d1";
		return;
	}
	checkLargeFiles($msg, $d1);
	$d1 = '/var/log/asterisk';
	if(chdir($d1) === false) {
		$msg[] = "Unable to cd to $d1";
		chdir($cwd);
		return;
	}
	checkLargeFiles($msg, $d1);
	chdir($cwd);
	// find /tmp -type f -size +50000k -delete
}

function checkLargeFiles(&$msg, $dir) {
	$cmd = "find . -maxdepth 1 -type f -size +50000k";
	$ret = exec($cmd, $out, $res);
	$cnt = count($out);
	if($cnt) {
		$msg[] = "$cnt file(s) > 50MB found in $dir:";
		foreach($out as $f) {
			$size = round(filesize($f)/1048576, 1);
			$f = str_replace('./', '', $f);
			$msg[] = "$f $size MB";
			if((posix_geteuid() === 0) && unlink($f))
				$msg[] = "Deleted $f";
		}
	}
}

function strposa(string $haystack, array $needles, int $offset=0) {
	foreach($needles as $needle) {
		if(strpos($haystack, $needle, $offset) !== false)
			return true;
	}
	return false;
}

function escapeXmlKey($key) {
	$key = str_replace([' ', ',', '/'], ['', '_', '_'], $key);
	if(is_numeric($key))
		$key = '_' . $key;
	return $key;
}
function escapeXmlValue($value) {
	$value = htmlspecialchars($value, ENT_NOQUOTES);
	return $value;
}
function arrayToXml($array, &$xml, $nType) {
	foreach($array as $key => $value) {
		if(is_object($value))
			$value = (array)$value;
		if(is_array($value)) {
			if(is_numeric($key)) {
				$subnode = $xml->addChild($nType);
				$subnode->addAttribute('id', $key);
				arrayToXml($value, $subnode, $nType);
			} else {
				$key = escapeXmlKey($key);
				$subnode = $xml->addChild($key);
				arrayToXml($value, $subnode, $nType);
			}
		} else {
			$key = escapeXmlKey($key);
			$value = escapeXmlValue($value);
			$xml->addChild($key, $value);
		}
	}
}

function outputXmlFile($data, $filename=null) {
	header('Expires: 0');
	header('Cache-Control: private, must-revalidate, post-check=60, pre-check=120');
	if($filename) {
		header('Content-Description: File Transfer');
		header('Content-Disposition: attachment; filename="' . $filename . '"');
	}
	header('Content-Type: text/xml');
	ob_clean();
	flush();
	echo $data;
	exit();
}

function arrayToObj($array, $keys) {
	$obj = new stdClass();
	foreach($keys as $key) {
		if(isset($array[$key])) {
			// Trim leading and trailing whitespace. This function is usually used for processing form submits
			$obj->$key = trim($array[$key]);
		}
	}
	return $obj;
}
function csvToArray($csv) {
	return $csv ? array_map(function($s) { return trim($s, " ;\n\r\t\v\x00"); }, explode(',', $csv)) : [];
}
function arrayToCsv($a) {
	return is_array($a) ? implode(',', $a) : $a;
}

function parseIntList($s) {
	if(!$s || preg_match_all('/(\d+)/', $s, $m) < 1)
		return [];
	return $m[0];
}

function ok($s) {
	global $html;
	if(isset($html))
		return $html->span($s, 'ok') . BR;
	return "$s\n";
}
function error($s) {
	global $html;
	if(isset($html))
		return $html->span($s, 'error') . BR;
	return "ERROR: $s\n";
}
function errMsg($msg, $logToFile=false) {
	echo error($msg);
	if($logToFile)
		logErr($msg);
}
function logErr($msg) {
	echo $msg . NL;
	logToFile($msg);
}
function okMsg($msg) {
	global $html;
	echo $html->p($msg, 'ok');
}
function varDump($var, $return=false) {
	global $html;
	if(is_array($var) || is_object($var))
		$var = print_r($var, true);
	else {
		if(!isset($var))
			$var = '[not set]';
		elseif($var === null)
			$var = '[null]';
		elseif($var === true)
			$var = "[true]";
		elseif($var === false)
			$var = "[false]";
		elseif($var === '')
			$var = "''";
		else
			$var = htmlspecial($var);
	}
	if($return)
		return $var;
	echo $html->pre($var);
}
function getRequestParms() {
	return (empty($_GET) ? $_POST : $_GET);
}
function getScriptName() {
	$name = $_SERVER['SCRIPT_NAME'];
	$name = preg_replace("#/index.php$#", "/", $name);
	return $name;
}
function getRequestURI() {
	return $_SERVER['REQUEST_URI'];
}
function validIpAddr($ipa) {
	if(!$ipa)
		return false;
	return (filter_var($ipa, FILTER_VALIDATE_IP) !== false || $ipa === 'localhost');
}
function validEmail($email) {
	$emailMatchString = "/^\w+[\+\.\w-]*@([\w-]+\.)*\w+[\w-]*\.([a-z]{2,10}|\d+)$/i";
	$valid = (preg_match($emailMatchString, $email) == 1);
	return $valid;
}
function scanCtypes($buf) {
	$ret = new stdClass();
	$ret->nSpaces = $ret->Digits = $ret->nChars = 0;
	$len = strlen($buf);
	for($x=0; $x < $len; $x++) {
		if($buf[$x] == ' ')
			$ret->nSpaces++;
		elseif(ctype_digit($buf[$x]))
			$ret->nDigits++;
		elseif(ctype_alpha($buf[$x]))
			$ret->nChars++;
	}
	return $ret;
}
function checkAsciiChar($c) {
	$c = ord($c);
	if($c < 9 || $c > 126)
		return false;
	if($c > 10 && $c < 32 && $c != 13)
		return false;
	return true;
}
function checkAscii($str) {
	$len = strlen($str);
	for($n=0; $n < $len; $n++) {
		if(!checkAsciiChar($str[$n]))
			return false;
	}
	return true;
}
function validDbID($i) {
	return ($i > 0 && ctype_digit((string)$i));
}
function validUint($i) {
	return ($i >= 0 && ctype_digit((string)$i));
}
function validInt32($i) {
	return ($i >= -32768 && $i <= 32767 && ctype_digit((string)abs($i)));
}

function roundp($val, $prec=0) { // Round while maintaining specified precision
	$out = round($val, $prec);
	if($out == '-0')
		$out = 0;
	if($prec > 0)
		$out = sprintf("%0.{$prec}f", $out);
	return $out;
}
function _count($x) { // Count function that doesn't output warnings for non-arrays
	if(!$x)
		return 0;
	return is_array($x) ? count($x) : 1;
}

function getRemoteAddr() {
	$id = getenv('REMOTE_ADDR');
	if(strlen($id) < 7 || strlen($id) > 39 || preg_match('/[^0-9a-f\.:]/', $id) == 1)
		return null;
	return $id;
}

function readFileLines($fname, &$msg, $bak=false) {
	if(!file_exists($fname)) {
		$msg[] = "$fname not found";
		return false;
	}
	// Read in file and save a copy to .bak extension, verify we have write permission
	$f = file_get_contents($fname);
	if(!$f) {
		$msg[] = error("Read $fname failed. Check directory/file permissions");
		return false;
	}
	if($bak && !file_put_contents("$fname.bak", $f)) {
		$msg[] = error("Write $fname.bak failed. Check directory/file permissions");
		return false;
	}
	/* if($bak && !chmod("$fname.bak", 0664)) {
		$msg[] = "Chmod 0664 $fname.bak failed. Check directory/file permissions";
		return false;
	} */
	return explode(NL, $f);
}

function writeFileLines($fname, $f, &$msg) {
	$f = implode(NL, $f);
	if(!file_put_contents($fname, $f)) {
		$msg[] = error("Write $fname failed. Check directory/file permissions");
		return false;
	}
	/*if(!chmod($fname, 0664)) {
		$msg[] = "Chmod 0664 $fname.new failed. Check directory/file permissions";
		return false;
	}*/
	return true;
}

// Verify INT fields in an object have a numeric value, set to '0' if not.
function checkIntVals(&$o, $k) {
	foreach($k as $p) {
		if(!isset($o->$p) || !is_numeric($o->$p))
			$o->$p = '0';
	}
}

// Escape commas with double quotes, newlines with space, convert objects to arrays
function escapeCsv($data) {
	if(is_array($data)) {
		foreach($data as &$d)
			$d = escapeCsv($d);
	} elseif(is_object($data)) {
		$data = escapeCsv((array)$data);
	} else {
		$data = str_replace(NL, ' ', $data);
		if(strpos($data, ',') !== false) {
			$data = '"' . $data . '"';
		}
	}
	return $data;
}
function outputCsvHeader($filename) {
	// Escape double-quotes. Seems to be the only thing that works for all browsers
	$filename = str_replace('"', "''", $filename);
	header('Expires: 0');
	header('Cache-Control: private, must-revalidate, post-check=60, pre-check=120');
	header('Content-Description: File Transfer');
	header('Content-Disposition: attachment; filename="' . $filename . '"');
	header('Content-Type: text/csv');
	ob_clean();
	flush();
}

function outputTxtHeader($filename) {
	// Escape double-quotes. Seems to be the only thing that works for all browsers
	$filename = str_replace('"', "''", $filename);
	header('Expires: 0');
	header('Cache-Control: private, must-revalidate, post-check=60, pre-check=120');
	header('Content-Description: File Transfer');
	header('Content-Disposition: attachment; filename="' . $filename . '"');
	header("Content-type: text/plain");
	ob_clean();
	flush();
}

// path examples: '/' for allscan root dir, 'user/' for login page, or 'test/x.php' (no leading slash)
function redirect($path='') {
	global $asdir;
	$loc = $asdir ? "/$asdir/$path" : "/$path";
	header("Location: $loc");
	exit();
}

function asDir($local=true) {
	global $wwwroot, $asdir;
	return $local ? ($wwwroot . '/' . $asdir . '/') : '/etc/allscan/';
}

function asExit($errMsg=null) {
	global $html;
	if($errMsg)
		errMsg($errMsg);
	if(isset($html))
		echo "</body>\n</html>\n";
	exit();
}
