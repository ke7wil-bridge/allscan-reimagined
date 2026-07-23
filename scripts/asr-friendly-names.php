#!/usr/bin/env php
<?php
const ASR_CONFIG_FILE = '/etc/allscan-reimagined/config.json';
const ASR_LOCAL_DB_FILE = '/etc/allscan/asdb.txt';
const ASR_FRIENDLY_NAME_FILES = [
    ASR_LOCAL_DB_FILE,
    '/var/www/html/asr/astdb.txt',
    '/srv/http/asr/astdb.txt',
    '/var/www/html/supermon/astdb.txt',
    '/var/log/asterisk/astdb.txt',
];

if (($argv[1] ?? '') === '--self-test') {
    foreach (ASR_FRIENDLY_NAME_FILES as $file) {
        if (strpos($file, '/var/www/html/allscan/') === 0 || strpos($file, '/srv/http/allscan/') === 0) {
            throw new RuntimeException('Friendly-name helper targets the stock AllScan web root.');
        }
    }
    if (!in_array('/var/www/html/asr/astdb.txt', ASR_FRIENDLY_NAME_FILES, true)) {
        throw new RuntimeException('Friendly-name helper does not target the ASR web root.');
    }
    echo "friendly-name stock-isolation self-test: ok" . PHP_EOL;
    exit(0);
}

$config = is_readable(ASR_CONFIG_FILE)
    ? json_decode((string) file_get_contents(ASR_CONFIG_FILE), true)
    : null;
if (!is_array($config) || (empty($config['maintainFriendlyNames']) && (($argv[1] ?? '') !== '--once'))) {
    exit(0);
}

$bridges = [];
foreach ((array) ($config['bridges'] ?? []) as $bridge) {
    if (!is_array($bridge)) continue;
    $node = trim((string) ($bridge['node'] ?? ''));
    $title = trim((string) ($bridge['friendlyName'] ?? ''));
    if ($title === '') $title = trim((string) ($bridge['title'] ?? ''));
    if (!preg_match('/^[0-9]{3,10}$/', $node) || $title === '') continue;
    $title = preg_replace('/[\x00-\x1F\x7F|]/', ' ', $title);
    $title = trim(preg_replace('/\s+/', ' ', $title));
    if ($title === '') continue;
    $bridges[$node] = $title;
}

if (!$bridges) {
    exit(0);
}

foreach (ASR_FRIENDLY_NAME_FILES as $file) {
    if (!file_exists($file)) {
        if ($file !== ASR_LOCAL_DB_FILE) continue;
        if (!is_dir(dirname($file)) && !@mkdir(dirname($file), 0775, true)) continue;
        $raw = [];
    } else {
        if (!is_readable($file)) continue;
        $raw = file($file, FILE_IGNORE_NEW_LINES);
        if ($raw === false) continue;
    }

    $lines = [];
    foreach ($raw as $line) {
        $node = strtok($line, '|');
        if ($node !== false && isset($bridges[$node])) continue;
        $lines[] = $line;
    }

    foreach ($bridges as $node => $title) {
        $lines[] = "{$node}|{$title}||";
    }

    $data = implode(PHP_EOL, $lines) . PHP_EOL;
    $tmp = $file . '.asr-tmp.' . getmypid();
    if (file_put_contents($tmp, $data) === false) {
        @unlink($tmp);
        continue;
    }
    $perms = fileperms($file);
    if ($perms !== false) @chmod($tmp, $perms & 0777);
    else @chmod($tmp, 0664);
    if (!@rename($tmp, $file)) {
        @unlink($tmp);
        continue;
    }
    if ($file === ASR_LOCAL_DB_FILE) {
        @chown($file, 'root');
        @chgrp($file, webGroup());
        @chmod($file, 0664);
    }
}

function webGroup(): string
{
    foreach (['www-data', 'apache', 'http'] as $group) {
        if (function_exists('posix_getgrnam') && posix_getgrnam($group) !== false) {
            return $group;
        }
    }
    return 'root';
}
