"""Voice squelch in the scanner — rejecting a channel must not re-park on it.

A pager or a data channel keeps transmitting for far longer than one hold
period. Muting it is only half the job: without a lockout the very next sweep
finds the same carrier still up, parks on it again, listens for another 0.6 s
and repeats — a loop that scans nothing. These cover the lockout that stops it.

Run:  ./.venv/bin/python -m tests.test_voice_scanner      (from backend/)
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np

from app.modes.scanner import DETECT_BLOCK, VOICE_SKIP_S, ScannerMode

SR = 2_400_000.0


class _FakeManager(SimpleNamespace):
    def __init__(self) -> None:
        super().__init__(sample_rate=SR, center_freq=156.2e6, gain=0,
                         freq_correction=0, converter_offset=0.0, json_msgs=[])

    def hw_freq(self, real_hz: float) -> float:
        return float(real_hz)

    def emit_json(self, msg: dict) -> None:
        self.json_msgs.append(msg)


class _FakeSdr:
    """Reads back one carrier, parked on ``freq``, over a quiet noise floor."""

    def __init__(self, freq: float, center: float) -> None:
        self.freq, self.center = freq, center
        self.center_freq = center
        self.sample_rate = SR

    def reset_buffer(self) -> None:
        pass

    def read_samples(self, n: int) -> np.ndarray:
        rng = np.random.default_rng(3)
        t = np.arange(n) / SR
        noise = (rng.normal(scale=1e-3, size=n) + 1j * rng.normal(scale=1e-3, size=n))
        return 0.5 * np.exp(2j * np.pi * (self.freq - self.center) * t) + noise


def _scanner() -> tuple[ScannerMode, dict, dict]:
    mode = ScannerMode(_FakeManager())
    mode._rebuild(SR)
    blk = mode._blocks[0]
    return mode, blk, blk["channels"][1]        # not the block centre (DC spike)


def test_detect_finds_the_active_channel():
    mode, blk, ch = _scanner()
    sdr = _FakeSdr(ch["freq"], blk["center"])
    assert mode._detect(sdr, blk, SR) is ch
    print("✓ baseline: a carrier on a channel is found")


def test_locked_out_channel_is_not_parked_on_again():
    mode, blk, ch = _scanner()
    sdr = _FakeSdr(ch["freq"], blk["center"])
    ch["_skip_until"] = time.monotonic() + VOICE_SKIP_S    # as a rejection would set it
    assert mode._detect(sdr, blk, SR) is not ch
    # the signal is real, so the display must still show it as active
    assert ch["_active"], "a locked-out channel is still an active channel"
    print("✓ lockout: the same carrier is skipped, but still shown as active")


def test_lockout_expires():
    mode, blk, ch = _scanner()
    sdr = _FakeSdr(ch["freq"], blk["center"])
    ch["_skip_until"] = time.monotonic() - 0.01           # just elapsed
    assert mode._detect(sdr, blk, SR) is ch
    print("✓ lockout expires: the channel is eligible again")


def test_priority_channel_respects_its_lockout():
    """Otherwise a data burst on the priority channel pre-empts everything, forever."""
    mode, blk, ch = _scanner()
    mode.priority = ch["label"]
    sdr = _FakeSdr(ch["freq"], blk["center"])
    assert mode._detect(sdr, blk, SR) is ch               # priority wins normally
    ch["_skip_until"] = time.monotonic() + VOICE_SKIP_S
    assert mode._detect(sdr, blk, SR) is not ch
    print("✓ priority: a locked-out priority channel stops pre-empting")


def test_turning_voice_squelch_off_clears_lockouts():
    mode, _, ch = _scanner()
    mode.voice_squelch = True
    ch["_skip_until"] = time.monotonic() + VOICE_SKIP_S
    mode.configure({"voice_squelch": False})
    assert all(c["_skip_until"] == 0.0 for c in mode._channels)
    print("✓ switching it off releases every channel it had locked out")


def test_config_round_trips_the_settings():
    mode, _, _ = _scanner()
    mode.configure({"voice_squelch": True, "voice_sens": "strict"})
    msg = mode._config_msg()
    assert msg["voice_squelch"] is True and msg["voice_sens"] == "strict"
    mode.configure({"voice_sens": "nonsense"})           # ignored, not crashed
    assert mode.voice_sens == "strict"
    print("✓ config: settings echo back, and a bogus sensitivity is ignored")


if __name__ == "__main__":
    test_detect_finds_the_active_channel()
    test_locked_out_channel_is_not_parked_on_again()
    test_lockout_expires()
    test_priority_channel_respects_its_lockout()
    test_turning_voice_squelch_off_clears_lockouts()
    test_config_round_trips_the_settings()
    print("all scanner voice squelch tests passed")
