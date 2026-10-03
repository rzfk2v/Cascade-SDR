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
tests/test_sstv.py. Supported modes: the RGB sequential ones — Martin M1/M2 and
Scottie S1/S2/DX — plus the YUV families Robot36/Robot72 and PD50/90/120/160/180
(luma + colour-difference channels, converted back to RGB on decode).
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

# Starting a picture from its line syncs when the VIS header was missed (a weak
# satellite pass often only comes up out of the noise after the header). The
# syncs of the last TRAIN_WINDOW_S are folded at every mode's line period; a
# mode wins when most of its lines carry a sync. Shortest period first: a train
# of 150 ms lines also folds perfectly at 300 ms (Robot 72) and 1050 ms
# (Scottie DX), but a real Robot 72 would only put syncs on half the 150 ms grid.
TRAIN_WINDOW_S = 10.0   # how far back a found train can start the picture
TRAIN_EVAL_S = 0.5      # search cadence
TRAIN_MIN_LINES = 8     # lines with a sync before a train counts
TRAIN_MIN_FRAC = 0.7    # of the train's lines (from its first one) that must carry one
TRAIN_MIN_CONTRAST = 4.0
CATCHUP_PERIODS = 4     # scan periods decoded per call while catching up on history

# Channel indices used inside a line layout. RGB modes use R/G/B; YUV modes reuse
# the same slots as luma + the two colour-difference channels, plus a 2nd luma
# slot (Y2) for the PD modes (which pack two image rows per scan period).
R, G, B = 0, 1, 2
Y, CR, CB = 0, 1, 2   # luma, R-Y (Cr), B-Y (Cb)
Y2 = 3                # PD second-line luma


def _yuv_to_rgb(y: np.ndarray, cr: np.ndarray, cb: np.ndarray) -> np.ndarray:
    """Convert one row of 0–255 Y/Cr/Cb samples to an interleaved RGB byte row.

    Uses the JPEG/PIL YCbCr convention (the encoders these modes target), where
    128 is neutral chroma: R = Y + 1.402·(Cr−128), etc.
    """
    yf = y.astype(np.float32)
    crf = cr.astype(np.float32) - 128.0
    cbf = cb.astype(np.float32) - 128.0
    r = yf + 1.402 * crf
    g = yf - 0.344136 * cbf - 0.714136 * crf
    b = yf + 1.772 * cbf
    out = np.zeros(y.size * 3, dtype=np.uint8)
    out[0::3] = np.clip(r, 0, 255).astype(np.uint8)
    out[1::3] = np.clip(g, 0, 255).astype(np.uint8)
    out[2::3] = np.clip(b, 0, 255).astype(np.uint8)
    return out


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
    # "RGB" (Martin/Scottie), "YUV422" (Robot72), "ROBOT36" (4:2:0 alternating
    # chroma), or "PD" (two image rows per scan period). See _emit_line.
    color: str = "RGB"
    sync_porch_ms: float = 0.0   # YUV: porch after the sync pulse, before Y
    porch_ms: float = 0.0        # YUV: 1900 Hz porch before a chroma scan / PD porch
    y_scan_ms: float = 0.0       # YUV: luma sweep duration
    c_scan_ms: float = 0.0       # YUV: chroma sweep duration
    # Filled in __post_init__:
    segments: list = field(default_factory=list)   # (kind, channel, dur_ms)
    line_ms: float = 0.0
    sync_offset_ms: float = 0.0   # time from line start to the sync pulse

    def __post_init__(self) -> None:
        if self.color == "RGB":
            segs = self._rgb_segments()
        elif self.color == "ROBOT36":
            # sync, porch, Y, separator (tone selects chroma), 1900 porch, chroma.
            # The chroma sweep alternates R-Y / B-Y per line; _emit_line resolves
            # which from the line parity, so it parks in the CR slot here.
            segs = [
                ("sync", -1, self.sync_ms),
                ("sep", -1, self.sync_porch_ms),
                ("scan", Y, self.y_scan_ms),
                ("sep", -1, self.sep_ms),
                ("sep", -1, self.porch_ms),
                ("scan", CR, self.c_scan_ms),
            ]
        elif self.color == "YUV422":
            segs = [
                ("sync", -1, self.sync_ms),
                ("sep", -1, self.sync_porch_ms),
                ("scan", Y, self.y_scan_ms),
                ("sep", -1, self.sep_ms),
                ("sep", -1, self.porch_ms),
                ("scan", CR, self.c_scan_ms),
                ("sep", -1, self.sep_ms),
                ("sep", -1, self.porch_ms),
                ("scan", CB, self.c_scan_ms),
            ]
        elif self.color == "PD":
            scan = self.pixel_ms * self.width
            segs = [
                ("sync", -1, self.sync_ms),
                ("sep", -1, self.porch_ms),
                ("scan", Y, scan),     # Y of the 1st (even) image row
                ("scan", CR, scan),    # R-Y, shared by both rows
                ("scan", CB, scan),    # B-Y, shared by both rows
                ("scan", Y2, scan),    # Y of the 2nd (odd) image row
            ]
        else:
            raise ValueError(f"unknown color mode {self.color!r}")
        self.segments = segs
        self.line_ms = sum(d for _, _, d in segs)
        off = 0.0
        for kind, _, d in segs:
            if kind == "sync":
                break
            off += d
        self.sync_offset_ms = off

    def _rgb_segments(self) -> list:
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
        return segs


