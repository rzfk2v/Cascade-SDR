"""ACARS mode — aircraft VHF data-link messages (around 131 MHz).

ACARS is short text/data sent by aircraft (and ground) on VHF AM channels. We
don't demodulate it ourselves: we spawn **acarsdec** (which owns the dongle and
can watch several channels at once) and have it emit one JSON object per message
over UDP. We parse those, keep a rolling feed of recent messages, and forward the
list to the browser as a log (ACARS rarely carries a position, so it's a feed,
not a map).

Subprocess mode (``owns_device = False``): the DeviceManager cancels :meth:`run`
on a mode switch; the ``finally`` kills acarsdec so the dongle is freed.

acarsdec isn't in Homebrew — build it from source (see the README). We find it
on ``PATH`` or in the README's build dir; set ``ACARSDEC_BIN`` to override.
Default channels are the common EU ACARS frequencies; tweak ``CHANNELS`` for your
region (North America centres on 131.550).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from collections import deque
from pathlib import Path

from app.modes.base import Mode

UDP_HOST = "127.0.0.1"
UDP_PORT = 5556
MAX_FEED = 200
CHANNELS = ["131.725", "131.525", "131.825"]  # EU; within one ~2.4 MHz capture


class _JsonProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_obj) -> None:
        self.on_obj = on_obj

    def datagram_received(self, data: bytes, _addr) -> None:
        for line in data.decode(errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                self.on_obj(json.loads(line))
            except (ValueError, TypeError):
                pass


class AcarsMode(Mode):
    name = "acars"
    owns_device = False
    default_center_freq = 131_725_000.0

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self.feed: deque[dict] = deque(maxlen=MAX_FEED)
        self._seen: set[tuple] = set()
        self._proc: asyncio.subprocess.Process | None = None
        self._dirty = False

    @staticmethod
    def _exe() -> str | None:
        """Locate the acarsdec binary, returning an absolute path or None.

        Tried in order: the ``ACARSDEC_BIN`` override, then ``PATH``, then the
        build dir from the README's "build from source" steps. The fallback
        matters because a GUI-launched backend gets a stripped-down PATH that
        omits Homebrew, so ``shutil.which`` alone can miss a working binary.
        """
        override = os.environ.get("ACARSDEC_BIN")
        if override and os.access(override, os.X_OK):
            return override
        found = shutil.which("acarsdec")
        if found:
            return found
        fallback = Path.home() / ".local/src/acarsdec/build/acarsdec"
        if os.access(fallback, os.X_OK):
            return str(fallback)
        return None

    def _cmd(self) -> list[str]:
        cmd = [self._exe(), "-j", f"{UDP_HOST}:{UDP_PORT}"]
        if isinstance(self.manager.gain, (int, float)):
            cmd += ["-g", str(int(self.manager.gain))]
        cmd += ["-r", "0", *CHANNELS]   # rtlsdr device 0, then channels (MHz)
        return cmd

    async def run(self) -> None:
        if self._exe() is None:
            self.manager.emit_json({
                "type": "error",
                "message": "acarsdec not found. Build it from source (see README).",
            })
            return

        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _JsonProtocol(self._on_obj), local_addr=(UDP_HOST, UDP_PORT)
        )
        self._proc = await asyncio.create_subprocess_exec(
            *self._cmd(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        err = self._watch_stderr(self._proc)
        self.manager.emit_json({"type": "acars_status",
                                "message": f"acarsdec running · {', '.join(CHANNELS)} MHz"})
        try:
            last_emit = 0.0
            while True:
                if self._proc.returncode is not None:
                    raise RuntimeError(self._exit_error("acarsdec", err))
                await asyncio.sleep(0.25)
                now = time.monotonic()
                if self._dirty and now - last_emit >= 0.5:
                    last_emit = now
                    self._dirty = False
                    self._emit()
        finally:
            transport.close()
            await self._kill_proc()

    async def _kill_proc(self) -> None:
        self._cancel_stderr_watch()
        if self._proc is None or self._proc.returncode is not None:
            return
        try:
            self._proc.terminate()
            await asyncio.wait_for(self._proc.wait(), timeout=3.0)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass

    # --- message parsing ----------------------------------------------------
    def _on_obj(self, d: dict) -> None:
        msg = parse_acars(d)
        if msg is None:
            return
        key = (msg.get("tail"), msg.get("flight"), msg.get("label"), msg.get("text"))
        if key in self._seen:        # acarsdec can repeat a msg across channels
            return
        self._seen.add(key)
        if len(self._seen) > MAX_FEED * 2:
            self._seen.clear()
        self.feed.appendleft(msg)
        self._dirty = True

    def _feed_msg(self) -> dict:
        return {"type": "acars", "messages": list(self.feed), "count": len(self.feed)}

    def _emit(self) -> None:
        self.manager.emit_json(self._feed_msg())

    def snapshot(self) -> list[dict]:
        return [self._feed_msg()]


def parse_acars(d: dict) -> dict | None:
    """Normalise one acarsdec JSON object (v2 flat or v3 nested) into a feed row."""
    m = d.get("acars", d)  # acarsdec v3 nests the message under "acars"
    text = m.get("text") or m.get("message") or ""
    tail = (m.get("tail") or m.get("registration") or "").strip()
    flight = (m.get("flight") or m.get("flight_id") or "").strip()
    label = (m.get("label") or "").strip()
    if not (text or tail or flight):
        return None
    ts = m.get("timestamp") or d.get("timestamp") or time.time()
    out = {"t": float(ts), "text": text.strip()}
    if tail:
        out["tail"] = tail
    if flight:
        out["flight"] = flight
    if label:
        out["label"] = label
    freq = m.get("freq")
    if freq is not None:
        out["freq"] = freq
    return out
