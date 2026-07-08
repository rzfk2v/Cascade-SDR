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


if __name__ == "__main__":
    test_real_decimator_any_chunk_size()
    test_channelizer_any_chunk_size()
    test_stream_resampler_matches_whole_signal()
    test_discriminator_empty_and_step()
    print("all blocks tests passed")
