"""NOAA APT decoder — turns a 137 MHz weather-satellite pass into image lines.

NOAA 15/18/19 send Automatic Picture Transmission (APT): the FM-demodulated audio
carries a **2400 Hz AM subcarrier** whose amplitude is the pixel brightness. The
line rate is 2 lines/s and each line is **2080 pixels** (pixel rate 4160 px/s):
each line holds a sync, telemetry, and two 909-px image channels (visible + IR).

Pipeline (streaming, fed FM-demodulated audio):
  1. band-pass ~2400 Hz, rectify + low-pass → the AM envelope (pixel amplitude),
  2. resample the envelope to 4160 px/s,
  3. find line starts by correlating against the Channel-A sync (a 1040 Hz square
     burst), lock once and step 2080 px with ±tracking,
  4. normalise to 0–255 and emit one 2080-byte grayscale line at a time.

Hand-written (no external decoder); validated by a synthetic image round-trip in
tests/test_apt.py.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from scipy.signal import firwin, lfilter

from app.dsp.blocks import StreamResampler

PIXEL_RATE = 4160          # APT pixels per second
LINE_PX = 2080             # pixels per line (2 lines/s)
SUBCARRIER = 2400.0        # AM subcarrier (Hz)


def sync_a_reference() -> np.ndarray:
    """Channel-A sync: 7 cycles of a 1040 Hz square (2 px high, 2 px low at 4160).

    Returned zero-mean for cross-correlation. Real APT uses exactly this pattern,
    so it locks on genuine passes too — not just the synthetic test.
    """
    cyc = np.array([1.0, 1.0, -1.0, -1.0])    # 1040 Hz at 4160 px/s
    pat = np.tile(cyc, 7)
    return pat - pat.mean()


class AptDecoder:
    def __init__(self, audio_rate: float,
                 on_line: Optional[Callable[[np.ndarray], None]] = None) -> None:
        self.fs = float(audio_rate)
        self.on_line = on_line
        nyq = self.fs / 2.0
        # band-pass around the 2400 Hz subcarrier, then rectify + low-pass = envelope
        self._bp = firwin(129, [max(300.0, 1200.0) / nyq, min(3600.0, nyq * 0.95) / nyq],
                          pass_zero=False)
        self._zi_bp = np.zeros(128)
        self._lp = firwin(129, 1200.0 / nyq)
        self._zi_lp = np.zeros(128)
        # stateful resampler: no per-chunk edge transients / pixel-clock slip
        self._resamp = StreamResampler(PIXEL_RATE, int(round(self.fs)))
        self._px_buf = np.zeros(0)         # envelope resampled to 4160 px/s
        self._sync = sync_a_reference()
        self._locked = False
        self._lo = 0.05                    # running brightness range for normalisation
        self._hi = 0.5
        self.lines = 0

    def process(self, audio: np.ndarray) -> None:
        if audio.size == 0:
            return
        bp, self._zi_bp = lfilter(self._bp, 1.0, audio, zi=self._zi_bp)
        env, self._zi_lp = lfilter(self._lp, 1.0, np.abs(bp), zi=self._zi_lp)
        env *= np.pi / 2.0                 # rectifier DC correction
        px = self._resamp.process(env)
        if px.size == 0:
            return
        self._px_buf = np.concatenate([self._px_buf, px])
        self._emit_lines()

    def _emit_lines(self) -> None:
        buf = self._px_buf
        if not self._locked:
            # need a couple of lines to find the sync reliably
            if buf.size < LINE_PX * 2:
                return
            self._start = self._find_sync(buf, 0, buf.size - LINE_PX)
            self._locked = True
        start = self._start
        while start + LINE_PX <= self._px_buf.size:
            # track the sync within ±6 px so slow drift doesn't walk off the line
            s = self._find_sync(self._px_buf, max(0, start - 6), start + 6)
            if abs(s - start) <= 6:
                start = s
            line = self._px_buf[start:start + LINE_PX]
            self._emit(line)
            start += LINE_PX
        # keep the unconsumed tail; next line starts at 0 of the retained buffer
        self._px_buf = self._px_buf[start:]
        self._start = 0

    def _find_sync(self, buf: np.ndarray, lo: int, hi: int) -> int:
        lo = max(0, lo)
        hi = min(hi, buf.size - self._sync.size)
        if hi <= lo:
            return lo
        seg = buf[lo:hi + self._sync.size]
        corr = np.correlate(seg - seg.mean(), self._sync, mode="valid")
        return lo + int(np.argmax(corr))

    def _emit(self, line: np.ndarray) -> None:
        # adaptive contrast: track a slow min/max and map to 0–255
        lo, hi = np.percentile(line, 2), np.percentile(line, 98)
        self._lo += 0.05 * (lo - self._lo)
        self._hi += 0.05 * (hi - self._hi)
        span = max(1e-6, self._hi - self._lo)
        out = np.clip((line - self._lo) / span, 0.0, 1.0)
        row = (out * 255).astype(np.uint8)
        if row.size < LINE_PX:
            row = np.pad(row, (0, LINE_PX - row.size))
        self.lines += 1
        if self.on_line is not None:
            self.on_line(row[:LINE_PX])
