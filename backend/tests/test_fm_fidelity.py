"""The WFM chain reproduces what was transmitted, not merely at the right pitch.

Companion to ``test_audio_rate.py`` (GitHub issue #2), which checks the audio
*rate* and that a 1 kHz tone comes back at 1 kHz. Those pass even when the chain
mangles the signal: a folded channel, an under-filtered decimation or a seam at
every block boundary all leave the pitch alone and wreck the sound.

So here we transmit a mathematically exact FM broadcast signal — a pre-emphasised
tone at 75 kHz deviation, phase-continuous across the whole capture — and measure
what comes back. Harmonics and intermodulation products are the chain's own,
because the input has none.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from scipy.signal import bilinear, lfilter

from app.hub import FrameTag
from app.modes.radio import AUDIO_RATE, RadioMode

CENTER = 100e6
RATES = [1_024_000.0, 1_200_000.0, 1_800_000.0, 2_048_000.0, 2_400_000.0, 3_200_000.0]
DEVIATION = 75_000.0        # broadcast peak deviation
SETTLE_S = 0.5              # skip the squelch ramp and filter start-up


class _FakeManager(SimpleNamespace):
    def __init__(self, sr: float) -> None:
        super().__init__(sample_rate=sr, center_freq=CENTER, json_msgs=[], audio=[])

    def emit_json(self, msg: dict) -> None:
        self.json_msgs.append(msg)

    def emit_binary(self, tag: int, data: bytes) -> None:
        if tag == FrameTag.AUDIO:
            self.audio.append(data)


def _fm_iq(sr: float, secs: float, tones: tuple[float, ...]) -> np.ndarray:
    """Broadcast-style WFM IQ carrying ``tones``, generated in one piece.

    Pre-emphasised (50 µs) so the receiver's de-emphasis restores it flat, which
    keeps the harmonic measurement honest — otherwise de-emphasis would quietly
    attenuate the harmonics more than the fundamental and flatter the result.
    """
    t = np.arange(int(sr * secs)) / sr
    mpx = sum(np.sin(2.0 * np.pi * f * t) for f in tones) / len(tones)
    b, a = bilinear([50e-6, 1.0], [1.0], fs=sr)     # inverse of the de-emphasis
    mpx = lfilter(b, a, mpx)
    mpx /= np.abs(mpx).max() * 1.02                 # stay inside peak deviation
    return np.exp(1j * 2.0 * np.pi * DEVIATION * np.cumsum(mpx) / sr)


def _decode(sr: float, iq: np.ndarray) -> np.ndarray:
    """Run the real RadioMode chain over the IQ; return the left channel."""
    mgr = _FakeManager(sr)
    mode = RadioMode(mgr)
    mode.configure({"demod": "wfm", "tuned_freq": CENTER, "squelch": -120.0,
                    "volume": 1.0})
    bs = mode.block_size
    for i in range(0, iq.size - bs, bs):
        mode.process(iq[i:i + bs])
    assert mgr.audio, "the chain emitted no audio"
    pcm = np.frombuffer(b"".join(mgr.audio), dtype="<i2")[0::2].astype(np.float64)
    return pcm[int(AUDIO_RATE * SETTLE_S):] / 32768.0


def _spectrum(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = 1 << int(np.floor(np.log2(x.size)))
    x = x[:n] * np.hanning(n)
    return np.fft.rfftfreq(n, 1.0 / AUDIO_RATE), np.abs(np.fft.rfft(x))


def _power_at(freqs: np.ndarray, spec: np.ndarray, hz: float,
              width: float = 12.0) -> float:
    """Peak power in a narrow band, so the Hann skirt doesn't leak in."""
    band = (freqs > hz - width) & (freqs < hz + width)
    return float(spec[band].max()) ** 2 if band.any() else 0.0


@pytest.mark.parametrize("sr", RATES)
def test_recovered_tone_is_undistorted(sr: float) -> None:
    """Harmonics of the recovered tone must sit far below it at every rate.

    A chain that folds the channel onto itself (the pre-e1274c1 fixed ÷10 IF
    decimation) or that restarts a filter every block produces harmonics here while
    leaving pitch and sample count correct.
    """
    tone = 1_000.0
    audio = _decode(sr, _fm_iq(sr, 1.8, (tone,)))
    freqs, spec = _spectrum(audio)

    fundamental = _power_at(freqs, spec, tone)
    assert fundamental > 0.0, "no tone recovered"
    harmonics = sum(_power_at(freqs, spec, tone * k)
                    for k in range(2, 8) if tone * k < AUDIO_RATE / 2)
    thd_dbc = 10.0 * np.log10(harmonics / fundamental)
    assert thd_dbc < -60.0, f"{sr/1e6:.3f} MS/s: THD {thd_dbc:.1f} dBc"


def test_two_tones_do_not_intermodulate() -> None:
    """Nonlinearity shows up between tones even when each looks clean alone."""
    sr, f1, f2 = 2_400_000.0, 300.0, 3_000.0
    audio = _decode(sr, _fm_iq(sr, 1.8, (f1, f2)))
    freqs, spec = _spectrum(audio)

    carriers = min(_power_at(freqs, spec, f1), _power_at(freqs, spec, f2))
    assert carriers > 0.0, "no tones recovered"
    for label, hz in (("f2-f1", f2 - f1), ("f2+f1", f2 + f1),
                      ("2f1-f2", abs(2 * f1 - f2)), ("2f2-f1", 2 * f2 - f1)):
        rel = 10.0 * np.log10(_power_at(freqs, spec, hz) / carriers)
        assert rel < -50.0, f"IMD product {label} at {hz:.0f} Hz: {rel:.1f} dBc"
