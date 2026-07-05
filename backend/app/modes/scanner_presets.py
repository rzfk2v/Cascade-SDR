"""Channel presets for the Scanner mode.

Each preset is a named list of channels to cycle through. A channel is a fixed
frequency + demod + a short label. The Scanner groups a preset's channels into
2.4 MHz capture blocks, watches every channel in a block at once (one FFT), and
parks on whichever breaks squelch.

Frequencies are in Hz. Marine VHF uses the simplex *voice* channels (ship-to-ship,
plus the Swedish leisure/fishing channels and Ch 16) — the data channels (70 DSC,
87B/88B AIS) are intentionally excluded since there's nothing to listen to.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_NFM = {"demod": "nfm", "bw": 12_500}
_AM = {"demod": "am", "bw": 8_000}

# Bandwidth (Hz) inferred from a channel's demod for user-defined channels.
_DEMOD_BW = {"nfm": 12_500, "am": 8_000}

# Range search (beta): the preset id used while sweeping a frequency range, and
# the cap on synthesized slots (bounds the scanner-state traffic and grid size).
RANGE_PRESET = "__range__"
MAX_RANGE_SLOTS = 800

# User-saved custom presets live here (gitignored, like the AIS/ISM caches).
# ``SCANNER_CACHE_PATH`` overrides the location (handy for tests / other disks).
CUSTOM_PATH = Path(os.environ.get("SCANNER_CACHE_PATH")
                   or Path(__file__).resolve().parents[2] / "data" / "scanner_custom.json")


def _ch(label: str, mhz: float, kind: dict) -> dict:
    return {"label": label, "freq": mhz * 1e6, **kind}


# Marine VHF — simplex voice channels (international + Swedish leisure/fishing).
_MARINE = [
    _ch("16", 156.800, _NFM),   # international distress, safety & calling
    _ch("06", 156.300, _NFM),   # ship-to-ship
    _ch("08", 156.400, _NFM),
    _ch("09", 156.450, _NFM),
    _ch("10", 156.500, _NFM),
    _ch("13", 156.650, _NFM),   # bridge-to-bridge
    _ch("67", 156.375, _NFM),   # small craft / SAR
    _ch("69", 156.475, _NFM),
    _ch("72", 156.625, _NFM),
    _ch("73", 156.675, _NFM),
    _ch("74", 156.725, _NFM),
    _ch("77", 156.875, _NFM),
    _ch("L1", 155.500, _NFM),   # Swedish leisure
    _ch("L2", 155.525, _NFM),
    _ch("L3", 155.650, _NFM),
    _ch("F1", 155.625, _NFM),   # Swedish fishing
    _ch("F2", 155.775, _NFM),
    _ch("F3", 155.825, _NFM),
]

# PMR446 — licence-free handhelds, 16 channels, 12.5 kHz spacing.
_PMR = [_ch(str(n + 1), 446.00625 + n * 0.0125, _NFM) for n in range(16)]

# Airband — a couple of universal AM frequencies (a starting point; airband is
# AM and very location-specific, so this is deliberately small).
_AIRBAND = [
    _ch("Guard", 121.500, _AM),   # international air distress
    _ch("Air-air", 123.450, _AM),
]

PRESETS: dict[str, list[dict]] = {
    "marine": _MARINE,
    "pmr446": _PMR,
    "airband": _AIRBAND,
}

# Display names + default demod hint for the UI dropdown.
PRESET_LABELS = {
    "marine": "Marine VHF",
    "pmr446": "PMR446",
    "airband": "Airband (AM)",
}


# --- user-defined channels / presets ---------------------------------------
def make_channel(label: str, mhz: float, demod: str) -> dict:
    """Build a channel dict from user input (bw inferred from the demod)."""
    demod = demod if demod in _DEMOD_BW else "nfm"
    return {"label": (str(label)[:12] or "?"), "freq": float(mhz) * 1e6,
            "demod": demod, "bw": _DEMOD_BW[demod]}


def channels_from_client(items) -> list[dict]:
    """Validate a client-supplied channel list (label, mhz, demod)."""
    out: list[dict] = []
    for d in items or []:
        if not isinstance(d, dict):
            continue
        try:
            mhz = float(d["mhz"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (24.0 <= mhz <= 1766.0):     # R820T tuning range
            continue
        out.append(make_channel(d.get("label", "?"), mhz, d.get("demod", "nfm")))
    return out


def channels_from_range(d: dict) -> tuple[list[dict], dict | None]:
    """Synthesize a channel grid from a range spec (search/sweep scanner, beta).

    ``d`` is ``{start_mhz, stop_mhz, step_khz, demod}`` from the client. Returns
    ``(channels, cfg)`` where ``cfg`` echoes the validated range (with the stop
    clamped if the slot cap kicked in) for the UI, or ``([], None)`` if invalid.
    """
    try:
        start = float(d.get("start_mhz"))
        stop = float(d.get("stop_mhz"))
        step = float(d.get("step_khz", 12.5))
    except (TypeError, ValueError):
        return [], None
    demod = d.get("demod") if d.get("demod") in _DEMOD_BW else "nfm"
    start = max(24.0, min(1766.0, start))
    stop = max(24.0, min(1766.0, stop))
    if stop <= start or not (1.0 <= step <= 1000.0):
        return [], None
    n = int(round((stop - start) * 1000.0 / step)) + 1
    clamped = n > MAX_RANGE_SLOTS
    if clamped:
        n = MAX_RANGE_SLOTS
        stop = start + (n - 1) * step / 1000.0
    chans: list[dict] = []
    for i in range(n):
        mhz = start + i * step / 1000.0
        label = f"{mhz:.4f}".rstrip("0").rstrip(".")
        chans.append({"label": label[:12], "freq": mhz * 1e6,
                      "demod": demod, "bw": _DEMOD_BW[demod]})
    cfg = {"start_mhz": round(start, 4), "stop_mhz": round(stop, 4),
           "step_khz": step, "demod": demod, "slots": n, "clamped": clamped}
    return chans, cfg


def load_custom() -> dict[str, list[dict]]:
    """Load saved custom presets, keeping only well-formed channels."""
    try:
        data = json.loads(CUSTOM_PATH.read_text())
        presets = data.get("presets", {})
    except Exception:
        return {}
    out: dict[str, list[dict]] = {}
    if isinstance(presets, dict):
        for name, chans in presets.items():
            if not (isinstance(name, str) and isinstance(chans, list)):
                continue
            norm: list[dict] = []
            for c in chans:
                if not (isinstance(c, dict) and "freq" in c and "label" in c):
                    continue
                demod = c.get("demod") if c.get("demod") in _DEMOD_BW else "nfm"
                norm.append({"label": str(c["label"])[:12], "freq": float(c["freq"]),
                             "demod": demod, "bw": _DEMOD_BW[demod]})
            if norm:
                out[name] = norm
    return out


def save_custom(presets: dict[str, list[dict]]) -> None:
    """Persist custom presets atomically (tmp + replace)."""
    try:
        CUSTOM_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CUSTOM_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"presets": presets}))
        tmp.replace(CUSTOM_PATH)
    except Exception:
        pass
