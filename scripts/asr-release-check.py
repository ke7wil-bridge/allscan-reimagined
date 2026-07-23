#!/usr/bin/env python3
"""Cache the newest published ASR release for the read-only web API."""

from __future__ import annotations

import grp
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ASR_INSTALLED_VERSION = "1.0.0-beta.6"
ASR_INSTALLED_LABEL = "v1.0.0 Beta 6"
ASR_RELEASES_API = (
    "https://api.github.com/repos/ke7wil-bridge/allscan-reimagined/releases?per_page=20"
)
ASR_RELEASE_CACHE = Path(
    "/run/allscan-reimagined/release-check/release-status.json"
)
ASR_REPOSITORY_URL_PREFIX = (
    "https://github.com/ke7wil-bridge/allscan-reimagined/"
)
VERSION_PATTERN = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)(?:-([a-z]+)[.-]?([0-9]+(?:\.[0-9]+)*))?$",
    re.IGNORECASE,
)


def version_parts(version: str) -> tuple[tuple[int, int, int], str, tuple[int, ...]] | None:
    normalized = re.sub(r"^v", "", version.strip(), flags=re.IGNORECASE)
    match = VERSION_PATTERN.fullmatch(normalized)
    if not match:
        return None
    return (
        (int(match.group(1)), int(match.group(2)), int(match.group(3))),
        (match.group(4) or "").lower(),
        tuple(int(part) for part in (match.group(5) or "").split(".") if part),
    )


def normalize_version(version: str) -> str:
    return re.sub(r"^v", "", version.strip(), flags=re.IGNORECASE)


def version_key(version: str) -> tuple[Any, ...] | None:
    parts = version_parts(version)
    if parts is None:
        return None
    core, channel, sequence = parts
    channel_ranks = {"dev": 0, "alpha": 1, "beta": 2, "rc": 3}
    # A stable release sorts after any prerelease of the same core version.
    stable = 1 if channel == "" else 0
    return (*core, stable, channel_ranks.get(channel, 0), sequence)


def is_newer(left: str, right: str) -> bool:
    left_key = version_key(left)
    right_key = version_key(right)
    if left_key is None or right_key is None:
        return left > right
    return left_key > right_key


