#!/usr/bin/env python3
import importlib.util
import math
from pathlib import Path
import struct

path = Path(__file__).with_name("asr-bridge-audio-telemetry.py")
spec = importlib.util.spec_from_file_location("audio_telemetry", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

assert module.pcm16_rms(struct.pack("<hhhh", 0, 0, 0, 0)) == 0
assert module.pcm16_rms(struct.pack("<hh", 32767, -32768)) > 32767
assert math.isclose(module.pcm16_rms(struct.pack("<hh", 1000, -1000)), 1000)
assert module.sample("inbound", struct.pack("<h", 100), 123)["inboundAudioEpoch"] == 123
assert module.sample("outbound", struct.pack("<h", -100), 123)["outboundAudioRms"] == 100
for frame in (b"", b"\x00", b"\x00\x00\x00"):
    try:
        module.pcm16_rms(frame)
        raise AssertionError("malformed frame accepted")
    except ValueError:
        pass
print("Bridge audio telemetry self-test passed")
old = {"schema": 1, "bridges": {"dmr": {"inboundAudioRms": 250, "inboundAudioEpoch": 90}, "ysf": {"outboundAudioRms": 12, "outboundAudioEpoch": 98}}}
merged = module.merge_sample(old, "zello", module.sample("inbound", struct.pack("<h", 500), 100), now=100)
assert "dmr" not in merged["bridges"]
assert merged["bridges"]["ysf"]["outboundAudioRms"] == 12
assert merged["bridges"]["zello"]["inboundAudioRms"] == 500
assert merged["schema"] == 1
for invalid in ("../zello", "ZELLO", ""):
    try:
        module.merge_sample({}, invalid, {"inboundAudioRms": 100, "inboundAudioEpoch": 100}, now=100)
        raise AssertionError("unsafe bridge ID accepted")
    except ValueError:
        pass
print("Audio snapshot merge tests passed")

import json
assert json.loads(module.encode_snapshot(merged)) == merged
for invalid in (
    {"schema": 2, "bridges": {}},
    {"schema": 1, "bridges": {"../escape": {"inboundAudioRms": 1, "inboundAudioEpoch": 100}}},
    {"schema": 1, "bridges": {"zello": {"inboundAudioRms": float("nan"), "inboundAudioEpoch": 100}}},
    {"schema": 1, "bridges": {"zello": {"inboundAudioRms": 10}}},
):
    try:
        module.encode_snapshot(invalid)
        raise AssertionError("invalid snapshot accepted")
    except ValueError:
        pass
print("Audio snapshot serialization tests passed")
voice = bytearray(352)
voice[:4] = b"USRP"
voice[32:] = struct.pack("<160h", *([1000, -1000] * 80))
assert module.usrp_sample("inbound", bytes(voice), 100) == {"inboundAudioRms": 1000.0, "inboundAudioEpoch": 100.0}
assert module.usrp_voice_pcm(bytes(voice)) == bytes(voice[32:])
assert module.usrp_voice_pcm(bytes(voice[:-1])) is None
voice[20:24] = struct.pack("!I", 1)
assert module.usrp_sample("outbound", bytes(voice), 100) is None
voice[20:24] = bytes(4)
voice[:4] = b"BAD!"
assert module.usrp_voice_pcm(bytes(voice)) is None
print("USRP PCM voice extraction tests passed")
