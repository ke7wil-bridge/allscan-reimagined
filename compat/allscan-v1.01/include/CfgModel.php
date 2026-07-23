<?php
// CfgModel.php class
// Author: David Gleason - AllScan.info
// AllScan global Configuration Parameter definitions
// Some modules have their own DB table to store data. cfg table stores other general configs
// For simplicty and space efficiency cfgs are stored in the cfg table by a numeric cfgID
// CfgMgr manages the read/write of cfgs to the DB and updating of the $gCfg struct.
// Callers need only reference $gCfg, and call saveCfgs() if they modify a $gCfg value.
define('publicPermission', 1);
define('favsIniLoc', 2);
define('call', 3);
define('location', 4);
define('title', 5);
define('autodisc_def', 6);
define('nodenum', 7);
define('amihost', 8);
define('amiport', 9);
define('amiuser', 10);
define('amipass', 11);
define('cmdbuttons', 12);
define('updatecheck', 13);
if(!defined('ASR_ACCESS_POLICY_FILE'))
	define('ASR_ACCESS_POLICY_FILE', '/etc/allscan-reimagined/config.json');

// Global Cfgs Default Values
$gCfgDef = [
	publicPermission => PERMISSION_READ_ONLY,
	favsIniLoc => ['favorites.ini', '../supermon/favorites.ini', '/etc/allscan/favorites.ini'],
	call => '',
	location => '',
	title => '',
	autodisc_def => 1,
	nodenum => '',
	amihost => '',
	amiport => '',
	amiuser => '',
	amipass => '',
	cmdbuttons => [],
	updatecheck => 1
];

$gCfgName = [
	publicPermission => 'Public Permission',
	favsIniLoc => 'Favorites.ini Locations',
	call => 'Call Sign',
	location => 'Location',
	title => 'Node Title',
	autodisc_def => 'DiscBeforeConn Default',
	nodenum => 'Node Number',
	amihost => 'AMI Host',
	amiport => 'AMI Port',
	amiuser => 'AMI User',
	amipass => 'AMI Pass',
	cmdbuttons => 'Custom Cmd Buttons',
	updatecheck => 'Check For Updates'
];

$publicPermissionVals = [
	PERMISSION_NONE			=> 'None (No Access)',
	PERMISSION_READ_ONLY	=> 'Read Only',
	PERMISSION_READ_MODIFY	=> 'Read/Modify',
	PERMISSION_FULL			=> 'Full'];

$checkboxVals = [0=>'Off', 1=>'On'];

// Value definition arrays for enumerated cfgs. Specify null for plain text/numeric cfgs
$gCfgVals = [
	publicPermission => $publicPermissionVals,
	favsIniLoc => null,
	call => null,
	location => null,
	title => null,
	autodisc_def => $checkboxVals,
	nodenum => null,
	amihost => null,
	amiport => null,
	amiuser => null,
	amipass => null,
	cmdbuttons => null,
	updatecheck => $checkboxVals
];

// Global Cfgs structure
$gCfg = $gCfgDef;
// Last update time of each gCfg (unix tstamp)
$gCfgUpdated = [];

// ASR shares stock users/configuration but keeps public access policy in its
// protected runtime config. Missing or invalid config fails closed.
function asrCfgRequireLogin() {
	global $asdir;
	if($asdir !== 'asr')
		return null;
	$file = ASR_ACCESS_POLICY_FILE;
	if(!is_readable($file))
		return true;
	$config = json_decode((string)file_get_contents($file), true);
	if(!is_array($config) || !array_key_exists('requireLogin', $config))
		return true;
	return !empty($config['requireLogin']);
}

function asrApplyAccessPolicy() {
	global $gCfg;
	$requireLogin = asrCfgRequireLogin();
	if($requireLogin === null)
		return;
	$gCfg[publicPermission] = $requireLogin ? PERMISSION_NONE : PERMISSION_READ_ONLY;
}

// Below functions used to enable/disable site functions based on user and global permission settings
// CfgModel and UserModel classes must be instantiated before below are called.
// If readOk() returns false user is not allowed access to any pages or data.
function readOk() {
	global $user, $gCfg;
	return (isset($gCfg[publicPermission]) && $gCfg[publicPermission] >= PERMISSION_READ_ONLY)
		|| (isset($user) && userPermission() >= PERMISSION_READ_ONLY);
}

function modifyOk() {
	global $user, $gCfg;
	return (isset($gCfg[publicPermission]) && $gCfg[publicPermission] >= PERMISSION_READ_MODIFY)
		|| (isset($user) && userPermission() >= PERMISSION_READ_MODIFY);
}

function writeOk() {
	global $user, $gCfg;
	return (isset($gCfg[publicPermission]) && $gCfg[publicPermission] >= PERMISSION_FULL)
		|| (isset($user) && userPermission() >= PERMISSION_FULL);
}

function adminUser() {
	global $user;
	return (isset($user) && userPermission() >= PERMISSION_ADMIN);
}
function superUser() {
	global $user;
	return (isset($user) && userPermission() >= PERMISSION_SUPERUSER);
}

function cfgCompare($a, $b) {
	if(is_numeric($a) && is_numeric($b))
		return ($a == $b);
	return ($a === $b);
}

