"""Scanner block-centre placement — the DC spike must not break squelch.

The dongle shows a false carrier at its own tune frequency. A range search's
uniform grid put a slot exactly on the block midpoint, so that slot parked on
static forever. The centre is now nudged into a gap between channels, and the
FFT bins right at DC are excluded from channel scoring as a backstop.
"""
from __future__ import annotations

import numpy as np

from app.modes.scanner import _DC_KEEP, DC_GUARD_BINS, FFT_SIZE, ScannerMode
from app.modes.scanner_presets import channels_from_range

SR = 2_400_000.0
USABLE = SR * 0.9


def _center_for(chans: list[dict]) -> float:
    lo, hi = chans[0]["freq"], chans[-1]["freq"]
    slack = (USABLE - (hi - lo)) / 2.0
    return ScannerMode._dc_safe_center(chans, (lo + hi) / 2.0, slack, SR)


def test_range_grid_center_clears_every_slot():
    # 175–176.5 @ 25 kHz: the old midpoint (175.75) sat exactly on slot 31.
    chans, _ = channels_from_range(
        {"start_mhz": 175.0, "stop_mhz": 176.5, "step_khz": 25.0})
    center = _center_for(chans)
    # DC must land outside every slot's band (i.e. in a gap between slots)...
    assert all(abs(c["freq"] - center) > c["bw"] / 2.0 for c in chans)
    assert min(abs(c["freq"] - center) for c in chans) > 12_400.0
    # ...and every slot must have band bins the DC mask doesn't cover.
    guard = (DC_GUARD_BINS + 0.5) * SR / FFT_SIZE
    assert all(abs(c["freq"] - center) + c["bw"] / 2.0 > guard for c in chans)
    # All slots must still fit in the usable bandwidth around the new centre.
    assert all(abs(c["freq"] - center) <= USABLE / 2.0 for c in chans)


def test_dense_grid_lands_between_slots():
    # 12.5 kHz step with 12.5 kHz bw: bands touch, so perfect clearance is
    # impossible — the centre must at least sit on a gap midpoint.
    chans, _ = channels_from_range(
        {"start_mhz": 155.0, "stop_mhz": 156.0, "step_khz": 12.5})
    center = _center_for(chans)
    nearest = min(abs(c["freq"] - center) for c in chans)
    assert nearest > 6_200.0     # ~half a step away from every slot


def test_single_channel_block_gets_nudged():
    chans = [{"label": "x", "freq": 145.5e6, "demod": "nfm", "bw": 12_500}]
    center = _center_for(chans)
    assert abs(center - 145.5e6) > 12_500 / 2.0


def test_dc_bins_excluded_from_channel_level():
    # A spike in the centre FFT bins alone must not raise a channel's level.
    center = 155.75e6
    freqs = center + (np.arange(FFT_SIZE) / FFT_SIZE - 0.5) * SR
    row = np.full(FFT_SIZE, -60.0, dtype=np.float32)
    row[FFT_SIZE // 2 - DC_GUARD_BINS:FFT_SIZE // 2 + DC_GUARD_BINS + 1] = -10.0
    ch = {"freq": center, "bw": 25_000}
    lvl = ScannerMode._channel_level(None, row, freqs, ch, -60.0)
    assert lvl == 0.0
    # ...but a real signal wider than the spike still registers.
    row[FFT_SIZE // 2 - 5:FFT_SIZE // 2 + 6] = -20.0
    lvl = ScannerMode._channel_level(None, row, freqs, ch, -60.0)
    assert lvl >= 40.0


def test_dc_keep_mask_shape():
    assert _DC_KEEP.sum() == FFT_SIZE - (2 * DC_GUARD_BINS + 1)
