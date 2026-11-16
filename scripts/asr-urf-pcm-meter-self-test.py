#!/usr/bin/env python3
"""Compile and exercise the real TCD PCM accumulator without a reflector."""
from pathlib import Path
import subprocess
import os
import json
import stat
import tempfile

root = Path(__file__).resolve().parent.parent
patch = root / 'runtime/urf/patches/tcd'
controller = (patch / 'Controller.cpp').read_text()
audio_to_usrp = controller.split('void CController::AudiotoUSRP(', 1)[1].split('void CController::USRPtoAudio(', 1)[0]
usrp_to_audio = controller.split('void CController::USRPtoAudio(', 1)[1].split('void CController::ProcessUSRPThread(', 1)[0]
assert 'asr_pcm_meter.Sample(true, packet->GetUSRPData(), 160' in audio_to_usrp
assert 'asr_pcm_meter.Sample(false, packet->GetAudioSamples(), 160' in usrp_to_audio
assert audio_to_usrp.index('packet->SetUSRPData') < audio_to_usrp.index('asr_pcm_meter.Sample(true')
assert usrp_to_audio.index('packet->SetAudioSamples') < usrp_to_audio.index('asr_pcm_meter.Sample(false')
assert controller.count('asr_pcm_meter.Sample(') == 2
assert 'asr_pcm_meter.Publish(now)' in controller.split('void CController::AsrMeterPublisherThread()', 1)[1].split('void CController::Stop()', 1)[0]
assert 'asrMeterFuture.valid()' in controller
assert 'CAsrPcmMeter asr_pcm_meter;' in (patch / 'Controller.h').read_text()
assert '"AsrPcmMeter.h"' in (patch / 'Controller.h').read_text()
assert '"AsrPcmMeter.h"' in (root / 'scripts/asr-bridge-urf-native-runtime.py').read_text()
assert 'patches/tcd/AsrPcmMeter.h' in (root / 'runtime/urf/Dockerfile.tcd-arm-qemu').read_text()
source = r'''
#include "AsrPcmMeter.h"
#include <array>
#include <cmath>
#include <thread>
#include <vector>
#include <cstdlib>
#include <fstream>
#include <string>
int main() {
    CAsrPcmMeter meter;
    std::array<int16_t, 160> input{};
    std::array<int16_t, 160> output{};
    input.fill(-32768);
    output.fill(8192);
    if (const char *path = std::getenv("ASR_TCD_AUDIO_METER_TEST_PATH")) {
        setenv("ASR_TCD_AUDIO_METER_PATH", path, 1);
    }
    meter.Sample(true, input.data(), input.size(), 100.0);
    meter.Publish(100.0);
    if (const char *path = std::getenv("ASR_TCD_AUDIO_METER_TEST_PATH")) {
        std::ifstream in(path);
        std::string payload((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
        if (payload.find("\"inboundAudioPeak\":32768") == std::string::npos) return 6;
        if (payload.find("\"outboundAudioPeak\":0") == std::string::npos) return 7;
    }
    meter.Sample(false, output.data(), output.size(), 100.1);
    auto levels = meter.Read(100.2);
    if (levels.inbound.peak != 32768 || levels.inbound.rms != 32768 ||
        levels.outbound.peak != 8192 || levels.outbound.rms != 8192) return 1;
    levels = meter.Read(100.8);
    if (levels.inbound.peak != 0 || levels.outbound.peak != 8192) return 2;
    levels = meter.Read(101.0);
    if (levels.outbound.peak != 0) return 3;
    meter.Sample(true, nullptr, 160, 102);
    meter.Sample(true, input.data(), 159, 102);
    meter.Sample(true, input.data(), 160, NAN);
    if (meter.Read(102).inbound.peak != 0) return 4;
    input.fill(0);
    meter.Sample(true, input.data(), 160, 103);
    if (meter.Read(103).inbound.peak != 0 || meter.Read(103).inbound.epoch != 103) return 5;
    std::vector<std::thread> workers;
    for (int i = 0; i < 8; ++i) workers.emplace_back([&meter, i]() {
        std::array<int16_t, 160> samples{};
        samples.fill(int16_t(1000 + i));
        for (int j = 0; j < 1000; ++j) {
            meter.Sample(i % 2 == 0, samples.data(), samples.size(), 200 + i);
            (void)meter.Read(201);
        }
    });
    for (auto &worker : workers) worker.join();
    meter.Publish(207.0);
    if (const char *path = std::getenv("ASR_TCD_AUDIO_METER_TEST_PATH")) {
        // A backwards clock must not freeze telemetry publication.
        input.fill(4567);
        meter.Sample(true, input.data(), 160, 50.0);
        meter.Publish(50.0);
        std::ifstream in(path);
        std::string payload((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
        if (payload.find("\"inboundAudioPeak\":4567") == std::string::npos) return 8;
        // Fail open on unwritable destinations: PCM still reaches the meter.
        setenv("ASR_TCD_AUDIO_METER_PATH", "/asr136-does-not-exist/audio-meter.json", 1);
        meter.Sample(false, output.data(), 160, 51.0);
        meter.Publish(51.0);
        if (meter.Read(51.0).outbound.peak != 8192) return 9;
        setenv("ASR_TCD_AUDIO_METER_PATH", "/../tmp/asr136-unsafe.json", 1);
        meter.Sample(false, output.data(), 160, 52.0);
        meter.Publish(52.0);
        if (meter.Read(52.0).outbound.peak != 8192) return 10;
    }
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='asr-urf-pcm-') as tmp:
    source_file = Path(tmp) / 'meter.cpp'
    binary = Path(tmp) / 'meter'
    source_file.write_text(source)
    selected_binary = os.environ.get('ASR_URF_METER_TEST_BINARY')
    if not selected_binary:
        subprocess.run(['g++', '-std=c++17', '-O0', '-pthread', '-I', str(patch), str(source_file), '-o', str(binary)], check=True)
        selected_binary = str(binary)
    meter_file = Path(tmp) / 'audio-meter.json'
    env = {**os.environ, 'ASR_TCD_AUDIO_METER_TEST_PATH': str(meter_file)}
    subprocess.run([selected_binary], check=True, env=env)
    published = json.loads(meter_file.read_text())
    assert published['inboundAudioPeak'] == 4567
    assert published['outboundAudioPeak'] == 0
    assert published['inboundAudioEpoch'] == 50.0
    assert published['outboundAudioEpoch'] == 0
    assert stat.S_IMODE(meter_file.stat().st_mode) == 0o644
    assert meter_file.stat().st_nlink == 1
    assert not list(Path(tmp).glob('audio-meter.json.*'))
print('URF actual PCM accumulator, stale decay, validation, concurrency: PASS')
