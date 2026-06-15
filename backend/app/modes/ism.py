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
from pathlib import Path

from app.modes.base import Mode

DEFAULT_FREQ = "433.92M"
MAX_DEVICES = 80          # evict the least-recently-heard beyond this
EMIT_EVERY = 0.5          # s — throttle pushes to the browser
HISTORY_LEN = 60          # points kept per numeric field, for UI sparklines

# Persist the device feed (incl. per-field history) so trends survive a mode
# switch or a backend restart, like the AIS name cache.
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "ism_cache.json"
CACHE_FLUSH_SECONDS = 10

# rtl_433 JSON keys that are metadata, not a measurement to chip out in the UI.
_META = {
    "time", "model", "id", "channel", "subtype", "mic", "mod", "freq",
    "freq1", "freq2", "rssi", "snr", "noise", "protocol", "sequence_num",
}


def _load_cache() -> dict[str, dict]:
    """Load the saved device feed, keeping only well-formed entries."""
    try:
        data = json.loads(CACHE_PATH.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for key, dev in data.items():
        if isinstance(dev, dict) and isinstance(dev.get("model"), str):
            dev.setdefault("history", {})
            dev.setdefault("count", 0)
            out[key] = dev
    return out


def _graphable(key: str, value) -> bool:
    """True for a numeric measurement worth trending (not a 0/1 flag)."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and not key.endswith("_ok"))


class IsmMode(Mode):
    name = "ism"
    owns_device = False
    default_center_freq = 433_920_000.0   # 433.92 MHz; display only

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self._proc: asyncio.subprocess.Process | None = None
        self._devices: dict[str, dict] = _load_cache()   # restore last session
        self._dirty = False
        self._cache_dirty = False

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
        if self._devices:
            self._emit()        # show cached devices/trends right away
        assert self._proc.stdout is not None
        try:
            last_emit = last_flush = 0.0
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
                if self._cache_dirty and now - last_flush >= CACHE_FLUSH_SECONDS:
                    last_flush = now
                    self._flush_cache()
        finally:
            self._flush_cache()      # persist anything learned this session
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
            dev = {"key": key, "model": model, "count": 0, "first": now,
                   "history": {}}
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

        # Append each numeric measurement to its rolling series for sparklines.
        hist = dev.setdefault("history", {})
        for k, v in fields.items():
            if _graphable(k, v):
                series = hist.setdefault(k, [])
                series.append(round(float(v), 3))
                if len(series) > HISTORY_LEN:
                    del series[: len(series) - HISTORY_LEN]
        self._dirty = True
        self._cache_dirty = True

        if len(self._devices) > MAX_DEVICES:
            oldest = min(self._devices, key=lambda k: self._devices[k].get("last", 0))
            self._devices.pop(oldest, None)

    def _feed_msg(self) -> dict:
        devs = sorted(self._devices.values(),
                      key=lambda v: v.get("last", 0), reverse=True)
        return {"type": "ism", "devices": devs, "count": len(devs)}

    def _emit(self) -> None:
        self.manager.emit_json(self._feed_msg())

    def _flush_cache(self) -> None:
        """Persist the device feed atomically, so trends survive a restart."""
        if not self._cache_dirty:
            return
        self._cache_dirty = False
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = CACHE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._devices))
            tmp.replace(CACHE_PATH)       # atomic so a crash can't corrupt it
        except Exception:
            pass

    def snapshot(self) -> list[dict]:
        return [self._feed_msg()]
