#!/usr/bin/env python3
"""Pure PCM16 gain processing for bridge RX/TX paths; no service side effects."""
import math
import struct


def apply_pcm16_gain(frame: bytes, gain_db: float) -> bytes:
    """Scale signed little-endian PCM16 and saturate, without wraparound."""
    if not isinstance(frame, bytes) or not frame or len(frame) % 2:
        raise ValueError('expected nonempty aligned PCM16 frame')
    if isinstance(gain_db, bool) or not isinstance(gain_db, (int, float)) or not math.isfinite(gain_db) or not -24 <= gain_db <= 24:
        raise ValueError('gain must be between -24 and +24 dB')
    if gain_db == 0:
        return frame
    factor = 10 ** (gain_db / 20)
    values = (max(-32768, min(32767, round(value * factor))) for (value,) in struct.iter_unpack('<h', frame))
    return struct.pack('<' + 'h' * (len(frame) // 2), *values)
