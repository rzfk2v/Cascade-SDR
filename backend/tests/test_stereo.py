"""Offline test for the FM stereo decoder — proves channel separation.

Build a synthetic FM multiplex carrying a tone in the LEFT channel only, decode
it, and confirm the recovered RIGHT channel is far below the left (good
separation). Also check that a mono multiplex (no pilot) falls back cleanly.

Run:  ./.venv/bin/python -m tests.test_stereo      (from backend/)
"""
from __future__ import annotations

import numpy as np

from app.dsp.blocks import RealDecimator, StereoDecoder

FS = 240_000.0
DECIM = 5            # 240k -> 48k


def _decode(mpx):
    mono = RealDecimator(FS, DECIM, 15_000.0).process(mpx)
    s, pilot_frac = StereoDecoder(FS, DECIM, 15_000.0).process(mpx)
    left, right = mono + s, mono - s
    return left, right, pilot_frac


def _power_db(x, ref):
    skip = len(x) // 5          # drop filter warm-up
    p = np.mean(x[skip:] ** 2)
    pr = np.mean(ref[skip:] ** 2)
    return 10.0 * np.log10((p + 1e-20) / (pr + 1e-20))


def test_left_only_separation():
    t = np.arange(int(FS * 0.6)) / FS
    L = np.cos(2 * np.pi * 1000 * t)        # 1 kHz tone in LEFT only
    R = np.zeros_like(t)
    m, s = L + R, L - R                       # sum / difference
    mpx = m + s * np.cos(2 * np.pi * 38_000 * t) + 0.1 * np.cos(2 * np.pi * 19_000 * t)
    mpx += 0.01 * np.random.randn(mpx.size)
    left, right, pilot = _decode(mpx)
    sep = _power_db(right, left)             # right should be far below left
    assert pilot > 0.03, f"pilot not detected: {pilot}"
    assert sep < -20.0, f"poor separation: {sep:.1f} dB"
    print(f"✓ left-only: pilot={pilot:.3f}, R/L = {sep:.1f} dB (separation)")


def test_mono_no_pilot():
    t = np.arange(int(FS * 0.4)) / FS
    mono = np.cos(2 * np.pi * 1000 * t)     # mono, no pilot, no subcarrier
    mpx = mono + 0.01 * np.random.randn(mono.size)
    left, right, pilot = _decode(mpx)
    # with no pilot, L and R must be (essentially) identical -> no S leakage
    diff = _power_db(left - right, left)
    assert pilot < 0.03, f"false pilot: {pilot}"
    assert diff < -40.0, f"S leaked into mono: {diff:.1f} dB"
    print(f"✓ mono/no-pilot: pilot={pilot:.3f}, (L-R)/L = {diff:.1f} dB")


if __name__ == "__main__":
    test_left_only_separation()
    test_mono_no_pilot()
    print("all stereo tests passed")
