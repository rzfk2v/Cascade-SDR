"""SSTV mode — live slow-scan-TV image decode.

A thin preset over :class:`~app.modes.radio.RadioMode`: narrow-FM at the 2 m SSTV
calling frequency (144.500 MHz in EU) with the SSTV decoder enabled, decoding
immediately (no click). The same SSTV decoding runs in any RadioMode when its
SSTV toggle is on, so HF SSTV works too — switch demod to USB and tune to e.g.
14.230 MHz. The mode is auto-detected from the VIS header (Martin / Scottie).
"""
from __future__ import annotations

from app.modes.radio import DEMODS, RadioMode


class SstvMode(RadioMode):
    name = "sstv"
    resets_tuning = True                   # snap to the SSTV calling frequency
    default_center_freq = 144_500_000.0    # 2 m SSTV calling (EU)

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self.demod = "nfm"
        self.bandwidth = float(DEMODS["nfm"]["bw"])
        self.sstv_enabled = True
        self.rds_enabled = False
        self.stereo_enabled = False
        self.apt_enabled = False
        self.tuned_freq = self.default_center_freq

    def on_start(self) -> None:
        self.tuned_freq = self.manager.center_freq
        self._user_tuned = True            # decode the centre channel right away
        super().on_start()
