"""ADS-B mode — aircraft positions via dump1090.

ADS-B (1090 MHz Mode-S) demodulation is genuinely hard, so we don't reimplement
it: we spawn ``dump1090`` (FlightAware build), which owns the dongle in this mode,
and read its BaseStation/SBS feed on TCP 30003. We aggregate the CSV messages by
ICAO address into per-aircraft state and forward the list to the browser, which
plots it on a map.

This is a subprocess mode (``owns_device = False``): the DeviceManager runs
:meth:`run` as a task and cancels it on mode switch; the ``finally`` makes sure
dump1090 is killed so the device is released before any other mode opens it.
"""
from __future__ import annotations

import asyncio
import shutil
import time

from app.modes.base import Mode

SBS_HOST = "127.0.0.1"
SBS_PORT = 30003
STALE_SECONDS = 60.0


class AdsbMode(Mode):
    name = "adsb"
    owns_device = False

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self.aircraft: dict[str, dict] = {}
        self._proc: asyncio.subprocess.Process | None = None

    def _dump1090_cmd(self) -> list[str]:
        exe = shutil.which("dump1090") or shutil.which("dump1090-fa") or "dump1090"
        cmd = [
            exe,
            "--device-type", "rtlsdr",
            "--net",
            "--net-bind-address", SBS_HOST,
            "--quiet",
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

        self._proc = await asyncio.create_subprocess_exec(
            *self._dump1090_cmd(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.manager.emit_json({"type": "adsb_status", "message": "starting dump1090…"})

        reader = writer = None
        try:
            # dump1090 needs a moment to grab the device and open port 30003
            for _ in range(40):
                if self._proc.returncode is not None:
                    raise RuntimeError(
                        "dump1090 exited immediately — is the dongle free and connected?"
                    )
                try:
                    reader, writer = await asyncio.open_connection(SBS_HOST, SBS_PORT)
                    break
                except OSError:
                    await asyncio.sleep(0.3)
            if reader is None:
                raise RuntimeError("could not connect to dump1090 (SBS port 30003)")

            self.manager.emit_json({"type": "adsb_status", "message": "dump1090 running"})
            last_emit = 0.0
            while True:
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=1.0)
                    if not line:
                        break  # dump1090 closed the connection
                    self._parse_sbs(line.decode(errors="ignore"))
                except asyncio.TimeoutError:
                    pass
                now = time.monotonic()
                if now - last_emit >= 1.0:
                    last_emit = now
                    self._prune(now)
                    self._emit(now)
        finally:
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
            await self._kill_proc()

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

    # --- SBS BaseStation CSV parsing ---------------------------------------
    def _parse_sbs(self, line: str) -> None:
        p = line.strip().split(",")
        if len(p) < 22 or p[0] != "MSG":
            return
        icao = p[4].strip()
        if not icao:
            return
        ac = self.aircraft.setdefault(icao, {"icao": icao})
        ac["t"] = time.monotonic()

        def num(idx: int, key: str, cast) -> None:
            v = p[idx].strip()
            if v:
                try:
                    ac[key] = cast(v)
                except ValueError:
                    pass

        cs = p[10].strip()
        if cs:
            ac["flight"] = cs
        num(11, "alt", lambda x: int(float(x)))
        num(12, "speed", lambda x: round(float(x)))
        num(13, "track", lambda x: round(float(x)))
        num(14, "lat", float)
        num(15, "lon", float)
        num(16, "vert_rate", lambda x: round(float(x)))
        sq = p[17].strip()
        if sq:
            ac["squawk"] = sq
        og = p[21].strip()
        if og:
            ac["ground"] = og in ("1", "-1")
        ac["msgs"] = ac.get("msgs", 0) + 1

    def _prune(self, now: float) -> None:
        stale = [k for k, v in self.aircraft.items() if now - v["t"] > STALE_SECONDS]
        for k in stale:
            del self.aircraft[k]

    def _aircraft_msg(self, now: float) -> dict:
        out = []
        for ac in self.aircraft.values():
            item = {"icao": ac["icao"], "age": round(now - ac["t"], 1)}
            for k in ("flight", "alt", "speed", "track", "lat", "lon",
                      "vert_rate", "squawk", "ground", "msgs"):
                if k in ac:
                    item[k] = ac[k]
            out.append(item)
        positioned = sum(1 for a in out if "lat" in a)
        return {"type": "aircraft", "aircraft": out,
                "count": len(out), "positioned": positioned}

    def _emit(self, now: float) -> None:
        self.manager.emit_json(self._aircraft_msg(now))

    def snapshot(self) -> list[dict]:
        return [self._aircraft_msg(time.monotonic())]
