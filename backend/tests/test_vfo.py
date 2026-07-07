"""Extra receivers (sub-VFOs B/C/D) — demod, squelch gating, and mixing.

A sub-VFO is an independent channelizer + demod chain fed from the same
captured band as the main channel; its audio is mixed into the emitted stream.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from app.hub import FrameTag
from app.modes.radio import AUDIO_RATE, RadioMode, SubVfo

SR = 2_400_000.0
CENTER = 100e6
BLOCK = 51_200


def _fm_carrier(offset_hz: float, tone_hz: float = 1_000.0,
                dev: float = 3_000.0, amp: float = 0.5) -> np.ndarray:
    """One block of complex IQ: an NFM carrier at ``offset_hz`` from centre."""
    t = np.arange(BLOCK) / SR
    phase = 2.0 * np.pi * offset_hz * t + (dev / tone_hz) * np.sin(2.0 * np.pi * tone_hz * t)
    return (amp * np.exp(1j * phase)).astype(np.complex128)


def _noise(scale: float = 1e-4) -> np.ndarray:
    rng = np.random.default_rng(7)
    return (rng.normal(scale=scale, size=BLOCK)
            + 1j * rng.normal(scale=scale, size=BLOCK)).astype(np.complex128)


def test_subvfo_demodulates_offset_carrier():
    v = SubVfo()
    v.config({"slot": 1, "on": True, "freq": CENTER + 300e3, "demod": "nfm",
              "squelch": -60.0, "volume": 1.0})
    a1 = v.process(_fm_carrier(300e3), CENTER, SR)
    a2 = v.process(_fm_carrier(300e3), CENTER, SR)   # past the gate ramp
    assert a1 is not None and a1.size == BLOCK / (SR / AUDIO_RATE)
    assert v.open and v.level_db > -20.0
    assert float(np.sqrt(np.mean(a2 ** 2))) > 0.05   # the 1 kHz tone is audible


def test_subvfo_squelch_closes_on_noise():
    v = SubVfo()
    v.config({"slot": 1, "on": True, "freq": CENTER + 300e3, "demod": "nfm",
              "squelch": -60.0, "volume": 1.0})
    v.process(_noise(), CENTER, SR)
    a = v.process(_noise(), CENTER, SR)              # gate fully closed by now
    assert not v.open
    assert float(np.max(np.abs(a))) == 0.0


def test_subvfo_outside_band_returns_none():
    v = SubVfo()
    v.config({"slot": 1, "on": True, "freq": CENTER + 2e6})  # > sr/2 away
    assert v.process(_fm_carrier(300e3), CENTER, SR) is None
    assert not v.open


class _FakeManager(SimpleNamespace):
    def __init__(self) -> None:
        super().__init__(sample_rate=SR, center_freq=CENTER,
                         json_msgs=[], audio=[])

    def emit_json(self, msg: dict) -> None:
        self.json_msgs.append(msg)

    def emit_binary(self, tag: int, data: bytes) -> None:
        if tag == FrameTag.AUDIO:
            self.audio.append(data)


def test_radio_mixes_subvfo_without_main_tune():
    """An enabled sub-VFO produces audio even before the main channel is tuned."""
    mgr = _FakeManager()
    mode = RadioMode(mgr)
    mode.configure({"vfo": {"slot": 1, "on": True, "freq": CENTER + 300e3,
                            "demod": "nfm", "squelch": -60.0, "volume": 1.0}})
    assert not mode._user_tuned
    for _ in range(3):
        mode.process(_fm_carrier(300e3))
    assert mgr.audio, "expected mixed audio frames from the sub-VFO alone"
    pcm = np.frombuffer(mgr.audio[-1], dtype="<i2")
    assert float(np.max(np.abs(pcm))) > 500          # tone present, not silence
    levels = [m for m in mgr.json_msgs if m.get("type") == "radio_level"]
    assert levels and levels[-1]["vfos"][0]["open"]
    assert len(levels[-1]["vfos"]) == 3


def test_radio_config_reports_vfos():
    mgr = _FakeManager()
    mode = RadioMode(mgr)
    mode.configure({"vfo": {"slot": 2, "on": True, "freq": CENTER - 200e3,
                            "demod": "am"}})
    cfg = [m for m in mgr.json_msgs if m.get("type") == "radio_config"][-1]
    assert cfg["vfos"][1] == {"on": True, "freq": CENTER - 200e3, "demod": "am",
                              "squelch": -60.0, "volume": 0.7}
    # slots are 1-based from the client; bad slots are ignored
    mode.configure({"vfo": {"slot": 9, "on": True}})
    cfg = [m for m in mgr.json_msgs if m.get("type") == "radio_config"][-1]
    assert all(not v["on"] for i, v in enumerate(cfg["vfos"]) if i != 1)


def test_radio_mixes_main_and_subvfo():
    """Main channel and a sub-VFO on different carriers both reach the mix."""
    mgr = _FakeManager()
    mode = RadioMode(mgr)
    mode.configure({"demod": "nfm", "tuned_freq": CENTER - 300e3,
                    "squelch": -60.0, "volume": 1.0,
                    "vfo": {"slot": 1, "on": True, "freq": CENTER + 300e3,
                            "demod": "nfm", "squelch": -60.0, "volume": 1.0}})
    two = _fm_carrier(-300e3, tone_hz=700.0) + _fm_carrier(300e3, tone_hz=1_500.0)
    for _ in range(3):
        mode.process(two)
    assert mgr.audio
    levels = [m for m in mgr.json_msgs if m.get("type") == "radio_level"]
    assert levels[-1]["open"] and levels[-1]["vfos"][0]["open"]
    # both tones present: spectrum of the last frame has peaks at 700 & 1500 Hz
    pcm = np.frombuffer(mgr.audio[-1], dtype="<i2")[0::2].astype(np.float64)
    spec = np.abs(np.fft.rfft(pcm * np.hanning(pcm.size)))
    freqs = np.fft.rfftfreq(pcm.size, 1.0 / AUDIO_RATE)
    floor = np.median(spec) + 1e-9
    for tone in (700.0, 1_500.0):
        peak = spec[np.abs(freqs - tone) < 60.0].max()
        assert peak / floor > 10.0, f"{tone} Hz tone missing from the mix"


def test_band_move_disables_stranded_subvfo():
    mgr = _FakeManager()
    mode = RadioMode(mgr)
    mode.configure({"vfo": {"slot": 1, "on": True, "freq": CENTER + 300e3,
                            "demod": "nfm"}})
    mode.process(_noise())                            # latch the current band
    mgr.center_freq = CENTER + 50e6                   # retune far away
    mode.process(_noise())
    assert not mode.vfos[0].on
