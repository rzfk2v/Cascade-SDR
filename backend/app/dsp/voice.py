"""Voice squelch — open the audio for speech, not for everything else that
breaks squelch.

A level squelch opens on anything above its threshold: a dead carrier, a pager
burst, a trunking control channel, a two-tone page, a birdie. On a busy band that
means the speaker blares data for as long as the transmission lasts, and the
scanner parks on channels that will never carry a word. This detector scores the
demodulated audio, and the radio gates on it alongside the level (and CTCSS/DCS)
squelch.

The evidence for speech is **syllabic modulation**: speech energy rises and falls
at the syllable rate, roughly 2-10 Hz, and drops into the gaps between words. A
carrier, a steady tone and a continuous data stream are flat by comparison, and
squelch hiss spreads its envelope evenly across every rate instead of favouring
that band. Measured over a sliding ~0.6 s window of 10 ms frames, that puts
speech around 0.6 and everything else under 0.2.

That share is scale-invariant, though, so a noise envelope that wobbles by a
third of a decibel still has a *shape* — which is why hiss leaked through on its
own. The **depth** of the swing settles it: speech moves its envelope by 3 dB
even at 0 dB SNR and far more in the clear, while hiss and a dead carrier manage
about 1 dB and data manages none.

Two further cues can only *veto*, never vouch — white noise is untonal and
wideband, and neither of those makes it a voice:

* **Voice-band share** — speech puts nearly all of its energy in 300-3000 Hz,
  while FM discriminator hiss is weighted towards the top of the audio band. It
  only means something when the demod's audio passband reaches past the voice
  band at all: SSB stops at 3 kHz, so the veto is disabled there (see
  ``BAND_CUE_MIN_HZ``).
* **Tonality** — one dominant line in the averaged spectrum is a test tone, a
  siren, a heterodyne or an FSK mark/space, not a voice, which spreads across
  many bins. This is what rejects a warbling alarm, whose envelope alone looks
  syllabic enough to pass.

Score is evidence times both vetoes, compared against a threshold set by the
sensitivity. Those numbers came from measuring the cues on synthetic speech,
hiss, carriers, tones, AFSK and two-tone pages — starting points, not laws of
nature, which is why the sensitivity is exposed to the user rather than baked in.
Music is genuinely tonal and will be muted; this is a voice squelch, and on the
NFM/AM/SSB channels it applies to, music is not what you are listening for.

Deciding takes one window (~0.6 s), and a gate that simply waited for it would
clip the first word of every transmission. ``reset()`` instead puts the detector
back into *probation*: it reports speech until it has heard enough to say
otherwise, so audio flows from the moment the carrier appears, and only mutes if
what followed turns out not to be voice.
"""
from __future__ import annotations

import numpy as np

FRAME_MS = 10.0                  # audio is scored in 10 ms frames...
WIN_FRAMES = 64                  # ...over a sliding 0.64 s window
HOP_FRAMES = 10                  # ...re-decided every 0.1 s
ENV_RATE = 1_000.0 / FRAME_MS    # one envelope sample per frame -> 100 Hz

SPEC_N = 512                     # FFT size for the per-frame spectrum
SPEC_SMOOTH = 0.85               # EMA across frames -> ~0.6 s of spectral averaging

SYLLABIC_HZ = (2.0, 10.0)        # the syllable rate of speech
VOICE_HZ = (300.0, 3_000.0)      # where speech energy sits
BAND_CUE_MIN_HZ = 3_500.0        # audio passband needed before the band veto means anything

# Each cue maps onto 0..1 across this range: at the first number it reads "not
# voice", at the second "voice", interpolating in between. TONAL runs backwards
# on purpose — a high peak-to-mean ratio is a tone, not a talker. Measured
# medians: speech mod 0.58 / depth 71 dB / band 0.84 / tonal 9.2; hiss 0.11 /
# 0.9 dB / 0.44 / 3.0; steady tone 0.00 / 0.0 dB / 1.00 / 15.9; AFSK 0.01 /
# 0.02 dB / 1.00 / 4.1. Speech buried at 0 dB SNR still swings 4 dB.
MOD_RANGE = (0.15, 0.45)         # 2-10 Hz share of the envelope's AC energy
DEPTH_RANGE = (1.3, 3.0)         # std of the envelope (dB): hiss ~0.9, data ~0
BAND_RANGE = (0.35, 0.70)        # 300-3000 Hz share of the audio energy
TONAL_RANGE = (18.0, 12.0)       # loudest bin / mean bin, within the voice band

# What the score has to reach to count as speech.
SENSITIVITIES = {"lenient": 0.38, "normal": 0.50, "strict": 0.62}

# Consecutive decisions needed to *open*. Hiss and carrier noise throw the odd
# high-scoring window; speech sustains one. Costs 0.1 s, and never applies to the
# start of a transmission, which probation covers.
OPEN_AGREE = 2

HANG_FRAMES = int(round(1.5 * ENV_RATE))   # hold open 1.5 s past the last "voice"


