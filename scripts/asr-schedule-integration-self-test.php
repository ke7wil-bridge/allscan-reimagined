#!/usr/bin/php
<?php
require_once(__DIR__ . '/../compat/allscan-v1.06/include/scheduleUtils.php');
function must($condition, $message) { if(!$condition) throw new RuntimeException($message); }

$error = '';
$entry = schedValidate([
	'action'=>'connect','node'=>'12345','time'=>'08:30','days'=>'1,3,5',
	'perm'=>'','autodisc'=>'','duration'=>'45'
], $error);
must(is_array($entry) && $entry['duration'] === 45 && $entry['days'] === [1,3,5], 'duration/day validation failed');
$error = '';
must(schedValidate(['action'=>'connect','node'=>'1','time'=>'08:30','days'=>'','perm'=>'true','duration'=>'10'], $error) === false, 'permanent duration accepted');
$error = '';
must(schedValidate(['action'=>'disconnect','node'=>'1','time'=>'08:30','days'=>'','duration'=>'10'], $error) === false, 'disconnect duration accepted');

date_default_timezone_set('America/New_York');
$first = (new DateTimeImmutable('2026-11-01T01:30:00-04:00'))->getTimestamp();
$second = (new DateTimeImmutable('2026-11-01T01:30:00-05:00'))->getTimestamp();
must(schedWallMinute($first) === schedWallMinute($second), 'fall DST duplicate key differs');
$beforeSpring = (new DateTimeImmutable('2026-03-08 01:59:00', new DateTimeZone('America/New_York')))->modify('+1 minute');
must($beforeSpring->format('H:i') === '03:00', 'spring DST transition assumption changed');

$connect = ['id'=>'00000001','action'=>'connect','node'=>'123','enabled'=>true];
$disconnect = ['id'=>'ffffffff','action'=>'disconnect','node'=>'123','enabled'=>true];
$resolved = schedResolveConflicts([$connect, $disconnect], false);
must(count($resolved) === 1 && $resolved[0]['action'] === 'disconnect', 'disconnect did not win conflict');

echo "ASR schedule integration self-test: ok\n";
