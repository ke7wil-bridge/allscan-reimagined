#!/usr/bin/env python3
"""Isolated CLI bootstrap regression checks; never touches host AllScan."""
import json
import pathlib
import subprocess
import tempfile

root = pathlib.Path(__file__).resolve().parent.parent
helper = root / 'scripts/asr-stock-admin-bootstrap.php'
model = root / 'compat/allscan-v1.05/include/UserModel.php'
with tempfile.TemporaryDirectory(prefix='asr-admin-qa-') as tmp:
    base = pathlib.Path(tmp)
    stock = base / 'allscan'
    (stock / 'include').mkdir(parents=True)
    (stock / 'include' / 'UserModel.php').write_bytes(model.read_bytes())
    (stock / 'include' / 'common.php').write_text('''<?php
require_once 'UserModel.php';
function asInit(&$msg) {}
function dbInit() {return new FakeDb();}
function checkTables($db, &$msg) {}
function _count($x) {return is_array($x) ? count($x) : 0;}
function csvToArray($x) {return $x ? explode(',', $x) : [];}
function arrayToCsv($x) {return implode(',', $x);}
function checkIntVals($x, $fields) {}
class FakeDb {
  public $error;
  private $path;
  function __construct() {$this->path = getenv('ASR_TEST_DB');}
  function getRecords($table, $where=null, $orderBy=null) {
    $rows = json_decode(file_get_contents($this->path));
    if ($where && preg_match('/permission >= (\\d+)/', $where, $m))
      $rows = array_values(array_filter($rows, fn($u) => $u->permission >= (int)$m[1]));
    if ($where && preg_match("/name='([^']+)'/", $where, $m))
      $rows = array_values(array_filter($rows, fn($u) => $u->name === $m[1]));
    return $rows;
  }
  function insertRow($table, $cols, $vals) {
    $rows = json_decode(file_get_contents($this->path));
    $row = (object)array_combine($cols, $vals);
    $row->last_login = 0;
    $rows[] = $row;
    file_put_contents($this->path, json_encode($rows));
    return 1;
  }
}
''')
    import os
    db = base / 'db.json'
    env = dict(os.environ, ASR_TEST_DB=str(db))
    def invoke(data):
        return subprocess.run(['php', str(helper), str(base), str(stock)], input=data,
                              text=True, capture_output=True, env=env, timeout=10)
    def records():
        return json.loads(db.read_text())
    db.write_text('[]')
    assert invoke('TESTADMIN\ninvalid password\n').returncode == 5
    assert records() == []
    assert invoke('TESTADMIN\nValidPass123!\n').returncode == 0
    rows = records()
    assert len(rows) == 1 and rows[0]['permission'] == 10
    assert rows[0]['hash'].startswith('$2y$') and 'ValidPass123!' not in db.read_text()
    assert invoke('ANOTHER\nValidPass123!\n').returncode == 4
    assert len(records()) == 1
    db.write_text(json.dumps([dict(name='EXISTING', permission=2, last_login=0, nodenums='', hash='old')]))
    assert invoke('EXISTING\nValidPass123!\n').returncode == 6
    assert invoke('ADMIN2\nValidPass123!\n').returncode == 0
    assert len(records()) == 2
    db.write_text(json.dumps([dict(name='OLDADMIN', permission=14, last_login=0, nodenums='', hash='preserved')]))
    assert invoke('NEWADMIN\nValidPass123!\n').returncode == 4
    assert records()[0]['hash'] == 'preserved'
    db.write_text('[]')
    assert invoke('PARTIAL\n').returncode == 5
    assert records() == []
    assert invoke('BAD\nshort\n').returncode == 5
    assert invoke('BAD\nValidPass123!\n').returncode == 0
    assert invoke('BAD\nValidPass123!\n').returncode == 4
    print('Stock administrator bootstrap isolated self-test: ok')
