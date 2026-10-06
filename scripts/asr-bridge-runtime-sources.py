#!/usr/bin/env python3
"""Fetch and build pinned upstream bridge runtime components."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ANALOG_COMMIT = "dbc4c58f56a70b2da43861efd2393fa415e38c53"
MMDVM_COMMIT = "285e51e98dca637eab296a8fe00879e39381f678"
BUILD_PACKAGES = ("build-essential", "libmosquitto-dev", "nlohmann-json3-dev")
RUNTIME_PACKAGES = ("libasound2", "libsndfile1", "libstdc++6", "libmosquitto1",
                    "mosquitto", "mosquitto-clients")
# The working virtual-node NXDN/YSF stack uses this software AMBE+2 emulator.
# These are official DVSwitch Debian 12 packages, pinned by package SHA-256.
CODEC_PACKAGES = {
    "amd64": ("20240812-20", "b46f3080799de924f6b5d3871fd917c425509e209e53a4098bc40c15d01f0259"),
    "arm64": ("20240812-21", "3a3448cb5ce58e84c0388ca5a056beab91dd5a0d6d952746388e5eba163b064f"),
}

class SourceError(RuntimeError):
    pass

@dataclass(frozen=True)
class ModeSource:
    repository: str
    commit: str
    archive_sha256: str
    directory: str
    binary: str
    license: str = "GPL-2.0"

MODES = {
    "p25": ModeSource(
        "https://github.com/g4klx/P25Clients",
        "3c5fb387c4e2d676a7c79069d2bb3541473b0528",
        "6d462fc3a363848788d07afae7d4937840c600c82e276d324db39b684451957e",
        "P25Gateway", "P25Gateway",
    ),
    "nxdn": ModeSource(
        "https://github.com/g4klx/NXDNClients",
        "8950677e9876e577fb87b955cfa93bacd059209d",
        "460dde5c1082226a104394681cae8a1e5e3c30769ba0028eef11818bc833725b",
        "NXDNGateway", "NXDNGateway",
    ),
    "ysf": ModeSource(
        "https://github.com/g4klx/YSFClients",
        "9ca8293609d084c42e4e112e418a7bd2e7086665",
        "2a7445a0de92417d97efaab54e1d5fbaefb23cc3572884d63da4283318112b17",
        "YSFGateway", "YSFGateway",
    ),
}

SOURCE_PATCHES = {
    "p25": ["Reflectors.cpp: open the JSON hosts catalog read-only"],
    "nxdn": ["Reflectors.cpp: open the JSON hosts catalog read-only"],
    "ysf": ["Log.cpp: bound vsnprintf to the remaining destination buffer"],
}

ARCHITECTURES = {
    "x86_64": ("amd64", {
        "analog": "f632733ed688b43e5f5c14c4371af1fa622cde836960e431d76e9d872c576fc9",
        "mmdvm": "afc4c7f30ac3376afb195b71facfab102fcda5c7b3d0b5e03e42cfad69e7b380",
        "remote": "a13a0fcf7b20482c4269d3c519706b071138b2491639441b2bb253f3e63b9648",
    }),
    "aarch64": ("arm64", {
        "analog": "6470bcd333f39017563ce17e64b1dc8174405d15f5300c7cff806ef908eadfae",
        "mmdvm": "b45741293a45880dccb7e960304b99a7ccbad176b30960c96be2c467a38ac9f3",
        "remote": "922617dd93380c81b17d8b712b2efa42b89ffd36ef956537f8dc94ed4889af71",
    }),
    "armv7l": ("armhf", {
        "analog": "a9bf8d157afde0badd87d8866ca1cd4309ffad730c14cb9a12ebb7877abc4f84",
        "mmdvm": "554721a6006f959fbc9f1ebeca63077e033d944655688fc9a0ae57f2a84a1b5f",
        "remote": "922617dd93380c81b17d8b712b2efa42b89ffd36ef956537f8dc94ed4889af71",
    }),
}

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def download(url: str, target: Path, expected: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "ASR-Bridge-Setup/1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status != 200:
                raise SourceError(f"download failed with HTTP {response.status}")
            with target.open("wb") as output:
                shutil.copyfileobj(response, output)
    except (OSError, urllib.error.URLError) as exc:
        raise SourceError(f"could not download pinned source: {url}") from exc
    actual = sha256(target)
    if actual != expected:
        target.unlink(missing_ok=True)
        raise SourceError(f"checksum mismatch for {url}")

def safe_extract(archive: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        roots = {Path(member.name).parts[0] for member in members if member.name}
        if len(roots) != 1:
            raise SourceError("source archive has an unexpected layout")
        for member in members:
            path = Path(member.name)
            if (path.is_absolute() or ".." in path.parts
                    or not (member.isfile() or member.isdir())):
                raise SourceError("source archive contains an unsafe path")
        if sys.version_info >= (3, 12):
            bundle.extractall(destination, filter="data")
        else:
            bundle.extractall(destination)
    return destination / roots.pop()

def architecture() -> tuple[str, dict[str, str]]:
    machine = platform.machine().lower()
    if machine not in ARCHITECTURES:
        raise SourceError(f"unsupported architecture: {machine}")
    return ARCHITECTURES[machine]

def require_commands(commands: tuple[str, ...]) -> None:
    missing = [command for command in commands if not shutil.which(command)]
    if missing:
        raise SourceError("missing build commands: " + ", ".join(missing))

def runtime_packages_for_release(release: dict[str, str]) -> tuple[str, ...]:
    distro = release.get("ID", "")
    version = release.get("VERSION_ID", "")
    debian = re.match(r"^(12|13)(?:\.|$)", version) if distro == "debian" else None
    ubuntu = re.match(r"^26\.04(?:\.|$)", version) if distro == "ubuntu" else None
    if not debian and not ubuntu:
        raise SourceError("digital bridge packages require Debian 12/13 or Ubuntu 26.04")
    alsa = "libasound2" if debian and debian.group(1) == "12" else "libasound2t64"
    return (alsa, *(package for package in RUNTIME_PACKAGES if package != "libasound2"))

def runtime_packages() -> tuple[str, ...]:
    release = platform.freedesktop_os_release()
    return runtime_packages_for_release(release)


def install_packages(build: bool = True) -> None:
    if os.geteuid() != 0:
        raise SourceError("package installation requires root")
    packages = (*runtime_packages(), *(BUILD_PACKAGES if build else ()))
    missing = []
    for package in packages:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", package],
            capture_output=True, text=True, check=False,
        )
        if result.returncode or result.stdout.strip() != "install ok installed":
            missing.append(package)
    if not missing:
        return
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    for command in (
        ["apt-get", "update", "-qq"],
        ["apt-get", "install", "-y", "-qq", "--no-install-recommends",
         "--no-upgrade", "--no-remove", *missing],
    ):
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        if result.returncode:
            raise SourceError(f"dependency installation failed: {result.stderr[-500:]}")
    # A newly installed broker must not expose Debian's unrelated default listener.
    if "mosquitto" in missing and Path("/run/systemd/system").is_dir():
        subprocess.run(
            ["systemctl", "disable", "--now", "mosquitto.service"],
            check=True, capture_output=True, text=True,
        )

def raw_url(repository: str, commit: str, path: str) -> str:
    owner_repo = repository.removeprefix("https://github.com/")
    return f"https://raw.githubusercontent.com/{owner_repo}/{commit}/{path}"

def archive_url(source: ModeSource) -> str:
    return f"{source.repository}/archive/{source.commit}.tar.gz"

def binary_artifacts() -> dict[str, dict[str, str]]:
    suffix, checksums = architecture()
    return {
        "Analog_Bridge": {
            "url": raw_url(
                "https://github.com/DVSwitch/Analog_Bridge", ANALOG_COMMIT,
                f"opt/Analog_Bridge/Analog_Bridge.{suffix}",
            ),
            "sha256": checksums["analog"],
            "upstreamCommit": ANALOG_COMMIT,
            "license": "upstream binary distribution; installer does not redistribute",
        },
        "RemoteCommand": {
            "url": raw_url(
                "https://github.com/DVSwitch/MMDVM_Bridge", MMDVM_COMMIT,
                f"opt/MMDVM_Bridge/RemoteCommand.{suffix}",
            ),
            "sha256": checksums["remote"],
            "upstreamCommit": MMDVM_COMMIT,
            "license": "upstream binary distribution; installer does not redistribute",
        },
        "MMDVM_Bridge": {
            "url": raw_url(
                "https://github.com/DVSwitch/MMDVM_Bridge", MMDVM_COMMIT,
                f"opt/MMDVM_Bridge/MMDVM_Bridge.{suffix}",
            ),
            "sha256": checksums["mmdvm"],
            "upstreamCommit": MMDVM_COMMIT,
            "license": "upstream binary distribution; installer does not redistribute",
        },
    }

def codec_artifact() -> dict[str, str]:
    arch, _ = architecture()
    if arch not in CODEC_PACKAGES:
        raise SourceError(f"pinned software emulator is unavailable for {arch}")
    version, checksum = CODEC_PACKAGES[arch]
    return {
        "url": ("http://dvswitch.org/DVSwitch_Repository/pool/hamradio/m/"
                f"md380-emu/md380-emu_{version}_{arch}.deb"),
        "sha256": checksum,
        "version": version,
        "repository": "DVSwitch signed Bookworm package repository",
    }

def stage_codec(work: Path, destination: Path) -> dict[str, str]:
    require_commands(("dpkg-deb",))
    artifact = codec_artifact()
    package = work / "md380-emu.deb"
    download(artifact["url"], package, artifact["sha256"])
    unpacked = work / "md380-package"
    subprocess.run(["dpkg-deb", "-x", str(package), str(unpacked)], check=True)
    source = unpacked / "opt/md380-emu"
    for name in ("md380-emu", "qemu-arm-static"):
        binary = source / name
        if not binary.is_file() or binary.is_symlink():
            raise SourceError(f"pinned emulator package lacks {name}")
        shutil.copy2(binary, destination / name)
        os.chmod(destination / name, 0o755)
    return artifact

def describe(mode: str) -> dict[str, Any]:
    source = MODES[mode]
    suffix, _checksums = architecture()
    gateway = {
        "repository": source.repository,
        "commit": source.commit,
        "archive": archive_url(source),
        "archiveSha256": source.archive_sha256,
        "buildPackages": list(BUILD_PACKAGES),
        "buildCommand": f"make -C {source.directory}",
        "result": source.binary,
        "license": source.license,
    }
    if mode in SOURCE_PATCHES:
        gateway["patches"] = SOURCE_PATCHES[mode]
    return {
        "schema": 1,
        "mode": mode,
        "architecture": suffix,
        "gateway": gateway,
        "runtimePackages": list(RUNTIME_PACKAGES),
        "binaryArtifacts": binary_artifacts(),
        "softwareVocoder": codec_artifact(),
    }


def patch_gateway_source(gateway_dir: Path, mode: str) -> None:
    """Apply narrow audited fixes to checksum-verified pinned source."""
    if mode in {"p25", "nxdn"}:
        path = gateway_dir / "Reflectors.cpp"
        original = "std::fstream file(fileName);"
        replacement = "std::ifstream file(fileName);"
        text = path.read_text(encoding="utf-8")
        if text.count(original) != 1:
            raise SourceError(
                f"pinned {mode.upper()} gateway hosts source no longer matches its audited patch"
            )
        path.write_text(text.replace(original, replacement), encoding="utf-8")
        return
    if mode != "ysf":
        return
    path = gateway_dir / "Log.cpp"
    original = "::vsnprintf(buffer + ::strlen(buffer), 500, fmt, vl);"
    replacement = "::vsnprintf(buffer + ::strlen(buffer), 500 - ::strlen(buffer), fmt, vl);"
    text = path.read_text(encoding="utf-8")
    if text.count(original) != 1:
        raise SourceError("pinned YSF gateway logging source no longer matches its audited patch")
    path.write_text(text.replace(original, replacement), encoding="utf-8")

def copy_gateway_assets(source_dir: Path, output: Path, mode: str) -> list[str]:
    copied: list[str] = []
    candidates = [f"{source_dir.name}.ini", "Audio", "FCSRooms.txt",
                  "DMRIds.dat", "NXDN.csv", "schema.json"]
    for name in candidates:
        item = source_dir / name
        if not item.exists():
            continue
        destination = output / name
        if item.is_dir():
            shutil.copytree(item, destination)
        else:
            shutil.copy2(item, destination)
        copied.append(name)
    license_path = source_dir.parent / "LICENCE"
    if license_path.is_file():
        shutil.copy2(license_path, output / "LICENCE.gateway")
        copied.append("LICENCE.gateway")
    return copied

def prepare(mode: str, destination: Path, packages: bool) -> dict[str, Any]:
    if packages:
        install_packages()
    require_commands(("make", "c++"))
    if destination.exists() and any(destination.iterdir()):
        raise SourceError("destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    source = MODES[mode]
    installed: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix=f"asr-{mode}-source-") as temporary:
        work = Path(temporary)
        for name, artifact in binary_artifacts().items():
            target = work / name
            download(artifact["url"], target, artifact["sha256"])
            os.chmod(target, 0o755)
            shutil.copy2(target, destination / name)
            installed.append({"name": name, "sha256": artifact["sha256"]})
        stage_codec(work, destination)
        for name in ("md380-emu", "qemu-arm-static"):
            installed.append({"name": name, "sha256": sha256(destination / name)})
        templates = {
            "Analog_Bridge.ini.template": (
                "https://github.com/DVSwitch/Analog_Bridge", ANALOG_COMMIT,
                "opt/Analog_Bridge/Analog_Bridge.ini",
                "b5dd63dc6618a131eaabb8bc8183665669d8cff83208538885663bda7c8cc24a",
            ),
            "MMDVM_Bridge.ini.template": (
                "https://github.com/DVSwitch/MMDVM_Bridge", MMDVM_COMMIT,
                "opt/MMDVM_Bridge/MMDVM_Bridge.ini",
                "08d236ba8e8138271ec4d0e9622b667e0e6536500c49dd2db70469993db1bd96",
            ),
        }
        templates.update({
            "DVSwitch.ini.template": (
                "https://github.com/DVSwitch/MMDVM_Bridge", MMDVM_COMMIT,
                "opt/MMDVM_Bridge/DVSwitch.ini",
                "d796db3e5e6b772cefeae28df0cd61af5b8f3826361a6c92508cd82347faf428",
            ),
            "dvswitch.sh": (
                "https://github.com/DVSwitch/MMDVM_Bridge", MMDVM_COMMIT,
                "opt/MMDVM_Bridge/dvswitch.sh",
                "64c2a18d5d1b236c8416bc039307bd78198fb5877a74c1949e326cffa2f836a9",
            ),
            "dvsm.macro": (
                "https://github.com/DVSwitch/Analog_Bridge", ANALOG_COMMIT,
                "opt/Analog_Bridge/dvsm.macro",
                "6d91ed75573244c2a4104ee28350e1275ba318dadac7430596d4eeab621c1941",
            ),
        })
        for name, (repository, commit, path, checksum) in templates.items():
            download(raw_url(repository, commit, path), destination / name, checksum)
        archive = work / "gateway.tar.gz"
        download(archive_url(source), archive, source.archive_sha256)
        tree = safe_extract(archive, work / "source")
        gateway_dir = tree / source.directory
        patch_gateway_source(gateway_dir, mode)
        subprocess.run(
            ["make", "-C", str(gateway_dir), "-j2"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        gateway = gateway_dir / source.binary
        if not gateway.is_file():
            raise SourceError("gateway build did not produce its expected binary")
        os.chmod(gateway, 0o755)
        shutil.copy2(gateway, destination / source.binary)
        installed.append({"name": source.binary, "sha256": sha256(gateway)})
        copied = copy_gateway_assets(gateway_dir, destination, mode)
    manifest = {
        **describe(mode),
        "installed": installed,
        "copiedAssets": copied,
        "assets": [
            {"name": item.relative_to(destination).as_posix(), "sha256": sha256(item)}
            for item in sorted(destination.rglob("*"))
            if item.is_file() and item.name not in {entry["name"] for entry in installed}
        ],
    }
    manifest_path = destination / "asr-runtime-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(manifest_path, 0o644)
    return manifest

def verify(destination: Path, mode: str) -> dict[str, Any]:
    manifest_path = destination / "asr-runtime-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SourceError("runtime manifest is missing or invalid") from exc
    expected = describe(mode)
    for key in ("mode", "architecture", "gateway", "binaryArtifacts", "softwareVocoder"):
        if manifest.get(key) != expected[key]:
            raise SourceError("runtime provenance does not match pinned sources")
    for item in manifest.get("installed", []):
        path = destination / str(item.get("name", ""))
        if not path.is_file() or sha256(path) != item.get("sha256"):
            raise SourceError(f"runtime verification failed: {path.name}")
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        raise SourceError("runtime asset manifest is missing")
    for item in assets:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name or name.startswith("/") or ".." in Path(name).parts:
            raise SourceError("runtime asset path is invalid")
        path = destination / name
        if path.is_symlink() or not path.is_file() or sha256(path) != item.get("sha256"):
            raise SourceError(f"runtime asset verification failed: {name}")
    required_assets = {"Analog_Bridge.ini.template", "MMDVM_Bridge.ini.template",
                       "DVSwitch.ini.template", "dvswitch.sh", "dvsm.macro",
                       f"{MODES[mode].directory}.ini"}
    if not required_assets.issubset({item["name"] for item in assets}):
        raise SourceError("runtime configuration templates are missing")
    required = {"Analog_Bridge", "MMDVM_Bridge", "RemoteCommand", MODES[mode].binary,
                "md380-emu", "qemu-arm-static"}
    present = {str(item.get("name", "")) for item in manifest.get("installed", [])}
    if present != required:
        raise SourceError("runtime manifest has an incomplete component set")
    return {"ok": True, "mode": mode, "destination": str(destination)}

def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("describe", "prepare", "verify"))
    result.add_argument("--mode", choices=tuple(MODES), required=True)
    result.add_argument("--destination", type=Path)
    result.add_argument("--install-packages", action="store_true")
    return result

def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "describe":
            result = describe(args.mode)
        else:
            if args.destination is None or not args.destination.is_absolute():
                raise SourceError("an absolute destination is required")
            if args.command == "prepare":
                result = prepare(args.mode, args.destination, args.install_packages)
            else:
                result = verify(args.destination, args.mode)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (SourceError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
