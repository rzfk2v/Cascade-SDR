"""FFT helpers for the waterfall.

Turns a block of complex IQ samples into a single power-spectrum row in dBFS,
arranged so the left edge is the lowest frequency and the right edge the highest
(i.e. DC moved to the centre with ``fftshift``).
"""
from __future__ import annotations

import numpy as np


class Spectrum:
    """Reusable FFT front-end with a cached window."""

    def __init__(self, fft_size: int = 2048) -> None:
        self.fft_size = fft_size
        self._window = np.hanning(fft_size).astype(np.float32)
        # Coherent power gain of the window, for amplitude normalisation.
        self._win_norm = np.sum(self._window) ** 2

    def row(self, samples: np.ndarray) -> np.ndarray:
        """Return one float32 power row (dBFS) of length ``fft_size``.

        If the block spans multiple FFT windows, the windows are averaged
        (Bartlett-style) to reduce variance and keep the output rate steady.
        """
        n = self.fft_size
        if samples.size < n:
            # Zero-pad a short block up to one window.
            samples = np.concatenate([samples, np.zeros(n - samples.size, dtype=samples.dtype)])
        usable = (samples.size // n) * n
        blocks = samples[:usable].reshape(-1, n)
        spec = np.fft.fftshift(np.fft.fft(blocks * self._window, axis=1), axes=1)
        power = (np.abs(spec) ** 2).mean(axis=0) / self._win_norm
        # dBFS relative to full-scale; +1e-12 guards log(0).
        return (10.0 * np.log10(power + 1e-12)).astype(np.float32)
