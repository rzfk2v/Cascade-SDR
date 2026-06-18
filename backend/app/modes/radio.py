"""Radio mode — demodulate one channel within the captured band, with audio.

The dongle stays at the hardware ``center_freq`` and captures the whole
``sample_rate`` band. A *channel* anywhere inside that band is selected by the
client (click-to-tune) and demodulated digitally — no hardware retune. The
waterfall keeps streaming the full band the whole time, so you can see and pick
signals while listening.

Per IQ block:
  1. emit a waterfall FFT row of the whole band (rate-capped),
  2. mix the tuned channel to baseband, low-pass + decimate to an IF rate,
  3. FM (or AM) demodulate,
  4. decimate to the audio rate, de-emphasise (FM), and emit int16 PCM.

Audio sample rate is fixed at 48 kHz; ``block_size`` is chosen so every
decimation ratio is integer and the decimation grid stays aligned across blocks.
"""
from __future__ import annotations

import queue
import threading
import time

import numpy as np

from app.dsp.blocks import (
    BlockAgc,
    ComplexChannelizer,
    DeEmphasis,
    FmDiscriminator,
    RealDecimator,
    SsbDemod,
    StereoDecoder,
)
from app.dsp.apt import AptDecoder
from app.dsp.cw import CwDecoder
from app.dsp.fft import Spectrum
from app.dsp.rds import RdsDemod
from app.hub import FrameTag
from app.modes.base import Mode

CW_ENV_RATE = 1000  # envelope rate for the Morse decoder

AUDIO_RATE = 48_000
IF_DECIM = 10          # 2.4 MS/s -> 240 kHz IF

# Per-demodulator defaults: channel bandwidth (Hz), audio low-pass (Hz),
# FM deviation (Hz, FM only). Picked when the user switches demod.
DEMODS = {
    "wfm": {"bw": 200_000, "audio": 15_000, "dev": 75_000, "deemph": True},
    "nfm": {"bw": 12_500, "audio": 4_000, "dev": 5_000, "deemph": False},
    "am":  {"bw": 10_000, "audio": 5_000},
    "usb": {"bw": 2_800, "audio": 3_000},
    "lsb": {"bw": 2_800, "audio": 3_000},
    "cw": {"bw": 800, "audio": 1_200},
}


