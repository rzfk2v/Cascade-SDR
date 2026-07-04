"""Stateful DSP building blocks for streaming demodulation.

Each block keeps the filter / oscillator / discriminator state it needs to be
called repeatedly on consecutive sample chunks *without* glitches at chunk
boundaries. That continuity is what keeps the audio click-free.
"""
from __future__ import annotations

import math

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.signal import bilinear, firwin, lfilter, lfilter_zi


def _fir_decimate(b_rev: np.ndarray, hist: np.ndarray, x: np.ndarray,
                  decim: int) -> tuple[np.ndarray, np.ndarray]:
    """Polyphase FIR low-pass + decimation that only computes the kept samples.

    Equivalent to ``lfilter(b, 1, x)[::decim]`` with state carried across calls,
    but ~``decim``× cheaper: instead of filtering at the full input rate and then
    throwing away ``decim-1`` of every ``decim`` outputs, it forms the strided
    windows for the decimated output positions and dots them with the (reversed)
    taps in one BLAS call. ``hist`` is the trailing ``numtaps-1`` input samples
    from the previous call (filter continuity); ``b_rev`` is ``taps[::-1]``.
    """
    numtaps = b_rev.size
    if x.size == 0:
        return x[:0], hist
    buf = np.concatenate((hist, x))               # length numtaps-1 + n
    win = sliding_window_view(buf, numtaps)[::decim]  # rows at m=0,decim,2decim,...
    y = win @ b_rev
    return y, buf[-(numtaps - 1):]


class PilotPll:
    """Second-order PLL that locks an NCO to the 19 kHz FM stereo pilot.

    Returns the NCO phase per sample (radians), which is delay-free and
    phase-accurate — unlike a band-pass'd reference, which carries filter group
    delay that wrecks synchronous detection of the 38 kHz (stereo) and 57 kHz
    (RDS) subcarriers. Also exposes ``pilot_rms`` so callers can detect whether a
    pilot is present (stereo / RDS only exist on a locked pilot).
    """

    def __init__(self, fs: float, f0: float = 19_000.0, bw: float = 12.0) -> None:
        self.phase = 0.0
        self.freq = 2.0 * math.pi * f0 / fs
        zeta = 0.707
        wn = 2.0 * math.pi * bw / fs
        self.alpha = 2.0 * zeta * wn
        self.beta = wn * wn
        self.pilot_rms = 0.0
        self._b = firwin(129, [(f0 - 800) / (fs / 2), (f0 + 800) / (fs / 2)],
                         pass_zero=False)
        self._zi = np.zeros(128)

    def run(self, mpx: np.ndarray) -> np.ndarray:
        pilot, self._zi = lfilter(self._b, 1.0, mpx, zi=self._zi)
        self.pilot_rms = float(np.sqrt(np.mean(pilot * pilot))) if pilot.size else 0.0
        out = np.empty(mpx.size)
        ph, fr = self.phase, self.freq
        a, b = self.alpha, self.beta
        sin, tau = math.sin, 2.0 * math.pi
        pil = pilot.tolist()
        for i in range(len(pil)):
            err = pil[i] * sin(ph)
            fr += b * err
            ph += fr + a * err
            if ph > math.pi:
                ph -= tau
            elif ph < -math.pi:
                ph += tau
            out[i] = ph
        self.phase, self.freq = ph, fr
        return out


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
        # Cached mixing oscillator: e^{-j inc k} for the current block size, so we
        # don't recompute a transcendental over every input sample each block.
        self._osc_base: np.ndarray | None = None
        self._osc_n = 0
        self._osc_inc = float("nan")
        self._set_taps(cutoff_hz)

    def _set_taps(self, cutoff_hz: float) -> None:
        nyq = self.in_rate / 2.0
        cutoff = float(np.clip(cutoff_hz, 1_000.0, nyq * 0.95))
        self._b = firwin(self._numtaps, cutoff / nyq).astype(np.complex128)
        self._b_rev = self._b[::-1].copy()
        self._hist = np.zeros(self._numtaps - 1, dtype=np.complex128)

    def set_cutoff(self, cutoff_hz: float) -> None:
        self._set_taps(cutoff_hz)

    def set_shift(self, shift_hz: float) -> None:
        self._shift = float(shift_hz)

    @property
    def out_rate(self) -> float:
        return self.in_rate / self.decim

    def process(self, x: np.ndarray) -> np.ndarray:
        n = x.size
        if n == 0:
            return x[:0]
        inc = 2.0 * np.pi * self._shift / self.in_rate
        # Rebuild the base oscillator only when the shift or block size changes;
        # otherwise just rotate it by the running phase (one scalar exp).
        if self._osc_base is None or self._osc_n != n or self._osc_inc != inc:
            k = np.arange(n, dtype=np.float64)
            self._osc_base = np.exp(-1j * inc * k)
            self._osc_n = n
            self._osc_inc = inc
        osc = self._osc_base * np.exp(-1j * self._phase)
        self._phase = (self._phase + inc * n) % (2.0 * np.pi)
        mixed = x * osc
        y, self._hist = _fir_decimate(self._b_rev, self._hist, mixed, self.decim)
        return y


