"""Offline tests for ACARS JSON parsing — no hardware / acarsdec needed.

Covers both acarsdec JSON shapes (v2 flat, v3 nested under "acars") and the
AcarsMode feed (dedup + newest-first ordering). The live acarsdec UDP path needs
the dongle + RF.

Run:  ./.venv/bin/python -m tests.test_acars      (from backend/)
"""
from __future__ import annotations

from app.modes.acars import AcarsMode, parse_acars

V2 = {  # acarsdec v2: flat object
    "timestamp": 1_700_000_000.0, "channel": 0, "freq": 131.725,
    "tail": "SE-RUA", "flight": "SK1429", "label": "5Z", "text": "REQ WX ESSA",
}
V3 = {  # acarsdec v3: message nested under "acars"
    "timestamp": 1_700_000_001.0,
    "acars": {"registration": "G-EZAB", "flight_id": "U22931",
              "label": "H1", "message": "POS REPORT", "freq": 131.525},
}
EMPTY = {"timestamp": 1_700_000_002.0, "acars": {"label": "_d"}}  # no content


def test_parse_shapes():
    a = parse_acars(V2)
    assert a["tail"] == "SE-RUA" and a["flight"] == "SK1429" and a["text"] == "REQ WX ESSA"
    b = parse_acars(V3)
    assert b["tail"] == "G-EZAB" and b["flight"] == "U22931" and b["text"] == "POS REPORT"
    assert parse_acars(EMPTY) is None     # nothing useful -> dropped
    print("✓ parse_acars: v2 flat + v3 nested; empty dropped")


def test_feed_dedup_and_order():
    m = AcarsMode(manager=_FakeMgr())
    m._on_obj(V2)
    m._on_obj(V2)        # duplicate (acarsdec repeats across channels)
    m._on_obj(V3)
    msg = m._feed_msg()
    assert msg["count"] == 2, msg                 # duplicate collapsed
    assert msg["messages"][0]["flight"] == "U22931"   # newest first
    print(f"✓ feed: {msg['count']} msgs, newest-first, deduped")


class _FakeMgr:
    center_freq = 131_725_000.0
    sample_rate = 2_400_000.0
    gain = "auto"
    freq_correction = 0


if __name__ == "__main__":
    test_parse_shapes()
    test_feed_dedup_and_order()
    print("all ACARS tests passed")
