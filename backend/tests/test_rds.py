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


def test_full_dsp():
    pi, ps = 0x1A2B, "RADIO 99"
    groups = _ps_groups(pi, ps)
    flat = [blk for g in groups for blk in g]
    data = _blocks_to_bits(flat * 8)        # ~2 s of RDS

    # differential encode the whole stream (enc[i] = data[i] ^ enc[i-1])
    enc = np.empty_like(data)
    prev = 0
    for i, d in enumerate(data):
        prev = d ^ prev
        enc[i] = prev

    # biphase symbols at 19 kHz: 1 -> [+ -], 0 -> [- +]
    half = SPS // 2
    sym = np.where(enc[:, None] == 1,
                   np.r_[np.ones(half), -np.ones(half)],
                   np.r_[-np.ones(half), np.ones(half)]).reshape(-1)

    # upsample 19 kHz -> 240 kHz
    from scipy.signal import resample_poly
    base = resample_poly(sym, 240, 19)

    t = np.arange(base.size) / FS
    pilot = np.cos(2 * np.pi * 19_000 * t)              # 19 kHz pilot (cosine)
    carrier = np.cos(2 * np.pi * 57_000 * t)            # 57 kHz = 3× pilot
    mpx = 0.1 * pilot + 0.5 * base * carrier
    mpx += 0.02 * np.random.randn(mpx.size)             # a little noise
    mpx = np.r_[np.random.randn(7) * 0.02, mpx]          # arbitrary timing offset

    demod = RdsDemod(FS)
    demod.process(mpx)
    snap = demod._decoder.snapshot()
    assert snap["pi"] == "1A2B", snap
    assert snap["ps"] == "RADIO 99", snap
    print(f"✓ full DSP: recovered PI={snap['pi']} PS='{snap['ps']}' from synthetic MPX")


if __name__ == "__main__":
    test_group_decoder()
    test_full_dsp()
    print("all RDS tests passed")
