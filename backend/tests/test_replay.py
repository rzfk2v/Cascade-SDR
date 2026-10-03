"""Replay transport: play/pause, seek, skip — the mode-side logic.

The device worker owns the file and makes the jumps; the mode holds what the
client asked for and what the worker must reset after a jump.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from app.modes.replay import ReplayMode


class _Manager(SimpleNamespace):
    def __init__(self) -> None:
        super().__init__(center_freq=438_150_000.0, sample_rate=2_400_000.0, json=[])

    def emit_json(self, msg: dict) -> None:
        self.json.append(msg)

    def emit_binary(self, tag: int, data: bytes) -> None:
        pass


def _mode(duration_s: float = 400.0) -> ReplayMode:
    mode = ReplayMode(_Manager())
    mode.duration_s = duration_s          # as _select_file would set from the file size
    return mode


def test_seek_is_clamped_into_the_file():
    mode = _mode()
    mode.configure({"seek": 213.5})
    assert mode.take_seek() == 213.5
    assert mode.take_seek() is None                     # taken exactly once
    mode.configure({"seek": -5})
    assert mode.take_seek() == 0.0
    mode.configure({"seek": 9_999})
    assert 399.0 < mode.take_seek() < 400.0             # not past the end


def test_skips_accumulate_before_the_worker_catches_up():
    """Three quick '+10 s' presses must land 30 s on, not 10."""
    mode = _mode()
    mode.position_s = 100.0
    for _ in range(3):
        mode.configure({"skip": 10})
    assert mode.take_seek() == 130.0
    mode.position_s = 130.0
    mode.configure({"skip": -60})
    assert mode.take_seek() == 70.0


def test_status_reports_position_and_duration():
    mode = _mode(405.9)
    mode.position_s = 214.62
    msg = mode._replay_status_msg()
    assert msg["type"] == "replay_status"
    assert msg["position"] == 214.6 and msg["duration"] == 405.9


def test_a_jump_restarts_what_tracks_the_signal():
    """After a seek the stream no longer joins up: SSTV detection and the
    Doppler tracker must start over, and the client hears where it landed."""
    mode = _mode()
    mode.configure({"demod": "nfm", "sstv": True, "tuned_freq": 437_550_000.0,
                    "bandwidth": 36_000.0})
    mode.process(np.zeros(51_200, dtype=np.complex64))     # build the chain
    assert mode._track is not None
    mode._track.offset, mode._track.locked = 3_000.0, True
    mode._sstv_dirty = False
    mode.on_seek(120.0)
    assert mode._sstv_dirty and mode._track.offset == 0.0 and not mode._track.locked
    assert mode.position_s == 120.0
    assert mode.manager.json[-1]["type"] == "replay_status"
    assert mode.manager.json[-1]["position"] == 120.0
