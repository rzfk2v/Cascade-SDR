"""Wideband scan mode — a swept spectrum panorama.

A single RTL-SDR sees only ~2.4 MHz at once. To survey a much wider range (e.g.
the whole 88-108 MHz FM band) we retune the dongle across the range, FFT each
~2.4 MHz slice, and stitch the slices into one wide spectrum row. The browser
renders it with the *same* waterfall/scope/axis as a normal capture — we just
tell it the "band" is the whole scanned span.

This mode drives the tuner itself (``controls_tuning = True``), so the
DeviceManager hands it the device and calls :meth:`sweep` instead of running the
usual reader/processor split.

Slices overlap and the DC spike at each slice centre is notched out (replaced
with the local median) so neighbouring slices fill it back in via a max-combine.
"""
from __future__ import annotations

import time

import numpy as np

from app.dsp.fft import Spectrum
from app.hub import FrameTag
from app.modes.base import Mode


class ScanMode(Mode):
    name = "scan"
    owns_device = True
    controls_tuning = True
    default_center_freq = 98_000_000.0
    default_sample_rate = 2_400_000.0

    FFT_SIZE = 1024        # per-slice FFT
    OUT_BINS = 1200        # stitched output resolution across the whole span
    USABLE = 0.8           # fraction of each slice kept (drop band-edge rolloff)
    STEP_BLOCK = 16_384    # samples FFT'd per slice (multiple windows -> averaged)
    SETTLE = 4_096         # samples discarded after retune (PLL relock)

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self.start_freq = 88_000_000.0
        self.stop_freq = 108_000_000.0
        self._spectrum = Spectrum(self.FFT_SIZE)

    # --- config -------------------------------------------------------------
    def configure(self, params: dict) -> None:
        if params.get("start_freq"):
            self.start_freq = self.manager.clamp_freq(params["start_freq"])
        if params.get("stop_freq"):
            self.stop_freq = self.manager.clamp_freq(params["stop_freq"])
        # keep at least a sensible minimum span
        if self.stop_freq - self.start_freq < 1_000_000:
            self.stop_freq = self.manager.clamp_freq(self.start_freq + 1_000_000)
        self.manager.emit_json(self._scan_config_msg())

    def _scan_config_msg(self) -> dict:
        return {
            "type": "spectrum_config",
            "fft_size": self.OUT_BINS,
            "center_freq": (self.start_freq + self.stop_freq) / 2.0,
            "sample_rate": (self.stop_freq - self.start_freq),
            "scan": True,
        }

    def snapshot(self) -> list[dict]:
        return [self._scan_config_msg()]

    # --- sweep loop (runs in the device worker thread) ----------------------
    def sweep(self, sdr, stop_event) -> None:
        sr = float(self.manager.sample_rate)
        sdr.sample_rate = sr
        usable_bw = sr * self.USABLE
        self.manager.emit_json(self._scan_config_msg())
        applied_ppm = int(self.manager.freq_correction)  # already set on device open

        while not stop_event.is_set():
            # apply current gain/ppm at the top of each sweep (cheap, picks up
            # changes made while scanning). Only write ppm on change (a no-op
            # set errors in librtlsdr and can wedge the next USB call).
            try:
                sdr.gain = self.manager.gain
            except Exception:
                pass
            if int(self.manager.freq_correction) != applied_ppm:
                try:
                    sdr.freq_correction = int(self.manager.freq_correction)
                    applied_ppm = int(self.manager.freq_correction)
                except Exception:
                    pass
            start, stop = self.start_freq, self.stop_freq
            span = stop - start
            if span <= 0:
                time.sleep(0.1)
                continue
            # overlap slices ~10% so each slice's notched DC gap is covered
            n_steps = max(1, int(np.ceil(span / (usable_bw * 0.9))))
            out = np.full(self.OUT_BINS, -130.0, dtype=np.float32)

            for i in range(n_steps):
                if stop_event.is_set():
                    break
                center = start + (i + 0.5) * span / n_steps
                sdr.center_freq = self.manager.hw_freq(center)
                try:
                    sdr.reset_buffer()
                except Exception:
                    pass
                sdr.read_samples(self.SETTLE)          # flush PLL transient
                x = sdr.read_samples(self.STEP_BLOCK)
                row = self._spectrum.row(x)            # dB, fftshifted, len FFT_SIZE

                freqs = center + (np.arange(self.FFT_SIZE) / self.FFT_SIZE - 0.5) * sr
                keep = np.abs(freqs - center) <= usable_bw / 2.0
                # notch the DC spike: replace centre bins with the slice median
                dc = np.abs(freqs - center) < sr * 0.004
                if dc.any():
                    row = row.copy()
                    row[dc] = float(np.median(row[keep]))

                fr = freqs[keep]
                vv = row[keep]
                gi = ((fr - start) / span * self.OUT_BINS).astype(int)
                ok = (gi >= 0) & (gi < self.OUT_BINS)
                np.maximum.at(out, gi[ok], vv[ok])

            self.manager.emit_binary(FrameTag.FFT, out.tobytes())
