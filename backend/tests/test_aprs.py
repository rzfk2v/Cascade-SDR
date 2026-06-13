"""Offline tests for APRS parsing — no hardware / direwolf needed.

Exercises the two pure pieces: pulling a TNC2 packet out of a direwolf monitor
line, and turning the AprsMode pipeline (extract -> aprslib -> aggregate) into
plotted stations. The live rtl_fm|direwolf path needs the dongle + an RF signal.

Run:  ./.venv/bin/python -m tests.test_aprs      (from backend/)
"""
from __future__ import annotations

from app.modes.aprs import AprsMode, extract_tnc2

# Real-world-ish direwolf monitor lines (with the [chan.level] prefix it adds).
SAMPLES = [
    "[0.1] SA0BXI-9>APDR16,WIDE1-1,WIDE2-1:!5912.34N/01803.56E>Hello from Sweden",
    "[0.3] SM7ABC>APRS,TCPIP*:=5540.12N/01259.87E-Home station",
    "LA1XYZ-7>APAT81,WIDE2-2:/123456h6010.00N/01030.00E>120/045Mobile",
]


def test_extract_tnc2():
    for line in SAMPLES:
        pkt = extract_tnc2(line)
        assert pkt and ">" in pkt and ":" in pkt, line
        assert not pkt.startswith("["), pkt   # the [chan.level] tag is stripped
    assert extract_tnc2("DIREWOLF version 1.8") is None
    assert extract_tnc2("Audio level = 50") is None
    print("✓ extract_tnc2: pulls TNC2 packets, ignores noise lines")


def test_pipeline_to_stations():
    m = AprsMode(manager=_FakeMgr())
    for line in SAMPLES:
        m._on_line(line)
    msg = m._stations_msg(now=_now(m))
    calls = {s["call"] for s in msg["stations"]}
    assert {"SA0BXI-9", "SM7ABC", "LA1XYZ-7"} <= calls, calls
    assert msg["positioned"] == 3, msg
    # spot-check one decoded position
    sa = next(s for s in msg["stations"] if s["call"] == "SA0BXI-9")
    assert 59.0 < sa["lat"] < 59.5 and 18.0 < sa["lon"] < 18.2, sa
    print(f"✓ pipeline: {msg['count']} stations, {msg['positioned']} positioned "
          f"(SA0BXI-9 @ {sa['lat']},{sa['lon']})")


class _FakeMgr:
    center_freq = 144_800_000.0
    sample_rate = 2_400_000.0
    gain = "auto"
    freq_correction = 0


def _now(m):
    import time
    # ensure all stations have a timestamp
    return time.monotonic() + 0.0


if __name__ == "__main__":
    test_extract_tnc2()
    test_pipeline_to_stations()
    print("all APRS tests passed")
