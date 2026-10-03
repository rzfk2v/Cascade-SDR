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
    BLACK_HZ,
    CENTER_HZ,
    MODES,
    SstvDecoder,
    SYNC_HZ,
    TRAIN_WINDOW_S,
    WHITE_HZ,
)

FS = 48_000.0


def _vis_bits(code: int) -> list[float]:
    """7 data bits (LSB first) + even parity, as tone frequencies."""
    bits = [(code >> i) & 1 for i in range(7)]
    bits.append(sum(bits) & 1)  # even parity
    return [1100.0 if b else 1300.0 for b in bits]


def _hold(hz: np.ndarray, total_ms: float) -> np.ndarray:
    """Pixel tones held for exactly `total_ms` between them. Rounding samples
    *per pixel* instead (13.2 -> 13) made Robot 36 lines 151.3 ms, not 150: a
    0.9% slant no real transmitter has, hidden by per-line sync re-anchoring."""
    n = int(round(total_ms * FS / 1000.0))
    return hz[np.arange(n) * hz.size // n]


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
                freqs.append(_hold(1500.0 + vals / 255.0 * 800.0, dur))

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
        freqs.append(_hold(1500.0 + np.clip(vals, 0, 255) / 255.0 * 800.0, total_ms))

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


def test_robot36_with_dc_offset():
    """An off-centre FM carrier (Doppler on an ISS pass: ~10 kHz at 70 cm against
    5 kHz deviation) demodulates to a DC level twice the tone's amplitude."""
    mode = next(m for m in MODES.values() if m.name == "Robot 36")
    img = _gradient_image(mode.width, mode.height)
    audio = encode_yuv(mode, img)
    audio = audio / np.max(np.abs(audio)) + 2.0
    started, rows = _decode(mode, audio)
    assert started.get("name") == mode.name, f"mode detect: {started}"
    assert len(rows) >= mode.height - 2, f"got {len(rows)} rows"


def test_second_picture_after_the_first():
    """ARISS sends a picture every couple of minutes: once one completes the
    decoder must go back to listening for the next VIS header."""
    mode = next(m for m in MODES.values() if m.name == "Robot 36")
    img = _gradient_image(mode.width, mode.height)
    one = encode_yuv(mode, img)
    gap = np.random.default_rng(0).standard_normal(int(FS * 3)) * 0.3
    starts: list[str] = []
    rows: list[np.ndarray] = []
    dec = SstvDecoder(FS, on_start=lambda n, w, h: starts.append(n),
                      on_row=lambda r: rows.append(r.copy()))
    audio = np.concatenate([gap, one, gap, one, gap])
    for i in range(0, audio.size, 1024):
        dec.process(audio[i:i + 1024])
    assert starts == [mode.name, mode.name], starts
    assert len(rows) >= 2 * (mode.height - 2), f"got {len(rows)} rows"
    # Bounded by the sync-train window (the VIS search itself only looks at
    # the unexamined tail, so its cost doesn't grow with this).
    assert dec._f.size < FS * (TRAIN_WINDOW_S + 2), "search buffer must stay bounded"


def test_mode_keeps_channel_set_before_start():
    """The client sends the channel (off-centre, clear of the DC spike) while the
    dongle is still opening; starting the mode must not snap it to the centre."""
    from app.modes.sstv import SstvMode

    class Manager:
        center_freq = 438_150_000.0
        sample_rate = 2_400_000.0

        def emit_json(self, msg): pass
        def emit_binary(self, tag, body): pass

    mode = SstvMode(Manager())
    mode.configure({"tuned_freq": 437_550_000.0, "bandwidth": 36_000.0})
    mode.on_start()
    mode.process(np.zeros(4096, dtype=np.complex64))
    assert mode.tuned_freq == 437_550_000.0

    bare = SstvMode(Manager())          # no channel sent: decode the centre
    bare.on_start()
    assert bare.tuned_freq == Manager.center_freq


def test_vis_table_unique():
    assert len({m.vis for m in MODES.values()}) == len(MODES)


def _scan_reference(dec: SstvDecoder, start: float, dur: float, width: int) -> np.ndarray:
    """The original per-pixel _scan: the vectorized one must match it exactly."""
    out = np.zeros(width, dtype=np.uint8)
    px = dur / width
    for i in range(width):
        seg = dec._slice(start + i * px, start + (i + 1) * px)
        f = float(np.mean(seg)) if seg.size else BLACK_HZ
        v = (f - BLACK_HZ) / (WHITE_HZ - BLACK_HZ)
        out[i] = int(np.clip(v, 0.0, 1.0) * 255.0 + 0.5)
    return out


@pytest.mark.parametrize("start,dur,width", [
    (1_000.5, 7_000.0, 640),     # ordinary sweep, half-sample start
    (1_000.0, 300.0, 640),       # slots under a sample wide: some come out empty
    (-80.0, 2_000.0, 320),       # starts before the retained history
    (9_000.0, 2_500.0, 320),     # runs off the end of the buffer
    (20_000.0, 500.0, 64),       # entirely past the buffer: all black
])
def test_vectorized_scan_matches_per_pixel_reference(start, dur, width):
    dec = SstvDecoder(FS, on_start=lambda *a: None, on_row=lambda r: None)
    rng = np.random.default_rng(int(start) & 0xFFFF)
    dec._f = rng.uniform(BLACK_HZ - 200.0, WHITE_HZ + 200.0, size=10_000)  # past both clips
    dec._origin = 37
    np.testing.assert_array_equal(dec._scan(start, dur, width),
                                  _scan_reference(dec, start, dur, width))


# --- starting a picture from its syncs (no VIS header) ----------------------

HEADER_MS = sum(ms for _, ms in _vis_header()) + 30.0 + 8 * 30.0 + 30.0   # + start/bits/stop


def _headerless(mode, img: np.ndarray, noise: float, seed: int = 3) -> np.ndarray:
    """The picture as a weak pass delivers it: header lost, noise either side."""
    enc = encode_yuv if mode.color in ("ROBOT36", "YUV422", "PD") else encode
    pic = enc(mode, img)[int(HEADER_MS * FS / 1000.0):]
    rng = np.random.default_rng(seed)
    audio = np.concatenate([rng.standard_normal(int(3 * FS)) * 0.3, pic,
                            rng.standard_normal(int(2 * FS)) * 0.3])
    return audio + rng.standard_normal(audio.size) * noise


@pytest.mark.parametrize("noise,mae_limit,max_lost", [
    (0.0, 5.0, 2),     # clean: proves the start line, line timing and colour parity
    (0.2, 20.0, 8),    # noisy: still found, right mode — the error is the noise
])
@pytest.mark.parametrize("vis_code", [8, 12, 44, 60, 95])   # Robot 36/72, Martin M1, Scottie S1, PD 120
def test_picture_starts_from_its_syncs_without_a_header(vis_code, noise, mae_limit, max_lost):
    mode = MODES[vis_code]
    img = _gradient_image(mode.width, mode.height)
    audio = _headerless(mode, img, noise=noise)
    starts, rows = [], []
    dec = SstvDecoder(FS, on_start=lambda n, w, h: starts.append((n, dec.started_by)),
                      on_row=lambda r: rows.append(r.copy()))
    for i in range(0, audio.size, 1024):
        dec.process(audio[i:i + 1024])
    # One picture, the right mode — not a harmonic (Robot 36 folds perfectly at
    # Robot 72's and Scottie DX's line periods too) — found by its syncs.
    assert starts == [(mode.name, "sync")], starts
    assert len(rows) >= mode.height - 2, f"got {len(rows)} rows"
    got = np.array(rows[:mode.height]).reshape(-1, mode.width, 3).astype(float)
    inner = slice(10, mode.width - 10)
    # Align for any lines lost at the top (Robot 36 skips one to start on R-Y).
    mae, lost = min((np.abs(got[2:mode.height - k - 2, inner] - img[k + 2:mode.height - 2, inner]).mean(), k)
                    for k in range(0, 9))
    assert lost <= max_lost, f"{mode.name}: started {lost} lines in"
    assert mae < mae_limit, f"{mode.name}: mean abs error {mae:.1f}"


def test_noise_never_starts_a_picture():
    """The sync search must not hallucinate a train in a minute of noise."""
    rng = np.random.default_rng(11)
    audio = rng.standard_normal(int(60 * FS)) * 0.5
    starts = []
    dec = SstvDecoder(FS, on_start=lambda n, w, h: starts.append(n))
    for i in range(0, audio.size, 1024):
        dec.process(audio[i:i + 1024])
    assert starts == []


def _find_sync_reference(dec: SstvDecoder, expect_abs: float):
    """The original per-sample _find_sync loop: the vectorized one must match it."""
    m = dec.mode
    tol = int(dec._ms(min(9.0, m.sync_ms + m.sep_ms)))
    run = max(1, int(dec._ms(m.sync_ms * 0.6)))
    lo = int(expect_abs) - tol
    seg = dec._slice(lo, int(expect_abs) + tol + run)
    if seg.size < run + 2:
        return None
    band = np.abs(seg - SYNC_HZ) < 60.0
    first_ok = None
    for k in range(1, seg.size - run):
        if band[k:k + run].mean() > 0.8:
            if first_ok is None:
                first_ok = k
            if not band[k - 1]:
                return lo + k
    return lo + first_ok if first_ok is not None else None


@pytest.mark.parametrize("vis_code", [8, 44, 95])
def test_vectorized_find_sync_matches_reference(vis_code):
    dec = SstvDecoder(FS)
    dec.mode = MODES[vis_code]
    rng = np.random.default_rng(vis_code)
    f = rng.uniform(1000.0, 2400.0, size=200_000)
    for at in rng.integers(1_000, 199_000, size=300):          # plant sync runs, some ragged
        f[at:at + rng.integers(50, 1_200)] = SYNC_HZ + rng.normal(0, 40, 1)
    dec._f, dec._origin = f, 0
    for expect in rng.integers(0, 200_000, size=400):
        assert dec._find_sync(float(expect)) == _find_sync_reference(dec, float(expect)), expect


def test_sstv_mode_catches_a_weak_drifting_pass():
    """End to end, the failure from a real pass: no header, a carrier sliding
    with Doppler, in noise. The tracker must follow it and the picture must
    start from its syncs, through the whole SSTV mode chain."""
    from app.hub import FrameTag
    from app.modes.sstv import SstvMode

    RF = 240_000.0                         # a 240 kHz "dongle" keeps the test light
    CHAN = 50_000.0                        # channel offset from the capture centre

    class Manager:
        center_freq = 437_500_000.0
        sample_rate = RF

        def __init__(self):
            self.json, self.rows = [], []

        def emit_json(self, msg):
            self.json.append(msg)

        def emit_binary(self, tag, body):
            if tag == FrameTag.SSTV:
                self.rows.append(np.frombuffer(body, dtype=np.uint8).copy())

    mode36 = MODES[8]
    img = _gradient_image(mode36.width, mode36.height)
    pic = encode_yuv(mode36, img)[int(HEADER_MS * FS / 1000.0):]
    lead, tail = int(2.0 * RF), int(1.0 * RF)
    n = lead + int(pic.size * RF / FS) + tail
    t = np.arange(n) / RF
    audio = np.zeros(n)
    audio[lead:n - tail] = np.interp(np.arange(n - lead - tail) * FS / RF, np.arange(pic.size), pic)
    doppler = 5_000.0 - 3_000.0 * t / t[-1]                  # +5 -> +2 kHz across the pass
    inst = CHAN + doppler + 3_000.0 * audio                  # ±3 kHz deviation, like the ISS
    rng = np.random.default_rng(5)
    iq = np.exp(1j * 2 * np.pi * np.cumsum(inst) / RF)
    iq[:lead] = 0.0                                          # nothing on the air until it starts
    iq = (iq + 0.5 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)
    del inst, audio

    mgr = Manager()
    mode = SstvMode(mgr)
    mode.configure({"tuned_freq": mgr.center_freq + CHAN, "bandwidth": 36_000.0, "squelch": -120.0})
    mode.on_start()
    afc_err = []
    for i in range(0, n - 5_120 + 1, 5_120):                 # 21 ms blocks, as live
        mode.process(iq[i:i + 5_120])
        lvl = next((m for m in reversed(mgr.json) if m.get("type") == "radio_level"), None)
        if lvl and lvl.get("afc") and lvl["afc"]["lock"] and i > lead + RF:
            afc_err.append(abs(lvl["afc"]["hz"] - doppler[i]))

    starts = [m for m in mgr.json if m.get("type") == "sstv_start"]
    assert [(m["mode"], m["by"]) for m in starts] == [("Robot 36", "sync")], starts
    assert afc_err and np.median(afc_err) < 500.0, np.median(afc_err) if afc_err else "never locked"
    assert len(mgr.rows) >= mode36.height - 2, f"got {len(mgr.rows)} rows"
    got = np.array(mgr.rows[:mode36.height]).reshape(-1, mode36.width, 3).astype(float)
    inner = slice(10, mode36.width - 10)
    mae = min(np.abs(got[2:mode36.height - k - 2, inner] - img[k + 2:mode36.height - 2, inner]).mean()
              for k in range(0, 9))
    assert mae < 20.0, f"mean abs error {mae:.1f}"
