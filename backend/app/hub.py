"""Broadcast hub: fan-out of messages from the active mode to all WebSocket clients.

The hub is transport-agnostic. Modes push two kinds of payloads:
  * JSON-serialisable dicts  -> sent as text frames
  * bytes                    -> sent as binary frames (FFT rows, PCM audio, ...)

Binary frames begin with a 4-byte header (tag byte + 3 padding bytes) so the
payload that follows is 4-byte aligned — that lets the browser wrap it directly
in a ``Float32Array``/``Int16Array`` without a copy. See ``FrameTag``.

Each client gets its own bounded send queue drained by its own sender task, so
one slow client (full TCP buffer — e.g. a phone that walked out of WiFi range)
only loses *its own* frames instead of stalling the event loop and starving
audio/FFT delivery to everyone else. When a queue is full the oldest frame is
dropped; a client that far behind is already unusable and will resync from the
mode snapshot on reconnect.
"""
from __future__ import annotations

import asyncio
import json
from enum import IntEnum
from typing import Any


class FrameTag(IntEnum):
    """First byte of every binary frame, identifying the payload kind."""

    FFT = 0x01      # waterfall magnitude row (float32 little-endian)
    AUDIO = 0x02    # demodulated PCM (int16 little-endian, interleaved stereo)
    APT = 0x03      # one NOAA APT image line (2080 bytes, uint8 grayscale)
    SSTV = 0x04     # one SSTV image row (width*3 bytes, uint8 RGB)


# ~72 frames/s in radio mode (audio + FFT), so this is ≈1.8 s of backlog.
QUEUE_DEPTH = 128


class Hub:
    """Tracks connected clients and fans messages out to them."""

    def __init__(self) -> None:
        self._queues: dict[Any, asyncio.Queue] = {}
        self._senders: dict[Any, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def register(self, ws: Any) -> None:
        async with self._lock:
            q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_DEPTH)
            self._queues[ws] = q
            self._senders[ws] = asyncio.create_task(self._sender(ws, q))

    async def unregister(self, ws: Any) -> None:
        async with self._lock:
            self._queues.pop(ws, None)
            task = self._senders.pop(ws, None)
        if task is not None:
            task.cancel()

    @property
    def client_count(self) -> int:
        return len(self._queues)

    async def broadcast_json(self, message: dict[str, Any]) -> None:
        self._enqueue_all(text=json.dumps(message))

    async def broadcast_binary(self, tag: FrameTag, payload: bytes) -> None:
        # 4-byte header keeps `payload` 4-aligned for typed-array views in JS.
        header = bytes([tag, 0, 0, 0])
        self._enqueue_all(binary=header + payload)

    async def send_json(self, ws: Any, message: dict[str, Any]) -> None:
        """Send to a single client, via its queue — the sender task is the only
        writer per socket, which also keeps frame order consistent with
        broadcasts."""
        q = self._queues.get(ws)
        if q is not None:
            self._enqueue(q, (json.dumps(message), None))

    def _enqueue_all(self, *, text: str | None = None, binary: bytes | None = None) -> None:
        item = (text, binary)
        for q in list(self._queues.values()):
            self._enqueue(q, item)

    @staticmethod
    def _enqueue(q: asyncio.Queue, item: tuple) -> None:
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            # Slow client: drop its oldest frame rather than stalling everyone.
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass

    async def _sender(self, ws: Any, q: asyncio.Queue) -> None:
        try:
            while True:
                text, binary = await q.get()
                if text is not None:
                    await ws.send_text(text)
                else:
                    await ws.send_bytes(binary)
        except asyncio.CancelledError:
            pass
        except Exception:
            # Send failed: the client is gone or wedged. Leave the final cleanup
            # to the endpoint's unregister (its receive loop errors out too);
            # meanwhile its queue just fills and drops, harming nobody.
            pass