def version_label(version: str) -> str:
    normalized = normalize_version(version)
    match = re.fullmatch(
        r"(.+)-(alpha|beta|rc)[.-]?([0-9]+(?:\.[0-9]+)*)",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return f"v{normalized}"
    return f"v{match.group(1)} {match.group(2).title()} {match.group(3)}"


def http_get(
    url: str, accept: str, timeout: int = 12, max_bytes: int = 4 * 1024 * 1024
) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": f"AllScan-Reimagined/{ASR_INSTALLED_VERSION} release-check",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError("Release response exceeded the size limit.")
        return body


def select_release(releases: list[Any]) -> dict[str, Any] | None:
    selected: dict[str, Any] | None = None
    selected_version = ""
    for candidate in releases:
        if not isinstance(candidate, dict) or candidate.get("draft"):
            continue
        version = normalize_version(str(candidate.get("tag_name", "")))
        if version_key(version) is None:
            continue
        if selected is None or is_newer(version, selected_version):
            selected = candidate
            selected_version = version
    if selected is None:
        return None
    return {**selected, "_asr_version": selected_version}


def release_asset(release: dict[str, Any], name: str) -> dict[str, Any]:
    for candidate in release.get("assets", []):
        if not isinstance(candidate, dict) or str(candidate.get("name", "")) != name:
            continue
        url = str(candidate.get("browser_download_url", ""))
        if url.startswith(ASR_REPOSITORY_URL_PREFIX):
            return {
                "name": name,
                "url": url,
                "size": max(0, int(candidate.get("size", 0) or 0)),
            }
    return {}


def release_checksum(release: dict[str, Any], package_name: str) -> str:
    asset = release_asset(release, f"{package_name}.sha256")
    if not asset:
        return ""
    try:
        body = http_get(
            str(asset["url"]), "text/plain", 10, max_bytes=65536
        ).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        return ""
    for line in body.splitlines():
        match = re.fullmatch(
            r"\s*([a-f0-9]{64})\s+\*?(.+?)\s*",
            line,
            flags=re.IGNORECASE,
        )
        if match and Path(match.group(2)).name == package_name:
            return match.group(1).lower()
    return ""


def build_payload(release: dict[str, Any], checksum: str = "") -> dict[str, Any]:
    version = str(release.get("_asr_version", ""))
    release_url = str(release.get("html_url", ""))
    if not release_url.startswith(ASR_REPOSITORY_URL_PREFIX):
        release_url = ""
    package_name = f"allscan-reimagined-{version}.tar.gz"
    package = release_asset(release, package_name)
    update_available = is_newer(version, ASR_INSTALLED_VERSION)
    return {
        "ok": True,
        "status": "update_available" if update_available else "up_to_date",
        "updateAvailable": update_available,
        "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "installedVersion": ASR_INSTALLED_VERSION,
        "installedLabel": ASR_INSTALLED_LABEL,
        "availableVersion": version,
        "availableLabel": version_label(version),
        "releaseUrl": release_url,
        "publishedAt": str(release.get("published_at", "")),
        "package": {
            "name": str(package.get("name", "")),
            "url": str(package.get("url", "")),
            "size": int(package.get("size", 0) or 0),
            "sha256": checksum.lower() if package else "",
        },
    }


def web_group() -> int:
    requested = os.environ.get("ASR_WEB_GROUP", "")
    for name in dict.fromkeys([requested, "www-data", "apache", "http"]):
        if not name:
            continue
        try:
            return grp.getgrnam(name).gr_gid
        except KeyError:
            continue
    return -1


def write_cache(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    group_id = web_group()
    if group_id >= 0:
        os.chown(path.parent, -1, group_id)
    os.chmod(path.parent, 0o750)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".release-status.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, separators=(",", ": "))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if group_id >= 0:
            os.chown(temporary, -1, group_id)
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def self_test() -> None:
    global http_get, web_group

    assert is_newer("1.0.0-beta.5.11", "1.0.0-beta.5.10")
    assert is_newer("1.0.0-beta.6", "1.0.0-beta.5.11")
    assert is_newer("1.0.0", "1.0.0-beta.99")
    assert not is_newer("1.0.0-beta.5.11", "1.0.0-beta.5.11")
    assert not is_newer("1.0.0-beta.5.10", "1.0.0-beta.5.11")
    assert version_label("1.0.0-beta.5.11") == "v1.0.0 Beta 5.11"

    fixture = [
        {"tag_name": "v1.0.0-beta.5.11", "draft": False},
        {"tag_name": "v1.0.0-beta.6", "draft": False},
        {"tag_name": "v1.0.0-beta.6.1", "draft": False},
        {"tag_name": "v9.0.0", "draft": True},
        {"tag_name": "../../invalid", "draft": False},
    ]
    selected = select_release(fixture)
    assert selected is not None
    assert selected["_asr_version"] == "1.0.0-beta.6.1"
    package_name = "allscan-reimagined-1.0.0-beta.6.1.tar.gz"
    selected.update(
        {
            "html_url": (
                "https://github.com/ke7wil-bridge/allscan-reimagined/"
                "releases/tag/v1.0.0-beta.6.1"
            ),
            "published_at": "2026-07-23T12:00:00Z",
            "assets": [
                {
                    "name": package_name,
                    "browser_download_url": (
                        "https://github.com/ke7wil-bridge/allscan-reimagined/"
                        f"releases/download/v1.0.0-beta.6.1/{package_name}"
                    ),
                    "size": 6200000,
                },
                {
                    "name": f"{package_name}.sha256",
                    "browser_download_url": (
                        "https://github.com/ke7wil-bridge/allscan-reimagined/"
                        f"releases/download/v1.0.0-beta.6.1/{package_name}.sha256"
                    ),
                    "size": 96,
                },
            ],
        }
    )
    update_payload = build_payload(selected, "a" * 64)
    assert update_payload["updateAvailable"] is True
    assert update_payload["availableLabel"] == "v1.0.0 Beta 6.1"
    assert update_payload["package"]["name"] == package_name
    assert update_payload["package"]["sha256"] == "a" * 64
    untrusted = {
        "assets": [
            {
                "name": package_name,
                "browser_download_url": f"https://example.invalid/{package_name}",
                "size": 1,
            }
        ]
    }
    assert release_asset(untrusted, package_name) == {}

    original_http_get = http_get
    checksum_text = (
        f"{'b' * 64}  unrelated.tar.gz\n"
        f"{'a' * 64}  {package_name}\n"
    ).encode()
    http_get = lambda *_args, **_kwargs: checksum_text
    try:
        assert release_checksum(selected, package_name) == "a" * 64
    finally:
        http_get = original_http_get

    with tempfile.TemporaryDirectory(prefix="asr-release-self-test.") as directory:
        cache = Path(directory) / "release-status.json"
        payload = {
            "ok": True,
            "installedVersion": ASR_INSTALLED_VERSION,
            "updateAvailable": False,
        }
        original_web_group = web_group
        web_group = lambda: -1
        try:
            write_cache(payload, cache)
            assert json.loads(cache.read_text(encoding="utf-8")) == payload
            assert cache.stat().st_mode & 0o777 == 0o640
            assert cache.parent.stat().st_mode & 0o777 == 0o750
        finally:
            web_group = original_web_group

        # Network and publication failures keep the last known-good cache.
        original_cache = cache.read_bytes()
        old_cache_env = os.environ.get("ASR_RELEASE_CACHE")
        old_api_env = os.environ.get("ASR_RELEASES_API")
        os.environ["ASR_RELEASE_CACHE"] = str(cache)
        os.environ["ASR_RELEASES_API"] = "https://example.invalid/releases"
        http_get = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib.error.URLError("offline")
        )
        try:
            assert main([]) == 0
            assert cache.read_bytes() == original_cache
        finally:
            http_get = original_http_get
            if old_cache_env is None:
                os.environ.pop("ASR_RELEASE_CACHE", None)
            else:
                os.environ["ASR_RELEASE_CACHE"] = old_cache_env
            if old_api_env is None:
                os.environ.pop("ASR_RELEASES_API", None)
            else:
                os.environ["ASR_RELEASES_API"] = old_api_env

    class OversizedResponse:
        def __enter__(self) -> "OversizedResponse":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        @staticmethod
        def read(size: int) -> bytes:
            return b"x" * size

    original_urlopen = urllib.request.urlopen
    urllib.request.urlopen = lambda *_args, **_kwargs: OversizedResponse()
    try:
        try:
            original_http_get(
                "https://example.invalid/releases",
                "application/json",
                max_bytes=8,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("oversized release response was accepted")
    finally:
        urllib.request.urlopen = original_urlopen
    print("release notification self-test: ok")


def main(arguments: list[str]) -> int:
    if "--self-test" in arguments:
        self_test()
        return 0

    cache = Path(os.environ.get("ASR_RELEASE_CACHE", str(ASR_RELEASE_CACHE)))
    api_url = os.environ.get("ASR_RELEASES_API", ASR_RELEASES_API)
    try:
        decoded = json.loads(
            http_get(api_url, "application/vnd.github+json", 12).decode(
                "utf-8", errors="strict"
            )
        )
        release = select_release(decoded if isinstance(decoded, list) else [])
        if release is None:
            raise ValueError("No usable published release was returned.")
        version = str(release["_asr_version"])
        package_name = f"allscan-reimagined-{version}.tar.gz"
        if not release_asset(release, package_name):
            raise ValueError("The newest release package is not ready.")
        checksum = release_checksum(release, package_name)
        if not checksum:
            raise ValueError("The newest release checksum is not ready.")
        payload = build_payload(release, checksum)
        write_cache(payload, cache)
    except (
        OSError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        urllib.error.URLError,
        urllib.error.HTTPError,
    ):
        print(
            "Release check unavailable; keeping the previous cached result.",
            file=sys.stderr,
        )
        return 0

    if payload["updateAvailable"]:
        print(f"ASR update available: {payload['availableLabel']}")
    else:
        print("ASR release check complete; installed version is current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
