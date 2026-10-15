<?php
require_once('../include/apiInit.php');
require_once('AMI.php');

// Filter and validate input data before selecting the permission required by
// the requested operation. DTMF is a normal node-control action; restarting
// Asterisk remains restricted to Full users.
$fields = ['button', 'localnode', 'cmd'];
foreach($fields as $f)
	$$f = isset($_POST[$f]) ? trim(strip_tags($_POST[$f])) : '';

switch($button) {
	case 'dtmf':
		if(!modifyOk()) {
			http_response_code(403);
			exit("Insufficient user permission to execute node commands\n");
		}
		if(!preg_match("/^[\d*#,A-Da-d]+$/", $cmd)) {
			http_response_code(400);
			exit("Invalid command value\n");
		}
		break;
	case 'restart':
		if(!writeOk()) {
			http_response_code(403);
			exit("Insufficient user permission to restart Asterisk\n");
		}
		break;
	default:
		http_response_code(400);
		exit("Invalid command\n");
}

if(!$localnode || !preg_match("/^\d+$/", $localnode)) {
	http_response_code(400);
	exit("Invalid local node number\n");
}

chdir('..');

$msg = [];
if(!getAmiCfg($msg)) {
	http_response_code(503);
	exit('AMI credentials not found');
}
if($localnode != $amicfg->node) {
	http_response_code(400);
	exit("Node $localnode not in AMI Cfgs");
}

$ami = new AMI();
$fp = $ami->connect($amicfg->host, $amicfg->port);
if($fp === false) {
	http_response_code(503);
	exit("Could not connect\n");
}

if($ami->login($fp, $amicfg->user, $amicfg->pass) === false) {
	fclose($fp);
	http_response_code(503);
	exit("Could not login\n");
}

if($button === 'dtmf') {
	$message = "Executing cmd \"$cmd\" on $localnode...";
	$ctxt = "rpt fun $localnode $cmd";
} else {
	$message = 'Restarting Asterisk...';
	$ctxt = ($ami->aslver >= 3) ? 'core restart now' : 'restart now';
}

$resp = $ami->command($fp, $ctxt);
fclose($fp);
if($resp === 'Timeout') {
	http_response_code(504);
	exit($message . ' AMI timed out');
}
if($resp === 'ERROR' || $resp === 'Connection closed' || $resp === 'Write failed') {
	http_response_code(502);
	exit($message . ' ' . $resp);
}
echo $message . $resp;
