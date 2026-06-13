"""CW (Morse) decoder.

Works on the *envelope* of a narrow channel centred on the CW carrier (CW is just
the carrier keyed on/off). It tracks an adaptive on/off threshold, measures the
mark/gap durations, adapts the dot length, and turns dits/dahs into text. Best on
clean, steady signals — hand-sent or noisy CW will be imperfect.
"""
from __future__ import annotations

import numpy as np

MORSE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F",
    "--.": "G", "....": "H", "..": "I", ".---": "J", "-.-": "K", ".-..": "L",
    "--": "M", "-.": "N", "---": "O", ".--.": "P", "--.-": "Q", ".-.": "R",
    "...": "S", "-": "T", "..-": "U", "...-": "V", ".--": "W", "-..-": "X",
    "-.--": "Y", "--..": "Z",
    "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
    ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
    ".-.-.-": ".", "--..--": ",", "..--..": "?", "-..-.": "/", "-...-": "=",
    ".-.-.": "+", "-....-": "-", "---...": ":", ".--.-.": "@",
}


class CwDecoder:
    def __init__(self, rate: float) -> None:
        self.rate = rate
        self.peak = 1e-3
        self.noise = 0.0
        self.on = False
        self.run = 0
        self.dot = max(2, int(rate * 0.06))  # initial guess ~20 WPM
        self.symbol = ""
        self.last_was_char = False

    def process(self, env: np.ndarray) -> str:
        out: list[str] = []
        for v in env:
            # adaptive signal/noise envelope (fast capture, slow release)
            self.peak = v if v > self.peak else self.peak + (v - self.peak) * 0.0008
            self.noise = v if v < self.noise else self.noise + (v - self.noise) * 0.0008
            span = max(1e-6, self.peak - self.noise)
            thr_hi = self.noise + span * 0.55
            thr_lo = self.noise + span * 0.45

            # only key when there's real dynamic range (avoids latching on noise
            # at cold start, which would eat the first character)
            active = self.peak > self.noise * 4 + 1e-6
            on = self.on
            if not active:
                on = False
            elif not self.on and v > thr_hi:
                on = True
            elif self.on and v < thr_lo:
                on = False

            if on != self.on:
                if self.on:
                    self._end_mark(self.run)  # a mark (dit/dah) just finished
                self.on = on
                self.run = 1
            else:
                self.run += 1
                if not self.on:  # inside a gap — flush char / word once
                    if self.symbol and self.run == int(self.dot * 2.5):
                        self._flush(out)
                    elif (not self.symbol and self.last_was_char
                          and self.run == int(self.dot * 6)):
                        out.append(" ")
                        self.last_was_char = False
        return "".join(out)

    def _end_mark(self, dur: int) -> None:
        if dur < self.dot * 2:
            self.symbol += "."
            self.dot = int(0.85 * self.dot + 0.15 * max(2, dur))  # adapt to dot
        else:
            self.symbol += "-"
            self.dot = int(0.85 * self.dot + 0.15 * max(2, dur / 3))
        if len(self.symbol) > 8:  # runaway guard
            self.symbol = ""

    def _flush(self, out: list[str]) -> None:
        ch = MORSE.get(self.symbol)
        if ch:
            out.append(ch)
            self.last_was_char = True
        self.symbol = ""
