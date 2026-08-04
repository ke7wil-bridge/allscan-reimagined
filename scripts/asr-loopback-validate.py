#!/usr/bin/env python3
"""Safely validate ASR loopback HTTP/HTTPS endpoints during installation."""

from __future__ import annotations

import argparse
import ipaddress
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Mapping


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class ValidationError(RuntimeError):
    """A loopback endpoint did not return the expected safe response."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    content_type: str
    location: str
    body: bytes


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def describe(response: Response) -> str:
    return (
        f"scheme={urllib.parse.urlsplit(response.url).scheme or 'unknown'} "
        f"status={response.status} "
        f"redirect={response.location or 'none'} "
        f"content-type={response.content_type or 'none'}"
    )


def read_limited(stream) -> bytes:
    body = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValidationError("response exceeded the 2 MiB validation limit")
    return body


def fetch_once(url: str) -> Response:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not is_loopback_host(parsed.hostname):
        raise ValidationError(f"refusing non-loopback validation URL: {url}")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValidationError(f"refusing unsafe loopback validation URL: {url}")

    # Never send a loopback validation request through an environment proxy.
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.ProxyHandler({}),
        NoRedirect(),
    ]
    if parsed.scheme == "https":
        # Certificate bypass is confined to a URL already proven to be loopback.
        context = ssl._create_unverified_context()
        handlers.append(urllib.request.HTTPSHandler(context=context))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json, text/html;q=0.9"},
        method="GET",
    )

    try:
        with opener.open(request, timeout=15) as result:
            status = int(result.status)
            headers: Mapping[str, str] = result.headers
            body = read_limited(result)
    except urllib.error.HTTPError as error:
        status = int(error.code)
        headers = error.headers
        body = read_limited(error)
    except (OSError, urllib.error.URLError) as error:
        reason = getattr(error, "reason", error)
        raise ValidationError(
            f"loopback request failed: scheme={parsed.scheme} "
            f"status=unavailable redirect=none content-type=none error={reason}"
        ) from error

    return Response(
        url=url,
        status=status,
        content_type=str(headers.get("Content-Type", "")).strip(),
        location=str(headers.get("Location", "")).strip(),
        body=body,
    )


def safe_https_redirect(source: Response) -> str:
    target = urllib.parse.urljoin(source.url, source.location)
    source_parts = urllib.parse.urlsplit(source.url)
    target_parts = urllib.parse.urlsplit(target)
    if (
        source.status not in REDIRECT_STATUSES
        or source_parts.scheme != "http"
        or target_parts.scheme != "https"
        or not is_loopback_host(target_parts.hostname)
        or target_parts.username
        or target_parts.password
        or target_parts.fragment
        or target_parts.path != source_parts.path
        or target_parts.query != source_parts.query
    ):
        raise ValidationError(
            f"unsafe or unsupported redirect: {describe(source)}"
        )
    return target


def fetch_endpoint(url: str) -> tuple[Response, Response | None]:
    first = fetch_once(url)
    if first.status not in REDIRECT_STATUSES:
        return first, None
    if not first.location:
        raise ValidationError(f"redirect had no Location header: {describe(first)}")
    target = safe_https_redirect(first)
    second = fetch_once(target)
    if second.status in REDIRECT_STATUSES:
        raise ValidationError(
            f"multiple redirects are not permitted: first=({describe(first)}) "
            f"second=({describe(second)})"
        )
    return second, first


def validate_response(
    response: Response, expected: str, contains: str = ""
) -> None:
    if response.status != 200:
        raise ValidationError(f"endpoint validation failed: {describe(response)}")

    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if expected == "json":
        if media_type != "application/json":
            raise ValidationError(
                f"expected JSON response: {describe(response)}"
            )
        try:
            payload = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValidationError(
                f"invalid JSON response: {describe(response)}"
            ) from error
        if not isinstance(payload, dict):
            raise ValidationError(
                f"expected a JSON object: {describe(response)}"
            )
    else:
        if media_type != "text/html":
            raise ValidationError(
                f"expected HTML response: {describe(response)}"
            )

    if contains and contains.encode("utf-8") not in response.body:
        raise ValidationError(
            f"response did not contain {contains!r}: {describe(response)}"
        )


def validate_endpoint(url: str, expected: str, contains: str = "") -> bytes:
    response, redirect = fetch_endpoint(url)
    try:
        validate_response(response, expected, contains)
    except ValidationError as error:
        if redirect is None:
            raise
        raise ValidationError(
            f"{error}; initial redirect=({describe(redirect)})"
        ) from error
    return response.body


def self_test() -> None:
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    assert is_loopback_host("localhost")
    assert not is_loopback_host("example.com")

    source = Response(
        "http://127.0.0.1/asr/asr-api.php?action=runtime-config",
        301,
        "text/html",
        "https://127.0.0.1/asr/asr-api.php?action=runtime-config",
        b"",
    )
    assert safe_https_redirect(source).startswith("https://127.0.0.1/")

    for unsafe in (
        "https://example.com/asr/asr-api.php?action=runtime-config",
        "https://127.0.0.1/other",
        "http://127.0.0.1/asr/asr-api.php?action=runtime-config",
    ):
        candidate = Response(
            source.url, 301, "text/html", unsafe, b""
        )
        try:
            safe_https_redirect(candidate)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"unsafe redirect was accepted: {unsafe}")

    valid_json = Response(
        "https://127.0.0.1/asr/asr-api.php?action=runtime-config",
        200,
        "application/json; charset=utf-8",
        "",
        b'{"ok":true,"node":"680681"}',
    )
    validate_response(valid_json, "json")
    valid_html = Response(
        "https://127.0.0.1/asr/",
        200,
        "text/html; charset=UTF-8",
        "",
        b'<script src="/asr/assets/index-test.js"></script>',
    )
    validate_response(valid_html, "html", "assets/index-")

    for invalid in (
        Response(valid_json.url, 200, "text/html", "", b"<html></html>"),
        Response(valid_json.url, 200, "application/json", "", b"not json"),
        Response(valid_json.url, 403, "application/json", "", b'{"ok":false}'),
    ):
        try:
            validate_response(invalid, "json")
        except ValidationError:
            pass
        else:
            raise AssertionError("invalid endpoint response was accepted")

    print("ASR loopback endpoint validation self-test: ok")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--expect", choices=("json", "html"))
    parser.add_argument("--contains", default="")
    parser.add_argument("url", nargs="?")
    args = parser.parse_args()
    if not args.self_test and (not args.expect or not args.url):
        parser.error("--expect and URL are required")
    return args


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        body = validate_endpoint(args.url, args.expect, args.contains)
    except ValidationError as error:
        print(f"ASR loopback validation error: {error}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