class RadioMode(Mode):
    name = "radio"
    owns_device = True
    resets_tuning = False     # keep the current band when entering the spectrum view
    default_center_freq = 100_000_000.0
    default_sample_rate = 2_400_000.0
    # 51200 is a multiple of 256 (librtlsdr), 10 (IF decim) and 50 (total decim).
    # ~21 ms RF/block: large enough to sustain real-time USB throughput, small
    # enough for low latency; the player's jitter buffer smooths delivery.
    block_size = 51_200

    FFT_SIZE = 2048
    MAX_ROWS_PER_SEC = 25

    def __init__(self, manager) -> None:
        super().__init__(manager)
        # Channel parameters (set from the client; read by the worker thread).
        self.demod = "wfm"           # wfm | nfm | am | usb | lsb
        self.tuned_freq = self.default_center_freq
        self.bandwidth = float(DEMODS["wfm"]["bw"])
        self.volume = 0.7
        self.squelch_db = -80.0      # channel-power gate; -80 = effectively open
        self.deemph_us = 50.0        # FM de-emphasis: 50 µs (EU) / 75 µs (Americas)
        self.rds_enabled = True      # decode RDS (station name/radiotext) on WFM
        self._rds: RdsDemod | None = None
        self._rds_dirty = False      # tuned to a new station -> reset RDS
        self._rds_queue: queue.Queue | None = None
        self._rds_thread: threading.Thread | None = None
        self.stereo_enabled = True   # decode FM stereo (L−R from 38 kHz) on WFM
        self._stereo: StereoDecoder | None = None
        self._deemph_l: DeEmphasis | None = None
        self._deemph_r: DeEmphasis | None = None
        self.apt_enabled = False     # decode NOAA APT image (137 MHz weather sats)
        self._apt: AptDecoder | None = None
        self._apt_dirty = False
        self._need_rebuild = True
        self._user_tuned = False     # has the client picked a channel yet?
        self._last_band = (0.0, 0.0)  # detect Center/rate changes to follow the band
        self._last_level_emit = 0.0
        # DSP chain (built in on_start / on rebuild)
        self._chan: ComplexChannelizer | None = None
        self._disc = FmDiscriminator()
        self._audio_decim: RealDecimator | None = None
        self._deemph: DeEmphasis | None = None
        self._cplx_decim: ComplexChannelizer | None = None  # SSB audio-rate stage
        self._ssb: SsbDemod | None = None
        self._agc: BlockAgc | None = None
        self._env_decim: RealDecimator | None = None  # CW envelope -> ~1 kHz
        self._cw: CwDecoder | None = None
        self._fm_dev = 75_000.0
        # Waterfall
        self._spectrum = Spectrum(self.FFT_SIZE)
        self._min_interval = 1.0 / self.MAX_ROWS_PER_SEC
        self._last_emit = 0.0

    # --- configuration from the client (thread-safe attribute writes) -------
    def configure(self, params: dict) -> None:
        if "demod" in params:
            d = str(params["demod"]).lower()
            if d in DEMODS and d != self.demod:
                self.demod = d
                self.bandwidth = float(DEMODS[d]["bw"])  # sensible default per demod
                self._need_rebuild = True
        if "tuned_freq" in params and params["tuned_freq"] is not None:
            self.tuned_freq = float(params["tuned_freq"])
            self._user_tuned = True
            self._rds_dirty = True       # different station -> clear RDS
            self._apt_dirty = True       # different pass -> restart the image
        if "bandwidth" in params and params["bandwidth"] is not None:
            self.bandwidth = float(params["bandwidth"])
            self._need_rebuild = True
        if "volume" in params and params["volume"] is not None:
            self.volume = float(np.clip(params["volume"], 0.0, 2.0))
        if "squelch" in params and params["squelch"] is not None:
            self.squelch_db = float(params["squelch"])
        if "deemph" in params and params["deemph"] is not None:
            # accept 50 / 75 (µs); 0/None means leave unchanged
            tau = float(params["deemph"])
            if tau in (50.0, 75.0) and tau != self.deemph_us:
                self.deemph_us = tau
                self._need_rebuild = True
        if "rds" in params and params["rds"] is not None:
            self.rds_enabled = bool(params["rds"])
            self._need_rebuild = True
        if "stereo" in params and params["stereo"] is not None:
            self.stereo_enabled = bool(params["stereo"])
            self._need_rebuild = True
        if "apt" in params and params["apt"] is not None:
            self.apt_enabled = bool(params["apt"])
            self._need_rebuild = True
        self._announce_radio()

    def _radio_config_msg(self) -> dict:
        return {
            "type": "radio_config",
            "demod": self.demod,
            "tuned_freq": self.tuned_freq,
            "bandwidth": self.bandwidth,
            "volume": self.volume,
            "squelch": self.squelch_db,
            "deemph": self.deemph_us,
            "rds": self.rds_enabled,
            "stereo": self.stereo_enabled,
            "apt": self.apt_enabled,
            "audio_rate": AUDIO_RATE,
        }

    def _spectrum_config_msg(self) -> dict:
        return {
            "type": "spectrum_config",
            "fft_size": self.FFT_SIZE,
            "center_freq": self.manager.center_freq,
            "sample_rate": self.manager.sample_rate,
        }

    def snapshot(self) -> list[dict]:
        """Config a freshly-connected client needs to sync its UI."""
        return [self._spectrum_config_msg(), self._radio_config_msg()]

    def _announce_radio(self) -> None:
        self.manager.emit_json(self._radio_config_msg())

    def _emit_rds(self, snap: dict) -> None:
        self.manager.emit_json(snap)

    def _emit_apt(self, row) -> None:
        self.manager.emit_binary(FrameTag.APT, row.tobytes())

    # --- lifecycle (worker thread) ------------------------------------------
    def on_start(self) -> None:
        # Start at band centre unless the client already picked a channel
        # (e.g. clicked the waterfall, which sends a config right after set_mode).
        if not self._user_tuned:
            self.tuned_freq = self.manager.center_freq
        self._build_chain()
        self.manager.emit_json(self._spectrum_config_msg())
        self._announce_radio()

    def _rds_worker(self) -> None:
        q = self._rds_queue
        while True:
            block = q.get()
            if block is None:
                break
            rds = self._rds
            if rds is not None:
                rds.process(block)

    def _stop_rds_worker(self) -> None:
        q, t = self._rds_queue, self._rds_thread
        self._rds_queue = None
        self._rds_thread = None
        if q is not None:
            # drain then send sentinel so there's always room
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
            q.put(None)
        if t is not None and t.is_alive():
            t.join(timeout=1.0)

    def _build_chain(self) -> None:
        sr = self.manager.sample_rate
        cfg = DEMODS.get(self.demod, DEMODS["wfm"])
        # Stage 1: select the channel and bring it to the IF rate (240 kHz).
        self._chan = ComplexChannelizer(sr, IF_DECIM, max(self.bandwidth / 2.0, 1_500.0))
        if_rate = self._chan.out_rate
        audio_decim = int(round(if_rate / AUDIO_RATE))

        self._deemph = None
        self._cplx_decim = None
        self._ssb = None
        self._agc = None
        self._env_decim = None
        self._cw = None
        self._stop_rds_worker()
        self._rds = None
        self._stereo = None
        self._apt = None
        self._deemph_l = self._deemph_r = None
        if self.demod in ("wfm", "nfm"):
            self._disc = FmDiscriminator()
            self._fm_dev = float(cfg["dev"])
            self._audio_decim = RealDecimator(if_rate, audio_decim, cfg["audio"])
            if cfg.get("deemph"):
                self._deemph = DeEmphasis(self._audio_decim.out_rate, tau_us=self.deemph_us)
            if self.demod == "wfm" and self.stereo_enabled:
                self._stereo = StereoDecoder(if_rate, audio_decim, cfg["audio"])
                if cfg.get("deemph"):
                    rate = self._audio_decim.out_rate
                    self._deemph_l = DeEmphasis(rate, tau_us=self.deemph_us)
                    self._deemph_r = DeEmphasis(rate, tau_us=self.deemph_us)
            if self.demod == "wfm" and self.rds_enabled:
                # RDS lives at 57 kHz in the MPX (the discriminator output), so it
                # needs the full IF-rate signal, before audio decimation.
                # Run it on a background thread so the expensive resample_poly calls
                # don't stall audio production on the IQ worker thread.
                self._rds = RdsDemod(if_rate, self._emit_rds)
                self._rds_dirty = False
                self._rds_queue = queue.Queue(maxsize=4)
                self._rds_thread = threading.Thread(
                    target=self._rds_worker, daemon=True, name="rds-decoder")
                self._rds_thread.start()
            if self.demod == "wfm" and self.apt_enabled:
                self._apt = AptDecoder(self._audio_decim.out_rate, self._emit_apt)
                self._apt_dirty = False
        elif self.demod == "am":
            self._audio_decim = RealDecimator(if_rate, audio_decim, cfg["audio"])
        else:  # usb / lsb / cw  (SSB-style audio)
            # complex decimate IF -> audio rate, then one-sided sideband filter
            self._cplx_decim = ComplexChannelizer(if_rate, audio_decim, cfg["audio"] + 500)
            self._ssb = SsbDemod(self._cplx_decim.out_rate, lsb=(self.demod == "lsb"))
            self._agc = BlockAgc()
            if self.demod == "cw":
                env_decim = max(1, int(round(if_rate / CW_ENV_RATE)))
                self._env_decim = RealDecimator(if_rate, env_decim, 200)
                self._cw = CwDecoder(if_rate / env_decim)
        self._need_rebuild = False

    def process(self, samples: np.ndarray) -> None:
        # 1) waterfall of the whole band (rate-capped)
        now = time.monotonic()
        if now - self._last_emit >= self._min_interval:
            self._last_emit = now
            row = self._spectrum.row(samples)
            self.manager.emit_binary(FrameTag.FFT, row.tobytes())

        # If the captured band moved (Center retuned / zoomed) and the tuned channel
        # fell outside it, follow the band — otherwise the "tuned" readout (and any
        # bookmark made from it) goes stale, still pointing at the old channel. Done
        # before the spectrum-only return so it also tracks while just browsing.
        band = (self.manager.center_freq, self.manager.sample_rate)
        if band != self._last_band:
            self._last_band = band
            half = self.manager.sample_rate / 2.0
            if not (self.manager.center_freq - half < self.tuned_freq
                    < self.manager.center_freq + half):
                self.tuned_freq = self.manager.center_freq
                self._user_tuned = False
                self._announce_radio()

        # Spectrum-only until the user picks a channel (click-to-tune): show the
        # waterfall but emit no audio. This is what makes one combined view serve
        # as both "browse the band" and "listen". (APT decodes the centre channel
        # without a click, so it's exempt.)
        if not self._user_tuned and not self.apt_enabled:
            return

        # 2) (re)build the channel chain if bandwidth/demod changed
        if self._need_rebuild or self._chan is None:
            self._build_chain()

        assert self._chan is not None

        # 3) select + demodulate the channel
        self._chan.set_shift(self.tuned_freq - self.manager.center_freq)
        baseband = self._chan.process(samples)            # complex, IF rate
        if_rate = self._chan.out_rate

        # channel power (dBFS) drives the squelch + level meter
        power = float(np.mean(np.abs(baseband) ** 2)) if baseband.size else 0.0
        level_db = 10.0 * np.log10(power + 1e-12)
        squelched = level_db < self.squelch_db

        audio_lr: tuple[np.ndarray, np.ndarray] | None = None  # set for true stereo
        if self.demod in ("wfm", "nfm"):
            disc = self._disc.process(baseband)           # radians/sample = MPX
            disc *= if_rate / (2.0 * np.pi * self._fm_dev)
            # RDS: decode the 57 kHz subcarrier from the full MPX (pre-decimation)
            if self.demod == "wfm" and self.rds_enabled:
                if self._rds is None or self._rds_dirty:
                    self._stop_rds_worker()
                    self._rds = RdsDemod(if_rate, self._emit_rds)
                    self._rds_dirty = False
                    self.manager.emit_json({"type": "rds", "pi": None, "pty": None,
                                            "ps": "", "rt": ""})
                    self._rds_queue = queue.Queue(maxsize=4)
                    self._rds_thread = threading.Thread(
                        target=self._rds_worker, daemon=True, name="rds-decoder")
                    self._rds_thread.start()
                if self._rds_queue is not None:
                    try:
                        self._rds_queue.put_nowait(disc)
                    except queue.Full:
                        pass  # RDS thread busy; drop block (display data, not audio)
            mono = self._audio_decim.process(disc)
            # NOAA APT: decode the 2400 Hz image subcarrier from the FM audio
            # (before de-emphasis, which would tilt the subcarrier amplitude)
            if self.demod == "wfm" and self.apt_enabled:
                if self._apt is None or self._apt_dirty:
                    self._apt = AptDecoder(self._audio_decim.out_rate, self._emit_apt)
                    self._apt_dirty = False
                self._apt.process(mono)
            # Stereo: recover L−R from the 38 kHz subcarrier (only when a pilot is
            # present), otherwise fall back to mono (L = R).
            if (self.demod == "wfm" and self.stereo_enabled
                    and self._stereo is not None):
                diff, frac = self._stereo.process(disc)
                if frac >= 0.03:
                    left, right = mono + diff, mono - diff
                    if self._deemph_l is not None:
                        left = self._deemph_l.process(left)
                        right = self._deemph_r.process(right)
                    audio_lr = (left, right)
            if audio_lr is None:
                audio = self._deemph.process(mono) if self._deemph is not None else mono
        elif self.demod == "am":
            env = self._audio_decim.process(np.abs(baseband))
            carrier = float(np.mean(env))                  # carrier-normalised:
            audio = (env - carrier) / carrier if carrier > 1e-6 else env * 0.0
        else:  # usb / lsb / cw
            assert self._cplx_decim is not None and self._ssb is not None
            audio = self._ssb.process(self._cplx_decim.process(baseband))
            if self._agc is not None:
                audio = self._agc.process(audio)
            if self.demod == "cw" and self._cw is not None and self._env_decim is not None:
                text = self._cw.process(self._env_decim.process(np.abs(baseband)))
                if text:
                    self.manager.emit_json({"type": "cw_text", "text": text})

        if now - self._last_level_emit >= 0.1:  # ~10 Hz meter updates
            self._last_level_emit = now
            self.manager.emit_json({
                "type": "radio_level", "db": round(level_db, 1),
                "open": not squelched, "stereo": audio_lr is not None,
            })

        # 4) scale to int16 and emit as interleaved stereo (mono -> L = R), so the
        #    player has one consistent format. Silence when squelched.
        left, right = audio_lr if audio_lr is not None else (audio, audio)
        if squelched:
            left = np.zeros_like(left)
            right = np.zeros_like(right)
        l = np.clip(left * self.volume, -1.0, 1.0)
        r = np.clip(right * self.volume, -1.0, 1.0)
        inter = np.empty(l.size * 2, dtype="<i2")
        inter[0::2] = (l * 32767).astype("<i2")
        inter[1::2] = (r * 32767).astype("<i2")
        self.manager.emit_binary(FrameTag.AUDIO, inter.tobytes())
