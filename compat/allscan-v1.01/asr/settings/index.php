<?php
require_once('../../include/common.php');

$html = new Html();
$msg = [];
asInit($msg);
$db = dbInit();
$userCnt = checkTables($db, $msg);
$cfgModel = new CfgModel($db);
$userModel = new UserModel($db);
$user = $userModel->validate();
if(empty($user) || !isset($user->user_id) || !validDbID($user->user_id) || !adminUser())
	redirect('user/');

const ASR_CONFIG_FILE = '/etc/allscan-reimagined/config.json';
const ASR_DATA_DIR = '/var/lib/allscan-reimagined';
const ASR_DEFAULT_LOGO = '/allscan/asr-logo-bright-r-tight.png';

function asrSettingsReadConfig() {
	$data = is_readable(ASR_CONFIG_FILE) ? json_decode(file_get_contents(ASR_CONFIG_FILE), true) : [];
	if(!is_array($data))
		$data = [];
	if(empty($data['brandByline']))
		$data['brandByline'] = 'by KE7WIL';
	if(empty($data['footerByline']))
		$data['footerByline'] = 'customized by KE7WIL';
	if(empty($data['headerLogo']))
		$data['headerLogo'] = ASR_DEFAULT_LOGO;
	if(empty($data['footerLogo']))
		$data['footerLogo'] = $data['headerLogo'];
	if(empty($data['bridges']) || !is_array($data['bridges']))
		$data['bridges'] = [];
	return $data;
}

function asrSettingsCleanText($value, $max=80) {
	$value = trim(preg_replace('/\s+/', ' ', (string)$value));
	return substr($value, 0, $max);
}

