#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import struct

path = Path(__file__).with_name('asr-bridge-pcm-gain.py')
spec = importlib.util.spec_from_file_location('pcm_gain', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
apply = module.apply_pcm16_gain
frame = struct.pack('<5h', -32768, -1000, 0, 1000, 32767)
assert apply(frame, 0) == frame
attenuated = struct.unpack('<5h', apply(frame, -6))
assert -510 < attenuated[1] < -490 and 490 < attenuated[3] < 510
boosted = struct.unpack('<5h', apply(frame, 12))
assert boosted[0] == -32768 and boosted[-1] == 32767
assert boosted[1] < -3900 and boosted[3] > 3900
for invalid in (float('nan'), float('inf'), -25, 25, True):
    try:
        apply(frame, invalid)
    except ValueError:
        pass
    else:
        raise AssertionError(f'accepted invalid gain {invalid}')
for malformed in (b'', b'\x00'):
    try:
        apply(malformed, 0)
    except ValueError:
        pass
    else:
        raise AssertionError('accepted malformed frame')
print('Independent PCM16 gain math: unity, attenuation, boost, clipping, and validation PASS')
