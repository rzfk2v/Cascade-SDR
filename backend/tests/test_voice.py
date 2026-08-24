"""Offline tests for voice squelch — synthetic audio, no hardware.

The detector has to do two things: pass speech (including speech buried in
noise, where cutting someone off would be worse than the data burst we're trying
to avoid), and mute everything else that breaks a level squelch.

Run:  ./.venv/bin/python -m tests.test_voice      (from backend/)
"""
from __future__ import annotations

import numpy as np

from app.dsp.voice import VoiceDetector

FS = 48_000.0
BLOCK = 1024
NFM_AUDIO, SSB_AUDIO = 4_000.0, 3_000.0   # demod audio passbands


def _bandpass(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(x.size, 1 / FS)
    X[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(X, n=x.size)


def _speech(n: int, seed: int) -> np.ndarray:
    """A glottal pulse train shaped to the voice band, gated at the syllable rate."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / FS
    pitch = 120 + 15 * np.sin(2 * np.pi * 0.7 * t)
    src = np.sign(np.sin(2 * np.pi * np.cumsum(pitch) / FS)) + 0.3 * rng.standard_normal(n)
    env = np.zeros(n)
    i = 0
    while i < n:                                   # words, with gaps between them
        on = int(FS * rng.uniform(0.12, 0.35))
        env[i:i + on] = rng.uniform(0.5, 1.0)
        i += on + int(FS * rng.uniform(0.06, 0.25))
    k = int(FS * 0.05)                             # 50 ms attack/decay
    env = np.convolve(env, np.ones(k) / k, mode="same")
    return _bandpass(src, 250, 3400) * env


def _hiss(n: int, seed: int) -> np.ndarray:
    """FM discriminator noise — rises with frequency, hence the tilt."""
    rng = np.random.default_rng(seed)
    X = np.fft.rfft(rng.standard_normal(n)) * (np.fft.rfftfreq(n, 1 / FS) / 4_000.0)
    return _bandpass(np.fft.irfft(X, n=n), 100, 4_000) * 0.5


def _carrier(n: int, seed: int) -> np.ndarray:
    """An unmodulated carrier: the discriminator puts out near-silence."""
    return _bandpass(0.02 * np.random.default_rng(seed).standard_normal(n), 100, 4_000)


def _tone(n: int) -> np.ndarray:
    return 0.5 * np.sin(2 * np.pi * 1_000 * np.arange(n) / FS)


def _siren(n: int) -> np.ndarray:
    """A warbling alarm: syllabic envelope, but a tone — the tonality veto's job."""
    t = np.arange(n) / FS
    return 0.5 * np.sin(2 * np.pi * 1_000 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t))


def _pocsag(n: int, seed: int) -> np.ndarray:
    """1200 baud NRZ FSK — a pager burst lands as a near-square wave."""
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, n // int(FS / 1200) + 2)
    return 0.5 * np.repeat(np.where(bits == 1, 1.0, -1.0), int(FS / 1200))[:n]


def _afsk(n: int, seed: int) -> np.ndarray:
    """Bell 202: continuous 1200/2200 Hz data."""
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, n // int(FS / 1200) + 2)
    f = np.repeat(np.where(bits == 1, 1200.0, 2200.0), int(FS / 1200))[:n]
    return 0.5 * np.sin(2 * np.pi * np.cumsum(f) / FS)


def _open_fraction(sig: np.ndarray, audio_hz: float = NFM_AUDIO,
                   sens: str = "normal") -> float:
    """Share of the run (once the window has filled) with the gate open."""
    det = VoiceDetector(FS, audio_hz, sens)
    opened = decided = 0
    for i in range(0, sig.size - BLOCK, BLOCK):
        det.process(sig[i:i + BLOCK])
        if det._filled >= 64:                      # past probation, really deciding
            decided += 1
            opened += det.speaking
    return opened / max(decided, 1)


def test_speech_opens_at_every_sensitivity():
    sig = _speech(int(FS * 12), 1)
    for sens in ("lenient", "normal", "strict"):
        frac = _open_fraction(sig, sens=sens)
        assert frac > 0.95, (sens, frac)
    print("✓ speech: gate open >95% of the time at every sensitivity")


def test_speech_in_noise_still_opens():
    """Weak speech must not be cut off — that's worse than the data it mutes."""
    n = int(FS * 12)
    for snr_db in (12, 6, 0, -3):
        sig = 10 ** (snr_db / 20.0) * _speech(n, 2) + _hiss(n, 3)
        frac = _open_fraction(sig)
        assert frac > 0.9, (snr_db, frac)
    print("✓ speech in hiss: still opens down to −3 dB SNR")


def test_non_voice_stays_muted():
    n = int(FS * 12)
    for name, sig in (("hiss", _hiss(n, 4)), ("dead carrier", _carrier(n, 5)),
                      ("1 kHz tone", _tone(n)), ("warbling siren", _siren(n)),
                      ("POCSAG", _pocsag(n, 6)), ("AFSK", _afsk(n, 7))):
        for audio_hz in (NFM_AUDIO, SSB_AUDIO):
            frac = _open_fraction(sig, audio_hz)
            assert frac < 0.05, (name, audio_hz, frac)
    print("✓ carriers, tones, sirens, pager and packet data: gate stays shut")


def test_probation_passes_audio_before_deciding():
    """The first word must not be clipped while the window fills."""
    det = VoiceDetector(FS, NFM_AUDIO)
    assert det.speaking
    sig = _pocsag(int(FS * 2), 8)                  # not voice, but it takes time to know
    heard = 0.0
    for i in range(0, sig.size - BLOCK, BLOCK):
        if det.speaking:
            heard += BLOCK / FS
        det.process(sig[i:i + BLOCK])
    assert 0.4 < heard < 1.2, heard               # decided within about a window
    print(f"✓ probation: audio flows for {heard:.2f} s before a verdict, then mutes")


def test_reset_returns_to_probation():
    det = VoiceDetector(FS, NFM_AUDIO)
    det.process(_pocsag(int(FS * 3), 9))
    assert not det.speaking, "should have muted on data"
    det.reset()
    assert det.speaking, "reset must reopen for the next transmission"
    print("✓ reset: back to probation, so the next over starts unclipped")


def test_speech_is_not_chopped_between_words():
    """Natural pauses must not close the gate — that's what the hang time is for."""
    det = VoiceDetector(FS, NFM_AUDIO)
    sig = _speech(int(FS * 60), 10)
    shut = worst = 0.0
    for i in range(0, sig.size - BLOCK, BLOCK):
        det.process(sig[i:i + BLOCK])
        if det._filled >= 64:
            shut = 0.0 if det.speaking else shut + BLOCK / FS
            worst = max(worst, shut)
    assert worst < 0.5, worst
    print(f"✓ continuous speech: longest gap with the gate shut is {worst:.2f} s")


if __name__ == "__main__":
    test_speech_opens_at_every_sensitivity()
    test_speech_in_noise_still_opens()
    test_non_voice_stays_muted()
    test_probation_passes_audio_before_deciding()
    test_reset_returns_to_probation()
    test_speech_is_not_chopped_between_words()
    print("all voice squelch tests passed")
