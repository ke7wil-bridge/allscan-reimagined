<?php
define('AMI_DEBUG_LOG', 'log.txt');

class AMI {
public $aslver = '2.0/unknown';
private $readTimeout = 5;

function __construct($readTimeout = 5) {
	$this->readTimeout = max(1, (int) $readTimeout);
}

function connect($ip, $port) {
	if(!validIpAddr($ip) || !$port)
		return false;
	$fp = fsockopen($ip, $port, $errno, $errstr, 5);
	if($fp !== false)
		stream_set_timeout($fp, $this->readTimeout);
	return $fp;
}

function login($fp, $user, $password, $detectVersion=true) {
	$actionID = $user . $password;
	fwrite($fp,"ACTION: LOGIN\r\nUSERNAME: $user\r\nSECRET: $password\r\nEVENTS: 0\r\nActionID: $actionID\r\n\r\n");
	$res = $this->getResponse($fp, $actionID);
	// logToFile('RES: ' . varDumpClean($res, true), AMI_DEBUG_LOG);
	$ok = is_array($res) && strpos(implode("\n", $res), "Authentication accepted") !== false;
	if(!$ok)
		return false;
	if($detectVersion)
		$this->detectAslVersion($fp);
	return $ok;
}

function detectAslVersion($fp) {
	// Determine App-rpt version. ASL3 and Asterisk 20 have some differences in AMI commands
	// eg. in ASL2 restart command is "restart now" but in ASL3 it's "core restart now".
	$s = $this->command($fp, 'rpt show version');
	if(preg_match('/app_rpt version: ([0-9\.]{1,9})/', $s, $m) == 1)
		$this->aslver = $m[1];
}

function action($fp, $action, $fields=[]) {
	$actionID = strtolower($action) . mt_rand();
	$request = "ACTION: $action\r\n";
	foreach($fields as $name => $value)
		$request .= "$name: $value\r\n";
	$request .= "ActionID: $actionID\r\n\r\n";
	if(fwrite($fp, $request) === false)
		return 'Write failed';
	return $this->getResponse($fp, $actionID);
}

function coreStartupId($fp) {
	$res = $this->action($fp, 'CoreStatus');
	if(!is_array($res))
		return '';
	$values = [];
	foreach($res as $line) {
		if(preg_match('/^(CoreStartupDate|CoreStartupTime):\s*(.+)$/', $line, $m) == 1)
			$values[$m[1]] = trim($m[2]);
	}
	if(empty($values['CoreStartupDate']) || empty($values['CoreStartupTime']))
		return '';
	return $values['CoreStartupDate'] . 'T' . $values['CoreStartupTime'];
}

function command($fp, $cmdString, $debug=false) {
	// Generate ActionID to associate with response
	$actionID = 'cpAction_' . mt_rand();
	$ok = true;
	$msg = [];
	if((fwrite($fp, "ACTION: COMMAND\r\nCOMMAND: $cmdString\r\nActionID: $actionID\r\n\r\n")) > 0) {
		if($debug)
			logToFile('CMD: ' . $cmdString . ' - ' . $actionID, AMI_DEBUG_LOG);
		$res = $this->getResponse($fp, $actionID, $debug);
		if(!is_array($res))
			return $res;
		// Check for Asterisk AMI Success/Error response
		foreach($res as $r) {
			if($r === 'Response: Error')
				$ok = false;
			elseif(preg_match('/Output: (.*)/', $r, $m) == 1)
				$msg[] = $m[1];
		}
		if(_count($msg))
			return implode(NL, $msg);
		if($ok)
			return 'OK';
		return 'ERROR';
	}
	return "Get node $cmdString failed";
}

function commandOutput($fp, $cmdString, $debug=false) {
	$actionID = 'cpAction_' . mt_rand();
	if((fwrite($fp, "ACTION: COMMAND\r\nCOMMAND: $cmdString\r\nActionID: $actionID\r\n\r\n")) <= 0)
		return "Get node $cmdString failed";
	$res = $this->getResponse($fp, $actionID, $debug);
	if(!is_array($res))
		return $res;
	$ok = true;
	$msg = [];
	foreach($res as $line) {
		if($line === 'Response: Error') {
			$ok = false;
			continue;
		}
		if(preg_match('/^Output:\s?(.*)$/', $line, $match) == 1) {
			if($match[1] !== '' && $match[1] !== '--END COMMAND--')
				$msg[] = $match[1];
			continue;
		}
		if(strpos($line, 'Response: ') === 0
			|| strpos($line, 'ActionID: ') === 0
			|| $line === 'Privilege: Command'
			|| $line === 'Command output follows'
			|| $line === '--END COMMAND--')
			continue;
		$msg[] = $line;
	}
	if(!$ok)
		return 'ERROR';
	if(count($msg))
		return implode(NL, $msg);
	return 'OK';
}

/* 	Example ASL2 AMI response:
		Response: Follows
		Privilege: Command
		ActionID: cpAction_...
		--END COMMAND--
	Example ASL3 AMI response:
		Response: Success
		Command output follows
		Output:
		ActionID: cpAction_...
	=> "Response:" line indicates success of associated ActionID.
*/

function getResponse($fp, $actionID, $debug=false) {
	$ignore = ['Privilege: Command', 'Command output follows'];
	$t0 = time();
	$response = [];
	$sn = getScriptName();
	while(time() - $t0 < 20) {
		$str = fgets($fp);
		if($str === false) {
			$meta = stream_get_meta_data($fp);
			if(!empty($meta['timed_out'])) {
				if($debug)
					logToFile("$sn: Timeout", AMI_DEBUG_LOG);
				return 'Timeout';
			}
			return 'Connection closed';
		}
		$str = trim($str);
		if($str === '')
			continue;
		if($debug)
			logToFile("$sn 1: $str", AMI_DEBUG_LOG);
		if(strpos($str, 'Response: ') === 0) {
			$response[] = $str;
		} elseif($str === "ActionID: $actionID") {
			$response[] = $str;
			while(time() - $t0 < 20) {
				$str = fgets($fp);
				if($str === false) {
					$meta = stream_get_meta_data($fp);
					if(!empty($meta['timed_out']))
						return 'Timeout';
					return 'Connection closed';
				}
				if($str === "\r\n" || $str === "\n")
					return $response;
				$str = trim($str);
				if($str === '' || in_array($str, $ignore))
					continue;
				$response[] = $str;
				if($debug)
					logToFile("$sn 2: $str", AMI_DEBUG_LOG);
			}
		}
	}
	logToFile("$sn: Timeout", AMI_DEBUG_LOG);
	return 'Timeout';
}

}
