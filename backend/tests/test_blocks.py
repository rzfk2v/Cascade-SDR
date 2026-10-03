"""Offline tests for the core DSP blocks — streaming continuity.

Every stateful block must produce the *same* output stream whether a signal is
fed in one call or split into chunks of any size — that's what makes the audio
click-free and the decoders slip-free. These tests feed identical signals both
ways and require (near-)bit-exact agreement, including chunk sizes that are NOT
multiples of the decimation factor (the CW envelope path hits exactly that).

Run:  ./.venv/bin/python -m tests.test_blocks      (from backend/)
"""
from __future__ import annotations

import numpy as np
from scipy.signal import resample_poly

from app.dsp.blocks import (
    ComplexChannelizer,
    FmDiscriminator,
    FmTracker,
    NoiseBlanker,
    NotchFilter,
    RealDecimator,
    StreamResampler,
)

FS = 240_000.0


def test_real_decimator_any_chunk_size():
    rng = np.random.default_rng(7)
    x = rng.standard_normal(int(FS))
    ref = RealDecimator(FS, 240, 200.0).process(x)
    assert ref.size == int(FS) // 240          # exactly out_rate samples per s
    for chunk in (5120, 999, 100, 7):          # 5120 % 240 != 0 — the CW case
        d = RealDecimator(FS, 240, 200.0)
        y = np.concatenate([d.process(x[i:i + chunk])
                            for i in range(0, x.size, chunk)])
        assert y.size == ref.size, (chunk, y.size, ref.size)
        assert np.allclose(y, ref, atol=1e-12), chunk
    print("✓ RealDecimator: chunked == one-shot for any chunk size")


def test_channelizer_any_chunk_size():
    rng = np.random.default_rng(8)
    x = rng.standard_normal(int(FS)) + 1j * rng.standard_normal(int(FS))
    c = ComplexChannelizer(FS, 10, 100_000.0)
    c.set_shift(12_345.0)
    ref = c.process(x)
    c2 = ComplexChannelizer(FS, 10, 100_000.0)
    c2.set_shift(12_345.0)
    y = np.concatenate([c2.process(x[i:i + 7777])       # 7777 % 10 != 0
                        for i in range(0, x.size, 7777)])
    assert y.size == ref.size
    assert np.allclose(y, ref, atol=1e-9)
    print("✓ ComplexChannelizer: chunked == one-shot (chunk 7777, decim 10)")


def test_stream_resampler_matches_whole_signal():
    rng = np.random.default_rng(9)
    for up, down, chunk in ((19, 240, 5120), (13, 150, 1024)):  # RDS / APT ratios
        sig = rng.standard_normal(down * 400)
        whole = resample_poly(sig, up, down)
        r = StreamResampler(up, down)
        y = np.concatenate([r.process(sig[i:i + chunk])
                            for i in range(0, sig.size, chunk)])
        m = min(y.size, whole.size)
        skip = 300                     # one-time start transient (zero context)
        err = float(np.max(np.abs(y[skip:m] - whole[skip:m])))
        assert err < 1e-12, (up, down, err)
        # per-chunk output lengths must be integer & slip-free: total is exact
        assert y.size >= whole.size - down, (y.size, whole.size)
    print("✓ StreamResampler: chunked stream bit-exact vs whole-signal resample")


def test_discriminator_empty_and_step():
    d = FmDiscriminator()
    out = d.process(np.zeros(0, dtype=np.complex128))   # must not crash
    assert out.size == 0
    a = d.process(np.exp(1j * 0.3 * np.arange(10)))
    assert np.allclose(a[1:], 0.3)                       # constant phase step
    b = d.process(np.exp(1j * 0.3 * (np.arange(10) + 10)))
    assert np.allclose(b, 0.3)                           # continuous across calls
    print("✓ FmDiscriminator: empty input ok, phase continuous across chunks")


def test_noise_blanker_kills_impulses():
    rng = np.random.default_rng(11)
    n = 40_960
    sig = (0.3 * np.exp(1j * 2 * np.pi * 0.01 * np.arange(n))).astype(np.complex128)
    dirty = sig.copy()
    hits = rng.integers(0, n, 60)
    dirty[hits] += 20.0 * np.exp(1j * rng.uniform(0, 2 * np.pi, 60))  # ~36 dB spikes
    nb = NoiseBlanker()
    out = np.concatenate([nb.process(dirty[i:i + 5120]) for i in range(0, n, 5120)])
    assert float(np.max(np.abs(out))) < 1.0, "impulse survived the blanker"
    # untouched samples pass through bit-identically
    clean_mask = np.ones(n, bool)
    clean_mask[hits] = False
    assert np.array_equal(out[clean_mask], dirty[clean_mask])
    print("✓ NoiseBlanker: 36 dB impulses removed, clean samples untouched")


