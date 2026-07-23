#!/usr/bin/env python3
"""Repair the known TGIF Socket.IO reconnect leak in a companion client daemon."""

import argparse
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile


DEFAULT_TARGET = Path("/usr/local/sbin/connected-clients-daemon.py")
PATCH_MARKER = "ASR Beta 5.6: close every failed TGIF Socket.IO client"


class PatchError(RuntimeError):
    pass


def patched_source(source):
    function_marker = "def socket_thread():\n    if not TGIF_TOKEN:"
    thread_start_marker = "\n\nthreading.Thread(target=socket_thread, daemon=True).start()"
    start = source.rfind(function_marker)
    if start < 0:
        raise PatchError("compatible authenticated socket_thread() was not found")
    end = source.find(thread_start_marker, start)
    if end < 0:
        raise PatchError("socket_thread() end marker was not found")

    block = source[start:end]
    old_client = (
        "    while True:\n"
        "        try:\n"
        "            sio = socketio.Client(logger=False, engineio_logger=False, reconnection=True)"
    )
    new_client = (
        "    while True:\n"
        "        sio = None\n"
        "        try:\n"
        "            # " + PATCH_MARKER + " before retrying.\n"
        "            sio = socketio.Client(logger=False, engineio_logger=False, reconnection=False)"
    )
    old_session_loop = (
        "            while True:\n"
        "                sio.emit(\"cli-state\", {\"scope\": int(TGIF_TG), \"page\": \"tgadmin\", \"action\": \"sessions\"})\n"
        "                time.sleep(15)"
    )
    new_session_loop = (
        "            while sio.connected:\n"
        "                sio.emit(\"cli-state\", {\"scope\": int(TGIF_TG), \"page\": \"tgadmin\", \"action\": \"sessions\"})\n"
        "                time.sleep(15)"
    )
    old_error = (
        "        except Exception as e:\n"
        "            print(\"TGIF session error:\", e, flush=True)\n"
            "            time.sleep(5)"
    )
    required_repair = (
        PATCH_MARKER,
        "sio = None",
        "reconnection=False",
        "while sio.connected:",
        "finally:",
        "if sio is not None:",
        "sio.disconnect()",
        'getattr(sio, "shutdown", None)',
    )
    if PATCH_MARKER in block:
        if (
            all(item in block for item in required_repair)
            and old_client not in block
            and old_session_loop not in block
            and old_error not in block
        ):
            return source, False
        raise PatchError("reconnect patch marker exists but the repair is incomplete")
    new_error = (
        "        except Exception as e:\n"
        "            print(\"TGIF session error:\", e, flush=True)\n"
        "        finally:\n"
        "            if sio is not None:\n"
        "                try:\n"
        "                    sio.disconnect()\n"
        "                except Exception:\n"
        "                    pass\n"
        "                shutdown = getattr(sio, \"shutdown\", None)\n"
        "                if callable(shutdown):\n"
        "                    try:\n"
        "                        shutdown()\n"
        "                    except Exception:\n"
        "                        pass\n"
        "            time.sleep(5)"
    )

    replacements = (
        (old_client, new_client, "Socket.IO client construction"),
        (old_session_loop, new_session_loop, "TGIF session loop"),
        (old_error, new_error, "TGIF retry cleanup"),
    )
    for old, new, description in replacements:
        if block.count(old) != 1:
            raise PatchError(description + " did not match the known vulnerable daemon")
        block = block.replace(old, new, 1)

    updated = source[:start] + block + source[end:]
    compile(updated, "connected-clients-daemon.py", "exec")
    return updated, True


def apply_patch(path):
    source = path.read_text(encoding="utf-8")
    updated, changed = patched_source(source)
    if not changed:
        return False

    current = path.stat()
    backup = path.with_name(path.name + ".pre-asr-beta5.6")
    if not backup.exists():
        shutil.copy2(str(path), str(backup))
        try:
            os.chown(str(backup), current.st_uid, current.st_gid)
        except PermissionError:
            pass
    os.chmod(str(backup), 0o600)

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".asr-", dir=str(path.parent)
    )
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
    vulnerable = '''import socketio
import threading
import time
TGIF_TOKEN = "test"
TGIF_TG = "123"

def socket_thread():
    if not TGIF_TOKEN:
        print("TGIF_API_TOKEN is not set; DMR connected sessions will be empty", flush=True)
        return

    while True:
        try:
            sio = socketio.Client(logger=False, engineio_logger=False, reconnection=True)
            sio.connect("https://tgif.network", transports=["websocket", "polling"], wait_timeout=10)

            while True:
                sio.emit("cli-state", {"scope": int(TGIF_TG), "page": "tgadmin", "action": "sessions"})
                time.sleep(15)

        except Exception as e:
            print("TGIF session error:", e, flush=True)
            time.sleep(5)

threading.Thread(target=socket_thread, daemon=True).start()
'''
    repaired, changed = patched_source(vulnerable)
    if not changed:
        raise PatchError("self-test did not patch the vulnerable fixture")
    required = (
        PATCH_MARKER,
        "sio = None",
        "reconnection=False",
        "while sio.connected:",
        "finally:",
        "if sio is not None:",
        "sio.disconnect()",
        'getattr(sio, "shutdown", None)',
    )
    if not all(item in repaired for item in required):
        raise PatchError("self-test repaired fixture is incomplete")
    repaired_again, changed_again = patched_source(repaired)
    if changed_again or repaired_again != repaired:
        raise PatchError("self-test patch is not idempotent")
    marker_only = vulnerable.replace(
        "    while True:\n        try:\n",
        "    while True:\n        # " + PATCH_MARKER + "\n        try:\n",
        1,
    )
    try:
        patched_source(marker_only)
    except PatchError as error:
        if "repair is incomplete" not in str(error):
            raise
    else:
        raise PatchError("self-test trusted an incomplete reconnect marker")

    with tempfile.TemporaryDirectory(prefix="asr-connected-clients-test-") as directory:
        target = Path(directory) / "connected-clients-daemon.py"
        target.write_text(vulnerable, encoding="utf-8")
        target.chmod(0o750)
        if not apply_patch(target):
            raise PatchError("self-test did not patch the temporary daemon")
        backup = target.with_name(target.name + ".pre-asr-beta5.6")
        if not backup.is_file() or backup.read_text(encoding="utf-8") != vulnerable:
            raise PatchError("self-test did not preserve the original daemon")
        if stat.S_IMODE(backup.stat().st_mode) != 0o600:
            raise PatchError("self-test daemon backup is not protected")
        if stat.S_IMODE(target.stat().st_mode) != 0o750:
            raise PatchError("self-test changed the daemon executable mode")
        if apply_patch(target):
            raise PatchError("self-test temporary-file patch is not idempotent")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        print("connected-clients reconnect patch self-test: ok")
        return 0
    if not args.target.is_file():
        print("connected-clients daemon not present; no patch needed")
        return 3
    try:
        changed = apply_patch(args.target)
    except (OSError, UnicodeError, PatchError) as error:
        print("connected-clients daemon left unchanged: " + str(error), file=sys.stderr)
        return 2
    if not changed:
        print("connected-clients daemon reconnect cleanup already present")
        return 3
    print("patched connected-clients daemon TGIF reconnect cleanup")
    return 0


if __name__ == "__main__":
    sys.exit(main())
