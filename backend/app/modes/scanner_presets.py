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

_NFM = {"demod": "nfm", "bw": 12_500}
_AM = {"demod": "am", "bw": 8_000}


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
