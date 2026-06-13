"""RDS (Radio Data System) decoder — the 57 kHz data subcarrier in broadcast FM.

Two layers, kept separate so each is testable on its own:

* :class:`RdsGroupDecoder` — pure bit/data logic. Takes the recovered 1187.5 bps
  bitstream, finds block/group sync via the RDS offset-word syndromes, and parses
  the common groups: **0A/0B** (program service name → station name "PS") and
  **2A/2B** (radiotext "RT"), plus PI code and program type (PTY). No DSP here, so
  it can be unit-tested with synthetic groups.

* :class:`RdsDemod` — the DSP front-end. Takes the FM multiplex signal (the
  discriminator output) block by block, locks a PLL to the 19 kHz stereo pilot,
  derives the 57 kHz subcarrier (3× pilot), coherently demodulates the BPSK,
  recovers the symbol clock, biphase- + differential-decodes to bits, and feeds
  :class:`RdsGroupDecoder`.

RDS spec: EN 50067 / IEC 62106. The block is 16 info bits + a 10-bit checkword
(a shortened cyclic code with a per-block offset word added).
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from scipy.signal import firwin, lfilter, resample_poly

from app.dsp.blocks import PilotPll

# --- cyclic code (26,16) used for block sync / error detection --------------
_POLY = 0x5B9   # x^10 + x^8 + x^7 + x^5 + x^4 + x^3 + 1  (includes x^10)
_PLEN = 10

# Offset words added to each block's checkword (10-bit), per block in a group.
OFFSET_WORDS = {"A": 0x0FC, "B": 0x198, "C": 0x168, "Cp": 0x350, "D": 0x1B4}


def calc_syndrome(msg: int, mlen: int = 26) -> int:
    """Remainder of ``msg`` (mlen bits, MSB first) under the RDS generator."""
    reg = 0
    for i in range(mlen - 1, -1, -1):
        reg = (reg << 1) | ((msg >> i) & 1)
        if reg & (1 << _PLEN):
            reg ^= _POLY
    return reg & ((1 << _PLEN) - 1)


# Expected syndrome for each offset (CRC is linear, so a clean block of a given
# type has syndrome == calc_syndrome(its offset word)).
SYNDROMES = {name: calc_syndrome(off) for name, off in OFFSET_WORDS.items()}


def _crc10(data16: int) -> int:
    """10-bit CRC of 16 info bits (append 10 zeros, take remainder)."""
    return calc_syndrome((data16 & 0xFFFF) << 10, 26)


def make_block(data16: int, offset_name: str) -> int:
    """Encode a 26-bit block: 16 info bits + (crc ^ offset). For tests/synthesis."""
    check = _crc10(data16) ^ OFFSET_WORDS[offset_name]
    return ((data16 & 0xFFFF) << 10) | (check & 0x3FF)


class RdsGroupDecoder:
    """Bitstream -> synced groups -> PS / RT / PI / PTY. Pure logic (no DSP)."""

    _SEQ = ["A", "B", "C", "D"]

    def __init__(self, on_update: Optional[Callable[[dict], None]] = None) -> None:
        self.on_update = on_update
        self._reg = 0
        self._synced = False
        self._bitcount = 0
        self._pos = 0                     # index in _SEQ of the next block to close
        self._group = [0, 0, 0, 0]
        self._errs = 0
        # decoded state
        self.pi: Optional[int] = None
        self.pty: Optional[int] = None
        self.ps = [" "] * 8
        self.rt = [" "] * 64
        self._rt_ab: Optional[int] = None

    # --- bit input ----------------------------------------------------------
    def feed_bits(self, bits) -> None:
        for b in bits:
            self.feed_bit(int(b))

    def feed_bit(self, bit: int) -> None:
        self._reg = ((self._reg << 1) | (bit & 1)) & 0x3FFFFFF  # keep 26 bits
        if not self._synced:
            # hunt for a block-A boundary
            if calc_syndrome(self._reg) == SYNDROMES["A"]:
                self._synced = True
                self._bitcount = 0
                self._group[0] = (self._reg >> 10) & 0xFFFF
                self._pos = 1
                self._errs = 0
            return

        self._bitcount += 1
        if self._bitcount < 26:
            return
        self._bitcount = 0
        name = self._SEQ[self._pos]
        synd = calc_syndrome(self._reg)
        ok = synd == SYNDROMES[name] or (name == "C" and synd == SYNDROMES["Cp"])
        self._group[self._pos] = (self._reg >> 10) & 0xFFFF
        if not ok:
            self._errs += 1
        if self._pos == 3:
            self._parse_group(self._group)
            if self._errs >= 3:        # lost it — re-hunt for sync
                self._synced = False
            self._errs = 0
        self._pos = (self._pos + 1) % 4

    # --- group parsing ------------------------------------------------------
    def _parse_group(self, g: list[int]) -> None:
        a, b, c, d = g
        changed = False
        if self.pi != a:
            self.pi = a
            changed = True
        gtype = (b >> 12) & 0xF
        ver_b = (b >> 11) & 1
        pty = (b >> 5) & 0x1F
        if pty != self.pty:
            self.pty = pty
            changed = True

        if gtype == 0:                 # 0A/0B: program service name
            addr = b & 0x3
            for k, ch in enumerate(((d >> 8) & 0xFF, d & 0xFF)):
                idx = addr * 2 + k
                if 0 <= idx < 8:
                    self.ps[idx] = _ch(ch)
            changed = True
        elif gtype == 2:               # 2A/2B: radiotext
            ab = (b >> 4) & 1
            if self._rt_ab is not None and ab != self._rt_ab:
                self.rt = [" "] * 64   # A/B flag toggled -> message changed
            self._rt_ab = ab
            addr = b & 0xF
            if ver_b == 0:             # 2A: 4 chars (in C and D)
                chars = ((c >> 8) & 0xFF, c & 0xFF, (d >> 8) & 0xFF, d & 0xFF)
                base = addr * 4
            else:                      # 2B: 2 chars (in D)
                chars = ((d >> 8) & 0xFF, d & 0xFF)
                base = addr * 2
            for k, ch in enumerate(chars):
                if 0 <= base + k < 64:
                    self.rt[base + k] = _ch(ch)
            changed = True

        if changed and self.on_update is not None:
            self.on_update(self.snapshot())

    def snapshot(self) -> dict:
        return {
            "type": "rds",
            "pi": f"{self.pi:04X}" if self.pi is not None else None,
            "pty": self.pty,
            "ps": "".join(self.ps).strip(),
            "rt": "".join(self.rt).strip(),
        }


def _ch(code: int) -> str:
    """RDS uses a basic Latin charset; treat printable ASCII as-is, else space."""
    return chr(code) if 32 <= code < 127 else " "


# --- DSP front-end ----------------------------------------------------------
RDS_BITRATE = 1187.5
SUBCARRIER = 57_000.0
PILOT = 19_000.0


class RdsDemod:
    """FM multiplex (discriminator output) -> RDS groups, streaming."""

    def __init__(self, fs: float, on_update: Optional[Callable[[dict], None]] = None):
        self.fs = fs
        self._pll = PilotPll(fs, PILOT)
        # low-pass for the 57 kHz baseband after mixing (~2.4 kHz one-sided)
        self._lp = firwin(129, 2_400.0 / (fs / 2))
        self._zi_i = np.zeros(128)
        self._zi_q = np.zeros(128)
        # resample the baseband to 16 samples/symbol (1187.5 * 16 = 19 kHz)
        self._sps = 16
        self._sym_rate = RDS_BITRATE * self._sps   # 19000 Hz
        self._decoder = RdsGroupDecoder(on_update)
        self._prev_enc = 0
        self._sym_buf_i = np.zeros(0)
        self._sym_buf_q = np.zeros(0)
        self._locked = False       # symbol timing + I/Q branch locked once
        self._branch = 0           # 0 = I, 1 = Q (resolves the 90° ambiguity)

    def process(self, mpx: np.ndarray) -> None:
        if mpx.size == 0:
            return
        phase = self._pll.run(mpx)
        # 57 kHz reference = 3× pilot phase; coherent mix to baseband (I/Q)
        ref = 3.0 * phase
        i = mpx * np.cos(ref)
        q = mpx * np.sin(ref)
        i, self._zi_i = lfilter(self._lp, 1.0, i, zi=self._zi_i)
        q, self._zi_q = lfilter(self._lp, 1.0, q, zi=self._zi_q)
        # resample both to the symbol-clock rate (19 kHz, 16 samples/symbol)
        up, down = self._ratio()
        ri = resample_poly(i, up, down)
        rq = resample_poly(q, up, down)
        self._symbolize(ri, rq)

    def _ratio(self) -> tuple[int, int]:
        from math import gcd
        up, down = int(self._sym_rate), int(self.fs)
        g = gcd(up, down)
        return up // g, down // g

    def _symbolize(self, si: np.ndarray, sq: np.ndarray) -> None:
        """Recover symbols continuously across blocks.

        The earlier version re-searched the best sample phase on *every* block and
        threw away the leading samples each time — that dropped a partial symbol at
        every block boundary, slipping bits and wrecking sync on a live stream. Now
        the phase + I/Q branch are locked *once*; thereafter we keep one symbol of
        guard history and only nudge the phase by ±1 sample per block to track the
        dongle's clock drift, preserving continuity.
        """
        bi = np.concatenate([self._sym_buf_i, si])
        bq = np.concatenate([self._sym_buf_q, sq])
        sps, half = self._sps, self._sps // 2

        if not self._locked:
            if bi.size < sps * 8:            # gather enough for a solid estimate
                self._sym_buf_i, self._sym_buf_q = bi, bq
                return
            start, self._branch = self._search(bi, bq, sps, half)
            self._locked = True
        else:
            if bi.size < sps * 3:
                self._sym_buf_i, self._sym_buf_q = bi, bq
                return
            # nominal start is past the 1-symbol guard; track drift by ±1 sample
            d = bi if self._branch == 0 else bq
            start = max((sps - 1, sps, sps + 1), key=lambda s: self._energy(d, s, sps, half))

        d = bi if self._branch == 0 else bq
        n = (d.size - start) // sps
        if n <= 0:
            self._sym_buf_i, self._sym_buf_q = bi, bq
            return
        diff = _biphase(d[start:start + n * sps], sps, half)
        enc = (diff > 0).astype(np.int8)
        # differential decode: data = enc XOR previous enc
        prev = np.empty_like(enc)
        prev[0] = self._prev_enc
        prev[1:] = enc[:-1]
        self._prev_enc = int(enc[-1])
        self._decoder.feed_bits(enc ^ prev)
        # retain one symbol of history as the guard for the next block's tracking
        keep = max(0, start + n * sps - sps)
        self._sym_buf_i, self._sym_buf_q = bi[keep:], bq[keep:]

    @staticmethod
    def _energy(d: np.ndarray, start: int, sps: int, half: int) -> float:
        n = (d.size - start) // sps
        if n < 2:
            return -1.0
        return float(np.abs(_biphase(d[start:start + n * sps], sps, half)).mean())

    def _search(self, bi, bq, sps, half) -> tuple[int, int]:
        """One-time lock: best sample phase (0..sps-1) and I/Q branch."""
        best = (-1.0, 0, 0)
        for off in range(sps):
            ei = self._energy(bi, off, sps, half)
            eq = self._energy(bq, off, sps, half)
            if ei > best[0]:
                best = (ei, off, 0)
            if eq > best[0]:
                best = (eq, off, 1)
        return best[1], best[2]


def _biphase(sig: np.ndarray, sps: int, half: int) -> np.ndarray:
    """Per-symbol (first half − second half); its sign is the encoded bit."""
    n = sig.size // sps
    body = sig[: n * sps].reshape(n, sps)
    return body[:, :half].sum(axis=1) - body[:, half:].sum(axis=1)
