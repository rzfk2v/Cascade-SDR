"""Broadcast hub: fan-out of messages from the active mode to all WebSocket clients.

The hub is transport-agnostic. Modes push two kinds of payloads:
  * JSON-serialisable dicts  -> sent as text frames
  * bytes                    -> sent as binary frames (FFT rows, PCM audio, ...)

Binary frames begin with a 4-byte header (tag byte + 3 padding bytes) so the
payload that follows is 4-byte aligned — that lets the browser wrap it directly
in a ``Float32Array``/``Int16Array`` without a copy. See ``FrameTag``.
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


class Hub:
    """Tracks connected clients and fans messages out to them."""

    def __init__(self) -> None:
        self._clients: set[Any] = set()
        self._lock = asyncio.Lock()

    async def register(self, ws: Any) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def unregister(self, ws: Any) -> None:
        async with self._lock:
            self._clients.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def broadcast_json(self, message: dict[str, Any]) -> None:
        await self._broadcast(text=json.dumps(message))

    async def broadcast_binary(self, tag: FrameTag, payload: bytes) -> None:
        # 4-byte header keeps `payload` 4-aligned for typed-array views in JS.
        header = bytes([tag, 0, 0, 0])
        await self._broadcast(binary=header + payload)

    async def _broadcast(self, *, text: str | None = None, binary: bytes | None = None) -> None:
        if not self._clients:
            return
        # Snapshot so a disconnect mid-iteration doesn't mutate the set we loop over.
        async with self._lock:
            targets = list(self._clients)
        dead: list[Any] = []
        for ws in targets:
            try:
                if text is not None:
                    await ws.send_text(text)
                else:
                    await ws.send_bytes(binary)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)
