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
    ComplexChannelizer,
    DeEmphasis,
    FmDiscriminator,
    RealDecimator,
)
from app.dsp.fft import Spectrum
from app.hub import FrameTag
from app.modes.base import Mode

AUDIO_RATE = 48_000
IF_DECIM = 10          # 2.4 MS/s -> 240 kHz IF
WBFM_DEVIATION = 75_000.0


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
        self.demod = "fm"            # 'fm' | 'am'
        self.tuned_freq = self.default_center_freq
        self.bandwidth = 200_000.0   # WBFM broadcast
        self.volume = 0.7
        self.squelch_db = -80.0      # channel-power gate; -80 = effectively open
        self._need_rebuild = True
        self._user_tuned = False     # has the client picked a channel yet?
        self._last_level_emit = 0.0
        # DSP chain (built in on_start / on rebuild)
        self._chan: ComplexChannelizer | None = None
        self._disc = FmDiscriminator()
        self._audio_decim: RealDecimator | None = None
        self._deemph: DeEmphasis | None = None
        # Waterfall
        self._spectrum = Spectrum(self.FFT_SIZE)
        self._min_interval = 1.0 / self.MAX_ROWS_PER_SEC
        self._last_emit = 0.0

    # --- configuration from the client (thread-safe attribute writes) -------
    def configure(self, params: dict) -> None:
        if "demod" in params:
            self.demod = str(params["demod"]).lower()
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
        self._announce_radio()

    def _radio_config_msg(self) -> dict:
        return {
            "type": "radio_config",
            "demod": self.demod,
            "tuned_freq": self.tuned_freq,
            "bandwidth": self.bandwidth,
            "volume": self.volume,
            "squelch": self.squelch_db,
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
        cutoff = max(self.bandwidth / 2.0, 5_000.0)
        self._chan = ComplexChannelizer(sr, IF_DECIM, cutoff)
        if_rate = self._chan.out_rate
        # Audio path: IF -> 48 kHz. For FM keep 15 kHz; AM is narrower.
        audio_decim = int(round(if_rate / AUDIO_RATE))
        audio_cut = 15_000.0 if self.demod == "fm" else min(self.bandwidth / 2.0, 5_000.0)
        self._audio_decim = RealDecimator(if_rate, audio_decim, audio_cut)
        self._deemph = DeEmphasis(self._audio_decim.out_rate, tau_us=50.0)
        self._disc = FmDiscriminator()
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

        assert self._chan is not None and self._audio_decim is not None
        assert self._deemph is not None

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

        if self.demod == "am":
            audio = np.abs(baseband)
            audio = self._audio_decim.process(audio)
            audio = audio - np.mean(audio)                # strip carrier DC
        else:  # FM
            disc = self._disc.process(baseband)           # radians/sample
            disc *= if_rate / (2.0 * np.pi * WBFM_DEVIATION)
            audio = self._audio_decim.process(disc)
            audio = self._deemph.process(audio)

        # 4) scale to int16 PCM and emit (silence when squelched, to keep the
        #    audio stream continuous and the player's buffer fed)
        if squelched:
            audio = np.zeros_like(audio)
        pcm = np.clip(audio * self.volume, -1.0, 1.0)
        self.manager.emit_binary(FrameTag.AUDIO, (pcm * 32767).astype("<i2").tobytes())
