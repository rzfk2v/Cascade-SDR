"""Stateful DSP building blocks for streaming demodulation.

Each block keeps the filter / oscillator / discriminator state it needs to be
called repeatedly on consecutive sample chunks *without* glitches at chunk
boundaries. That continuity is what keeps the audio click-free.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import bilinear, firwin, lfilter, lfilter_zi


class ComplexChannelizer:
    """Mix a chosen frequency to baseband, low-pass it, and decimate.

    Pipeline per chunk: ``x -> (x * e^{-j 2pi f t}) -> FIR low-pass -> [::decim]``.
    The numerically-controlled oscillator (NCO) phase, the FIR state and the
    decimation grid all carry across chunks. ``chunk_len`` must be a multiple of
    ``decim`` so the decimation phase stays aligned.
    """

    def __init__(self, in_rate: float, decim: int, cutoff_hz: float,
                 numtaps: int = 129) -> None:
        self.in_rate = float(in_rate)
        self.decim = int(decim)
        self._shift = 0.0
        self._phase = 0.0
        self._numtaps = numtaps
        self._set_taps(cutoff_hz)

    def _set_taps(self, cutoff_hz: float) -> None:
        nyq = self.in_rate / 2.0
        cutoff = float(np.clip(cutoff_hz, 1_000.0, nyq * 0.95))
        self._b = firwin(self._numtaps, cutoff / nyq).astype(np.complex128)
        self._zi = np.zeros(self._numtaps - 1, dtype=np.complex128)

    def set_cutoff(self, cutoff_hz: float) -> None:
        self._set_taps(cutoff_hz)

    def set_shift(self, shift_hz: float) -> None:
        self._shift = float(shift_hz)

    @property
    def out_rate(self) -> float:
        return self.in_rate / self.decim

    def process(self, x: np.ndarray) -> np.ndarray:
        n = x.size
        k = np.arange(n, dtype=np.float64)
        inc = 2.0 * np.pi * self._shift / self.in_rate
        osc = np.exp(-1j * (self._phase + inc * k))
        self._phase = (self._phase + inc * n) % (2.0 * np.pi)
        mixed = x * osc
        filt, self._zi = lfilter(self._b, 1.0, mixed, zi=self._zi)
        return filt[:: self.decim]


class RealDecimator:
    """FIR low-pass + integer decimation for a real signal, stateful."""

    def __init__(self, in_rate: float, decim: int, cutoff_hz: float,
                 numtaps: int = 129) -> None:
        self.in_rate = float(in_rate)
        self.decim = int(decim)
        nyq = self.in_rate / 2.0
        cutoff = float(np.clip(cutoff_hz, 500.0, nyq * 0.95))
        self._b = firwin(numtaps, cutoff / nyq)
        self._zi = np.zeros(numtaps - 1, dtype=np.float64)

    @property
    def out_rate(self) -> float:
        return self.in_rate / self.decim

    def process(self, x: np.ndarray) -> np.ndarray:
        filt, self._zi = lfilter(self._b, 1.0, x, zi=self._zi)
        return filt[:: self.decim]


class FmDiscriminator:
    """Quadrature FM detector: phase difference between successive samples."""

    def __init__(self) -> None:
        self._last = complex(1.0, 0.0)

    def process(self, y: np.ndarray) -> np.ndarray:
        prev = np.empty_like(y)
        prev[0] = self._last
        prev[1:] = y[:-1]
        self._last = y[-1] if y.size else self._last
        return np.angle(y * np.conj(prev))


class DeEmphasis:
    """First-order de-emphasis (RC) filter applied at the audio rate."""

    def __init__(self, audio_rate: float, tau_us: float = 50.0) -> None:
        rc = tau_us * 1e-6
        # Continuous H(s) = 1/(1 + s*RC), discretised via bilinear transform.
        self._b, self._a = bilinear([1.0], [rc, 1.0], fs=audio_rate)
        self._zi = lfilter_zi(self._b, self._a) * 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        y, self._zi = lfilter(self._b, self._a, x, zi=self._zi)
        return y
