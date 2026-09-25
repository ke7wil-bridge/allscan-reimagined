#!/bin/bash
# First-install entry point. Run from a terminal; all later maintenance is browser-driven.
set -Eeuo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "Run this one-time bootstrap with sudo." >&2
  exit 1
fi
[ "$#" -eq 0 ] || { echo "Bootstrap takes no arguments." >&2; exit 2; }
for command in python3 bash; do
  command -v "$command" >/dev/null || { echo "Missing $command." >&2; exit 1; }
done

stage=$(mktemp -d /tmp/asr-bootstrap.XXXXXXXX)
chmod 700 "$stage"
trap 'rm -rf -- "$stage"' EXIT
# Only fixed GitHub repository release assets are accepted. The package SHA-256
# must match the companion release asset before any installer code runs.
version=$(python3 - "$stage" <<'PY'
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import urllib.request

stage = Path(sys.argv[1])
api = "https://api.github.com/repos/ke7wil-bridge/allscan-reimagined/releases?per_page=20"
version_pattern = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-(?:alpha|beta|rc)\.[0-9]+(?:\.[0-9]+)*)?")
def version_key(version):
    core, *suffix = version.split("-", 1)
    numbers = tuple(map(int, core.split(".")))
    if not suffix:
        return (*numbers, 1, 0, ())
    channel, sequence = suffix[0].split(".", 1)
    return (*numbers, 0, {"alpha": 1, "beta": 2, "rc": 3}[channel],
            tuple(map(int, sequence.split("."))))

def open_url(url, accept, limit):
    request = urllib.request.Request(url, headers={
        "Accept": accept, "User-Agent": "AllScan-Reimagined/bootstrap"})
    response = urllib.request.urlopen(request, timeout=20)
    if int(response.headers.get("Content-Length", "0")) > limit:
        response.close()
        raise ValueError("Release asset exceeds size limit")
    return response

with open_url(api, "application/vnd.github+json", 4 * 1024 * 1024) as response:
    data = response.read(4 * 1024 * 1024 + 1)
if len(data) > 4 * 1024 * 1024:
    raise ValueError("Release list exceeds size limit")
releases = json.loads(data)
if not isinstance(releases, list):
    raise ValueError("Invalid release list")
choices = []
for release in releases:
    if not isinstance(release, dict) or release.get("draft"):
        continue
    tag = str(release.get("tag_name", ""))
    version = tag.removeprefix("v")
    if version_pattern.fullmatch(version):
        choices.append((version_key(version), version, tag, release))
if not choices:
    raise ValueError("No published ASR release")
_, version, tag, release = max(choices)
name = f"allscan-reimagined-{version}.tar.gz"
prefix = f"https://github.com/ke7wil-bridge/allscan-reimagined/releases/download/{tag}/"
assets = release.get("assets", [])
urls = {}
for asset in assets if isinstance(assets, list) else []:
    if isinstance(asset, dict) and asset.get("name") in (name, name + ".sha256"):
        expected = prefix + asset["name"]
        if asset.get("browser_download_url") == expected:
            urls[asset["name"]] = expected
if len(urls) != 2:
    raise ValueError("Release archive and checksum are not available")
with open_url(urls[name + ".sha256"], "text/plain", 65536) as response:
    checksum_text = response.read(65537).decode("ascii")
match = re.fullmatch(r"\s*([a-fA-F0-9]{64})\s+\*?" + re.escape(name) + r"\s*", checksum_text)
if not match:
    raise ValueError("Invalid release checksum")
package = stage / name
digest = hashlib.sha256()
total = 0
with open_url(urls[name], "application/octet-stream", 256 * 1024 * 1024) as response:
    with package.open("xb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 256 * 1024 * 1024:
                raise ValueError("Release archive exceeds size limit")
            digest.update(chunk)
            output.write(chunk)
if digest.hexdigest() != match.group(1).lower():
    raise ValueError("Release SHA-256 does not match")
root = "allscan-reimagined-" + version
with tarfile.open(package, "r:gz") as archive:
    members = archive.getmembers()
    if len(members) > 20000:
        raise ValueError("Too many release files")
    seen = set()
    expanded = 0
    for member in members:
        path = PurePosixPath(member.name)
        canonical = "/".join(path.parts)
        if (not path.parts or path.parts[0] != root
                or member.name.rstrip("/") != canonical
                or ".." in path.parts or canonical in seen
                or not (member.isfile() or member.isdir())):
            raise ValueError("Unsafe release archive")
        seen.add(canonical)
        expanded += member.size
        if member.size > 128 * 1024 * 1024 or expanded > 768 * 1024 * 1024:
            raise ValueError("Release expands beyond size limit")
    required = (root + "/install.sh", root + "/payload/server/asr-api.php",
                root + "/payload/web/index.html")
    if any(path not in seen for path in required):
        raise ValueError("Release package is incomplete")
    if root + "/package.json" in seen:
        manifest = archive.extractfile(root + "/package.json")
        if manifest is None or json.load(manifest).get("version") != version:
            raise ValueError("Release version does not match archive")
    else:
        # Legacy published releases embed the version directly in install.sh.
        installer = archive.extractfile(root + "/install.sh")
        if (installer is None or archive.getmember(root + "/install.sh").size > 1024 * 1024
                or not any(line.strip() == b'ASR_VERSION="' + version.encode() + b'"'
                           for line in installer.read().splitlines())):
            raise ValueError("Legacy release version does not match archive")
    for member in members:
        target = stage.joinpath(*PurePosixPath(member.name).parts)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True, mode=0o755)
        else:
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("Unreadable release file")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            target.chmod(0o644)
print(version)
PY
)
echo "Verified ASR $version. Starting the initial installer..."
bash "$stage/allscan-reimagined-$version/install.sh"
