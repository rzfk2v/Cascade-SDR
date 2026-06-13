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

import time

import numpy as np

from app.dsp.blocks import (
    BlockAgc,
    ComplexChannelizer,
    DeEmphasis,
    FmDiscriminator,
    RealDecimator,
    SsbDemod,
)
from app.dsp.cw import CwDecoder
from app.dsp.fft import Spectrum
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
        self._need_rebuild = True
        self._user_tuned = False     # has the client picked a channel yet?
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

    # --- lifecycle (worker thread) ------------------------------------------
    def on_start(self) -> None:
        # Start at band centre unless the client already picked a channel
        # (e.g. clicked the waterfall, which sends a config right after set_mode).
        if not self._user_tuned:
            self.tuned_freq = self.manager.center_freq
        self._build_chain()
        self.manager.emit_json(self._spectrum_config_msg())
        self._announce_radio()

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
        if self.demod in ("wfm", "nfm"):
            self._disc = FmDiscriminator()
            self._fm_dev = float(cfg["dev"])
            self._audio_decim = RealDecimator(if_rate, audio_decim, cfg["audio"])
            if cfg.get("deemph"):
                self._deemph = DeEmphasis(self._audio_decim.out_rate, tau_us=self.deemph_us)
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
        if now - self._last_level_emit >= 0.1:  # ~10 Hz meter updates
            self._last_level_emit = now
            self.manager.emit_json(
                {"type": "radio_level", "db": round(level_db, 1), "open": not squelched}
            )

        if self.demod in ("wfm", "nfm"):
            disc = self._disc.process(baseband)           # radians/sample
            disc *= if_rate / (2.0 * np.pi * self._fm_dev)
            audio = self._audio_decim.process(disc)
            if self._deemph is not None:
                audio = self._deemph.process(audio)
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

        # 4) scale to int16 PCM and emit (silence when squelched, to keep the
        #    audio stream continuous and the player's buffer fed)
        if squelched:
            audio = np.zeros_like(audio)
        pcm = np.clip(audio * self.volume, -1.0, 1.0)
        self.manager.emit_binary(FrameTag.AUDIO, (pcm * 32767).astype("<i2").tobytes())
