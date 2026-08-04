#!/usr/bin/env python3
"""Integration tests for HTTP/HTTPS loopback installer validation."""

from __future__ import annotations

import http.server
import ssl
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
VALIDATOR = SCRIPT_DIR / "asr-loopback-validate.py"


class TestHandler(http.server.BaseHTTPRequestHandler):
    https_port = 0
    is_https = False

    def log_message(self, format: str, *args) -> None:
        return

    def send_body(
        self, status: int, content_type: str, body: bytes, location: str = ""
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if not self.is_https and path.startswith("/redirect-"):
            target = f"https://127.0.0.1:{self.https_port}{self.path}"
            self.send_body(301, "text/html", b"redirect", target)
            return
        if not self.is_https and path == "/external":
            self.send_body(
                301, "text/html", b"redirect", "https://example.com/external"
            )
            return
        if not self.is_https and path == "/wrong-path":
            target = f"https://127.0.0.1:{self.https_port}/other"
            self.send_body(301, "text/html", b"redirect", target)
            return
        if self.is_https and path == "/redirect-double":
            target = f"https://127.0.0.1:{self.https_port}{self.path}"
            self.send_body(302, "text/html", b"redirect", target)
            return
        if path in {"/direct-json", "/redirect-json"}:
            self.send_body(
                200,
                "application/json; charset=utf-8",
                b'{"ok":true,"node":"680681"}',
            )
            return
        if path == "/auth-login":
            self.send_body(
                200, "application/json", b'{"ok":true,"publicPermission":0}'
            )
            return
        if path == "/auth-public":
            self.send_body(
                200, "application/json", b'{"ok":true,"publicPermission":2}'
            )
            return
        if path == "/redirect-html":
            self.send_body(
                200,
                "text/html; charset=UTF-8",
                b'<script src="/asr/assets/index-test.js"></script>',
            )
            return
        if path == "/html-as-json":
            self.send_body(200, "text/html", b"<html>wrong vhost</html>")
            return
        if path == "/invalid-json":
            self.send_body(200, "application/json", b"not JSON")
            return
        if path == "/server-error":
            self.send_body(500, "text/plain", b"failure")
            return
        self.send_body(404, "text/html", b"not found")


def start_server(
    handler_type: type[TestHandler], ssl_context: ssl.SSLContext | None = None
) -> tuple[http.server.ThreadingHTTPServer, threading.Thread]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_type)
    if ssl_context is not None:
        server.socket = ssl_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def run_validator(*args: str, succeeds: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    if succeeds and result.returncode != 0:
        raise AssertionError(f"validator unexpectedly failed: {result.stderr}")
    if not succeeds and result.returncode == 0:
        raise AssertionError("validator unexpectedly accepted an invalid response")
    return result


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="asr-loopback-validate-integration."
    ) as temporary:
        root = Path(temporary)
        certificate = root / "certificate.pem"
        private_key = root / "private-key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-subj",
                "/CN=localhost",
                "-keyout",
                str(private_key),
                "-out",
                str(certificate),
                "-days",
                "1",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, private_key)

        class HttpsHandler(TestHandler):
            is_https = True

        https_server, https_thread = start_server(HttpsHandler, context)
        TestHandler.https_port = https_server.server_port
        HttpsHandler.https_port = https_server.server_port
        http_server, http_thread = start_server(TestHandler)

        http_base = f"http://127.0.0.1:{http_server.server_port}"
        https_base = f"https://127.0.0.1:{https_server.server_port}"
        try:
            run_validator("--expect", "json", f"{http_base}/direct-json")
            run_validator("--expect", "json", f"{https_base}/direct-json")
            run_validator("--expect", "json", f"{http_base}/redirect-json")
            run_validator("--expect", "json", f"{http_base}/auth-login")
            run_validator("--expect", "json", f"{http_base}/auth-public")
            run_validator(
                "--expect",
                "html",
                "--contains",
                "assets/index-",
                f"{http_base}/redirect-html",
            )
            for url in (
                f"{http_base}/external",
                f"{http_base}/wrong-path",
                f"{http_base}/redirect-double",
                f"{http_base}/html-as-json",
                f"{http_base}/invalid-json",
                f"{http_base}/missing",
                f"{http_base}/server-error",
            ):
                result = run_validator("--expect", "json", url, succeeds=False)
                assert "scheme=" in result.stderr or "unsafe" in result.stderr
                assert "status=" in result.stderr or "unsafe" in result.stderr
                assert "redirect=" in result.stderr or "unsafe" in result.stderr
                assert "content-type=" in result.stderr or "unsafe" in result.stderr
        finally:
            http_server.shutdown()
            https_server.shutdown()
            http_server.server_close()
            https_server.server_close()
            http_thread.join(timeout=2)
            https_thread.join(timeout=2)

    print("ASR loopback endpoint integration self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
