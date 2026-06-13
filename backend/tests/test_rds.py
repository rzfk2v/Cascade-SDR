"""Offline tests for the RDS decoder — no hardware needed.

1. Pure logic: synthetic groups -> RdsGroupDecoder recovers PI / PS / RT.
2. Full DSP: build a synthetic 57 kHz RDS subcarrier (pilot + biphase BPSK) in an
   FM multiplex, run it through RdsDemod, and confirm it recovers the station name.

Run:  ./.venv/bin/python -m tests.test_rds      (from backend/)
"""
from __future__ import annotations

import numpy as np

from app.dsp.rds import RdsDemod, RdsGroupDecoder, make_block

FS = 240_000.0
SPS = 16
SYM_RATE = 1187.5 * SPS  # 19000


def _ps_groups(pi: int, ps: str, pty: int = 10):
    """Return the four 0A blocks per PS segment (addr 0..3) as 26-bit ints."""
    ps = (ps + " " * 8)[:8]
    groups = []
    for addr in range(4):
        b = (0 << 12) | (0 << 11) | (pty << 5) | (addr & 0x3)   # group 0A
        d = (ord(ps[addr * 2]) << 8) | ord(ps[addr * 2 + 1])
        c = 0xCDCD
        groups.append([
            make_block(pi, "A"),
            make_block(b, "B"),
            make_block(c, "C"),
            make_block(d, "D"),
        ])
    return groups


def _blocks_to_bits(blocks):
    bits = []
    for blk in blocks:
        for i in range(25, -1, -1):
            bits.append((blk >> i) & 1)
    return np.array(bits, dtype=np.int8)


def test_group_decoder():
    dec = RdsGroupDecoder()
    groups = _ps_groups(0x2345, "TESTROCK", pty=10)
    flat = [blk for g in groups for blk in g]
    bits = _blocks_to_bits(flat * 4)        # repeat so it can sync then read
    dec.feed_bits(bits)
    snap = dec.snapshot()
    assert snap["pi"] == "2345", snap
    assert snap["ps"] == "TESTROCK", snap
    assert snap["pty"] == 10, snap
    print("✓ group decoder: PI=2345 PS=TESTROCK PTY=10")


def _synth_mpx(pi, ps, repeats=8):
    groups = _ps_groups(pi, ps)
    flat = [blk for g in groups for blk in g]
    data = _blocks_to_bits(flat * repeats)
    enc = np.empty_like(data)                  # differential encode
    prev = 0
    for i, d in enumerate(data):
        prev = d ^ prev
        enc[i] = prev
    half = SPS // 2                            # biphase: 1->[+ -], 0->[- +]
    sym = np.where(enc[:, None] == 1,
                   np.r_[np.ones(half), -np.ones(half)],
                   np.r_[-np.ones(half), np.ones(half)]).reshape(-1)
    from scipy.signal import resample_poly
    base = resample_poly(sym, 240, 19)         # 19 kHz -> 240 kHz
    t = np.arange(base.size) / FS
    pilot = np.cos(2 * np.pi * 19_000 * t)
    carrier = np.cos(2 * np.pi * 57_000 * t)
    mpx = 0.1 * pilot + 0.5 * base * carrier
    mpx += 0.02 * np.random.randn(mpx.size)
    return np.r_[np.random.randn(7) * 0.02, mpx]   # + arbitrary timing offset


def test_full_dsp():
    mpx = _synth_mpx(0x1A2B, "RADIO 99")
    demod = RdsDemod(FS)
    demod.process(mpx)                              # single call
    snap = demod._decoder.snapshot()
    assert snap["pi"] == "1A2B", snap
    assert snap["ps"] == "RADIO 99", snap
    print(f"✓ full DSP (1 call): PI={snap['pi']} PS='{snap['ps']}'")


def test_full_dsp_multiblock():
    """Feed the SAME signal in many small blocks — the live-stream path that the
    single-call test missed (per-block re-search used to slip bits here)."""
    mpx = _synth_mpx(0x5C3D, "STREAMOK", repeats=14)
    demod = RdsDemod(FS)
    block = 2048
    for i in range(0, mpx.size, block):
        demod.process(mpx[i:i + block])
    snap = demod._decoder.snapshot()
    assert snap["pi"] == "5C3D", snap
    assert snap["ps"] == "STREAMOK", snap
    print(f"✓ full DSP ({mpx.size // block} blocks of {block}): PI={snap['pi']} PS='{snap['ps']}'")


def test_full_dsp_drift():
    """Multi-block with a ~60 ppm sample-clock error (like a real dongle) — the
    ±1 symbol-timing tracking must follow the drift without losing sync."""
    from scipy.signal import resample_poly
    mpx = _synth_mpx(0x77E1, "DRIFTACQ", repeats=22)
    mpx = resample_poly(mpx, 1_000_000, 1_000_060)   # stretch time base ~60 ppm
    demod = RdsDemod(FS)
    block = 4096
    for i in range(0, mpx.size, block):
        demod.process(mpx[i:i + block])
    snap = demod._decoder.snapshot()
    assert snap["pi"] == "77E1", snap
    assert snap["ps"] == "DRIFTACQ", snap
    print(f"✓ full DSP (~60 ppm drift, multi-block): PI={snap['pi']} PS='{snap['ps']}'")


if __name__ == "__main__":
    test_group_decoder()
    test_full_dsp()
    test_full_dsp_multiblock()
    test_full_dsp_drift()
    print("all RDS tests passed")
