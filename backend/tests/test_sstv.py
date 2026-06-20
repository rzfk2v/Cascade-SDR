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


def _vis_header() -> list[tuple[float, float]]:
    """The calibration + start/stop framing common to every mode's preamble."""
    return [(CENTER_HZ, 300.0), (SYNC_HZ, 10.0), (CENTER_HZ, 300.0)]


def _rgb_to_ycbcr(img: np.ndarray):
    """JPEG/PIL YCbCr (same convention pySSTV's encoders use). Returns Y, Cb, Cr."""
    r, g, b = (img[..., i].astype(float) for i in range(3))
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = -0.168736 * r - 0.331264 * g + 0.5 * b + 128.0
    cr = 0.5 * r - 0.418688 * g - 0.081312 * b + 128.0
    return y, cb, cr


def encode_yuv(mode, img: np.ndarray) -> np.ndarray:
    """FM-encode `img` (H×W×3 uint8) for a YUV mode (Robot36/Robot72/PD)."""
    freqs: list[np.ndarray] = []

    def tone(hz: float, ms: float) -> None:
        freqs.append(np.full(int(round(ms * FS / 1000.0)), hz))

    def scan(vals: np.ndarray, total_ms: float) -> None:
        hz = 1500.0 + np.clip(vals, 0, 255) / 255.0 * 800.0
        per = int(round(total_ms * FS / 1000.0 / vals.size))
        freqs.append(np.repeat(hz, per))

    for hz, ms in _vis_header():
        tone(hz, ms)
    tone(SYNC_HZ, 30.0)                 # start bit
    for hz in _vis_bits(mode.vis):
        tone(hz, 30.0)
    tone(SYNC_HZ, 30.0)                 # stop bit

    y, cb, cr = _rgb_to_ycbcr(img)

    if mode.color == "ROBOT36":
        for line in range(mode.height):
            tone(SYNC_HZ, mode.sync_ms)
            tone(1500.0, mode.sync_porch_ms)
            scan(y[line], mode.y_scan_ms)
            if line % 2 == 0:           # even: R-Y, separator 1500 Hz
                tone(1500.0, mode.sep_ms)
                tone(CENTER_HZ, mode.porch_ms)
                scan(cr[line], mode.c_scan_ms)
            else:                       # odd: B-Y, separator 2300 Hz
                tone(2300.0, mode.sep_ms)
                tone(CENTER_HZ, mode.porch_ms)
                scan(cb[line], mode.c_scan_ms)
    elif mode.color == "YUV422":
        for line in range(mode.height):
            tone(SYNC_HZ, mode.sync_ms)
            tone(1500.0, mode.sync_porch_ms)
            scan(y[line], mode.y_scan_ms)
            tone(1500.0, mode.sep_ms)
            tone(CENTER_HZ, mode.porch_ms)
            scan(cr[line], mode.c_scan_ms)
            tone(2300.0, mode.sep_ms)
            tone(CENTER_HZ, mode.porch_ms)
            scan(cb[line], mode.c_scan_ms)
    elif mode.color == "PD":
        scan_ms = mode.pixel_ms * mode.width
        for i in range(0, mode.height, 2):
            tone(SYNC_HZ, mode.sync_ms)
            tone(1500.0, mode.porch_ms)
            scan(y[i], scan_ms)
            scan((cr[i] + cr[i + 1]) / 2.0, scan_ms)   # R-Y averaged over the pair
            scan((cb[i] + cb[i + 1]) / 2.0, scan_ms)   # B-Y averaged over the pair
            scan(y[i + 1], scan_ms)
    else:
        raise AssertionError(f"not a YUV mode: {mode.name}")

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


@pytest.mark.parametrize(
    "vis_code", [v for v, m in MODES.items() if m.color == "RGB"]
)
def test_round_trip_rgb_modes(vis_code):
    _check(vis_code)


def _check_yuv(vis_code: int, mae_limit: float = 10.0) -> None:
    mode = MODES[vis_code]
    img = _gradient_image(mode.width, mode.height)
    audio = encode_yuv(mode, img)
    started, rows = _decode(mode, audio)

    assert started.get("name") == mode.name, f"mode detect: {started}"
    assert started.get("w") == mode.width and started.get("h") == mode.height
    assert len(rows) >= mode.height - 2, f"got {len(rows)} rows"

    # Skip the outermost rows/cols, which blur through the tone-recovery filter
    # and the cross-line chroma pairing at the very top/bottom.
    n = min(len(rows), mode.height) - 2
    got = np.array(rows[:n]).reshape(n, mode.width, 3).astype(float)
    want = img[:n].astype(float)
    inner = slice(10, mode.width - 10)
    mae = np.abs(got[2:, inner, :] - want[2:, inner, :]).mean()
    assert mae < mae_limit, f"{mode.name} mean abs error too high: {mae:.1f}"


def test_round_trip_robot36():
    _check_yuv(8)


def test_round_trip_robot72():
    _check_yuv(12)


def test_round_trip_pd120():
    _check_yuv(95)


def test_vis_table_unique():
    assert len({m.vis for m in MODES.values()}) == len(MODES)