class CfgModel {
const	TABLENAME = 'cfg';
public  $db;
public  $error;

function __construct($db) {
	global $msg, $asdbfile;
	$this->db = $db;
	// Read global cfgs
	$this->readCfgs();
	if($this->error) {
		pageInit();
		if(!empty($msg) && is_array($msg))
			echo implode(BR, $msg) . BR;
		msg("Cfg Init failed. $asdbfile may be corrupted. Try copying .bak over .db if exists or delete .db file.");
		asExit($this->error);
	}
}

// Read global cfgs from DB into $gCfg
function readCfgs() {
	global $gCfg, $gCfgDef, $gCfgUpdated;
	$ids = implode(',', array_keys($gCfg));
	$where = "cfg_id IN($ids)";
	$cfgs = $this->getCfgs($where);
	if(empty($cfgs)) {
		asrApplyAccessPolicy();
		return;
	}
	foreach($cfgs as $c) {
		$k = $c->cfg_id;
		$gCfg[$k] = is_array($gCfgDef[$k]) ? explode(',', $c->val) : $c->val;
		//msg("Cfg $k val=" . $gCfg[$k]);
		$gCfgUpdated[$k] = $c->updated;
	}
	asrApplyAccessPolicy();
}

// Save global cfgs. Caller will have updated $gCfg. Loop through cfgs, compare vals to DB & Def Vals
function saveCfgs() {
	global $asdir, $gCfg, $gCfgDef, $gCfgUpdated;
	asrApplyAccessPolicy();
	$ids = array_keys($gCfg);
	if($asdir === 'asr')
		$ids = array_values(array_diff($ids, [publicPermission]));
	$where = "cfg_id IN(" . arrayToCsv($ids). ")";
	$cfgs = $this->getCfgs($where);
	foreach($ids as $k) {
		// If val=DBval nothing to be done
		$val = is_array($gCfgDef[$k]) ? arrayToCsv($gCfg[$k]) : $gCfg[$k];
		if(isset($cfgs[$k])) {
			$cVal = $cfgs[$k]->val;
			$dbVal = is_array($gCfgDef[$k]) ? arrayToCsv($cVal) : $cVal;
		} else {
			$dbVal = null;
		}
		if(cfgCompare($val, $dbVal)) {
			//msg("Cfg $k val=DBval");
			continue;
		}
		// If val=DefVal delete from DB, else write to DB
		$defVal = is_array($gCfgDef[$k]) ? arrayToCsv($gCfgDef[$k]) : $gCfgDef[$k];
		if(cfgCompare($val, $defVal)) {
			//msg("Cfg $k val=defVal");
			if($dbVal !== null) {
				//msg("Deleting Cfg $k");
				$this->delete($k);
				unset($gCfgUpdated[$k]);
			}
		} else {
			// Add if not in DB / Update otherwise
			$c = (object)['cfg_id'=>$k, 'val'=>$val, 'updated'=>$gCfgUpdated[$k]];
			//msg("Cfg $k val!=defVal, updating");
			$this->update($c, !isset($cfgs[$k]));
		}
	}
}

private function getCfgs($where=null, $orderBy=null) {
	$cfgs = $this->db->getRecords(self::TABLENAME, $where, $orderBy);
	$this->checkDbError(__METHOD__);
	if(!_count($cfgs))
		return null;
	// Index by ID
	$a = [];
	foreach($cfgs as $c) {
		// Validate update time
		if($c->updated < 1672964000 || !is_numeric($c->updated))
			$c->updated = 0;
		$a[$c->cfg_id] = $c;
	}
	return $a;
}
private function getCfg($id) {
	if(!validDbID($id))
		return null;
	$where = "cfg_id='$id'";
	$cfgs = $this->getCfgs($where);
	return empty($cfgs) ? null : $cfgs[$id];
}
private function add($c) {
	return $this->update($c, true);
}
private function update($c, $add=false) {
	if(!$add && !validDbID($c->cfg_id))
		return null;
	if(!$this->validateVal($c->val))
		return null;
	$cols = ['cfg_id', 'val', 'updated'];
	$vals = [$c->cfg_id, $c->val, time()];
	if($add) {
		$retval = $this->db->insertRow(self::TABLENAME, $cols, $vals);
	} else {
		$retval = $this->db->updateRow(self::TABLENAME, $cols, $vals, "cfg_id=$c->cfg_id");
	}
	$this->checkDbError(__METHOD__);
	return $retval;
}
private function delete($id) {
	if(!validDbID($id))
		return null;
	$retval = $this->db->deleteRows(self::TABLENAME, "cfg_id=$id");
	$this->checkDbError(__METHOD__);
	return $retval;
}

function getCount($where=null) {
	$retval = $this->db->getRecordCount(self::TABLENAME, $where);
	$this->checkDbError(__METHOD__);
	return $retval;
}

function validateVal($c) {
	if(strlen($c) > 65535) {
		$this->error = 'Invalid Cfg Val. Must be <= 64K chars';
		return false;
	}
	return true;
}

private function checkDbError($method, $extraTxt='') {
	if(isset($this->db->error)) {
		if($extraTxt !== '')
			$extraTxt = "($extraTxt)";
		$this->error = $method . $extraTxt . ': ' . $this->db->error;
		unset($this->db->error);
		return true;
	}
	return false;
}

}
