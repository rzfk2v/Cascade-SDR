"""APRS mode — plot packet-radio stations on the map.

APRS rides on AX.25 packet over 1200-baud Bell-202 AFSK (144.800 MHz in EU,
144.390 in NA). We don't demodulate it ourselves: we pipe ``rtl_fm`` (NBFM audio)
into **direwolf** (the standard soundcard TNC), read the decoded packets it prints
in TNC2 form, parse them with ``aprslib``, aggregate by station callsign, and
forward positions to the browser for plotting — same idea as ADS-B / AIS.

Subprocess mode (``owns_device = False``): the piped ``rtl_fm`` owns the dongle;
:meth:`run` is cancelled on mode switch and the process group is killed so the
dongle is freed.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import time

import aprslib

from app.modes.base import Mode

STALE_SECONDS = 1800.0   # beacons are infrequent (minutes); keep stations a while
AUDIO_RATE = 22_050

# A TNC2 packet: SRCCALL[-ssid]>DEST[,path,...]:payload  (direwolf may prefix it
# with a "[chan.level]" tag and an audio-level line, which we ignore).
_TNC2 = re.compile(r"([A-Z0-9]{1,6}(?:-\d{1,2})?>[A-Z0-9]{1,6}(?:-\d{1,2})?"
                   r"(?:,[A-Z0-9*-]+)*:.+)")


def extract_tnc2(line: str) -> str | None:
    """Pull a TNC2 packet string out of a direwolf monitor line, if present."""
    m = _TNC2.search(line)
    return m.group(1) if m else None


class AprsMode(Mode):
    name = "aprs"
    owns_device = False
    default_center_freq = 144_800_000.0

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self.stations: dict[str, dict] = {}
        self._proc: asyncio.subprocess.Process | None = None

    @staticmethod
    def _have_tools() -> tuple[str | None, str | None]:
        return shutil.which("rtl_fm"), shutil.which("direwolf")

    def _cmd(self) -> str:
        gain = (f"-g {int(self.manager.gain)}"
                if isinstance(self.manager.gain, (int, float)) else "")
        ppm = (f"-p {int(self.manager.freq_correction)}"
               if self.manager.freq_correction else "")
        freq = int(self.manager.center_freq)
        # rtl_fm: NBFM audio at 22050; squelch off (-l 0). direwolf: 1200-baud
        # AFSK from stdin, -t 0 disables colour codes for clean parsing.
        return (f"rtl_fm -f {freq} -M fm -s {AUDIO_RATE} -l 0 {gain} {ppm} - "
                f"| direwolf -t 0 -r {AUDIO_RATE} -B 1200 -")

    async def run(self) -> None:
        rtl, dw = self._have_tools()
        if rtl is None or dw is None:
            missing = "rtl_fm" if rtl is None else "direwolf"
            self.manager.emit_json({
                "type": "error",
                "message": f"{missing} not found. Install direwolf (brew install direwolf).",
            })
            return

        self._proc = await asyncio.create_subprocess_shell(
            self._cmd(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,   # own process group, so we can kill the pipe
        )
        self.manager.emit_json({"type": "aprs_status",
                                "message": "direwolf running · 144.800 MHz"})
        try:
            last_emit = 0.0
            while True:
                if self._proc.returncode is not None:
                    raise RuntimeError("rtl_fm/direwolf exited — dongle free?")
                try:
                    raw = await asyncio.wait_for(self._proc.stdout.readline(), 0.5)
                    if raw:
                        self._on_line(raw.decode(errors="ignore").strip())
                except asyncio.TimeoutError:
                    pass
                now = time.monotonic()
                if now - last_emit >= 1.0:
                    last_emit = now
                    self._prune(now)
                    self._emit(now)
        finally:
            await self._kill_proc()

    async def _kill_proc(self) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        try:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            await asyncio.wait_for(self._proc.wait(), timeout=3.0)
        except Exception:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
            except Exception:
                pass

    # --- packet parsing -----------------------------------------------------
    def _on_line(self, line: str) -> None:
        pkt = extract_tnc2(line)
        if not pkt:
            return
        try:
            d = aprslib.parse(pkt)
        except Exception:
            return
        call = d.get("from")
        if not call:
            return
        s = self.stations.setdefault(call, {"call": call})
        s["t"] = time.monotonic()
        lat, lon = d.get("latitude"), d.get("longitude")
        if lat is not None and lon is not None:
            s["lat"], s["lon"] = round(lat, 5), round(lon, 5)
        for src, dst in (("comment", "comment"), ("speed", "speed"),
                         ("course", "course"), ("altitude", "altitude"),
                         ("symbol", "symbol")):
            if d.get(src) not in (None, ""):
                s[dst] = d[src]
        pt = d.get("packet_type") or d.get("format")
        if pt:
            s["kind"] = str(pt)

    def _prune(self, now: float) -> None:
        for k in [k for k, v in self.stations.items() if now - v["t"] > STALE_SECONDS]:
            del self.stations[k]

    def _stations_msg(self, now: float) -> dict:
        out = []
        for s in self.stations.values():
            item = {"call": s["call"], "age": round(now - s["t"], 1)}
            for k in ("lat", "lon", "comment", "speed", "course", "altitude",
                      "symbol", "kind"):
                if k in s:
                    item[k] = s[k]
            out.append(item)
        positioned = sum(1 for s in out if "lat" in s)
        return {"type": "stations", "stations": out,
                "count": len(out), "positioned": positioned}

    def _emit(self, now: float) -> None:
        self.manager.emit_json(self._stations_msg(now))

    def snapshot(self) -> list[dict]:
        return [self._stations_msg(time.monotonic())]
