#!/usr/bin/env python3
"""Guard stock AllScan's public index message when no user is authenticated."""

import argparse
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile


DEFAULT_TARGETS = (
    Path("/var/www/html/allscan/index.php"),
    Path("/srv/http/allscan/index.php"),
)
PATCH_MARKER = "ASR Beta 5.9: public requests do not have an authenticated user object"
VULNERABLE = '$msg[] = "User: $user->name, IP: $user->ip_addr";'
SAFE_ASR_WRAPPER = "readfile(__DIR__ . '/index.html');"
REPAIRED = (
    "if(isset($user))\n"
    '    $msg[] = "User: {$user->name}, IP: {$user->ip_addr}";\n'
    "else\n"
    "    // " + PATCH_MARKER + ".\n"
    '    $msg[] = "Public user, IP: " . (getRemoteAddr() ?? "unknown");'
)


class PatchError(RuntimeError):
    pass


def patched_source(source):
    if PATCH_MARKER in source or SAFE_ASR_WRAPPER in source:
        return source, False
    if source.count(VULNERABLE) != 1:
        raise PatchError("known stock AllScan user-status line was not found")
    return source.replace(VULNERABLE, REPAIRED, 1), True


def apply_patch(path):
    source = path.read_text(encoding="utf-8")
    updated, changed = patched_source(source)
    if not changed:
        return False

    current = path.stat()
    backup = path.with_name(path.name + ".pre-asr-beta5.9.bak")
    if not backup.exists():
        shutil.copy2(str(path), str(backup))
        os.chmod(str(backup), 0o600)
        try:
            os.chown(str(backup), current.st_uid, current.st_gid)
        except PermissionError:
            pass

    file_descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".asr-", dir=str(path.parent))
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as temporary:
            temporary.write(updated)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, stat.S_IMODE(current.st_mode))
        try:
            os.chown(temporary_name, current.st_uid, current.st_gid)
        except PermissionError:
            pass
        os.replace(temporary_name, str(path))
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return True


def self_test():
    vulnerable = """<?php
require_once('include/common.php');
$user = $userModel->validate();
$msg[] = \"User: $user->name, IP: $user->ip_addr\";
"""
    repaired, changed = patched_source(vulnerable)
    if not changed or PATCH_MARKER not in repaired or "if(isset($user))" not in repaired:
        raise PatchError("self-test did not repair the vulnerable fixture")
    repaired_again, changed_again = patched_source(repaired)
    if changed_again or repaired_again != repaired:
        raise PatchError("self-test patch is not idempotent")
    wrapper = "<?php\nheader('Content-Type: text/html; charset=utf-8');\n" + SAFE_ASR_WRAPPER + "\n"
    wrapper_again, wrapper_changed = patched_source(wrapper)
    if wrapper_changed or wrapper_again != wrapper:
        raise PatchError("self-test did not recognize the safe ASR index wrapper")
    with tempfile.TemporaryDirectory(prefix="asr-stock-index-test-") as directory:
        target = Path(directory) / "index.php"
        target.write_text(vulnerable, encoding="utf-8")
        target.chmod(0o640)
        if not apply_patch(target):
            raise PatchError("self-test did not patch the temporary stock index")
        backup = target.with_name(target.name + ".pre-asr-beta5.9.bak")
        if not backup.is_file() or stat.S_IMODE(backup.stat().st_mode) != 0o600:
            raise PatchError("self-test did not create a protected stock-index backup")
        if apply_patch(target):
            raise PatchError("self-test file patch is not idempotent")


def default_target():
    for target in DEFAULT_TARGETS:
        if target.is_file():
            return target
    return DEFAULT_TARGETS[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        print("stock AllScan public-index guard self-test: ok")
        return 0
    target = args.target or default_target()
    if not target.is_file():
        print("stock AllScan index.php not present; no patch needed")
        return 3
    try:
        changed = apply_patch(target)
    except (OSError, UnicodeError, PatchError) as error:
        print("stock AllScan index.php left unchanged: " + str(error), file=sys.stderr)
        return 2
    if not changed:
        print("stock AllScan public-index guard already present or not needed")
        return 3
    print("patched stock AllScan public-index user guard")
    return 0


if __name__ == "__main__":
    sys.exit(main())
