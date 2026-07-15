"""Sub-audible tone detection for NFM: CTCSS tones and DCS codes.

Repeaters and shared channels mark transmissions with a sub-audible signature
below ~300 Hz. Two schemes exist:

* **CTCSS** — a single continuous tone from a standard table (67–254.1 Hz).
  Detected by FFT over a 1 s window: the dominant narrow line in the sub-audio
  band, matched to the nearest standard tone.

* **DCS** — a continuous 134.4 bps NRZ bitstream: one 23-bit Golay(23,12)
  codeword repeating forever. Detected by slicing the sub-audio at the bit
  rate, folding the repeats into one 23-bit cyclic word, and checking every
  rotation (and both polarities) for a valid parity + a known code number.
  DCS codes have rotation aliases by design, so (as with any scanner) the
  reported code is the canonical member of the alias set that matched.

The detector runs on the demodulated NFM audio, decimated to 1.2 kHz — the
whole thing costs a 129-tap FIR plus one small FFT every ~0.3 s.
"""
from __future__ import annotations

import numpy as np

from app.dsp.blocks import RealDecimator

SUB_RATE = 1_200.0          # detection sample rate (Hz)
DCS_BAUD = 134.4            # DCS bit rate (bits/s)

# Standard CTCSS tone set (Hz).
CTCSS_TONES = np.array([
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4,
    100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5,
    141.3, 146.2, 151.4, 156.7, 162.2, 167.9, 173.8, 179.9, 186.2, 192.8,
    199.5, 206.5, 213.8, 221.3, 229.1, 233.6, 241.8, 250.3, 254.1,
])

# Standard DCS code numbers (octal, as strings).
DCS_CODES = [
    "023", "025", "026", "031", "032", "036", "043", "047", "051", "053",
    "054", "065", "071", "072", "073", "074", "114", "115", "116", "122",
    "125", "131", "132", "134", "143", "145", "152", "155", "156", "162",
    "165", "172", "174", "205", "212", "223", "225", "226", "243", "244",
    "245", "246", "251", "252", "255", "261", "263", "265", "266", "271",
    "274", "306", "311", "315", "325", "331", "332", "343", "346", "351",
    "356", "364", "365", "371", "411", "412", "413", "423", "431", "432",
    "445", "446", "452", "454", "455", "462", "464", "465", "466", "503",
    "506", "516", "523", "526", "532", "546", "565", "606", "612", "624",
    "627", "631", "632", "654", "662", "664", "703", "712", "723", "731",
    "732", "734", "743", "754",
]

# Golay(23,12) generator polynomial (x^11 + x^9 + x^7 + x^6 + x^5 + x + 1).
_GOLAY_POLY = 0xAE3


def _golay_parity(data12: int) -> int:
    """11 parity bits for 12 data bits (systematic cyclic encoding)."""
    reg = data12 << 11
    for i in range(22, 10, -1):
        if reg & (1 << i):
            reg ^= _GOLAY_POLY << (i - 11)
    return reg & 0x7FF


def dcs_word(code_octal: str) -> int:
    """The 23-bit DCS word for a code: 9 code bits, '100' marker, 11 parity."""
    data = (0b100 << 9) | int(code_octal, 8)      # bits 0..8 code, 9..11 = 100b
    return (_golay_parity(data) << 12) | data


def dcs_bits(code_octal: str, invert: bool = False) -> np.ndarray:
    """One period of the transmitted bit sequence (LSB of the word first)."""
    w = dcs_word(code_octal)
    bits = np.array([(w >> i) & 1 for i in range(23)], dtype=np.uint8)
    return bits ^ 1 if invert else bits


# Precomputed cyclic patterns for every code (normal + inverted detection is
# handled by flipping the received bits, not by extra table entries).
_WORD_BY_CODE = {c: dcs_word(c) for c in DCS_CODES}
_CODE_BY_WORD = {w: c for c, w in _WORD_BY_CODE.items()}


