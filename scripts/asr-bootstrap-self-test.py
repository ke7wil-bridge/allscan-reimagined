#!/usr/bin/env python3
"""Exercise first-install release selection without contacting GitHub or a host install."""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
from unittest import mock
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
source = (ROOT / "bootstrap.sh").read_text()
code = source.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
VERSION = "1.0.0-beta.8"
NAME = "allscan-reimagined-" + VERSION + ".tar.gz"
PREFIX = "https://github.com/ke7wil-bridge/allscan-reimagined/releases/download/v" + VERSION + "/"


def archive_bytes(link=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, value in {
            "install.sh": b"#!/bin/bash\nexit 0\n",
            "package.json": json.dumps({"version": VERSION}).encode(),
            "payload/server/asr-api.php": b"<?php",
            "payload/web/index.html": b"<html></html>",
        }.items():
            data = tarfile.TarInfo("allscan-reimagined-" + VERSION + "/" + name)
            data.size = len(value)
            archive.addfile(data, io.BytesIO(value))
        if link:
            data = tarfile.TarInfo("allscan-reimagined-" + VERSION + "/unsafe")
            data.type = tarfile.SYMTYPE
            data.linkname = "/etc/passwd"
            archive.addfile(data)
    return output.getvalue()


class Response(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}


def run_case(payload, *, wrong_hash=False):
    releases = [{"tag_name": "v" + VERSION, "draft": False, "assets": [
        {"name": NAME, "browser_download_url": PREFIX + NAME},
        {"name": NAME + ".sha256", "browser_download_url": PREFIX + NAME + ".sha256"},
    ]}]
    checksum = ("0" * 64 if wrong_hash else hashlib.sha256(payload).hexdigest())
    def open_url(request, timeout):
        url = request.full_url
        if url.endswith("releases?per_page=20"):
            return Response(json.dumps(releases).encode())
        if url == PREFIX + NAME + ".sha256":
            return Response((checksum + "  " + NAME + "\n").encode())
        if url == PREFIX + NAME:
            return Response(payload)
        raise AssertionError("Unexpected download URL")
    with tempfile.TemporaryDirectory(prefix="asr-bootstrap-test-") as temp:
        with mock.patch.object(urllib.request, "urlopen", side_effect=open_url), mock.patch.object(
                sys, "argv", ["-", temp]), contextlib.redirect_stdout(io.StringIO()) as output:
            exec(compile(code, "bootstrap.sh", "exec"), {"__name__": "__main__"})
        assert output.getvalue().strip() == VERSION
        assert (Path(temp) / ("allscan-reimagined-" + VERSION) / "install.sh").is_file()


def fails(payload, *, wrong_hash=False):
    try:
        run_case(payload, wrong_hash=wrong_hash)
    except ValueError:
        return
    raise AssertionError("Invalid bootstrap release accepted")


run_case(archive_bytes())
fails(archive_bytes(link=True))
fails(archive_bytes(), wrong_hash=True)
print("ASR bootstrap self-test: ok")