def test_notch_filter_kills_tone_keeps_rest():
    fs = 48_000.0
    t = np.arange(int(fs)) / fs
    x = np.sin(2 * np.pi * 1000.0 * t) + np.sin(2 * np.pi * 2500.0 * t)
    nf = NotchFilter(fs, 1000.0)
    y = np.concatenate([nf.process(x[i:i + 1024]) for i in range(0, x.size, 1024)])
    spec = np.abs(np.fft.rfft(y[4096:] * np.hanning(y.size - 4096)))
    f = np.arange(spec.size) * fs / (y.size - 4096)
    at = lambda f0: float(spec[(f > f0 - 30) & (f < f0 + 30)].max())
    rej = 20 * np.log10(at(1000) / at(2500))
    assert rej < -25, f"notch rejection only {rej:.1f} dB"
    print(f"✓ NotchFilter: 1 kHz tone {rej:.0f} dB below the kept 2.5 kHz tone")


if __name__ == "__main__":
    test_real_decimator_any_chunk_size()
    test_channelizer_any_chunk_size()
    test_stream_resampler_matches_whole_signal()
    test_discriminator_empty_and_step()
    test_noise_blanker_kills_impulses()
    test_notch_filter_kills_tone_keeps_rest()
    print("all blocks tests passed")


def _drifting_fm(seconds: float, f_start: float, f_end: float, snr_noise: float = 0.3,
                 tone: float = 1_000.0, dev: float = 3_000.0, seed: int = 1):
    """Complex baseband at FS: an FM tone whose carrier slides like satellite
    Doppler from f_start to f_end, in complex noise. Returns (signal, carrier(t))."""
    n = int(seconds * FS)
    t = np.arange(n) / FS
    carrier = f_start + (f_end - f_start) * t / seconds
    inst = carrier + dev * np.sin(2 * np.pi * tone * t)
    x = np.exp(1j * 2 * np.pi * np.cumsum(inst) / FS)
    rng = np.random.default_rng(seed)
    x = x + snr_noise * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    return x.astype(np.complex64), carrier


def _track(x: np.ndarray, carrier: np.ndarray):
    trk = FmTracker(FS, 5, 6_500.0, 18_000.0)
    blk = 5_120                                   # 21 ms at 240 kHz, as in the app
    errs, out = [], []
    for i in range(0, x.size - blk + 1, blk):
        out.append(trk.process(x[i:i + blk]))
        if i > FS and trk.locked:                 # after the first second
            errs.append(abs(trk.offset - carrier[i + blk // 2]))
    return np.array(errs), np.concatenate(out), x.size / blk - FS / blk


def test_tracker_follows_a_drifting_carrier():
    """Doppler on a pass: the narrow channel must stay on the carrier, and the
    demodulated tone must come out clean wherever the carrier has gone. 300 Hz/s
    is about twice the steepest 70 cm ISS Doppler, near closest approach."""
    x, carrier = _drifting_fm(10.0, +6_000.0, +3_000.0)
    errs, out, blocks = _track(x, carrier)
    assert errs.size > 0.9 * blocks, "should stay locked on a clear signal"
    assert np.median(errs) < 150.0 and errs.max() < 400.0, (np.median(errs), errs.max())
    audio = out[-int(2 * FS / 5):]                           # last 2 s, at FS/5
    spec = np.abs(np.fft.rfft(audio * np.hanning(audio.size)))
    peak = np.fft.rfftfreq(audio.size, 5.0 / FS)[np.argmax(spec[1:]) + 1]
    assert abs(peak - 1_000.0) < 20.0, f"tone came out at {peak:.0f} Hz"


def test_tracker_keeps_up_with_extreme_drift():
    """Ten times real Doppler rates: it lags (smoothing), but the carrier must
    stay well inside the ±6.5 kHz channel it demodulates."""
    x, carrier = _drifting_fm(10.0, +8_000.0, -8_000.0)
    errs, _, blocks = _track(x, carrier)
    assert errs.size > 0.9 * blocks
    assert errs.max() < 1_000.0, errs.max()


def test_tracker_ignores_a_narrow_carrier():
    """A birdie or CW carrier is narrower than any FM signal: it mustn't pull
    the channel, and noise alone mustn't lock it."""
    n = int(5.0 * FS)
    t = np.arange(n) / FS
    rng = np.random.default_rng(2)
    x = (0.5 * np.exp(1j * 2 * np.pi * 10_000.0 * t)
         + 0.3 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)
    trk = FmTracker(FS, 5, 6_500.0, 18_000.0)
    for i in range(0, n - 5_120 + 1, 5_120):
        trk.process(x[i:i + 5_120])
        assert not trk.locked
    assert trk.offset == 0.0


def test_tracker_waits_before_locking():
    """A single noisy periodogram must not be trusted (it once locked at start-up)."""
    x, _ = _drifting_fm(0.2, 5_000.0, 5_000.0, snr_noise=0.05)
    trk = FmTracker(FS, 5, 6_500.0, 18_000.0)
    trk.process(x[:5_120])
    assert not trk.locked
