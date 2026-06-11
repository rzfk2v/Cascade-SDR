"""Spectrum / waterfall mode.

Reads IQ blocks, computes a power-spectrum row per block, and broadcasts it as a
tagged binary frame. The browser stacks the rows into a scrolling waterfall.

``process`` runs in the device worker thread. To keep the WebSocket from being
flooded, rows are emitted at a capped rate; blocks read in between are dropped
cheaply (no FFT computed).
"""
from __future__ import annotations

import time

import numpy as np

from app.dsp.fft import Spectrum
from app.hub import FrameTag
from app.modes.base import Mode


class SpectrumMode(Mode):
    name = "spectrum"
    owns_device = True
    default_center_freq = 100_000_000.0
    default_sample_rate = 2_400_000.0
    block_size = 65_536

    FFT_SIZE = 2048
    MAX_ROWS_PER_SEC = 25

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self._spectrum = Spectrum(self.FFT_SIZE)
        self._min_interval = 1.0 / self.MAX_ROWS_PER_SEC
        self._last_emit = 0.0

    def _spectrum_config_msg(self) -> dict:
        return {
            "type": "spectrum_config",
            "fft_size": self.FFT_SIZE,
            "center_freq": self.manager.center_freq,
            "sample_rate": self.manager.sample_rate,
        }

    def snapshot(self) -> list[dict]:
        return [self._spectrum_config_msg()]

    def on_start(self) -> None:
        # Tell clients the geometry so they can label axes correctly.
        self.manager.emit_json(self._spectrum_config_msg())

    def process(self, samples: np.ndarray) -> None:
        now = time.monotonic()
        if now - self._last_emit < self._min_interval:
            return  # drop this block (rate cap) without spending an FFT
        self._last_emit = now
        row = self._spectrum.row(samples)
        self.manager.emit_binary(FrameTag.FFT, row.tobytes())
