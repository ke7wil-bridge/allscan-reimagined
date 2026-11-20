<?php
// Authenticated standard ASR station details, adapted from ASRPlus.
require_once('../include/common.php');
$msg=[]; asInit($msg); asrInitAuthenticatedUser($msg);
header('Content-Type: application/json; charset=utf-8'); header('Cache-Control: private, max-age=30');
$node=$_GET['node']??''; if(!preg_match('/^[0-9]{3,7}$/D',$node)){http_response_code(400);echo '{}';exit;}
$dir=sys_get_temp_dir().'/asr-standard-node-info';if(!is_dir($dir))@mkdir($dir,0755,true);
$file="$dir/$node.json"; $now=time(); $ttl=300; $cacheVersion=2;
$cached=is_file($file)?@file_get_contents($file):false;
if($cached!==false && $now-filemtime($file)<$ttl && (json_decode($cached,true)["cache_version"]??0)===$cacheVersion){echo $cached;exit;}
// Global, cross-visitor token window: at most 12 upstream requests per 60 seconds.
$lock=@fopen("$dir/throttle.lock",'c+');
if(!$lock || !flock($lock,LOCK_EX|LOCK_NB)) {echo $cached!==false?$cached:json_encode(['node'=>$node,'status'=>'busy','links'=>[]]);exit;}
$stateFile="$dir/throttle.json";$state=json_decode(@file_get_contents($stateFile)?:'[]',true);if(!is_array($state))$state=[];
$state=array_values(array_filter($state,fn($t)=>is_numeric($t)&&$t>$now-60));
if(count($state)>=12){flock($lock,LOCK_UN);fclose($lock);echo $cached!==false?$cached:json_encode(['node'=>$node,'status'=>'rate-limited','links'=>[]]);exit;}
$state[]=$now;@file_put_contents($stateFile,json_encode($state),LOCK_EX);
flock($lock,LOCK_UN);fclose($lock);
$ctx=stream_context_create(['http'=>['timeout'=>4,'ignore_errors'=>true,'header'=>"User-Agent: ASR-Standard-NodeInfo/1.0\r\nAccept: application/json\r\n"]]);
$raw=@file_get_contents('https://stats.allstarlink.org/api/stats/'.$node,false,$ctx);
$code=0;foreach($http_response_header??[] as $h){if(preg_match('/^HTTP\/\S+\s+(\d+)/',$h,$m))$code=(int)$m[1];}
$data=$raw!==false?json_decode($raw,true):null;
if($code===429||$code>=500||!is_array($data)){
 echo $cached!==false?$cached:json_encode(['node'=>$node,'status'=>'unavailable','links'=>[]]);exit;
}
$stats=$data['stats']['data']??[];if(!is_array($stats))$stats=[];
$links=$stats['links']??[];if(!is_array($links))$links=[];
$links=array_values(array_unique(array_filter(array_map('strval',$links),fn($n)=>preg_match('/^[0-9]{3,7}$/D',$n))));
// Do not expose unverified arbitrary upstream fields or personally sensitive data.
$out=['node'=>$node,'status'=>'ok','reported'=>is_array($data['stats']??null),'links'=>array_slice($links,0,250),'cache_version'=>$cacheVersion,'updated'=>gmdate('c'),'source'=>'AllStarLink stats API'];
foreach(['callsign','description','location','name','node','uptime','version'] as $key){if(isset($stats[$key])&&is_scalar($stats[$key]))$out[$key]=substr((string)$stats[$key],0,250);}
// Identity is under top-level node; stats.data primarily contains runtime telemetry.
$identity=$data['node']??[];
if(is_array($identity)){
  foreach(['callsign'=>'callsign','User_ID'=>'callsign','node_frequency'=>'description','node_tone'=>'tone','SiteName'=>'location'] as $from=>$to){
    if(empty($out[$to]) && isset($identity[$from]) && is_scalar($identity[$from]))$out[$to]=substr((string)$identity[$from],0,250);
  }
  if(empty($out['name']) && !empty($identity['node_frequency']))$out['name']=substr((string)$identity['node_frequency'],0,250);
  if(empty($out['location']) && isset($identity['server']) && is_array($identity['server'])){
    foreach(['SiteName','Server_Name'] as $key)if(!empty($identity['server'][$key])){$out['location']=substr((string)$identity['server'][$key],0,250);break;}
  }
}
if(empty($out['uptime']) && isset($stats['apprptuptime']))$out['uptime']=(string)$stats['apprptuptime'].' seconds';
if(empty($out['version']) && isset($stats['apprptvers']))$out['version']=substr((string)$stats['apprptvers'],0,250);
$json=json_encode($out,JSON_INVALID_UTF8_SUBSTITUTE);@file_put_contents($file,$json,LOCK_EX);echo $json;
