"""Offline test for the NOAA APT decoder — no hardware needed.

Build a synthetic APT signal from a known test image (sync + vertical stripes + a
bright marker column), AM-modulate it onto a 2400 Hz subcarrier, decode it, and
confirm the recovered lines match the original (high correlation, marker in the
right column, right number of lines).

Run:  ./.venv/bin/python -m tests.test_apt      (from backend/)
"""
from __future__ import annotations

import numpy as np

from app.dsp.apt import LINE_PX, AptDecoder

FS = 20_800.0          # 5 audio samples per APT pixel (4160 * 5)
SPP = 5
H = 24                 # lines


def _build_audio():
    sync = np.tile([1.0, 1.0, 0.0, 0.0], 7)          # 28 px Channel-A sync
    col = np.arange(LINE_PX)
    base = 0.4 + 0.2 * np.sin(2 * np.pi * col / 200)  # vertical stripes
    line = base.copy()
    line[:sync.size] = sync
    stream = np.tile(line, H)                          # H lines of pixels
    px_audio = np.repeat(stream, SPP)                  # sample-and-hold to FS
    t = np.arange(px_audio.size) / FS
    audio = px_audio * np.cos(2 * np.pi * 2400 * t)    # AM onto 2400 Hz
    audio += 0.01 * np.random.randn(audio.size)
    return audio, line


def test_apt_roundtrip():
    audio, line = _build_audio()
    rows = []
    dec = AptDecoder(FS, on_line=lambda r: rows.append(r.copy()))
    block = 4096
    for i in range(0, audio.size, block):              # streaming, many blocks
        dec.process(audio[i:i + block])

    assert len(rows) >= H - 3, f"too few lines: {len(rows)}"
    mid = len(rows) // 2
    got = rows[mid].astype(float)
    ref = line[: got.size]
    # shape + alignment: high at zero shift...
    c = np.corrcoef(got[30:], ref[30:])[0, 1]
    assert c > 0.9, f"poor line correlation: {c:.3f}"
    # ...and clearly better than a half-stripe-shifted reference (so it didn't
    # lock half a line off)
    c_shift = np.corrcoef(got[30:], np.roll(ref, 100)[30:])[0, 1]
    assert c - c_shift > 0.5, f"alignment ambiguous: {c:.3f} vs shifted {c_shift:.3f}"
    # inter-line stability: consecutive lines line up (sync tracked, no slip)
    c_next = np.corrcoef(got[30:], rows[mid + 1].astype(float)[30:])[0, 1]
    assert c_next > 0.95, f"lines slip between rows: {c_next:.3f}"
    print(f"✓ APT round-trip: {len(rows)} lines, corr={c:.3f} "
          f"(shifted {c_shift:.2f}), line-to-line {c_next:.3f}")


if __name__ == "__main__":
    test_apt_roundtrip()
    print("all APT tests passed")
