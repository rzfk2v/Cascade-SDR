"""Synthetic round-trip for the SSTV decoder.

We FM-encode a known image with the same per-mode timings the decoder expects
(calibration + VIS header + scan lines), feed the audio through in streaming
chunks, and check the decoded image matches. This exercises VIS auto-detect,
sync tracking, and the channel slicing without needing a real over-air signal.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.dsp.sstv import (
    CENTER_HZ,
    MODES,
    SstvDecoder,
    SYNC_HZ,
)

FS = 48_000.0


def _vis_bits(code: int) -> list[float]:
    """7 data bits (LSB first) + even parity, as tone frequencies."""
    bits = [(code >> i) & 1 for i in range(7)]
    bits.append(sum(bits) & 1)  # even parity
    return [1100.0 if b else 1300.0 for b in bits]


def encode(mode, img: np.ndarray) -> np.ndarray:
    """Build an FM audio waveform for `img` (H×W×3 uint8) in the given mode."""
    freqs: list[np.ndarray] = []

    def tone(hz: float, ms: float) -> None:
        freqs.append(np.full(int(round(ms * FS / 1000.0)), hz))

    # calibration header + VIS word
    tone(CENTER_HZ, 300.0)
    tone(SYNC_HZ, 10.0)
    tone(CENTER_HZ, 300.0)
    tone(SYNC_HZ, 30.0)                 # start bit
    for hz in _vis_bits(mode.vis):
        tone(hz, 30.0)
    tone(SYNC_HZ, 30.0)                 # stop bit

    if mode.leading_sync:
        tone(SYNC_HZ, mode.sync_ms)

    for row in range(mode.height):
        for kind, ch, dur in mode.segments:
            if kind == "sync":
                tone(SYNC_HZ, dur)
            elif kind == "sep":
                tone(1500.0, dur)
            else:  # scan: one pixel per slot, value -> 1500..2300 Hz
                vals = img[row, :, ch].astype(float)
                hz = 1500.0 + vals / 255.0 * 800.0
                per = int(round(dur * FS / 1000.0 / mode.width))
                freqs.append(np.repeat(hz, per))

    f = np.concatenate(freqs)
    phase = np.cumsum(2.0 * np.pi * f / FS)
    return np.cos(phase)


def _gradient_image(w: int, h: int) -> np.ndarray:
    """A smooth test image (smoothness keeps it robust to the tone-recovery LPF)."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    xs = np.linspace(0, 255, w)
    ys = np.linspace(0, 255, h)
    for y in range(h):
        img[y, :, 0] = xs.astype(np.uint8)                      # R: horizontal ramp
        img[y, :, 1] = np.uint8(ys[y])                          # G: vertical ramp
        img[y, :, 2] = ((xs + ys[y]) / 2).astype(np.uint8)     # B: diagonal
    return img


def _decode(mode, audio: np.ndarray):
    rows: list[np.ndarray] = []
    started = {}

    def on_start(name, w, h):
        started.update(name=name, w=w, h=h)

    dec = SstvDecoder(FS, on_start=on_start, on_row=lambda r: rows.append(r.copy()))
    for i in range(0, audio.size, 50_000):       # stream in chunks
        dec.process(audio[i:i + 50_000])
    return started, rows


def _check(vis_code: int) -> None:
    mode = MODES[vis_code]
    img = _gradient_image(mode.width, mode.height)
    audio = encode(mode, img)
    started, rows = _decode(mode, audio)

    assert started.get("name") == mode.name, f"mode detect: {started}"
    assert started.get("w") == mode.width and started.get("h") == mode.height
    assert len(rows) >= mode.height - 1, f"got {len(rows)} rows"

    # Compare interior pixels (edges blur through the tone-recovery filter).
    n = min(len(rows), mode.height) - 2
    got = np.array(rows[:n]).reshape(n, mode.width, 3).astype(float)
    want = img[:n].astype(float)
    inner = slice(10, mode.width - 10)
    mae = np.abs(got[:, inner, :] - want[:, inner, :]).mean()
    assert mae < 8.0, f"{mode.name} mean abs error too high: {mae:.1f}"


@pytest.mark.parametrize("vis_code", list(MODES))
def test_round_trip_all_modes(vis_code):
    _check(vis_code)


def test_vis_table_unique():
    assert len({m.vis for m in MODES.values()}) == len(MODES)
