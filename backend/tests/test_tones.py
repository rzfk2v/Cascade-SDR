"""Offline tests for CTCSS/DCS detection — synthetic NFM audio, no hardware.

Run:  ./.venv/bin/python -m tests.test_tones      (from backend/)
"""
from __future__ import annotations

import numpy as np
from scipy.signal import firwin, lfilter

from app.dsp.tones import DCS_BAUD, DCS_CODES, ToneDetector, dcs_bits, dcs_word

FS = 48_000.0
BLOCK = 1024


def _voice(n: int, seed: int, amp: float = 0.3) -> np.ndarray:
    """Speech-band (300–3000 Hz) noise, so the sub-audio band stays honest."""
    rng = np.random.default_rng(seed)
    b = firwin(257, [300 / (FS / 2), 3000 / (FS / 2)], pass_zero=False)
    return amp * lfilter(b, 1.0, rng.standard_normal(n))


def _run(sig: np.ndarray) -> ToneDetector:
    det = ToneDetector(FS)
    for i in range(0, sig.size, BLOCK):
        det.process(sig[i:i + BLOCK])
    return det


def test_ctcss_detected():
    n = int(FS * 2.5)
    t = np.arange(n) / FS
    sig = 0.12 * np.sin(2 * np.pi * 88.5 * t) + _voice(n, 1)
    det = _run(sig)
    assert det.current == "88.5", det.current
    print("✓ CTCSS: 88.5 Hz under voice detected")


def test_ctcss_none_on_plain_voice():
    det = _run(_voice(int(FS * 2.5), 2))
    assert det.current is None, det.current
    print("✓ CTCSS: no false tone on plain voice")


def _dcs_signal(code: str, invert: bool, n: int, seed: int) -> np.ndarray:
    bits = dcs_bits(code, invert)
    t = np.arange(n) / FS
    idx = (t * DCS_BAUD).astype(int) % bits.size
    nrz = bits[idx].astype(np.float64) * 2.0 - 1.0
    return 0.1 * nrz + _voice(n, seed)


def test_dcs_detected():
    det = _run(_dcs_signal("023", False, int(FS * 3.0), 3))
    assert det.current == "D023", det.current
    print("✓ DCS: code 023 under voice detected")


def _normal_alias(code: str) -> str:
    """What a normal-polarity-first detector reports for `code` inverted.

    DCS codes come in N/I alias pairs (the bit-inverse of one code's cyclic
    word is a rotation of another's) — scanners canonicalise to the normal
    set, and so does ToneDetector. Derive the expected label the same way.
    """
    w = dcs_word(code)
    inv = [((w >> i) & 1) ^ 1 for i in range(23)]
    for r in range(23):
        v = 0
        for i in range(23):
            v |= inv[(r + i) % 23] << i
        for c in DCS_CODES:
            if dcs_word(c) == v:
                return f"D{c}"
    return f"D{code}I"


def test_dcs_inverted_detected():
    det = _run(_dcs_signal("754", True, int(FS * 3.0), 4))
    expect = _normal_alias("754")
    assert det.current == expect, (det.current, expect)
    print(f"✓ DCS: inverted 754 detected as its canonical alias {expect}")


if __name__ == "__main__":
    test_ctcss_detected()
    test_ctcss_none_on_plain_voice()
    test_dcs_detected()
    test_dcs_inverted_detected()
    print("all tone tests passed")
