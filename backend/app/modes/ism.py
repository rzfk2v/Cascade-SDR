"""ISM mode — 433.92 MHz devices via rtl_433.

The 433.92 MHz ISM band is full of cheap one-way transmitters: weather stations
(temperature / humidity / wind / rain), soil and pool sensors, **TPMS** tyre
pressure monitors, door/window contacts, remotes, energy meters... rtl_433 knows
how to demodulate hundreds of these protocols, so (like dump1090 / AIS-catcher)
we let it own the dongle and emit one JSON object per decode.

Rather than a flat scroll, we group decodes by **device** (model + id + channel)
and forward the latest reading for each, with a hit count and last-seen time —
sensors report periodically, so a per-device view is what you actually want.

Subprocess mode (``owns_device = False``): the DeviceManager cancels :meth:`run`
on a mode switch; the ``finally`` kills rtl_433 so the dongle is freed.

rtl_433 is in Homebrew (``brew install rtl_433``). We find it on ``PATH`` or via
the ``RTL_433_BIN`` override. It listens on 433.92 MHz by default; the band is
busiest in the evening — leave it running.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time

from app.modes.base import Mode

DEFAULT_FREQ = "433.92M"
MAX_DEVICES = 80          # evict the least-recently-heard beyond this
EMIT_EVERY = 0.5          # s — throttle pushes to the browser

# rtl_433 JSON keys that are metadata, not a measurement to chip out in the UI.
_META = {
    "time", "model", "id", "channel", "subtype", "mic", "mod", "freq",
    "freq1", "freq2", "rssi", "snr", "noise", "protocol", "sequence_num",
}


class IsmMode(Mode):
    name = "ism"
    owns_device = False
    default_center_freq = 433_920_000.0   # 433.92 MHz; display only

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self._proc: asyncio.subprocess.Process | None = None
        self._devices: dict[str, dict] = {}
        self._dirty = False

    @staticmethod
    def _exe() -> str | None:
        """Locate the rtl_433 binary (``RTL_433_BIN`` override, then ``PATH``)."""
        override = os.environ.get("RTL_433_BIN")
        if override and os.access(override, os.X_OK):
            return override
        return shutil.which("rtl_433")

    def _cmd(self) -> list[str]:
        cmd = [
            self._exe(), "-d", "0", "-f", DEFAULT_FREQ,
            "-F", "json", "-M", "time:unix", "-M", "level",
        ]
        if isinstance(self.manager.gain, (int, float)):
            cmd += ["-g", str(self.manager.gain)]
        if self.manager.freq_correction:
            cmd += ["-p", str(int(self.manager.freq_correction))]
        return cmd

    async def run(self) -> None:
        if self._exe() is None:
            self.manager.emit_json({
                "type": "error",
                "message": "rtl_433 not found. Install it: brew install rtl_433",
            })
            return

        self._proc = await asyncio.create_subprocess_exec(
            *self._cmd(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        err = self._watch_stderr(self._proc)
        self.manager.emit_json({"type": "ism_status",
                                "message": "rtl_433 running · 433.92 MHz"})
        assert self._proc.stdout is not None
        try:
            last_emit = 0.0
            while True:
                if self._proc.returncode is not None:
                    raise RuntimeError(self._exit_error("rtl_433", err))
                try:
                    raw = await asyncio.wait_for(
                        self._proc.stdout.readline(), timeout=0.5)
                except asyncio.TimeoutError:
                    raw = b""
                line = raw.decode(errors="ignore").strip()
                if line.startswith("{"):
                    self._ingest(line)
                now = time.monotonic()
                if self._dirty and now - last_emit >= EMIT_EVERY:
                    last_emit = now
                    self._dirty = False
                    self._emit()
        finally:
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

    # --- decode handling ----------------------------------------------------
    def _ingest(self, line: str) -> None:
        try:
            d = json.loads(line)
        except (ValueError, TypeError):
            return
        model = d.get("model")
        if not model:
            return
        ident = d.get("id")
        chan = d.get("channel")
        key = f"{model}|{ident}|{chan}"
        fields = {k: v for k, v in d.items() if k not in _META and v is not None}

        now = time.time()
        dev = self._devices.get(key)
        if dev is None:
            dev = {"key": key, "model": model, "count": 0, "first": now}
            if ident is not None:
                dev["id"] = ident
            if chan is not None:
                dev["channel"] = chan
            self._devices[key] = dev
        dev["count"] += 1
        dev["last"] = now
        dev["fields"] = fields
        if isinstance(d.get("rssi"), (int, float)):
            dev["rssi"] = round(d["rssi"], 1)
        self._dirty = True

        if len(self._devices) > MAX_DEVICES:
            oldest = min(self._devices, key=lambda k: self._devices[k]["last"])
            self._devices.pop(oldest, None)

    def _feed_msg(self) -> dict:
        devs = sorted(self._devices.values(), key=lambda v: v["last"], reverse=True)
        return {"type": "ism", "devices": devs, "count": len(devs)}

    def _emit(self) -> None:
        self.manager.emit_json(self._feed_msg())

    def snapshot(self) -> list[dict]:
        return [self._feed_msg()]