# VIS-code table. RGB modes give a per-pixel scan time + channel order; the YUV
# families (Robot/PD) give explicit luma/chroma sweep durations instead.
MODES: dict[int, Mode] = {
    m.vis: m
    for m in [
        Mode("Martin M1", 44, 320, 256, 4.862, 0.572, 0.4576, (G, B, R), True),
        Mode("Martin M2", 40, 320, 256, 4.862, 0.572, 0.2288, (G, B, R), True),
        Mode("Scottie S1", 60, 320, 256, 9.0, 1.5, 0.4320, (G, B, R), False, True),
        Mode("Scottie S2", 56, 320, 256, 9.0, 1.5, 0.2752, (G, B, R), False, True),
        Mode("Scottie DX", 76, 320, 256, 9.0, 1.5, 1.08, (G, B, R), False, True),
        # Robot YUV modes: sync 9, sync-porch 3, inter-channel gap 4.5, 1900 porch 1.5.
        Mode("Robot 36", 8, 320, 240, 9.0, 4.5, 0.0, (), True, color="ROBOT36",
             sync_porch_ms=3.0, porch_ms=1.5, y_scan_ms=88.0, c_scan_ms=44.0),
        Mode("Robot 72", 12, 320, 240, 9.0, 4.5, 0.0, (), True, color="YUV422",
             sync_porch_ms=3.0, porch_ms=1.5, y_scan_ms=138.0, c_scan_ms=69.0),
        # PD modes: sync 20, porch 2.08, four equal scans (Y, R-Y, B-Y, Y) per period.
        Mode("PD 50", 93, 320, 256, 20.0, 0.0, 0.286, (), True, color="PD", porch_ms=2.08),
        Mode("PD 90", 99, 320, 256, 20.0, 0.0, 0.532, (), True, color="PD", porch_ms=2.08),
        Mode("PD 120", 95, 640, 496, 20.0, 0.0, 0.190, (), True, color="PD", porch_ms=2.08),
        Mode("PD 160", 98, 512, 400, 20.0, 0.0, 0.382, (), True, color="PD", porch_ms=2.08),
        Mode("PD 180", 96, 640, 496, 20.0, 0.0, 0.286, (), True, color="PD", porch_ms=2.08),
    ]
}


_BY_PERIOD = sorted(MODES.values(), key=lambda m: m.line_ms)


_PHASES: dict[tuple[float, int], tuple[np.ndarray, np.ndarray]] = {}


def _fold(x: np.ndarray, period: float) -> np.ndarray:
    """Mean of `x` (1 ms bins) at each phase of a `period`-ms cycle."""
    nb = int(np.ceil(period))
    key = (period, x.size)
    if key not in _PHASES:          # the search window is a fixed size once full
        if len(_PHASES) > 64:
            _PHASES.clear()
        ph = np.floor(np.arange(x.size) % period).astype(np.int64)
        _PHASES[key] = (ph, np.maximum(np.bincount(ph, minlength=nb), 1))
    ph, count = _PHASES[key]
    return np.bincount(ph, weights=x, minlength=nb) / count


