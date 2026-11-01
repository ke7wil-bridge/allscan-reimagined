#!/usr/bin/env python3
"""Build and verify the pinned native URFD/TCD/TGIF runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

URFD = ("urfd", "bd0c114e43adc3df4c17823bc5693328425d0450",
        "3f831b595340752cbb157bc7b6c44bb7cd5602dc838e8d45761546c7e60bb78b")
TCD = ("tcd", "ff104384b3d1c51bf8f7f9f83d7940dffda0b076",
       "676ab2440eeb47b31eaf9de18c8ac6f75a160d1abb53a3ee27414438a3c786ed")
IMBE = ("imbe_vocoder", "03423cb00b8e9ba1be164493e8b98b5bd7c1508c",
        "f6a44b318245c2876b3031a7aa9f1a70f4749f37861a05f66c19d8bccfd84ce7")
MD380 = ("md380_vocoder", "0ebd2761d441db354f859280873d4286856f3e73",
         "0b4cf21dc7fdf0db31b9996065eb9229e65b8696e59a677258d557cb4b3f023f")
FIRMWARE = {
    "md380fw.img": ("https://www.dudetronics.com/radio/md380fw.img",
                    "c0351f250a834660641bca3b06be931c80cc1ed0ef808356c550b99cd0f4c632"),
    "md380ram.img": ("https://www.dudetronics.com/radio/md380ram.img",
                     "d40398183e9aa8db6288a62d9a0e0c233cb143ef5633680abd829cc03c719f66"),
}
SUPPORTED = {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "armhf"}
BUILD_PACKAGES = (
    "build-essential", "patch", "ca-certificates", "libcurl4-gnutls-dev",
    "nlohmann-json3-dev", "python3",
)
ARM_CROSS_PACKAGES = (
    "qemu-user-static", "gcc-arm-linux-gnueabihf", "g++-arm-linux-gnueabihf",
    "binutils-arm-linux-gnueabihf",
)
RUNTIME_PACKAGES = ("ca-certificates", "libcurl3-gnutls", "libstdc++6", "python3")


class SourceError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def architecture() -> str:
    machine = platform.machine().lower()
    if machine not in SUPPORTED:
        raise SourceError(f"native URF runtime is unsupported on {machine}")
    return SUPPORTED[machine]


def packages() -> tuple[str, ...]:
    values = (*RUNTIME_PACKAGES, *BUILD_PACKAGES,
              *(() if architecture() == "armhf" else ARM_CROSS_PACKAGES))
    return tuple(dict.fromkeys(values))


def describe(digital_sources) -> dict:
    return {
        "architecture": architecture(),
        "packages": list(packages()),
        "sources": {name: {"commit": commit, "archiveSha256": checksum}
                    for name, commit, checksum in (URFD, TCD, IMBE, MD380)},
        "firmware": {name: checksum for name, (_url, checksum) in FIRMWARE.items()},
        "mmdvm": digital_sources.binary_artifacts()["MMDVM_Bridge"],
    }


def install_packages() -> None:
    if os.geteuid() != 0:
        raise SourceError("native runtime package installation requires root")
    release = platform.freedesktop_os_release()
    if release.get("ID") != "debian" or release.get("VERSION_ID", "").split(".")[0] not in {"12", "13"}:
        raise SourceError("native URF runtime requires Debian 12 or 13")
    missing = []
    for package in packages():
        result = subprocess.run(["dpkg-query", "-W", "-f=${Status}", package],
                                capture_output=True, text=True)
        if result.returncode or result.stdout.strip() != "install ok installed":
            missing.append(package)
    if not missing:
        return
    environment = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    for command in (["apt-get", "update", "-qq"],
                    ["apt-get", "install", "-y", "-qq", "--no-install-recommends",
                     "--no-upgrade", "--no-remove", *missing]):
        result = subprocess.run(command, capture_output=True, text=True, env=environment)
        if result.returncode:
            raise SourceError(f"native runtime dependency installation failed: {result.stderr[-500:]}")


def download(url: str, target: Path, expected: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "ASR-Native-URF/1"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response, target.open("wb") as output:
            if response.status != 200:
                raise SourceError(f"download failed with HTTP {response.status}")
            shutil.copyfileobj(response, output)
    except (OSError, urllib.error.URLError) as exc:
        raise SourceError(f"could not download pinned source: {url}") from exc
    if sha256(target) != expected:
        target.unlink(missing_ok=True)
        raise SourceError(f"checksum mismatch for {url}")


def extract(archive: Path, destination: Path) -> Path:
    destination.mkdir()
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        roots = {Path(item.name).parts[0] for item in members if item.name}
        if len(roots) != 1:
            raise SourceError("source archive layout is invalid")
        tcd_links = {
            "IP.cpp", "IP.h", "TCPacketDef.h", "TCSocket.cpp", "TCSocket.h", "Timer.h",
        }
        regular_members = []
        for item in members:
            path = Path(item.name)
            if path.is_absolute() or ".." in path.parts:
                raise SourceError("source archive contains an unsafe path")
            if item.issym():
                expected = f"../urfd/reflector/{path.name}"
                if path.name not in tcd_links or item.linkname != expected:
                    raise SourceError("source archive contains an unsafe symbolic link")
                # The TCD repository intentionally links to its sibling URFD
                # checkout. Recreate these from the separately checksum-verified
                # URFD archive instead of extracting an escaping link.
                continue
            if not (item.isfile() or item.isdir()):
                raise SourceError("source archive contains an unsafe path")
            regular_members.append(item)
        if sys.version_info >= (3, 12):
            bundle.extractall(destination, members=regular_members, filter="data")
        else:
            bundle.extractall(destination, members=regular_members)
    return destination / roots.pop()


def fetch_source(work: Path, source: tuple[str, str, str]) -> Path:
    name, commit, checksum = source
    archive = work / f"{name}.tar.gz"
    download(f"https://github.com/nostar/{name}/archive/{commit}.tar.gz", archive, checksum)
    return extract(archive, work / f"{name}-source")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SourceError(f"pinned source no longer matches audited edit: {path.name}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def patch_protocol_enable_flags(urfd: Path) -> None:
    """Add explicit per-protocol listener enable flags to the pinned URFD source."""
    configure = urfd / "reflector/Configure.cpp"
    replace_once(configure, "\tcounter = 0;\n", "\tcounter = 0;\n\t// Old INI files retain the historical all-enabled behavior.\n\tdata[\"DCS.enable\"] = true; data[\"DExtra.enable\"] = true; data[\"DPlus.enable\"] = true;\n\tdata[\"DMRPlus.enable\"] = true; data[\"M17.enable\"] = true; data[\"MMDVM.enable\"] = true;\n\tdata[\"NXDN.enable\"] = true; data[\"P25.enable\"] = true; data[\"URF.enable\"] = true; data[\"YSF.enable\"] = true;\n")
    edits = [
        ("case ESection::dcs:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.dcs.port] = getUnsigned(value, \"DCS Port\", 1024, 65535, 30051);\n\t\t\t\telse\n", "case ESection::dcs:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.dcs.port] = getUnsigned(value, \"DCS Port\", 1024, 65535, 30051);\n\t\t\t\telse if (0 == key.compare(JENABLE)) data[\"DCS.enable\"] = IS_TRUE(value[0]);\n\t\t\t\telse\n"),
        ("case ESection::dextra:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.dextra.port] = getUnsigned(value, \"DExtra Port\", 1024, 65535, 30001);\n\t\t\t\telse\n", "case ESection::dextra:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.dextra.port] = getUnsigned(value, \"DExtra Port\", 1024, 65535, 30001);\n\t\t\t\telse if (0 == key.compare(JENABLE)) data[\"DExtra.enable\"] = IS_TRUE(value[0]);\n\t\t\t\telse\n"),
        ("case ESection::dplus:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.dplus.port] = getUnsigned(value, \"DPlus Port\", 1024, 65535, 20001);\n\t\t\t\telse\n", "case ESection::dplus:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.dplus.port] = getUnsigned(value, \"DPlus Port\", 1024, 65535, 20001);\n\t\t\t\telse if (0 == key.compare(JENABLE)) data[\"DPlus.enable\"] = IS_TRUE(value[0]);\n\t\t\t\telse\n"),
        ("case ESection::dmrplus:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.dmrplus.port] = getUnsigned(value, \"DMRPlus Port\", 1024, 65535, 8880);\n\t\t\t\telse\n", "case ESection::dmrplus:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.dmrplus.port] = getUnsigned(value, \"DMRPlus Port\", 1024, 65535, 8880);\n\t\t\t\telse if (0 == key.compare(JENABLE)) data[\"DMRPlus.enable\"] = IS_TRUE(value[0]);\n\t\t\t\telse\n"),
        ("case ESection::m17:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.m17.port] = getUnsigned(value, \"M17 Port\", 1024, 65535, 17000);\n\t\t\t\telse\n", "case ESection::m17:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.m17.port] = getUnsigned(value, \"M17 Port\", 1024, 65535, 17000);\n\t\t\t\telse if (0 == key.compare(JENABLE)) data[\"M17.enable\"] = IS_TRUE(value[0]);\n\t\t\t\telse\n"),
        ("case ESection::mmdvm:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.mmdvm.port] = getUnsigned(value, \"MMDVM Port\", 1024, 65535, 62030);\n", "case ESection::mmdvm:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.mmdvm.port] = getUnsigned(value, \"MMDVM Port\", 1024, 65535, 62030);\n\t\t\t\telse if (0 == key.compare(JENABLE)) data[\"MMDVM.enable\"] = IS_TRUE(value[0]);\n"),
        ("case ESection::nxdn:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.nxdn.port] = getUnsigned(value, \"NDXN Port\", 1024, 65535, 41400);\n", "case ESection::nxdn:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.nxdn.port] = getUnsigned(value, \"NDXN Port\", 1024, 65535, 41400);\n\t\t\t\telse if (0 == key.compare(JENABLE)) data[\"NXDN.enable\"] = IS_TRUE(value[0]);\n"),
        ("case ESection::p25:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.p25.port] = getUnsigned(value, \"P25 Port\", 1024, 65535, 41000);\n", "case ESection::p25:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.p25.port] = getUnsigned(value, \"P25 Port\", 1024, 65535, 41000);\n\t\t\t\telse if (0 == key.compare(JENABLE)) data[\"P25.enable\"] = IS_TRUE(value[0]);\n"),
        ("case ESection::urf:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.urf.port] = getUnsigned(value, \"URF Port\", 1024, 65535, 10017);\n\t\t\t\telse\n", "case ESection::urf:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.urf.port] = getUnsigned(value, \"URF Port\", 1024, 65535, 10017);\n\t\t\t\telse if (0 == key.compare(JENABLE)) data[\"URF.enable\"] = IS_TRUE(value[0]);\n\t\t\t\telse\n"),
        ("case ESection::ysf:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.ysf.port] = getUnsigned(value, \"YSF Port\", 1024, 65535, 42000);\n", "case ESection::ysf:\n\t\t\t\tif (0 == key.compare(JPORT))\n\t\t\t\t\tdata[g_Keys.ysf.port] = getUnsigned(value, \"YSF Port\", 1024, 65535, 42000);\n\t\t\t\telse if (0 == key.compare(JENABLE)) data[\"YSF.enable\"] = IS_TRUE(value[0]);\n"),
    ]
    for old, new in edits:
        replace_once(configure, old, new)

    protocols = urfd / "reflector/Protocols.cpp"
    blocks = [
        ("CDextraProtocol", '"XRF", EProtocol::dextra, uint16_t(g_Configure.GetUnsigned(g_Keys.dextra.port)), DSTAR_IPV4, DSTAR_IPV6', "DExtra"),
        ("CDplusProtocol", '"REF", EProtocol::dplus, uint16_t(g_Configure.GetUnsigned(g_Keys.dplus.port)), DSTAR_IPV4, DSTAR_IPV6', "DPlus"),
        ("CDcsProtocol", '"DCS", EProtocol::dcs, uint16_t(g_Configure.GetUnsigned(g_Keys.dcs.port)), DSTAR_IPV4, DSTAR_IPV6', "DCS"),
        ("CDmrmmdvmProtocol", "nullptr, EProtocol::dmrmmdvm, uint16_t(g_Configure.GetUnsigned(g_Keys.mmdvm.port)), DMR_IPV4, DMR_IPV6", "MMDVM"),
        ("CDmrplusProtocol", "nullptr, EProtocol::dmrplus, uint16_t(g_Configure.GetUnsigned(g_Keys.dmrplus.port)), DMR_IPV4, DMR_IPV6", "DMRPlus"),
        ("CYsfProtocol", '"YSF", EProtocol::ysf, uint16_t(g_Configure.GetUnsigned(g_Keys.ysf.port)), YSF_IPV4, YSF_IPV6', "YSF"),
        ("CM17Protocol", '"URF", EProtocol::m17, uint16_t(g_Configure.GetUnsigned(g_Keys.m17.port)), M17_IPV4, M17_IPV6', "M17"),
        ("CP25Protocol", '"P25", EProtocol::p25, uint16_t(g_Configure.GetUnsigned(g_Keys.p25.port)), P25_IPV4, P25_IPV6', "P25"),
        ("CNXDNProtocol", '"NXDN", EProtocol::nxdn, uint16_t(g_Configure.GetUnsigned(g_Keys.nxdn.port)), NXDN_IPV4, NXDN_IPV6', "NXDN"),
        ("CURFProtocol", '"URF", EProtocol::urf, uint16_t(g_Configure.GetUnsigned(g_Keys.urf.port)), URF_IPV4, URF_IPV6', "URF"),
    ]
    for cls, init, section in blocks:
        old = f"\t\tm_Protocols.emplace_back(std::unique_ptr<{cls}>(new {cls}));\n\t\tif (! m_Protocols.back()->Initialize({init}))\n\t\t\treturn false;\n"
        new = f"\t\tif (g_Configure.GetBoolean(\"{section}.enable\"))\n\t\t{{\n\t\t\tm_Protocols.emplace_back(std::unique_ptr<{cls}>(new {cls}));\n\t\t\tif (! m_Protocols.back()->Initialize({init}))\n\t\t\t\treturn false;\n\t\t}}\n"
        replace_once(protocols, old, new)


def build(destination: Path, runtime_source: Path, digital_sources) -> dict:
    if destination.exists() and any(destination.iterdir()):
        raise SourceError("native runtime destination must be empty")
    if not runtime_source.is_dir() or not (runtime_source / "patches").is_dir():
        raise SourceError("ASR URF patch source is unavailable")
    destination.mkdir(parents=True, exist_ok=True)
    bin_dir = destination / "bin"
    tgif_dir = destination / "tgif"
    bin_dir.mkdir(); tgif_dir.mkdir()
    with tempfile.TemporaryDirectory(prefix="asr-native-urf-") as temporary:
        work = Path(temporary)
        urfd = fetch_source(work, URFD)
        tcd = fetch_source(work, TCD)
        imbe = fetch_source(work, IMBE)
        md380 = fetch_source(work, MD380)
        for name in ("IP.cpp", "IP.h", "TCPacketDef.h", "TCSocket.cpp", "TCSocket.h", "Timer.h"):
            shutil.copy2(urfd / "reflector" / name, tcd / name)
        patch_protocol_enable_flags(urfd)

        patch_root = runtime_source / "patches"
        for name in ("Reflector.cpp", "AsrPttState.h", "Clients.cpp", "Clients.h",
                     "CodecStream.cpp", "DMRMMDVMProtocol.cpp", "M17Protocol.cpp",
                     "M17Client.h", "M17Client.cpp", "USRPProtocol.cpp", "M17Packet.cpp",
                     "DVHeaderPacket.cpp"):
            shutil.copy2(patch_root / name, urfd / "reflector" / name)
        for name in ("YSFProtocol-session-suppression.patch",
                     "P25Protocol-session-suppression.patch",
                     "NXDNProtocol-session-suppression.patch"):
            subprocess.run(["patch", "-d", str(urfd), "-p1", "-i", str(patch_root / name)],
                           check=True, capture_output=True, text=True)
        for name in ("YSFProtocol.cpp", "P25Protocol.cpp", "NXDNProtocol.cpp"):
            shutil.copy2(patch_root / name, urfd / "reflector" / name)
        shutil.copy2(patch_root / "tcd/Controller.cpp", tcd / "Controller.cpp")
        shutil.copy2(urfd / "config/urfd.mk", urfd / "reflector/urfd.mk")
        replace_once(urfd / "reflector/urfd.mk", "DHT = true", "DHT = false")
        subprocess.run(["make", "-C", str(urfd / "reflector"), "-j2"], check=True,
                       capture_output=True, text=True)
        for name in ("urfd", "inicheck", "dbutil"):
            shutil.copy2(urfd / "reflector" / name, bin_dir / name)
            os.chmod(bin_dir / name, 0o755)

        cross = architecture() != "armhf"
        cxx = "arm-linux-gnueabihf-g++" if cross else "g++"
        cc = "arm-linux-gnueabihf-gcc" if cross else "gcc"
        ar = "arm-linux-gnueabihf-ar" if cross else "ar"
        objcopy = "arm-linux-gnueabihf-objcopy" if cross else "objcopy"
        subprocess.run(["make", "-C", str(imbe), "-j2", f"CXX={cxx}", f"AR={ar}"],
                       check=True, capture_output=True, text=True)
        for name, (url, checksum) in FIRMWARE.items():
            download(url, md380 / name, checksum)
        makefile = md380 / "Makefile"
        text = makefile.read_text(encoding="utf-8")
        text = text.replace("wget -N http://www.dudetronics.com/radio/md380fw.img\n", "")
        text = text.replace("wget -N http://www.dudetronics.com/radio/md380ram.img\n", "")
        text = text.replace("\tobjcopy \\", f"\t{objcopy} \\")
        makefile.write_text(text, encoding="utf-8")
        subprocess.run(["make", "-C", str(md380), "-j2", f"CC={cc}", f"AR={ar}"],
                       check=True, capture_output=True, text=True)
        shutil.copy2(tcd / "config/tcd.mk", tcd / "tcd.mk")
        replace_once(tcd / "tcd.mk", "swambe2 = false", "swambe2 = true")
        replace_once(tcd / "tcd.mk", "swmodes = false", "swmodes = true")
        makefile = tcd / "Makefile"
        text = makefile.read_text(encoding="utf-8")
        text = text.replace("GCC = g++", f"GCC = {cxx}")
        text = text.replace("CFLAGS = -W", f"CFLAGS = -I{imbe} -I{md380} -W")
        text = text.replace("LDFLAGS = -limbe_vocoder -pthread",
                            f"LDFLAGS = -no-pie {imbe / 'libimbe_vocoder.a'} -pthread")
        text = text.replace("LDFLAGS += -lmd380_vocoder",
                            f"LDFLAGS += {md380 / 'libmd380_vocoder.a'}")
        makefile.write_text(text, encoding="utf-8")
        subprocess.run(["make", "-C", str(tcd), "-j2"], check=True,
                       capture_output=True, text=True)
        shutil.copy2(tcd / "tcd", bin_dir / "tcd-armhf")
        os.chmod(bin_dir / "tcd-armhf", 0o755)
        if cross:
            qemu = Path("/usr/bin/qemu-arm-static")
            if not qemu.is_file():
                raise SourceError("qemu-arm-static is unavailable after dependency installation")
            shutil.copy2(qemu, bin_dir / "qemu-arm-static")
            os.chmod(bin_dir / "qemu-arm-static", 0o755)

        artifact = digital_sources.binary_artifacts()["MMDVM_Bridge"]
        download(artifact["url"], bin_dir / "MMDVM_Bridge", artifact["sha256"])
        os.chmod(bin_dir / "MMDVM_Bridge", 0o755)
        for name in ("run.sh", "MMDVM_Bridge.ini.template", "DVSwitch.ini",
                     "asr-tgif-urf-dmr-bridge.py"):
            shutil.copy2(runtime_source / "tgif" / name, tgif_dir / name)
        os.chmod(tgif_dir / "run.sh", 0o755)
        os.chmod(tgif_dir / "asr-tgif-urf-dmr-bridge.py", 0o755)

    provenance = {
        "schema": 1, **describe(digital_sources),
        "patches": {item.relative_to(runtime_source).as_posix(): sha256(item)
                    for item in sorted((runtime_source / "patches").rglob("*")) if item.is_file()},
    }
    provenance["files"] = {
        item.relative_to(destination).as_posix(): sha256(item)
        for item in sorted(destination.rglob("*")) if item.is_file()
    }
    manifest = destination / "asr-native-runtime-manifest.json"
    manifest.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(manifest, 0o644)
    return provenance


def verify(destination: Path, runtime_source: Path, digital_sources) -> dict:
    manifest_path = destination / "asr-native-runtime-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SourceError("native runtime manifest is missing or invalid") from exc
    expected = describe(digital_sources)
    if (manifest.get("schema") != 1
            or any(manifest.get(key) != value for key, value in expected.items())):
        raise SourceError("native runtime provenance does not match pinned inputs")
    expected_patches = {item.relative_to(runtime_source).as_posix(): sha256(item)
                        for item in sorted((runtime_source / "patches").rglob("*")) if item.is_file()}
    if manifest.get("patches") != expected_patches:
        raise SourceError("native runtime ASR patch set does not match")
    files = manifest.get("files")
    required = {"bin/urfd", "bin/inicheck", "bin/dbutil", "bin/tcd-armhf",
                "bin/MMDVM_Bridge", "tgif/run.sh", "tgif/MMDVM_Bridge.ini.template",
                "tgif/DVSwitch.ini", "tgif/asr-tgif-urf-dmr-bridge.py"}
    if not isinstance(files, dict) or not required.issubset(files):
        raise SourceError("native runtime component set is incomplete")
    for name, checksum in files.items():
        path = destination / name
        if (not isinstance(name, str) or name.startswith("/") or ".." in Path(name).parts
                or path.is_symlink() or not path.is_file() or sha256(path) != checksum):
            raise SourceError(f"native runtime verification failed: {name}")
    return {"ok": True, "architecture": architecture(), "destination": str(destination)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "verify"))
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--runtime-source", required=True, type=Path)
    parser.add_argument("--install-packages", action="store_true")
    args = parser.parse_args()
    try:
        import importlib.util
        source_path = Path(__file__).resolve().parent / "asr-bridge-runtime-sources.py"
        spec = importlib.util.spec_from_file_location("asr_native_urf_digital_sources", source_path)
        if not spec or not spec.loader:
            raise SourceError("digital runtime source module is unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        if args.install_packages:
            install_packages()
        result = (build(args.destination, args.runtime_source, module) if args.command == "build"
                  else verify(args.destination, args.runtime_source, module))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (SourceError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
