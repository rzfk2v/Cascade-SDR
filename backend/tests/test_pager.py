"""Parsing of multimon-ng pager output into feed rows."""
from __future__ import annotations

from app.modes.pager import parse_pager


def test_pocsag_alpha():
    line = "POCSAG1200: Address:  1234567  Function: 3  Alpha:   HELLO WORLD"
    msg = parse_pager(line)
    assert msg is not None
    assert msg["proto"] == "POCSAG1200"
    assert msg["addr"] == "1234567"
    assert msg["func"] == 3
    assert msg["kind"] == "Alpha"
    assert msg["text"] == "HELLO WORLD"


def test_pocsag_numeric():
    line = "POCSAG512: Address:   123456  Function: 0  Numeric: 12345"
    msg = parse_pager(line)
    assert msg is not None
    assert msg["proto"] == "POCSAG512"
    assert msg["kind"] == "Numeric"
    assert msg["text"] == "12345"


def test_empty_message_dropped():
    # An address-only frame with no content isn't a useful feed row.
    assert parse_pager("POCSAG1200: Address:  1234567  Function: 0  Alpha:   ") is None


def test_non_message_lines_ignored():
    assert parse_pager("Enabled demodulators: POCSAG1200") is None
    assert parse_pager("") is None
    assert parse_pager("rtl_fm: tuned to 439987500 Hz") is None
