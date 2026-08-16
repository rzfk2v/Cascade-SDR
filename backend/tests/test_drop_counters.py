"""Dropped blocks and frames are counted, not swallowed.

Both queues in the sample path drop on backpressure by design — the reader must
never stall — but until now they did it silently, so a capture could lose a
sixth of its blocks and still look like a good file, and live audio could break
up with nothing anywhere to say why (GitHub issue #2).
"""
from __future__ import annotations

import asyncio
import queue

import numpy as np

from app.device import DeviceManager
from app.hub import QUEUE_DEPTH, FrameTag, Hub


class _StuckClient:
    """A client whose socket never completes a send (full TCP window)."""

    async def send_text(self, text: str) -> None:
        await asyncio.Event().wait()

    async def send_bytes(self, data: bytes) -> None:
        await asyncio.Event().wait()


def test_hub_counts_frames_dropped_for_a_stuck_client() -> None:
    async def scenario() -> int:
        hub = Hub()
        ws = _StuckClient()
        await hub.register(ws)
        await asyncio.sleep(0)          # let the sender task block on its first send
        for _ in range(QUEUE_DEPTH + 20):
            await hub.broadcast_binary(FrameTag.AUDIO, b"\x00" * 8)
        dropped = hub.dropped
        await hub.unregister(ws)
        return dropped

    # The sender holds one frame; everything past the queue depth is evicted.
    assert asyncio.run(scenario()) >= 15


def test_hub_drops_nothing_when_the_client_keeps_up() -> None:
    async def scenario() -> int:
        hub = Hub()

        class _Fine:
            async def send_text(self, text: str) -> None: ...
            async def send_bytes(self, data: bytes) -> None: ...

        ws = _Fine()
        await hub.register(ws)
        for _ in range(QUEUE_DEPTH * 3):
            await hub.broadcast_binary(FrameTag.AUDIO, b"\x00" * 8)
            await asyncio.sleep(0)      # let the sender drain
        dropped = hub.dropped
        await hub.unregister(ws)
        return dropped

    assert asyncio.run(scenario()) == 0


def _recording_manager(depth: int) -> DeviceManager:
    mgr = DeviceManager(Hub())
    mgr._recording = True
    mgr._rec_queue = queue.Queue(maxsize=depth)
    mgr._rec_dropped = 0
    mgr._rec_blocks = 0
    return mgr


def test_recording_reports_the_blocks_its_writer_could_not_take() -> None:
    """A slow disk (or an NFS share) must not corrupt a capture in silence."""
    mgr = _recording_manager(depth=2)
    for _ in range(5):
        mgr._write_iq(np.zeros(4, dtype=np.complex64))

    res = mgr.record_stop()
    assert res["dropped"] == 3, res
    assert res["blocks"] == 5, res
    assert res["lost_pct"] == 60.0, res


def test_a_clean_recording_reports_no_loss() -> None:
    mgr = _recording_manager(depth=64)
    for _ in range(10):
        mgr._write_iq(np.zeros(4, dtype=np.complex64))

    res = mgr.record_stop()
    assert res["dropped"] == 0
    assert res["blocks"] == 10
    assert res["lost_pct"] == 0.0


def test_status_exposes_the_counters() -> None:
    mgr = DeviceManager(Hub())
    drops = mgr.status()["drops"]
    assert drops == {"iq": 0, "net": 0, "rec": 0}
