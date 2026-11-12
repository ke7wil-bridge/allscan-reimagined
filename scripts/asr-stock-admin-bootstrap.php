<?php
// Installer-only CLI helper. Password arrives on stdin, never argv or disk.
if (PHP_SAPI !== 'cli' || count($argv) !== 3) exit(2);
[$script, $webRoot, $stockDir] = $argv;
$_SERVER['DOCUMENT_ROOT'] = $webRoot;
$_SERVER['SCRIPT_NAME'] = '/allscan/index.php';
chdir($stockDir);
require_once 'include/common.php';
$msg = [];
asInit($msg);
$db = dbInit();
checkTables($db, $msg);
$users = new UserModel($db);
$admins = $users->getUsers(null, null, PERMISSION_ADMIN);
if ($users->error) exit(3);
if (is_array($admins) && count($admins)) {
    fwrite(STDERR, "An administrator already exists; no account created.\n");
    exit(4);
}
$name = rtrim((string) fgets(STDIN), "\r\n");
$password = rtrim((string) fgets(STDIN), "\r\n");
if (!$users->validateName($name) || !$users->validatePassword($password)) {
    fwrite(STDERR, "Invalid administrator name or password (stock AllScan rules).\n");
    exit(5);
}
if ($users->getUsers("name='" . $name . "'")) {
    fwrite(STDERR, "Account name already exists; choose another.\n");
    exit(6);
}
if ($users->error) exit(3);
$u = (object) [
    'name' => $name, 'pass' => $password, 'email' => '',
    'location' => '', 'nodenums' => [], 'permission' => PERMISSION_ADMIN,
    'timezone_id' => 0, 'last_login' => 0,
];
$created = $users->add($u);
unset($password, $u->pass);
if (!$created || $users->error) {
    fwrite(STDERR, "Administrator creation failed.\n");
    exit(7);
}
$admins = $users->getUsers(null, null, PERMISSION_ADMIN);
if (!is_array($admins) || !count($admins)) exit(8);
echo "Stock AllScan administrator created.\n";