def _ramp(x: float, lo: float, hi: float) -> float:
    """Map ``x`` onto 0..1 across ``lo``..``hi`` (``hi < lo`` inverts the cue)."""
    if hi == lo:
        return 0.0
    return float(np.clip((x - lo) / (hi - lo), 0.0, 1.0))


class VoiceDetector:
    """Scores demodulated audio as speech or not-speech.

    ``audio_hz`` is the demod's audio passband (``DEMODS[...]["audio"]``), which
    decides whether the voice-band veto carries any information.
    """

    def __init__(self, audio_rate: float, audio_hz: float,
                 sensitivity: str = "normal") -> None:
        self.rate = float(audio_rate)
        self.frame = max(64, int(round(self.rate * FRAME_MS / 1_000.0)))
        self.threshold = SENSITIVITIES.get(sensitivity, SENSITIVITIES["normal"])
        self.score = 0.0
        self.speaking = True
        self._use_band = audio_hz >= BAND_CUE_MIN_HZ

        self._fwin = np.hanning(self.frame)
        self._ewin = np.hanning(WIN_FRAMES)
        hz = np.fft.rfftfreq(SPEC_N, 1.0 / self.rate)
        self._voice_bins = (hz >= VOICE_HZ[0]) & (hz <= VOICE_HZ[1])
        ehz = np.fft.rfftfreq(WIN_FRAMES, 1.0 / ENV_RATE)
        self._syl_bins = (ehz >= SYLLABIC_HZ[0]) & (ehz <= SYLLABIC_HZ[1])
        self.reset()

    def set_sensitivity(self, name: str) -> None:
        self.threshold = SENSITIVITIES.get(name, SENSITIVITIES["normal"])

    def reset(self) -> None:
        """Back to probation — report speech until there's evidence otherwise."""
        self._tail = np.zeros(0)             # audio left over from the last block
        self._env = np.zeros(WIN_FRAMES)     # frame levels (dB), oldest first
        self._filled = 0                     # frames seen since the reset
        self._since_hop = 0
        self._agree = 0
        self._spec = np.zeros(SPEC_N // 2 + 1)
        self._hang = 0
        self.score = 0.0
        self.speaking = True

    def process(self, audio: np.ndarray) -> None:
        """Feed a block of demodulated audio (any length)."""
        x = np.concatenate((self._tail, audio)) if self._tail.size else audio
        n = (x.size // self.frame) * self.frame
        self._tail = x[n:].copy()
        for f in x[:n].reshape(-1, self.frame):
            self._push(f)

    # --- one 10 ms frame ---------------------------------------------------
    def _push(self, f: np.ndarray) -> None:
        rms = float(np.sqrt(np.mean(f * f)))
        self._env[:-1] = self._env[1:]
        self._env[-1] = 20.0 * np.log10(rms + 1e-9)
        p = np.abs(np.fft.rfft(f * self._fwin, n=SPEC_N)) ** 2
        self._spec *= SPEC_SMOOTH
        self._spec += (1.0 - SPEC_SMOOTH) * p
        self._filled = min(self._filled + 1, WIN_FRAMES)
        self._since_hop += 1
        if self._filled >= WIN_FRAMES and self._since_hop >= HOP_FRAMES:
            self._since_hop = 0
            self._decide()

    # --- one decision over the window --------------------------------------
    def _decide(self) -> None:
        score = _ramp(self._modulation(), *MOD_RANGE)
        score *= _ramp(float(np.std(self._env)), *DEPTH_RANGE)
        if self._use_band:
            score *= _ramp(self._band_share(), *BAND_RANGE)
        score *= _ramp(self._peak_ratio(), *TONAL_RANGE)
        self.score = score
        if score >= self.threshold:
            self._agree += 1
            if self._agree >= OPEN_AGREE:
                self._hang = HANG_FRAMES
        else:
            self._agree = 0
            self._hang = max(0, self._hang - HOP_FRAMES)
        self.speaking = self._hang > 0

    def _modulation(self) -> float:
        """Share of the level envelope's AC energy sitting at the syllable rate."""
        e = self._env - float(np.mean(self._env))
        spec = np.abs(np.fft.rfft(e * self._ewin)) ** 2
        total = float(np.sum(spec[1:]))     # bin 0 is the mean, already removed
        if total <= 1e-12:
            return 0.0                      # dead flat: a carrier, not a voice
        return float(np.sum(spec[self._syl_bins])) / total

    def _band_share(self) -> float:
        """Share of the audio energy inside the voice band."""
        total = float(np.sum(self._spec[1:]))
        if total <= 1e-20:
            return 0.0
        return float(np.sum(self._spec[self._voice_bins])) / total

    def _peak_ratio(self) -> float:
        """Loudest bin against the mean, within the voice band: a tone spikes."""
        band = self._spec[self._voice_bins]
        mean = float(np.mean(band)) if band.size else 0.0
        if mean <= 1e-20:
            return 0.0
        return float(np.max(band)) / mean
