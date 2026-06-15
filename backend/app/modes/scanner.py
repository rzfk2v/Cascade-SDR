"""Scanner mode — cycle a list of channels and stop on activity.

A classic channel scanner: step through a preset's channels, and the moment one
breaks **squelch** (rises above the local noise floor), park there and play its
audio until it goes quiet for a short *hold*, then resume.

Because a single RTL-SDR sees only ~2.4 MHz at once, the preset's channels are
grouped into 2.4 MHz capture **blocks**; one FFT per block watches every channel
in it at the same time (so e.g. the marine simplex channels, which all sit within
~1.4 MHz, are monitored simultaneously — no slow per-channel hopping).

Drives the tuner itself (``controls_tuning = True``): the DeviceManager hands it
the device and calls :meth:`sweep`.
"""
from __future__ import annotations

import math
import queue as _queue
import threading
import time

import numpy as np

from app.dsp.blocks import ComplexChannelizer, FmDiscriminator, RealDecimator
from app.dsp.fft import Spectrum
from app.hub import FrameTag
from app.modes.base import Mode
from app.modes.radio import AUDIO_RATE, DEMODS, IF_DECIM
from app.modes.scanner_presets import PRESET_LABELS, PRESETS

FFT_SIZE = 1024
USABLE = 0.9            # fraction of a 2.4 MHz block we trust (drop edge rolloff)
DETECT_BLOCK = 16_384   # samples FFT'd to look for activity
PARK_BLOCK = 51_200     # samples per parked read (multiple of 50 -> clean decim)
SETTLE = 4_096          # samples discarded after a retune (PLL relock)