function asrSettingsWriteConfig($config) {
	$dir = dirname(ASR_CONFIG_FILE);
	if(!is_dir($dir) && !mkdir($dir, 0750, true))
		return 'Could not create ' . $dir;
	$tmp = ASR_CONFIG_FILE . '.tmp';
	$json = json_encode($config, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . PHP_EOL;
	if(file_put_contents($tmp, $json, LOCK_EX) === false)
		return 'Could not write temporary config file.';
	chmod($tmp, 0640);
	if(!rename($tmp, ASR_CONFIG_FILE))
		return 'Could not replace config file.';
	return '';
}

function asrSettingsLogoUpload(&$config) {
	if(empty($_FILES['logo']) || ($_FILES['logo']['error'] ?? UPLOAD_ERR_NO_FILE) === UPLOAD_ERR_NO_FILE)
		return '';
	if($_FILES['logo']['error'] !== UPLOAD_ERR_OK)
		return 'Logo upload failed.';
	$name = $_FILES['logo']['name'] ?? '';
	$ext = strtolower(pathinfo($name, PATHINFO_EXTENSION));
	if(!in_array($ext, ['png', 'jpg', 'jpeg', 'webp'], true))
		return 'Logo must be PNG, JPEG, or WebP.';
	if(!is_dir(ASR_DATA_DIR) && !mkdir(ASR_DATA_DIR, 0755, true))
		return 'Could not create logo storage directory.';
	$targetExt = $ext === 'jpeg' ? 'jpg' : $ext;
	$target = ASR_DATA_DIR . '/header-logo.' . $targetExt;
	if(!move_uploaded_file($_FILES['logo']['tmp_name'], $target))
		return 'Could not save uploaded logo.';
	chmod($target, 0644);
	$config['headerLogo'] = '/allscan/asr-custom-logo.' . $targetExt;
	$config['footerLogo'] = $config['headerLogo'];
	return '';
}

function asrSettingsBridgeDefaults() {
	return [
		'dmr' => ['DMR Bridge', 'Connected Clients'],
		'ysf' => ['YSF Bridge', 'Linked Gateways'],
		'zello' => ['Zello Bridge', 'Recent Talkers'],
		'dstar' => ['D-Star Bridge', 'Linked Gateways'],
	];
}

function asrSettingsBridgeMap($bridges) {
	$map = [];
	foreach($bridges as $bridge) {
		if(is_array($bridge) && !empty($bridge['id']))
			$map[$bridge['id']] = $bridge;
	}
	return $map;
}

$config = asrSettingsReadConfig();
$saveError = '';
$saved = false;
if(($_POST['Submit'] ?? '') === 'Save Reimagined Settings') {
	$config['headerTitle'] = asrSettingsCleanText($_POST['headerTitle'] ?? '');
	if($config['headerTitle'] === '')
		$config['headerTitle'] = '{CALLSIGN} | Node {NODE}';
	$config['browserTitle'] = $config['headerTitle'] . ' | ASR';
	$config['brandByline'] = 'by KE7WIL';
	$config['footerByline'] = 'customized by KE7WIL';
	$saveError = asrSettingsLogoUpload($config);
	$bridges = [];
	foreach(asrSettingsBridgeDefaults() as $id => $defaults) {
		if(empty($_POST["bridge_{$id}_enabled"]))
			continue;
		$node = trim((string)($_POST["bridge_{$id}_node"] ?? ''));
		if($node !== '' && !preg_match('/^[0-9]{3,10}$/', $node)) {
			$saveError = strtoupper($id) . ' bridge node must be 3-10 digits.';
			break;
		}
		$bridges[] = [
			'id' => $id,
			'node' => $node,
			'title' => asrSettingsCleanText($_POST["bridge_{$id}_title"] ?? $defaults[0]),
			'detailTitle' => asrSettingsCleanText($_POST["bridge_{$id}_detail"] ?? $defaults[1]),
		];
	}
	$config['bridges'] = $bridges;
	if($saveError === '') {
		$saveError = asrSettingsWriteConfig($config);
		$saved = ($saveError === '');
	}
}

pageInit();
h1('Reimagined Settings');
if($saved)
	okMsg('Reimagined settings saved. Run the Reimagined reapply command if a new logo does not appear immediately.');
if($saveError)
	errMsg($saveError);

$bridgeMap = asrSettingsBridgeMap($config['bridges']);
echo '<form class="asr-settings-form" method="post" enctype="multipart/form-data">';
echo '<fieldset><legend>Branding</legend>';
echo '<label>Header Title<br><input type="text" name="headerTitle" value="' . htmlattr($config['headerTitle'] ?? '') . '"></label>';
echo '<p>Browser tab title is saved automatically as Header Title | ASR.</p>';
echo '<label>Logo Upload<br><input type="file" name="logo" accept="image/png,image/jpeg,image/webp"></label>';
echo '<p>Leave logo blank to keep the current logo.</p>';
echo '</fieldset>';

echo '<fieldset><legend>Bridge Cards</legend>';
foreach(asrSettingsBridgeDefaults() as $id => $defaults) {
	$bridge = $bridgeMap[$id] ?? ['node' => '', 'title' => $defaults[0], 'detailTitle' => $defaults[1]];
	$checked = isset($bridgeMap[$id]) ? ' checked' : '';
	echo '<div class="asr-settings-bridge">';
	echo '<label><input type="checkbox" name="bridge_' . $id . '_enabled" value="1"' . $checked . '> ' . htmlspecial(strtoupper($id)) . '</label>';
	echo '<label>Node<br><input type="text" name="bridge_' . $id . '_node" value="' . htmlattr($bridge['node'] ?? '') . '"></label>';
	echo '<label>Name<br><input type="text" name="bridge_' . $id . '_title" value="' . htmlattr($bridge['title'] ?? $defaults[0]) . '"></label>';
	echo '<label>Detail<br><input type="text" name="bridge_' . $id . '_detail" value="' . htmlattr($bridge['detailTitle'] ?? $defaults[1]) . '"></label>';
	echo '</div>';
}
echo '</fieldset>';
echo '<input type="submit" name="Submit" value="Save Reimagined Settings">';
echo '</form>';

asExit();
