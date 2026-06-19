"""SSTV decoder — turns received audio into a slow-scan-TV image.

SSTV sends a picture as an FM-modulated audio tone: the instantaneous frequency
(1500 Hz = black … 2300 Hz = white, 1200 Hz = sync) traces each scan line. A
short **VIS** header at the start encodes which mode is being sent, so we can
auto-detect it and lay out the lines correctly.

Pipeline (streaming, fed demodulated mono audio):
  1. recover the instantaneous tone frequency f(t): heterodyne the audio down by
     1900 Hz, low-pass, then FM-discriminate — robust to level (it's FM),
  2. detect the VIS calibration header → pick the mode (Martin / Scottie),
  3. for each line, drift-correct against the 1200 Hz sync pulse, slice the R/G/B
     channel sweeps by their known timings, map frequency → 0–255, emit an RGB row.

Hand-written (no external decoder); validated by a synthetic round-trip in
tests/test_sstv.py. Supported modes are the common RGB ones — Martin M1/M2 and
Scottie S1/S2/DX; others (Robot36, PD) are not decoded yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
from scipy.signal import firwin, lfilter

CENTER_HZ = 1900.0      # heterodyne pivot (between the 1500–2300 video band)
BLACK_HZ = 1500.0
WHITE_HZ = 2300.0
SYNC_HZ = 1200.0
VIS_BIT_MS = 30.0       # each VIS bit is 30 ms

# Channel indices used inside a line layout.
R, G, B = 0, 1, 2


@dataclass
class Mode:
    """One SSTV mode: geometry + per-line segment timings (all times in ms)."""

    name: str
    vis: int
    width: int
    height: int
    sync_ms: float
    sep_ms: float
    pixel_ms: float          # per-pixel scan time
    order: tuple             # channel order of the scans, e.g. (G, B, R)
    sync_first: bool         # True: sync starts the line (Martin); else mid-line (Scottie)
    leading_sync: bool = False   # an extra sync precedes the very first line (Scottie)
    # Filled in __post_init__:
    segments: list = field(default_factory=list)   # (kind, channel, dur_ms)
    line_ms: float = 0.0
    sync_offset_ms: float = 0.0   # time from line start to the sync pulse

    def __post_init__(self) -> None:
        scan = self.pixel_ms * self.width
        segs: list[tuple[str, int, float]] = []
        if self.sync_first:
            segs.append(("sync", -1, self.sync_ms))
            for ch in self.order:
                segs.append(("sep", -1, self.sep_ms))
                segs.append(("scan", ch, scan))
        else:
            # Scottie: green, blue, then sync, then red (sync sits mid-line).
            g, b, r = self.order  # order is (G, B, R)
            segs.append(("sep", -1, self.sep_ms))
            segs.append(("scan", g, scan))
            segs.append(("sep", -1, self.sep_ms))
            segs.append(("scan", b, scan))
            segs.append(("sync", -1, self.sync_ms))
            segs.append(("sep", -1, self.sep_ms))
            segs.append(("scan", r, scan))
        self.segments = segs
        self.line_ms = sum(d for _, _, d in segs)
        off = 0.0
        for kind, _, d in segs:
            if kind == "sync":
                break
            off += d
        self.sync_offset_ms = off


# VIS-code table (the common RGB modes). order is the scan order within a line.
MODES: dict[int, Mode] = {
    m.vis: m
    for m in [
        Mode("Martin M1", 44, 320, 256, 4.862, 0.572, 0.4576, (G, B, R), True),
        Mode("Martin M2", 40, 320, 256, 4.862, 0.572, 0.2288, (G, B, R), True),
        Mode("Scottie S1", 60, 320, 256, 9.0, 1.5, 0.4320, (G, B, R), False, True),
        Mode("Scottie S2", 56, 320, 256, 9.0, 1.5, 0.2752, (G, B, R), False, True),
        Mode("Scottie DX", 76, 320, 256, 9.0, 1.5, 1.08, (G, B, R), False, True),
    ]
}


class SstvDecoder:
    """Streaming SSTV decoder. Feed mono audio; get VIS-detected RGB rows out."""

    def __init__(
        self,
        audio_rate: float,
        on_start: Optional[Callable[[str, int, int], None]] = None,
        on_row: Optional[Callable[[np.ndarray], None]] = None,
    ) -> None:
        self.fs = float(audio_rate)
        self.on_start = on_start
        self.on_row = on_row
        nyq = self.fs / 2.0
        # Heterodyne + low-pass to isolate the video tone around 1900 Hz.
        self._lp = firwin(65, 1000.0 / nyq)
        self._zi = np.zeros(64, dtype=complex)
        self._phase = 0.0                 # running heterodyne phase (radians)
        self._prev = 0.0 + 0.0j           # last sample, for the discriminator
        self._f = np.zeros(0)             # buffered instantaneous frequency
        self._origin = 0                  # absolute sample index of self._f[0]
        self._fed = 0                     # total samples fed (absolute clock)
        # Decoder state
        self.mode: Optional[Mode] = None
        self._cursor = 0.0                # absolute sample index of the next line
        self.rows = 0

    # --- front end: audio -> instantaneous frequency ------------------------
    def _to_freq(self, audio: np.ndarray) -> np.ndarray:
        n = np.arange(audio.size)
        osc = np.exp(-1j * (self._phase + 2.0 * np.pi * CENTER_HZ * n / self.fs))
        self._phase = (self._phase + 2.0 * np.pi * CENTER_HZ * audio.size / self.fs) % (
            2.0 * np.pi
        )
        x, self._zi = lfilter(self._lp, 1.0, audio * osc, zi=self._zi)
        prev = np.empty(x.size, dtype=complex)
        prev[0] = self._prev
        prev[1:] = x[:-1]
        self._prev = x[-1]
        dphase = np.angle(x * np.conj(prev))
        return CENTER_HZ + dphase * self.fs / (2.0 * np.pi)

    def process(self, audio: np.ndarray) -> None:
        if audio.size == 0:
            return
        f = self._to_freq(np.asarray(audio, dtype=float))
        self._f = np.concatenate([self._f, f])
        self._fed += audio.size
        if self.mode is None:
            self._try_vis()
        if self.mode is not None:
            self._decode_lines()
        self._trim()

    # --- helpers ------------------------------------------------------------
    def _ms(self, ms: float) -> float:
        return ms * self.fs / 1000.0

    def _slice(self, a: float, b: float) -> np.ndarray:
        """Frequency samples for absolute index range [a, b)."""
        lo = int(round(a)) - self._origin
        hi = int(round(b)) - self._origin
        lo = max(0, lo)
        hi = min(self._f.size, hi)
        return self._f[lo:hi] if hi > lo else self._f[lo:lo]

    def _trim(self) -> None:
        # Drop consumed history, keeping a small margin before the cursor.
        keep_from = int(self._cursor) - int(self._ms(60.0))
        drop = keep_from - self._origin
        if drop > self.fs:  # only bother once there's a second to reclaim
            self._f = self._f[drop:]
            self._origin += drop

    # --- VIS header detection ----------------------------------------------
    def _try_vis(self) -> None:
        # Need the full calibration + VIS word buffered before attempting.
        need = self._ms(300.0 + 10.0 + 300.0 + VIS_BIT_MS * 11)
        if self._f.size < need:
            return
        is1900 = np.abs(self._f - CENTER_HZ) < 70.0
        is1200 = np.abs(self._f - SYNC_HZ) < 70.0
        is1100 = np.abs(self._f - 1100.0) < 70.0
        is1300 = np.abs(self._f - 1300.0) < 70.0
        lead = int(self._ms(170.0))     # 1900 leader required before the start bit
        bitn = int(self._ms(VIS_BIT_MS))
        last = self._f.size - int(self._ms(VIS_BIT_MS * 10))
        for s in range(lead + 1, max(lead + 1, last)):
            # Start bit = a rising edge into 1200 Hz, preceded by the leader, with a
            # ~30 ms run (this length rules out the 10 ms calibration break).
            if not (is1200[s] and not is1200[s - 1]):
                continue
            if is1900[s - lead:s - bitn // 4].mean() < 0.8:
                continue
            run = 0
            while s + run < is1200.size and is1200[s + run]:
                run += 1
            if not (self._ms(20.0) <= run <= self._ms(45.0)):
                continue
            # Sample the 8 VIS bits (7 data LSB-first + parity) after the start bit.
            bit0 = s + bitn
            bits = []
            for i in range(8):
                c = bit0 + int((i + 0.5) * bitn)
                half = bitn // 3
                bits.append(1 if is1100[c - half:c + half].mean()
                            > is1300[c - half:c + half].mean() else 0)
            code = sum(b << i for i, b in enumerate(bits[:7]))
            mode = MODES.get(code)
            if mode is None:
                continue
            self._lock(mode, abs_index=self._origin + s + int(self._ms(VIS_BIT_MS * 10)))
            return

    def _lock(self, mode: Mode, abs_index: int) -> None:
        self.mode = mode
        self.rows = 0
        self._cursor = float(abs_index)
        if mode.leading_sync:
            self._cursor += self._ms(mode.sync_ms)
        if self.on_start is not None:
            self.on_start(mode.name, mode.width, mode.height)

    # --- line decoding ------------------------------------------------------
    def _find_sync(self, expect_abs: float) -> Optional[float]:
        """Locate the 1200 Hz sync near its expected position; return its start.

        We look for the *rising edge* into a sync-length run of ~1200 Hz, which
        anchors the line cleanly even when a 1200 region (a porch or the previous
        sync) sits just outside the window. A plain min-|f−1200| match would tie
        across a wide 1200 plateau and bias every line early.
        """
        m = self.mode
        assert m is not None
        tol = int(self._ms(min(9.0, m.sync_ms + m.sep_ms)))
        run = max(1, int(self._ms(m.sync_ms * 0.6)))
        lo = int(expect_abs) - tol
        seg = self._slice(lo, int(expect_abs) + tol + run)
        if seg.size < run + 2:
            return None
        band = np.abs(seg - SYNC_HZ) < 60.0
        first_ok = None
        for k in range(1, seg.size - run):
            if band[k:k + run].mean() > 0.8:
                if first_ok is None:
                    first_ok = k
                if not band[k - 1]:        # rising edge into the sync — best anchor
                    return lo + k
        return lo + first_ok if first_ok is not None else None

    def _decode_lines(self) -> None:
        m = self.mode
        assert m is not None
        line_n = self._ms(m.line_ms)
        while self.rows < m.height:
            line_start = self._cursor
            need_abs = line_start + line_n
            if self._origin + self._f.size < need_abs:
                return  # wait for more audio
            # Line 0's start comes straight from the VIS word (exact); later lines
            # re-anchor to their sync pulse to track timing drift.
            if self.rows > 0:
                sync = self._find_sync(line_start + self._ms(m.sync_offset_ms))
                if sync is not None:
                    line_start = sync - self._ms(m.sync_offset_ms)
            self._emit_line(line_start)
            self._cursor = line_start + line_n
            self.rows += 1

    def _emit_line(self, line_start: float) -> None:
        m = self.mode
        assert m is not None
        chans: list[Optional[np.ndarray]] = [None, None, None]
        t = line_start
        for kind, ch, dur in m.segments:
            d = self._ms(dur)
            if kind == "scan":
                chans[ch] = self._scan(t, d, m.width)
            t += d
        rgb = np.zeros(m.width * 3, dtype=np.uint8)
        for ci in range(3):
            col = chans[ci]
            if col is not None:
                rgb[ci::3] = col
        if self.on_row is not None:
            self.on_row(rgb)

    def _scan(self, start: float, dur: float, width: int) -> np.ndarray:
        """Read one channel sweep into `width` pixel values (0–255)."""
        out = np.zeros(width, dtype=np.uint8)
        px = dur / width
        for i in range(width):
            seg = self._slice(start + i * px, start + (i + 1) * px)
            f = float(np.mean(seg)) if seg.size else BLACK_HZ
            v = (f - BLACK_HZ) / (WHITE_HZ - BLACK_HZ)
            out[i] = int(np.clip(v, 0.0, 1.0) * 255.0 + 0.5)
        return out
