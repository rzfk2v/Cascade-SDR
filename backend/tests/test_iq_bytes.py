"""The raw-bytes sample path: conversion, and byte-exact recordings.

The async reader hands librtlsdr's own buffers around instead of pyrtlsdr's
converted arrays, so the conversion moved to one shared helper and captures are
now written exactly as the dongle produced them rather than being round-tripped
through floats and quantised back.
"""
from __future__ import annotations

import io
import queue

import numpy as np

from app.device import DeviceManager, iq_from_bytes
from app.hub import Hub


def test_iq_from_bytes_maps_the_uint8_range_onto_the_unit_square() -> None:
    # 0 -> -1, 127/128 -> ~0, 255 -> +1 on each axis
    raw = bytes([0, 0, 128, 128, 255, 255])
    iq = iq_from_bytes(raw)
    assert iq.dtype == np.complex64
    assert iq.size == 3
    assert iq[0].real < -0.99 and iq[0].imag < -0.99
    assert abs(iq[1].real) < 0.01 and abs(iq[1].imag) < 0.01
    assert iq[2].real > 0.99 and iq[2].imag > 0.99


def test_iq_from_bytes_ignores_a_trailing_half_sample() -> None:
    assert iq_from_bytes(b"\x80\x80\x80").size == 1


def test_iq_from_bytes_separates_i_and_q() -> None:
    iq = iq_from_bytes(bytes([255, 0, 0, 255]))
    assert iq[0].real > 0.99 and iq[0].imag < -0.99
    assert iq[1].real < -0.99 and iq[1].imag > 0.99


def test_a_recording_is_the_dongle_bytes_verbatim() -> None:
    """A capture must be exactly what came off the device — it is the evidence.

    The previous writer rebuilt the uint8 from complex samples, so every
    recording carried a quantisation round-trip the live path never saw.
    """
    class _Buffer(io.BytesIO):
        def close(self) -> None:
            pass        # the writer closes on exit; keep it readable to assert on

    f = _Buffer()
    q: queue.Queue = queue.Queue()
    blocks = [bytes(range(256)), bytes([7, 200] * 64)]
    for b in blocks:
        q.put(b)
    q.put(None)

    DeviceManager._rec_writer(f, q)
    assert f.getvalue() == b"".join(blocks)


def test_write_iq_hands_blocks_to_the_writer_untouched() -> None:
    mgr = DeviceManager(Hub())
    mgr._recording = True
    mgr._rec_queue = queue.Queue(maxsize=4)
    payload = bytes([1, 2, 3, 4])
    mgr._write_iq(payload)
    assert mgr._rec_queue.get_nowait() is payload