class RealDecimator:
    """FIR low-pass + integer decimation for a real signal, stateful."""

    def __init__(self, in_rate: float, decim: int, cutoff_hz: float,
                 numtaps: int = 129) -> None:
        self.in_rate = float(in_rate)
        self.decim = int(decim)
        self._numtaps = numtaps
        nyq = self.in_rate / 2.0
        cutoff = float(np.clip(cutoff_hz, 500.0, nyq * 0.95))
        self._b = firwin(numtaps, cutoff / nyq)
        self._b_rev = self._b[::-1].copy()
        self._hist = np.zeros(numtaps - 1, dtype=np.float64)

    @property
    def out_rate(self) -> float:
        return self.in_rate / self.decim

    def process(self, x: np.ndarray) -> np.ndarray:
        y, self._hist = _fir_decimate(self._b_rev, self._hist, x, self.decim)
        return y


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


class BlockAgc:
    """Cheap block-based automatic gain control for SSB/AM voice.

    Adjusts a single gain per block toward a target RMS, smoothed across blocks so
    weak and strong signals end up at a similar, audible level without per-sample
    cost. Not for FM (constant-amplitude).
    """

    def __init__(self, target: float = 0.25, smooth: float = 0.25,
                 max_gain: float = 200.0) -> None:
        self.gain = 1.0
        self.target = target
        self.smooth = smooth
        self.max_gain = max_gain

    def process(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            return x
        rms = float(np.sqrt(np.mean(x * x))) + 1e-6
        desired = min(self.max_gain, self.target / rms)
        self.gain += self.smooth * (desired - self.gain)
        return x * self.gain


class SsbDemod:
    """Single-sideband demodulator (filter/phasing method).

    Input is complex baseband centred on the (suppressed) carrier. A one-sided
    complex band-pass keeps only the wanted sideband — for USB the positive audio
    band, for LSB the negative — which yields the analytic signal of the sideband;
    its real part is the demodulated audio.
    """

    def __init__(self, audio_rate: float, lsb: bool = False,
                 low: float = 200.0, high: float = 2800.0, numtaps: int = 151) -> None:
        center = (low + high) / 2.0 * (-1.0 if lsb else 1.0)
        half = (high - low) / 2.0
        lp = firwin(numtaps, half / (audio_rate / 2.0))
        n = np.arange(numtaps) - (numtaps - 1) / 2.0
        self._b = (lp * np.exp(1j * 2 * np.pi * center * n / audio_rate)).astype(np.complex128)
        self._zi = np.zeros(numtaps - 1, dtype=np.complex128)

    def process(self, x: np.ndarray) -> np.ndarray:
        y, self._zi = lfilter(self._b, 1.0, x, zi=self._zi)
        return np.real(y) * 2.0  # ×2 compensates the discarded sideband's energy


class StereoDecoder:
    """Recover the L−R (difference) signal from an FM multiplex for stereo.

    The MPX (discriminator output) carries L+R at baseband, a 19 kHz pilot, and
    L−R as a DSB-SC subcarrier around 38 kHz (= 2× pilot). We derive a 38 kHz
    reference by squaring the band-passed pilot (cos²θ → ½(1+cos2θ)), isolate the
    cos2θ term, normalise it to unit amplitude, and synchronously detect L−R.
    Returns the decimated L−R at the audio rate plus the pilot level so the caller
    can fall back to mono when no pilot is present (mono station / weak signal).
    """

    def __init__(self, in_rate: float, decim: int, audio_lp_hz: float = 15_000.0):
        # wider loop bandwidth than RDS: stereo needs a tight phase lock for the
        # synchronous L−R detection (a slow-converging PLL rotates the reference).
        self._pll = PilotPll(in_rate, 19_000.0, bw=100.0)
        self._sdec = RealDecimator(in_rate, decim, audio_lp_hz)

    def process(self, mpx: np.ndarray) -> tuple[np.ndarray, float]:
        phase = self._pll.run(mpx)                  # locked, delay-free
        mpx_rms = float(np.sqrt(np.mean(mpx * mpx))) + 1e-12
        frac = self._pll.pilot_rms / mpx_rms
        # pilot is ~9% of the MPX on a stereo station; near zero on mono
        if frac < 0.02:
            # No pilot -> the PLL is unlocked and the 38 kHz reference is noise:
            # suppress the detected diff, but keep feeding the decimator so its
            # filter state stays continuous when the pilot (re)appears.
            diff = np.zeros_like(mpx)
        else:
            ref = np.cos(2.0 * phase)               # 38 kHz = 2× pilot, phase-correct
            diff = mpx * ref * 2.0                   # synchronous DSB-SC detect of L−R
        return self._sdec.process(diff), frac


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
