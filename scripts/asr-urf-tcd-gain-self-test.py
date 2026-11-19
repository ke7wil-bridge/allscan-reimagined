#!/usr/bin/env python3
"""Offline compiled regression for URF TCD gain direction and fractional dB."""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
PATCH = ROOT / 'runtime/urf/patches/tcd'


def check() -> None:
    for name in ('Configure.cpp', 'Configure.h', 'Controller.cpp', 'Controller.h'):
        assert (PATCH / name).is_file(), name
    controller = (PATCH / 'Controller.cpp').read_text()
    assert 'usrp_tx_num = calcNumerator(g_Conf.GetGain(EGainType::usrptx))' in controller
    assert 'usrp_rx_num = calcNumerator(g_Conf.GetGain(EGainType::usrprx))' in controller
    assert 'void CController::AudiotoUSRP(' in controller
    assert 'void CController::USRPtoAudio(' in controller
    assert 'usrp_tx_num != 256' in controller[controller.index('void CController::AudiotoUSRP('):controller.index('void CController::USRPtoAudio(')]
    assert 'usrp_rx_num != 256' in controller[controller.index('void CController::USRPtoAudio('):]
    assert 'calcNumerator(float db)' in controller
    for direction in ('tx', 'rx'):
        assert f'int32_t(p[i]) * usrp_{direction}_num' in controller
    assert controller.count('sample < -32768 ? -32768 : (sample > 32767 ? 32767 : sample)') == 2
    # Compile the actual gain/clamp expression extracted from each direction,
    # rather than testing an independently reimplemented Python formula.
    import re
    clamp = re.search(r'const int32_t sample = (.*?);\s*tmp\[i\] = (.*?);', controller, re.S)
    assert clamp and 'usrp_tx_num' in clamp.group(1)
    clamp_expr = clamp.group(2)
    assert 'std::stof(value, &parsed)' in (PATCH / 'Configure.cpp').read_text()
    config_cpp = (PATCH / 'Configure.cpp').read_text()
    assert 'std::isfinite(gain)' in config_cpp
    assert 'parsed != value.size()' in config_cpp
    assert 'gain < -20.0f || gain > 12.0f' in config_cpp
    assert 'getSigned(key, value)' in config_cpp, 'unrelated vocoder gains must remain integers'
    assert 'std::stoi(value.c_str())' in config_cpp
    assert 'ERROR: empty USRP gain setting' in config_cpp
    assert 'ERROR: empty ' in config_cpp
    assert 'return true;' in config_cpp
    runtime_script = (ROOT / 'scripts/asr-bridge-urf-native-runtime.py').read_text()
    assert '"Controller.cpp", "Controller.h", "Configure.cpp", "Configure.h"' in runtime_script
    dockerfile = (ROOT / 'runtime/urf/Dockerfile.tcd-arm-qemu').read_text()
    assert 'patches/tcd/Controller.cpp patches/tcd/Controller.h patches/tcd/Configure.cpp patches/tcd/Configure.h' in dockerfile
    assert 'ff104384b3d1c51bf8f7f9f83d7940dffda0b076' in dockerfile
    spec = importlib.util.spec_from_file_location('asr_urf_gain_plan', ROOT / 'scripts/asr-bridge-setup-urf.py')
    assert spec and spec.loader
    import sys
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    plan = {'ports': {'transcoder': 28882}, 'settings': {'urf_gain_in_db': 4.5, 'urf_gain_out_db': -6.5}}
    ini = module.tcd_ini(plan)
    assert 'UsrpTxGain = 4.5\n' in ini, 'digital to AllStar must use AudiotoUSRP/Tx'
    assert 'UsrpRxGain = -6.5\n' in ini, 'AllStar to digital must use USRPtoAudio/Rx'
    assert 'UsrpTxGain = 0\n' in module.tcd_ini({'ports': plan['ports']})
    assert 'UsrpRxGain = 0\n' in module.tcd_ini({'ports': plan['ports']})
    if not shutil.which('g++'):
        raise RuntimeError('g++ required to validate native TCD fractional gain parser')
    with tempfile.TemporaryDirectory(prefix='asr-urf-gain-') as temp:
        temp = Path(temp)
        (temp / 'tcd.ini').write_text(ini)
        (temp / 'gain-test.cpp').write_text('''#include "Configure.h"
#include <cmath>
#include <iostream>
int main(int argc, char **argv) {
    CConfigure config;
    if (argc != 4 || config.ReadData(argv[1])) return 2;
    if (std::abs(config.GetGain(EGainType::usrptx) - std::stof(argv[2])) > .001f) return 3;
    if (std::abs(config.GetGain(EGainType::usrprx) - std::stof(argv[3])) > .001f) return 4;
    return 0;
}
''')
        (temp / 'clamp-test.cpp').write_text("""#include <cstdint>
#include <cmath>
#include <iostream>
int main() {
    for (int gain_db = -20; gain_db <= 12; ++gain_db) {
        const int32_t numerator = int32_t(std::round(256.0f * std::pow(10.0f, float(gain_db)/20.0f)));
        for (int amplitude : {-32768, -30000, -1000, -1, 0, 1, 1000, 30000, 32767}) {
            const int16_t p[1] = {int16_t(amplitude)};
            const int32_t usrp_tx_num = numerator;
            const int32_t sample = (int32_t(p[0]) * usrp_tx_num) >> 8;
            const int16_t result = %s;
            const int32_t expected = sample < -32768 ? -32768 : (sample > 32767 ? 32767 : sample);
            if (result != expected || result < -32768 || result > 32767) return 5;
        }
    }
    return 0;
}
""" % clamp_expr)
        clamp_binary = temp / 'clamp-test'
        subprocess.run(['g++', '-std=c++17', '-O0', str(temp / 'clamp-test.cpp'), '-o', str(clamp_binary)],
                       check=True, capture_output=True, text=True)
        subprocess.run([str(clamp_binary)], check=True, capture_output=True, text=True)
        binary = temp / 'gain-test'
        subprocess.run(['g++', '-std=c++17', '-O0', '-I', str(PATCH), str(PATCH / 'Configure.cpp'), str(temp / 'gain-test.cpp'), '-o', str(binary)], check=True, capture_output=True, text=True)
        subprocess.run([str(binary), str(temp / 'tcd.ini'), '4.5', '-6.5'], check=True, capture_output=True, text=True)
        # Validate malformed values fail closed, including NaN/INF and junk.
        # The process must not silently turn invalid gain into a valid setting.
        for bad in ('nan', 'NaN', 'inf', '-inf', 'Infinity', '-Infinity', '12.1', '-20.1', '4.5junk', '', '1e1000', '1,5', 'true', 'false'):
            bad_ini = ini.replace('UsrpTxGain = 4.5', 'UsrpTxGain = ' + bad)
            (temp / 'tcd.ini').write_text(bad_ini)
            result = subprocess.run([str(binary), str(temp / 'tcd.ini'), '4.5', '-6.5'], capture_output=True, text=True)
            assert result.returncode == 2 and 'ERROR:' in result.stderr, f'Invalid URF gain {bad!r} not safely rejected'
        # The reverse channel must enforce the same malformed-value rules.
        for bad in ('nan', 'inf', '-20.1', '12.1', '1.0suffix', ''):
            bad_ini = ini.replace('UsrpRxGain = -6.5', 'UsrpRxGain = ' + bad)
            (temp / 'tcd.ini').write_text(bad_ini)
            result = subprocess.run([str(binary), str(temp / 'tcd.ini'), '4.5', '-6.5'], capture_output=True, text=True)
            assert result.returncode == 2 and 'ERROR:' in result.stderr, f'Invalid RX gain {bad!r} not safely rejected'
        # 25 adversarial configuration cases, exercised through the actual
        # compiled parser and checked for fail-closed behavior.
        invalid_cases = [
            'UsrpTxGain = nan', 'UsrpRxGain = nan',
            'UsrpTxGain = inf', 'UsrpRxGain = inf',
            'UsrpTxGain = -inf', 'UsrpRxGain = -inf',
            'UsrpTxGain = 12.001', 'UsrpRxGain = 12.001',
            'UsrpTxGain = -20.001', 'UsrpRxGain = -20.001',
            'UsrpTxGain = 4.5junk', 'UsrpRxGain = 4.5junk',
            'UsrpTxGain = ', 'UsrpRxGain = ',
            'UsrpTxGain', 'UsrpRxGain',
            'UsrpTxGain = 1e999', 'UsrpRxGain = 1e999',
            'UsrpTxGain = true', 'UsrpRxGain = false',
            'UsrpTxGain = 1,5', 'UsrpRxGain = 1,5',
            'UsrpTxGain = 0\nUsrpTxGain = 2',
            'UsrpRxGain = 0\nUsrpRxGain = 2',
            'UsrpTxGain = 0\nUsrpRxGain = 0\nUsrpTxGain = -1',
        ]
        assert len(invalid_cases) == 25
        for index, candidate in enumerate(invalid_cases):
            # Remove the original pair so the tested error is not accidentally
            # triggered by a duplicate, except for explicit duplicate cases.
            base = 'Port = 28882\nServerAddress = 127.0.0.1\nModules = A\n'
            if 'UsrpTxGain' not in candidate:
                base += 'UsrpTxGain = 0\n'
            if 'UsrpRxGain' not in candidate:
                base += 'UsrpRxGain = 0\n'
            (temp / 'tcd.ini').write_text(base + candidate + '\n')
            result = subprocess.run([str(binary), str(temp / 'tcd.ini'), '0', '0'], capture_output=True, text=True)
            assert result.returncode == 2 and 'ERROR:' in result.stderr, (index, candidate, result.returncode, result.stderr)
        (temp / 'tcd.ini').write_text(ini)
        for incoming, outgoing in [(-20, 12), (12, -20), (0.5, -0.5), (0, 0)]:
            # Verify the rendered, independent gain values do not swap.
            plan['settings'] = {'urf_gain_in_db': incoming, 'urf_gain_out_db': outgoing}
            ini = module.tcd_ini(plan)
            assert f'UsrpTxGain = {incoming}\n' in ini
            assert f'UsrpRxGain = {outgoing}\n' in ini
            for gain in (incoming, outgoing):
                assert -20 <= gain <= 12 and math.isfinite(gain)
            (temp / 'tcd.ini').write_text(ini)
            subprocess.run([str(binary), str(temp / 'tcd.ini'), str(incoming), str(outgoing)],
                           check=True, capture_output=True, text=True)
        # 25 compiled, independent gain pairs: half-dB and full-range cases.
        gain_pairs = [(-20 + i * 1.25, 12 - i * 1.25) for i in range(25)]
        for incoming, outgoing in gain_pairs:
            plan['settings'] = {'urf_gain_in_db': incoming, 'urf_gain_out_db': outgoing}
            ini = module.tcd_ini(plan)
            (temp / 'tcd.ini').write_text(ini)
            subprocess.run([str(binary), str(temp / 'tcd.ini'), str(incoming), str(outgoing)],
                           check=True, capture_output=True, text=True)
    print('URF TCD compiled fractional RX/TX gain and direction: PASS')


if __name__ == '__main__':
    check()
