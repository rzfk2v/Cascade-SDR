"""Replay mode — play a recorded .cu8 IQ file back through the live pipeline.

Reuses :class:`~app.modes.radio.RadioMode`'s DSP (full-band FFT waterfall +
channel demod + audio) but sources its samples from a file on disk instead of the
dongle. So you can re-open a capture, browse its spectrum, and demodulate
*anything* inside the recorded band — no hardware needed. The device worker
(see ``DeviceManager._replay_worker``) streams the file at ~real-time pacing and
loops at EOF.

The capture's center frequency and sample rate are read from the standard
recording filename (``iq_<ts>_<center>Hz_<rate>sps.cu8``).
"""
from __future__ import annotations

import re
from pathlib import Path

from app.modes.radio import RadioMode

_NAME_RE = re.compile(r"_(\d+)Hz_(\d+)sps")


def parse_capture_name(name: str) -> tuple[float, float] | None:
    """Return (center_hz, sample_rate) parsed from a recording filename, or None."""
    m = _NAME_RE.search(name)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


class ReplayMode(RadioMode):
    name = "replay"
    owns_device = True       # uses the worker thread machinery...
    reads_file = True        # ...but the worker reads a file, not the dongle
    controls_tuning = False

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self.file_path: Path | None = None
        self.playing = False
        self.position_s = 0.0           # where the worker is in the file
        self.duration_s = 0.0
        self._seek: float | None = None  # a jump the worker should make (seconds)

    # --- configuration from the client --------------------------------------
    def configure(self, params: dict) -> None:
        if params.get("file"):
            self._select_file(str(params["file"]))
        if "playing" in params and params["playing"] is not None:
            self.playing = bool(params["playing"])
            self._announce_replay()
        # Transport: jump to a position, or skip relative to the current one.
        # The worker makes the jump (it owns the file), even while paused.
        if params.get("seek") is not None:
            self._seek = self._clamp(float(params["seek"]))
        elif params.get("skip") is not None:
            base = self._seek if self._seek is not None else self.position_s
            self._seek = self._clamp(base + float(params["skip"]))
        # demod / bandwidth / volume / squelch / deemph / tuned_freq
        super().configure(params)

    def _select_file(self, name: str) -> None:
        from app.device import RECORDINGS_DIR

        target = (RECORDINGS_DIR / name).resolve()
        if (target.parent != RECORDINGS_DIR.resolve()
                or target.suffix != ".cu8" or not target.exists()):
            self.manager.emit_json(
                {"type": "error", "message": f"recording not found: {name}"}
            )
            return
        meta = parse_capture_name(name)
        if meta:
            self.manager.center_freq, self.manager.sample_rate = meta
        self.file_path = target
        self.duration_s = target.stat().st_size / 2.0 / max(1.0, self.manager.sample_rate)
        self.position_s = 0.0
        self._seek = None
        self.playing = True
        self._user_tuned = False          # start silent on a fresh file
        self.tuned_freq = self.manager.center_freq
        self._need_rebuild = True          # rebuild DSP chain at the file's rate
        # resync the client UI to the new band + state
        self.manager.emit_json(self._spectrum_config_msg())
        self._announce_radio()
        self._announce_replay()

    def _clamp(self, t: float) -> float:
        return float(min(max(t, 0.0), max(0.0, self.duration_s - 0.1)))

    def take_seek(self) -> float | None:
        """The pending jump, if any (worker thread): taken exactly once."""
        t, self._seek = self._seek, None
        return t

    def on_seek(self, position_s: float) -> None:
        """The stream just jumped (worker thread): what came before no longer
        joins up with what follows, so restart anything that tracks the signal."""
        self.position_s = position_s
        self._sstv_dirty = True
        self._apt_dirty = True
        self._rds_dirty = True
        if self._track is not None:
            self._track.reset()
        self._announce_replay()

    def _replay_status_msg(self) -> dict:
        return {
            "type": "replay_status",
            "file": self.file_path.name if self.file_path else None,
            "playing": self.playing,
            "position": round(self.position_s, 1),
            "duration": round(self.duration_s, 1),
        }

    def _announce_replay(self) -> None:
        self.manager.emit_json(self._replay_status_msg())

    def snapshot(self) -> list[dict]:
        return [*super().snapshot(), self._replay_status_msg()]