class ToneDetector:
    """Streaming CTCSS/DCS detector for demodulated NFM audio.

    Feed every audio block via :meth:`process`; read :attr:`current` — ``None``
    (no signature), ``"88.5"`` (CTCSS Hz) or ``"D023"``/``"D023I"`` (DCS code,
    ``I`` = inverted). Detection needs ~0.6 s of signal; two consecutive
    agreeing evaluations declare a signature and three empty ones clear it, so
    the reading doesn't flap on voice peaks.
    """

    _WIN = int(SUB_RATE)                # 1 s analysis window
    _EVAL_EVERY = int(SUB_RATE * 0.3)   # re-evaluate every 0.3 s

    def __init__(self, audio_rate: float) -> None:
        decim = max(1, int(round(audio_rate / SUB_RATE)))
        self._dec = RealDecimator(audio_rate, decim, 280.0)
        self._buf = np.zeros(0)
        self._since_eval = 0
        self._candidate: str | None = None
        self._agree = 0
        self._misses = 0
        self.current: str | None = None

    def process(self, audio: np.ndarray) -> None:
        x = self._dec.process(audio)
        if x.size == 0:
            return
        self._buf = np.concatenate((self._buf, x))[-2 * self._WIN:]
        self._since_eval += x.size
        if self._since_eval < self._EVAL_EVERY or self._buf.size < self._WIN:
            return
        self._since_eval = 0
        found = self._detect(self._buf[-self._WIN:])
        if found is not None and found == self._candidate:
            self._agree += 1
            self._misses = 0
            if self._agree >= 2:
                self.current = found
        elif found is not None:
            self._candidate = found
            self._agree = 1
            self._misses = 0
        else:
            self._misses += 1
            if self._misses >= 3:
                self.current = None
                self._candidate = None
                self._agree = 0

    # --- one evaluation over a 1 s window -----------------------------------
    def _detect(self, x: np.ndarray) -> str | None:
        x = x - float(np.mean(x))
        if float(np.sqrt(np.mean(x * x))) < 1e-4:    # dead air
            return None
        tone = self._detect_ctcss(x)
        if tone is not None:
            return tone
        return self._detect_dcs(x)

    def _detect_ctcss(self, x: np.ndarray) -> str | None:
        spec = np.abs(np.fft.rfft(x * np.hanning(x.size))) ** 2
        hz_per_bin = SUB_RATE / x.size               # 1.2 Hz at the 1 s window
        lo, hi = int(60 / hz_per_bin), int(262 / hz_per_bin)
        band = spec[lo:hi]
        if band.size < 8:
            return None
        k = int(np.argmax(band))
        peak = float(band[k])
        total = float(np.sum(band)) + 1e-12
        median = float(np.median(band)) + 1e-12
        if peak / total < 0.25 or peak / median < 50.0:
            return None                              # no single dominant line
        # parabolic interpolation refines the peak to a fraction of a bin
        if 0 < k < band.size - 1:
            a, b, c = band[k - 1], band[k], band[k + 1]
            k = k + 0.5 * (a - c) / (a - 2 * b + c + 1e-12)
        freq = (lo + k) * hz_per_bin
        nearest = float(CTCSS_TONES[np.argmin(np.abs(CTCSS_TONES - freq))])
        if abs(nearest - freq) > 1.2:                # tones sit ~2.3 Hz apart
            return None
        return f"{nearest:g}"

    def _detect_dcs(self, x: np.ndarray) -> str | None:
        spb = SUB_RATE / DCS_BAUD                    # ~8.93 samples per bit
        n_bits = int(x.size / spb) - 1
        if n_bits < 3 * 23:
            return None
        best = None                                  # (consistency, bits)
        for phase in (0.0, spb / 3.0, 2.0 * spb / 3.0):
            centers = ((np.arange(n_bits) + 0.5) * spb + phase).astype(int)
            centers = centers[centers < x.size - 1]
            # average 3 samples around each bit centre before slicing
            level = x[centers - 1] + x[centers] + x[centers + 1]
            bits = (level > 0).astype(np.uint8)
            same = float(np.mean(bits[:-23] == bits[23:]))
            if best is None or same > best[0]:
                best = (same, bits)
        consistency, bits = best
        if consistency < 0.93:                       # not a repeating 23-bit stream
            return None
        n = (bits.size // 23) * 23
        folded = (np.mean(bits[:n].reshape(-1, 23), axis=0) > 0.5).astype(np.uint8)
        for flip, suffix in ((0, ""), (1, "I")):
            p = folded ^ flip
            for r in range(23):
                w = 0
                for i in range(23):
                    w |= int(p[(r + i) % 23]) << i
                code = _CODE_BY_WORD.get(w)
                if code is not None:
                    return f"D{code}{suffix}"
        return None
