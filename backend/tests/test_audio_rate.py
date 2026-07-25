"""The emitted audio stream is 48 kHz at every dongle sample rate.

Regression test for GitHub issue #2. The IF decimation used to be a fixed ÷10
and the audio decimation ``round(if_rate / 48000)``, so both the IF width and
the audio rate were only right at 2.4 MS/s. At 1.2 MS/s the chain emitted
60 kHz audio labelled as 48 kHz (the player then dropped a chunk at a time to
keep up) through a 120 kHz IF that folded a 200 kHz WFM channel onto itself.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app.dsp.blocks import ComplexChannelizer
from app.hub import FrameTag
from app.modes.radio import AUDIO_RATE, RadioMode, SubVfo, if_decim

CENTER = 100e6
# Rates the dongle can be asked for, spanning integer and awkward ratios.
RATES = [1_024_000.0, 1_200_000.0, 1_800_000.0, 2_048_000.0, 2_400_000.0, 3_200_000.0]


class _FakeManager(SimpleNamespace):
    def __init__(self, sr: float) -> None:
        super().__init__(sample_rate=sr, center_freq=CENTER, json_msgs=[], audio=[])

    def emit_json(self, msg: dict) -> None:
        self.json_msgs.append(msg)

    def emit_binary(self, tag: int, data: bytes) -> None:
        if tag == FrameTag.AUDIO:
            self.audio.append(data)


def _wfm_block(sr: float, n: int, start: int = 0, tone_hz: float = 1_000.0,
               dev: float = 75_000.0, offset_hz: float = 0.0) -> np.ndarray:
    """One block of broadcast-style WFM IQ at ``offset_hz`` from centre.

    ``start`` is the block's first sample index, so successive blocks join up
    without a phase step (a discontinuity would smear the recovered tone).
    """
    t = (start + np.arange(n)) / sr
    phase = (2.0 * np.pi * offset_hz * t
             + (dev / tone_hz) * np.sin(2.0 * np.pi * tone_hz * t))
    return (0.5 * np.exp(1j * phase)).astype(np.complex128)


@pytest.mark.parametrize("sr", RATES)
def test_audio_leaves_at_48k_whatever_the_dongle_rate(sr: float) -> None:
    mgr = _FakeManager(sr)
    mode = RadioMode(mgr)
    mode.configure({"demod": "wfm", "tuned_freq": CENTER, "squelch": -120.0,
                    "volume": 1.0})
    blocks = 12
    for _ in range(blocks):
        mode.process(_wfm_block(sr, mode.block_size))

    pcm = b"".join(mgr.audio)
    got = len(pcm) // 4                       # interleaved stereo int16
    want = blocks * mode.block_size / sr * AUDIO_RATE
    # The resampler holds a few samples of guard, so allow a small shortfall.
    assert want * 0.97 <= got <= want, (
        f"{sr/1e6:.3f} MS/s emitted {got} samples, expected ~{want:.0f} "
        f"({got / (blocks * mode.block_size / sr):.0f} Hz)")


@pytest.mark.parametrize("sr", RATES)
def test_demodulated_tone_keeps_its_pitch(sr: float) -> None:
    """A 1 kHz tone must come back at 1 kHz — a wrong audio rate transposes it."""
    mgr = _FakeManager(sr)
    mode = RadioMode(mgr)
    mode.configure({"demod": "wfm", "tuned_freq": CENTER, "squelch": -120.0,
                    "volume": 1.0})
    for i in range(24):
        mode.process(_wfm_block(sr, mode.block_size, start=i * mode.block_size))

    # skip the first frames: the squelch gate ramps in over ~5 ms
    left = np.frombuffer(b"".join(mgr.audio[4:]), dtype="<i2")[0::2].astype(float)
    spec = np.abs(np.fft.rfft(left * np.hanning(left.size)))
    freqs = np.fft.rfftfreq(left.size, 1.0 / AUDIO_RATE)
    peak = freqs[spec.argmax()]
    assert abs(peak - 1_000.0) < 5.0, f"{sr/1e6:.3f} MS/s put the tone at {peak:.0f} Hz"


@pytest.mark.parametrize("sr", RATES)
def test_if_is_wide_enough_for_the_channel(sr: float) -> None:
    """A 200 kHz WFM channel must fit inside the IF, or it aliases onto itself."""
    bw = 200_000.0
    if_rate = sr / if_decim(sr, bw)
    assert if_rate >= bw, f"{sr/1e6:.3f} MS/s -> {if_rate/1e3:.1f} kHz IF"


def test_channelizer_cutoff_cannot_pass_the_output_nyquist() -> None:
    """Asking for a cutoff above the decimated Nyquist must not fold energy back."""
    sr = 1_200_000.0
    ch = ComplexChannelizer(sr, 10, 100_000.0)      # 120 kHz out, 60 kHz Nyquist
    n = 1 << 16
    t = np.arange(n) / sr
    out = ch.process(np.exp(2j * np.pi * 80_000.0 * t).astype(np.complex128))
    spec = np.abs(np.fft.fft(out * np.hanning(out.size)))
    freqs = np.fft.fftfreq(out.size, 1.0 / ch.out_rate)
    peak = abs(freqs[spec.argmax()])
    # An 80 kHz tone is outside a 60 kHz Nyquist: it must be filtered away, not
    # reappear mirrored at 40 kHz.
    assert spec.max() < 0.01 * n, f"tone survived at {peak/1e3:.1f} kHz"


@pytest.mark.parametrize("sr", RATES)
def test_subvfo_audio_is_48k_too(sr: float) -> None:
    """Sub-VFO audio is mixed into the same stream, so it needs the same rate."""
    v = SubVfo()
    v.config({"slot": 1, "on": True, "freq": CENTER + 300e3, "demod": "nfm",
              "squelch": -120.0, "volume": 1.0})
    block = int(51_200)
    blocks = 12
    got = 0
    for _ in range(blocks):
        a = v.process(_wfm_block(sr, block, dev=3_000.0, offset_hz=300e3),
                      CENTER, sr)
        got += 0 if a is None else a.size
    want = blocks * block / sr * AUDIO_RATE
    assert want * 0.97 <= got <= want