class ScannerMode(Mode):
    name = "scanner"
    owns_device = True
    controls_tuning = True
    default_center_freq = 156_200_000.0   # marine VHF
    default_sample_rate = 2_400_000.0

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self.preset = "marine"
        self.squelch_margin = 8.0   # dB above the block's noise floor = "active"
        self.hold = 3.0             # s of silence before resuming the scan
        self.volume = 0.7
        self._spectrum = Spectrum(FFT_SIZE)
        self._dirty = True          # rebuild channel/block layout in the worker
        self._channels: list[dict] = []
        self._blocks: list[dict] = []

    # --- config -------------------------------------------------------------
    def configure(self, params: dict) -> None:
        if params.get("preset") in PRESETS:
            self.preset = params["preset"]
            self._dirty = True
        if params.get("squelch") is not None:
            self.squelch_margin = float(np.clip(params["squelch"], 0.0, 40.0))
        if params.get("hold") is not None:
            self.hold = float(np.clip(params["hold"], 0.5, 30.0))
        if params.get("volume") is not None:
            self.volume = float(np.clip(params["volume"], 0.0, 2.0))
        self.manager.emit_json(self._config_msg())

    def _rebuild(self, sr: float) -> None:
        # flat channel list (preset order) + 2.4 MHz capture blocks
        self._channels = [dict(c, _level=0.0, _active=False) for c in PRESETS[self.preset]]
        usable = sr * USABLE
        blocks: list[list[dict]] = []
        for c in sorted(self._channels, key=lambda x: x["freq"]):
            if blocks and c["freq"] - blocks[-1][0]["freq"] <= usable:
                blocks[-1].append(c)
            else:
                blocks.append([c])
        self._blocks = []
        for bi, chans in enumerate(blocks):
            center = (chans[0]["freq"] + chans[-1]["freq"]) / 2.0
            for c in chans:
                c["block"] = bi
            self._blocks.append({"center": center, "channels": chans})
        self._dirty = False

    def _config_msg(self) -> dict:
        return {
            "type": "scanner_config",
            "presets": [{"id": k, "label": PRESET_LABELS[k]} for k in PRESETS],
            "preset": self.preset,
            "squelch": self.squelch_margin,
            "hold": self.hold,
            "channels": [
                {"label": c["label"], "mhz": round(c["freq"] / 1e6, 4),
                 "demod": c["demod"]}
                for c in PRESETS[self.preset]
            ],
        }

    def _state_msg(self, parked_idx: int) -> dict:
        return {
            "type": "scanner_state",
            "parked": parked_idx,                       # index into channels, or -1
            "channels": [
                {"active": bool(c["_active"]), "level": round(c["_level"], 1)}
                for c in self._channels
            ],
        }

    def snapshot(self) -> list[dict]:
        return [self._config_msg()]

    # --- audio --------------------------------------------------------------
    def _emit_audio(self, audio: np.ndarray) -> None:
        l = np.clip(audio * self.volume, -1.0, 1.0)
        inter = np.empty(l.size * 2, dtype="<i2")
        inter[0::2] = (l * 32767).astype("<i2")
        inter[1::2] = inter[0::2]                        # mono -> L = R
        self.manager.emit_binary(FrameTag.AUDIO, inter.tobytes())

    def _channel_level(self, row: np.ndarray, freqs: np.ndarray,
                       ch: dict, floor: float) -> float:
        band = np.abs(freqs - ch["freq"]) <= ch["bw"] / 2.0
        peak = float(row[band].max()) if band.any() else floor
        return peak - floor

    # --- scan loop (runs in the device worker thread) -----------------------
    def sweep(self, sdr, stop_event) -> None:
        sr = float(self.manager.sample_rate)
        sdr.sample_rate = sr
        self._rebuild(sr)
        self.manager.emit_json(self._config_msg())
        self._applied_ppm = int(self.manager.freq_correction)
        block_i = 0
        last_state = 0.0

        while not stop_event.is_set():
            if self._dirty:                              # preset changed
                self._rebuild(sr)
                block_i = 0
            self._apply_radio(sdr)
            if not self._blocks:
                time.sleep(0.1)
                continue

            blk = self._blocks[block_i]
            best = self._detect(sdr, blk, sr)            # inline read is fine (no audio)
            now = time.monotonic()
            if best is not None:
                self._emit_state(self._channels.index(best))
                self._park(sdr, stop_event, best, sr)    # smooth audio via a reader thread
                last_state = 0.0                          # force a fresh state on resume
            elif now - last_state >= 0.25:
                last_state = now
                self._emit_state(-1)
            block_i = (block_i + 1) % len(self._blocks)

    def _apply_radio(self, sdr) -> None:
        try:
            sdr.gain = self.manager.gain
        except Exception:
            pass
        if int(self.manager.freq_correction) != self._applied_ppm:
            try:
                sdr.freq_correction = int(self.manager.freq_correction)
                self._applied_ppm = int(self.manager.freq_correction)
            except Exception:
                pass

    def _detect(self, sdr, blk: dict, sr: float) -> dict | None:
        """Tune a block, FFT it, score every channel; return the strongest active."""
        center = blk["center"]
        sdr.center_freq = center
        self._reset(sdr)
        x = sdr.read_samples(DETECT_BLOCK)
        freqs = center + (np.arange(FFT_SIZE) / FFT_SIZE - 0.5) * sr
        row = self._spectrum.row(x)
        floor = float(np.median(row))
        best, best_level = None, self.squelch_margin
        for c in blk["channels"]:
            lvl = self._channel_level(row, freqs, c, floor)
            c["_level"], c["_active"] = lvl, lvl >= self.squelch_margin
            if c["_active"] and lvl >= best_level:
                best, best_level = c, lvl
        return best

    def _park(self, sdr, stop_event, ch: dict, sr: float) -> None:
        """Hold on a channel and play it until silent for `hold`.

        A dedicated reader thread drains the dongle continuously (so audio doesn't
        glitch while we demodulate), while this loop consumes blocks for audio +
        a periodic squelch check.
        """
        center = self._blocks[ch["block"]]["center"]
        sdr.center_freq = center
        self._reset(sdr)
        cfg = DEMODS.get(ch["demod"], DEMODS["nfm"])
        chan = ComplexChannelizer(sr, IF_DECIM, max(ch["bw"] / 2.0, 6_000.0))
        if_rate = chan.out_rate
        chan.set_shift(ch["freq"] - center)
        disc = FmDiscriminator()
        adec = RealDecimator(if_rate, round(if_rate / AUDIO_RATE), cfg["audio"])
        dev = float(cfg.get("dev", 5_000))
        idx = self._channels.index(ch)
        freqs = center + (np.arange(FFT_SIZE) / FFT_SIZE - 0.5) * sr

        q: _queue.Queue = _queue.Queue(maxsize=8)
        stop_reader = threading.Event()

        def reader() -> None:
            while not stop_reader.is_set() and not stop_event.is_set():
                try:
                    b = sdr.read_samples(PARK_BLOCK)
                except Exception:
                    break
                try:
                    q.put_nowait(b)
                except _queue.Full:                      # consumer fell behind: drop oldest
                    try:
                        q.get_nowait()
                        q.put_nowait(b)
                    except (_queue.Empty, _queue.Full):
                        pass

        rt = threading.Thread(target=reader, daemon=True)
        rt.start()
        last_active = time.monotonic()
        last_state = 0.0
        try:
            while not stop_event.is_set() and not self._dirty:
                try:
                    x = q.get(timeout=0.5)
                except _queue.Empty:
                    continue
                bb = chan.process(x)
                if ch["demod"] == "am":
                    env = adec.process(np.abs(bb))
                    carrier = float(np.mean(env))
                    audio = (env - carrier) / carrier if carrier > 1e-6 else env * 0.0
                else:
                    audio = adec.process(disc.process(bb) * (if_rate / (2.0 * math.pi * dev)))
                self._emit_audio(audio)

                row = self._spectrum.row(x)
                lvl = self._channel_level(row, freqs, ch, float(np.median(row)))
                ch["_level"], ch["_active"] = lvl, lvl >= self.squelch_margin
                now = time.monotonic()
                if ch["_active"]:
                    last_active = now
                elif now - last_active > self.hold:
                    break
                if now - last_state >= 0.25:
                    last_state = now
                    self._emit_state(idx)
        finally:
            stop_reader.set()
            rt.join(timeout=1.5)

    def _emit_state(self, idx: int) -> None:
        self.manager.emit_json(self._state_msg(idx))

    @staticmethod
    def _reset(sdr) -> None:
        try:
            sdr.reset_buffer()
        except Exception:
            pass
        sdr.read_samples(SETTLE)        # flush the PLL retune transient
