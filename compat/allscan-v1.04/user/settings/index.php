<?php
// Compatibility entry point. My Account now lives in the canonical Settings shell.
require_once('../../include/common.php');
$status = ($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'POST' ? 307 : 302;
header('Location: ' . rtrim((string)$urlbase, '/') . '/asr-settings/?section=account', true, $status);
exit();
