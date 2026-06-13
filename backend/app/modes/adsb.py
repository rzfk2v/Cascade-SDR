"""ADS-B mode — aircraft positions via dump1090.

ADS-B (1090 MHz Mode-S) demodulation is hard, so we run ``dump1090`` (FlightAware
build), which owns the dongle in this mode. We have it write its decoded
``aircraft.json`` to a temp directory once a second and simply forward each
snapshot to the browser, which plots the aircraft (and their tracks) on a map.
aircraft.json is richer than the SBS feed — it carries the emitter **category**
(light / large / heavy / rotorcraft / …), which is our "what kind of aircraft".

Subprocess mode (``owns_device = False``): the DeviceManager cancels :meth:`run`
on mode switch; the ``finally`` kills dump1090 so the dongle is released.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile

from app.modes.base import Mode

# ADS-B emitter categories -> human label
AC_CATEGORY = {
    "A1": "Light", "A2": "Small", "A3": "Large", "A4": "Large (high-vortex)",
    "A5": "Heavy", "A6": "High-performance", "A7": "Rotorcraft",
    "B1": "Glider", "B2": "Lighter-than-air", "B3": "Parachutist",
    "B4": "Ultralight", "B6": "Drone (UAV)", "B7": "Spacecraft",
    "C1": "Surface vehicle", "C2": "Surface vehicle", "C3": "Obstacle",
}


class AdsbMode(Mode):
    name = "adsb"
    owns_device = False

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self._proc: asyncio.subprocess.Process | None = None
        self._jsondir: str | None = None
        self._latest = {"type": "aircraft", "aircraft": [], "count": 0, "positioned": 0}

    def _cmd(self) -> list[str]:
        exe = shutil.which("dump1090") or shutil.which("dump1090-fa") or "dump1090"
        cmd = [
            exe, "--device-type", "rtlsdr",
            "--write-json", self._jsondir, "--write-json-every", "1", "--quiet",
        ]
        if self.manager.gain != "auto":
            cmd += ["--gain", str(self.manager.gain)]
        if self.manager.freq_correction:
            cmd += ["--ppm", str(int(self.manager.freq_correction))]
        return cmd

    async def run(self) -> None:
        if shutil.which("dump1090") is None and shutil.which("dump1090-fa") is None:
            self.manager.emit_json({
                "type": "error",
                "message": "dump1090 not found. Install it: brew install dump1090-mutability",
            })
            return

        self._jsondir = tempfile.mkdtemp(prefix="cascade-adsb-")
        self._proc = await asyncio.create_subprocess_exec(
            *self._cmd(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.manager.emit_json({"type": "adsb_status", "message": "starting dump1090…"})
        path = os.path.join(self._jsondir, "aircraft.json")

        try:
            announced = False
            while True:
                if self._proc.returncode is not None:
                    raise RuntimeError(
                        "dump1090 exited — is the dongle free and connected?"
                    )
                await asyncio.sleep(1.0)
                msg = self._read(path)
                if msg is not None:
                    if not announced:
                        self.manager.emit_json(
                            {"type": "adsb_status", "message": "dump1090 running"}
                        )
                        announced = True
                    self.manager.emit_json(msg)
        finally:
            await self._kill_proc()
            if self._jsondir:
                shutil.rmtree(self._jsondir, ignore_errors=True)

    async def _kill_proc(self) -> None:
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

    def _read(self, path: str) -> dict | None:
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            return None  # not written yet, or mid-write

        out = []
        for a in data.get("aircraft", []):
            hexid = (a.get("hex") or "").strip()
            if not hexid:
                continue
            item: dict = {"icao": hexid}
            flight = (a.get("flight") or "").strip()
            if flight:
                item["flight"] = flight
            alt = a.get("alt_baro")
            if isinstance(alt, (int, float)):
                item["alt"] = int(alt)
            elif alt == "ground":
                item["ground"] = True
            if a.get("gs") is not None:
                item["speed"] = round(a["gs"])
            if a.get("track") is not None:
                item["track"] = round(a["track"])
            if a.get("lat") is not None and a.get("lon") is not None:
                item["lat"] = a["lat"]
                item["lon"] = a["lon"]
            if a.get("baro_rate") is not None:
                item["vert_rate"] = round(a["baro_rate"])
            if a.get("squawk"):
                item["squawk"] = a["squawk"]
            cat = a.get("category")
            if cat:
                item["type"] = AC_CATEGORY.get(cat, cat)
            if a.get("messages") is not None:
                item["msgs"] = a["messages"]
            if a.get("seen") is not None:
                item["age"] = round(a["seen"], 1)
            out.append(item)

        positioned = sum(1 for a in out if "lat" in a)
        self._latest = {"type": "aircraft", "aircraft": out,
                        "count": len(out), "positioned": positioned}
        return self._latest

    def snapshot(self) -> list[dict]:
        return [self._latest]
