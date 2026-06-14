"""APT mode — live NOAA weather-satellite image (137 MHz).

A thin preset over :class:`~app.modes.radio.RadioMode`: wide-FM at a NOAA APT
frequency with the APT image decoder enabled, decoding immediately (no click).
The same APT decoding also runs in Replay (RadioMode subclass) when its APT
toggle is on, so a recorded pass can be decoded afterwards.
"""
from __future__ import annotations

from app.modes.radio import RadioMode


class AptMode(RadioMode):
    name = "apt"
    resets_tuning = True                   # snap to the satellite freq, not the last band
    default_center_freq = 137_620_000.0   # NOAA 15 (NOAA 18: 137.9125, NOAA 19: 137.1)

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self.demod = "wfm"
        self.bandwidth = 50_000.0          # wide enough for the APT FM signal
        self.apt_enabled = True
        self.rds_enabled = False
        self.stereo_enabled = False
        self.tuned_freq = self.default_center_freq

    def on_start(self) -> None:
        self.tuned_freq = self.manager.center_freq
        self._user_tuned = True            # decode the centre channel right away
        super().on_start()