def _train(s: np.ndarray, m: Mode) -> Optional[tuple[np.ndarray, np.ndarray]]:
    """Look for mode m's line syncs in per-ms sync-likeness `s`.

    Returns the train's line grid (ms into `s`, starting at its first confirmed
    line) and which of those lines carry a sync — or None if it isn't there.
    """
    period, n_sync = m.line_ms, max(1, int(round(m.sync_ms)))
    c = np.concatenate(([0.0], np.cumsum(s)))
    sm = (c[n_sync:] - c[:-n_sync]) / n_sync          # mean over a sync length from j
    if sm.size < TRAIN_MIN_LINES * period:
        return None
    phi = int(np.argmax(_fold(sm, period)))            # the syncs' phase in the cycle
    pos = np.round(phi + np.arange(int((sm.size - 1 - phi) // period) + 1) * period)
    pos = pos.astype(np.int64)
    # A line carries a sync if it stands out from the rest of *that* line, not
    # from the window: a clean picture reads ~0 between its syncs and noise
    # ~0.03, one window can hold both, and noise ahead of a picture then passes
    # for syncs — differently on every mode's grid.
    # (sm is a moving average over the sync length, so neighbouring bins are
    # nearly the same: every few bins gives the same median, several times cheaper)
    body = np.arange(n_sync + 2, int(period) - 2, max(1, n_sync // 2))
    rest = np.median(sm[np.clip(pos[:, None] + body, 0, sm.size - 1)], axis=1)
    peak = sm[np.clip(pos[:, None] + np.array([-1, 0, 1]), 0, sm.size - 1)].max(axis=1)
    hit = peak > np.maximum(3.0 * rest, 0.05)
    # The train is judged from its first confirmed line on, so whatever came
    # before it doesn't count — and a short-period mode reaches its minimum
    # line count first, before any longer mode its syncs also fit.
    k0 = next((k for k in range(hit.size) if hit[k] and hit[k:k + 4].sum() >= 3), None)
    if k0 is None:
        return None
    h = hit[k0:]
    if h.sum() < TRAIN_MIN_LINES or h.mean() < TRAIN_MIN_FRAC:
        return None
    # Contrast over the train too, not the window: diluted by the noise ahead
    # of a picture, it crept over the line first for Robot 72's coarser fold
    # while Robot 36 had every line.
    tf = _fold(sm[pos[k0]:], period)
    if tf.max() < TRAIN_MIN_CONTRAST * (float(np.median(tf)) + 1e-3):
        return None
    if not _sync_width_fits(s, period, phi, m):
        return None
    return pos[k0:], h


def _sync_width_fits(s: np.ndarray, period: float, phi: int, m: Mode) -> bool:
    """The syncs must be about as long as the mode's: otherwise a 9 ms Robot
    train that happens to fold at a PD line period would pose as PD's 20 ms."""
    prof = _fold(s, period)
    w = int(round(m.sync_ms))
    seg = np.roll(prof, -(phi - 3))[: 2 * w + 10] - float(np.median(prof))
    top = float(seg.max())
    if top <= 0.0:
        return False
    width = int((seg > 0.5 * top).sum())
    return 0.6 * m.sync_ms <= width <= 1.5 * m.sync_ms + 2.0


def _parity_separator(m: Mode) -> tuple[float, float]:
    """(offset, duration) in ms of Robot 36's even/odd tone: the sep after the Y scan."""
    t, seen_scan = 0.0, False
    for kind, _ch, dur in m.segments:
        if kind == "sep" and seen_scan:
            return t, dur
        seen_scan = seen_scan or kind == "scan"
        t += dur
    return 0.0, 0.0


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
        # DC blocker (~30 Hz high-pass): an off-centre FM carrier — e.g. Doppler
        # on a satellite pass — demodulates to a DC level that can dwarf the tone.
        r = 1.0 - 2.0 * np.pi * 30.0 / self.fs
        self._dc_b, self._dc_a = np.array([1.0, -1.0]), np.array([1.0, -r])
        self._dc_zi = np.zeros(1)
        self._phase = 0.0                 # running heterodyne phase (radians)
        self._prev = 0.0 + 0.0j           # last sample, for the discriminator
        self._f = np.zeros(0)             # buffered instantaneous frequency
        self._origin = 0                  # absolute sample index of self._f[0]
        self._fed = 0                     # total samples fed (absolute clock)
        # Decoder state
        self.mode: Optional[Mode] = None
        self._cursor = 0.0                # absolute sample index of the next line
        self._vis_next = 0                # absolute index the VIS search resumes at
        self._train_from = 0              # sync evidence before this was the last picture
        self._train_next = int(self.fs * TRAIN_EVAL_S)  # when the train search next runs
        self.started_by: Optional[str] = None   # "vis" or "sync": how this picture was found
        self._sbins = np.zeros(0)         # per-ms sync-likeness, for the train search
        self._sbin0 = 0                   # absolute ms-bin index of _sbins[0]
        self.rows = 0                     # image rows emitted so far
        self._scan_idx = 0               # scan periods consumed (≠ rows for PD/Robot36)
        # Robot36 cross-line chroma reconstruction (one chroma per line, paired):
        self._y_top: Optional[np.ndarray] = None
        self._cr: Optional[np.ndarray] = None

    # --- front end: audio -> instantaneous frequency ------------------------
    def _to_freq(self, audio: np.ndarray) -> np.ndarray:
        audio, self._dc_zi = lfilter(self._dc_b, self._dc_a, audio, zi=self._dc_zi)
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
        self._tally_syncs()
        if self.mode is None:
            self._try_vis()
        if self.mode is None and self._fed >= self._train_next:
            self._train_next = self._fed + int(self._ms(TRAIN_EVAL_S * 1000.0))
            self._try_train()
        if self.mode is not None:
            self._decode_lines(audio.size)
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
        # Drop consumed history, keeping a small margin before the cursor — or,
        # while still searching, the leader a not-yet-examined start bit needs.
        if self.mode is None:
            keep_from = min(self._vis_next - int(self._ms(200.0)),
                            self._fed - int(self._ms((TRAIN_WINDOW_S + 1.0) * 1000.0)))
        else:
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
        lead = int(self._ms(170.0))     # 1900 leader required before the start bit
        bitn = int(self._ms(VIS_BIT_MS))
        last = self._f.size - int(self._ms(VIS_BIT_MS * 10))
        # Each candidate start is examined exactly once: resume where the last
        # call stopped rather than rescanning the buffer (which would cost more
        # than real time while waiting on plain noise).
        first = max(lead + 1, self._vis_next - self._origin)
        if last <= first:
            return
        self._vis_next = self._origin + last
        # Only the unexamined tail (plus the leader a start bit needs): the buffer
        # also holds seconds of history for the sync-train search.
        base = first - lead - 1
        fv = self._f[base:]
        first, last = first - base, last - base
        is1200 = np.abs(fv - SYNC_HZ) < 70.0
        # Start bit = a rising edge into 1200 Hz, preceded by the leader, with a
        # ~30 ms run (this length rules out the 10 ms calibration break).
        edges = first + np.flatnonzero(is1200[first:last] & ~is1200[first - 1:last - 1])
        if edges.size == 0:
            return
        n1900 = np.concatenate(([0], np.cumsum(np.abs(fv - CENTER_HZ) < 70.0)))
        span = lead - bitn // 4
        edges = edges[(n1900[edges - bitn // 4] - n1900[edges - lead]) >= 0.8 * span]
        if edges.size == 0:
            return
        is1100 = np.abs(fv - 1100.0) < 70.0
        is1300 = np.abs(fv - 1300.0) < 70.0
        max_run = int(self._ms(45.0))
        for s in edges:
            s = int(s)
            run = 0
            while run <= max_run and s + run < is1200.size and is1200[s + run]:
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
            self.started_by = "vis"
            self._lock(mode, abs_index=self._origin + base + s + int(self._ms(VIS_BIT_MS * 10)))
            return

    def _tally_syncs(self) -> None:
        """Extend the per-ms sync tally with the bins the new audio completed.

        Kept incrementally (a few dozen bins per call) so the train search never
        has to re-derive seconds of history from the raw frequency buffer.
        """
        spb = self.fs / 1000.0
        j0 = self._sbin0 + self._sbins.size          # next bin to fill
        j1 = int(self._fed // spb)                    # bins the audio now completes
        if j1 <= j0:
            return
        e = np.round(np.arange(j0, j1 + 1) * spb).astype(np.int64) - self._origin
        if e[0] < 0:                                  # history gone: restart the tally
            self._sbins, self._sbin0 = np.zeros(0), j1
            return
        c = np.concatenate(([0], np.cumsum(np.abs(self._f[e[0]:e[-1]] - SYNC_HZ) < 60.0)))
        new = (c[e[1:] - e[0]] - c[e[:-1] - e[0]]) / np.diff(e)
        keep = int((TRAIN_WINDOW_S + 1.0) * 1000.0)
        self._sbins = np.concatenate((self._sbins, new))[-keep:]
        self._sbin0 = j1 - self._sbins.size

    def _try_train(self) -> None:
        """Start a picture from its train of line syncs, when no VIS header came."""
        spb = self.fs / 1000.0
        end = self._sbin0 + self._sbins.size
        lo = max(int(np.ceil(self._train_from / spb)), self._sbin0,
                 end - int(TRAIN_WINDOW_S * 1000.0))
        s = self._sbins[lo - self._sbin0:]
        for m in _BY_PERIOD:                 # shortest first: harmonics can't win
            found = _train(s, m)
            if found is None:
                continue
            pos, hit = found                 # pos[0]: the picture's first confirmed line
            line = self._ms(m.line_ms)
            sync = self._refine_sync((lo + pos[0]) * spb, line, np.flatnonzero(hit), m)
            start = sync - self._ms(m.sync_offset_ms)   # Scottie: sync is mid-line
            while start < self._origin:
                start += line
            if m.color == "ROBOT36" and self._robot36_odd(start, line, m):
                start += line                # chroma pairs must begin on an R-Y line
            self.started_by = "sync"
            self._lock(m, int(start))
            self._cursor = float(start)      # _lock's leading-sync step is VIS-only
            return

    def _refine_sync(self, coarse: float, line: float, lines: np.ndarray, m: Mode) -> float:
        """Sample-accurate sync start (absolute index): align a sync-length matched
        filter across the train's lines within ±1.5 ms of the 1 ms grid estimate."""
        n = max(1, int(round(self._ms(m.sync_ms))))
        r = int(self._ms(1.5))
        score = np.zeros(2 * r + 1)
        for k in lines:
            at = int(round(coarse + k * line)) - self._origin
            if at - r < 0 or at + r + n > self._f.size:
                continue
            c = np.concatenate(([0], np.cumsum(np.abs(self._f[at - r:at + r + n] - SYNC_HZ) < 60.0)))
            score += (c[n:] - c[:-n]) / n
        return coarse + float(np.argmax(score) - r)

    def _robot36_odd(self, start: float, line: float, m: Mode) -> bool:
        """Does the line at `start` carry B-Y? Its separator is 2300 Hz, R-Y's 1500."""
        off, dur = _parity_separator(m)
        votes = n = 0
        for k in range(12):
            a = int(start + k * line + self._ms(off)) - self._origin
            b = a + int(self._ms(dur))
            if a < 0 or b > self._f.size:
                break
            odd_here = float(np.median(self._f[a:b])) > CENTER_HZ
            votes += odd_here != (k % 2 == 1)    # this line says line 0 is odd
            n += 1
        return n > 0 and 2 * votes > n

    def _lock(self, mode: Mode, abs_index: int) -> None:
        self.mode = mode
        self.rows = 0
        self._scan_idx = 0
        self._y_top = None
        self._cr = None
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
        # Every candidate k at once (a per-k loop walked the whole window in
        # noise, which is exactly when a weak pass needs it): a run-length mean
        # from a cumulative sum, then the first rising edge into a sync run.
        c = np.concatenate(([0], np.cumsum(band)))
        ks = np.arange(1, seg.size - run)
        ok = ks[(c[ks + run] - c[ks]) / run > 0.8]
        if ok.size == 0:
            return None
        rising = ok[~band[ok - 1]]       # rising edge into the sync — best anchor
        return lo + int(rising[0] if rising.size else ok[0])

    def _decode_lines(self, new_samples: int = 0) -> None:
        m = self.mode
        assert m is not None
        line_n = self._ms(m.line_ms)
        # A sync-started picture begins seconds in the past; spread that backlog
        # over a few calls rather than decoding it in one burst on the IQ thread.
        # Whatever the new audio holds is always decoded, so a picture found by
        # its header (no backlog) is never held back.
        budget = int(new_samples // line_n) + 1 + CATCHUP_PERIODS
        while self.rows < m.height:
            if budget == 0:
                return
            budget -= 1
            line_start = self._cursor
            need_abs = line_start + line_n
            if self._origin + self._f.size < need_abs:
                return  # wait for more audio
            # The first scan period's start comes straight from the VIS word
            # (exact); later periods re-anchor to their sync pulse to track drift.
            if self._scan_idx > 0:
                sync = self._find_sync(line_start + self._ms(m.sync_offset_ms))
                if sync is not None:
                    line_start = sync - self._ms(m.sync_offset_ms)
            self.rows += self._emit_line(line_start)
            self._cursor = line_start + line_n
            self._scan_idx += 1
        # Picture complete: go back to listening for the next VIS header — and
        # don't let this picture's own syncs start another one.
        self.mode = None
        self._vis_next = int(self._cursor)
        self._train_from = int(self._cursor)

    def _emit_line(self, line_start: float) -> int:
        """Decode one scan period; emit its image row(s); return how many."""
        m = self.mode
        assert m is not None
        scans: dict[int, np.ndarray] = {}
        t = line_start
        for kind, ch, dur in m.segments:
            d = self._ms(dur)
            if kind == "scan":
                scans[ch] = self._scan(t, d, m.width)
            t += d

        if m.color == "RGB":
            rgb = np.zeros(m.width * 3, dtype=np.uint8)
            for ci in range(3):
                if ci in scans:
                    rgb[ci::3] = scans[ci]
            self._emit_row(rgb)
            return 1

        if m.color == "YUV422":          # Robot72: Y + R-Y + B-Y, all this line
            self._emit_row(_yuv_to_rgb(scans[Y], scans[CR], scans[CB]))
            return 1

        if m.color == "ROBOT36":
            # 4:2:0: even lines carry R-Y, odd lines carry B-Y. Buffer the even
            # line and emit the pair together once its B-Y arrives, so both rows
            # share the same chroma (vertical chroma subsampling).
            if self._scan_idx % 2 == 0:
                self._y_top = scans[Y]
                self._cr = scans[CR]
                return 0
            cb = scans[CR]                       # odd line's chroma sweep is B-Y
            cr = self._cr if self._cr is not None else cb
            y_top = self._y_top if self._y_top is not None else scans[Y]
            self._emit_row(_yuv_to_rgb(y_top, cr, cb))
            self._emit_row(_yuv_to_rgb(scans[Y], cr, cb))
            return 2

        # PD: one period packs two image rows that share the colour-difference
        # sweeps (R-Y, B-Y were averaged across the pair on transmit).
        cr, cb = scans[CR], scans[CB]
        self._emit_row(_yuv_to_rgb(scans[Y], cr, cb))
        self._emit_row(_yuv_to_rgb(scans[Y2], cr, cb))
        return 2

    def _emit_row(self, rgb: np.ndarray) -> None:
        if self.on_row is not None:
            self.on_row(rgb)

    def _scan(self, start: float, dur: float, width: int) -> np.ndarray:
        """Read one channel sweep into `width` pixel values (0–255).

        Each pixel is the mean frequency over its slot, the same index ranges
        `_slice` would cut. One cumulative sum gives every slot's mean at once:
        a per-pixel loop made ~2,500 tiny numpy calls per PD120 line pair, all
        in one burst on the IQ worker thread, which on a Pi could back the
        reader up far enough to drop IQ mid-picture.
        """
        px = dur / width
        edges = start + np.arange(width + 1) * px
        idx = np.round(edges).astype(np.int64) - self._origin   # == int(round(x))
        lo = np.clip(idx[:-1], 0, self._f.size)
        hi = np.clip(idx[1:], 0, self._f.size)
        a, b = int(lo[0]), int(hi[-1])                         # edges ascend
        cs = np.concatenate(([0.0], np.cumsum(self._f[a:b], dtype=np.float64)))
        n = hi - lo
        f = np.where(n > 0, (cs[hi - a] - cs[lo - a]) / np.maximum(n, 1), BLACK_HZ)
        v = (f - BLACK_HZ) / (WHITE_HZ - BLACK_HZ)
        return (np.clip(v, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
