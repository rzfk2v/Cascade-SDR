"""AIS mode — ship positions via AIS-catcher.

Like ADS-B, we don't demodulate AIS ourselves: we spawn **AIS-catcher** (which
owns the dongle and listens on the two marine AIS channels around 162 MHz) and
have it emit NMEA (AIVDM) sentences over UDP to us. We reassemble multi-fragment
sentences, decode them with ``pyais``, aggregate by MMSI into per-vessel state,
and forward the list to the browser for plotting on the map.

Subprocess mode (``owns_device = False``): the DeviceManager cancels :meth:`run`
on mode switch; the ``finally`` kills AIS-catcher so the dongle is freed.
"""
from __future__ import annotations

import asyncio
import shutil
import time

from pyais import decode as ais_decode

from app.modes.base import Mode

UDP_HOST = "127.0.0.1"
UDP_PORT = 10110
STALE_SECONDS = 600.0  # vessels report slowly (anchored/Class B); keep them a while

_POS_TYPES = {1, 2, 3, 18, 19, 27}
_STATIC_TYPES = {5, 24}


class _NmeaProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_line) -> None:
        self.on_line = on_line

    def datagram_received(self, data: bytes, _addr) -> None:
        for line in data.decode(errors="ignore").splitlines():
            line = line.strip()
            if line:
                self.on_line(line)


class AisMode(Mode):
    name = "ais"
    owns_device = False

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self.vessels: dict[str, dict] = {}
        self._frags: dict[tuple, dict] = {}  # multipart reassembly buffer
        self._proc: asyncio.subprocess.Process | None = None

    @staticmethod
    def _exe() -> str | None:
        return shutil.which("AIS-catcher") or shutil.which("ais-catcher")

    def _cmd(self) -> list[str]:
        # -X off: do NOT upload received data to the aiscatcher.org community feed
        # (it is ON by default). -q: keep NMEA off the subprocess stdout.
        cmd = [self._exe(), "-u", UDP_HOST, str(UDP_PORT), "-q", "-X", "off"]
        if self.manager.freq_correction:
            cmd += ["-p", str(int(self.manager.freq_correction))]
        return cmd

    async def run(self) -> None:
        if self._exe() is None:
            self.manager.emit_json({
                "type": "error",
                "message": "AIS-catcher not found. Build it from source (see README).",
            })
            return

        loop = asyncio.get_running_loop()
        # listen for NMEA first, then start AIS-catcher pointing at us
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _NmeaProtocol(self._on_nmea), local_addr=(UDP_HOST, UDP_PORT)
        )
        self._proc = await asyncio.create_subprocess_exec(
            *self._cmd(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.manager.emit_json({"type": "ais_status", "message": "AIS-catcher running"})

        try:
            last_emit = 0.0
            while True:
                if self._proc.returncode is not None:
                    raise RuntimeError(
                        "AIS-catcher exited — is the dongle free and connected?"
                    )
                await asyncio.sleep(0.25)
                now = time.monotonic()
                if now - last_emit >= 1.0:
                    last_emit = now
                    self._prune(now)
                    self._emit(now)
        finally:
            transport.close()
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

    # --- NMEA reassembly + decode ------------------------------------------
    def _on_nmea(self, line: str) -> None:
        if not line.startswith("!"):
            return
        f = line.split(",")
        if len(f) < 7:
            return
        try:
            count, num = int(f[1]), int(f[2])
        except ValueError:
            return
        if count == 1:
            self._decode([line])
            return
        key = (f[3], f[4])  # sequence id + channel
        slot = self._frags.setdefault(key, {})
        slot[num] = line
        if len(slot) >= count:
            parts = [slot[i] for i in sorted(slot)]
            self._frags.pop(key, None)
            self._decode(parts)

    def _decode(self, parts: list[str]) -> None:
        try:
            d = ais_decode(*parts).asdict()
        except Exception:
            return
        mmsi = d.get("mmsi")
        if not mmsi:
            return
        v = self.vessels.setdefault(str(mmsi), {"mmsi": mmsi})
        v["t"] = time.monotonic()
        mtype = d.get("msg_type")

        if mtype in _POS_TYPES:
            lat, lon = d.get("lat"), d.get("lon")
            if lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180:
                v["lat"], v["lon"] = lat, lon
            spd = d.get("speed")
            if spd is not None and spd < 102.3:
                v["speed"] = round(spd, 1)
            crs = d.get("course")
            if crs is not None and crs < 360:
                v["course"] = round(crs)
            hdg = d.get("heading")
            if hdg is not None and hdg < 511:
                v["heading"] = hdg
        if mtype in _STATIC_TYPES:
            name = (d.get("shipname") or "").strip()
            if name:
                v["name"] = name
            if d.get("ship_type") is not None:
                v["ship_type"] = d["ship_type"]
            cs = (d.get("callsign") or "").strip()
            if cs:
                v["callsign"] = cs
            dest = (d.get("destination") or "").strip()
            if dest:
                v["dest"] = dest

    def _prune(self, now: float) -> None:
        for k in [k for k, v in self.vessels.items() if now - v["t"] > STALE_SECONDS]:
            del self.vessels[k]

    def _vessels_msg(self, now: float) -> dict:
        out = []
        for v in self.vessels.values():
            item = {"mmsi": v["mmsi"], "age": round(now - v["t"], 1)}
            for k in ("name", "lat", "lon", "speed", "course", "heading",
                      "ship_type", "callsign", "dest"):
                if k in v:
                    item[k] = v[k]
            out.append(item)
        positioned = sum(1 for v in out if "lat" in v)
        return {"type": "vessels", "vessels": out,
                "count": len(out), "positioned": positioned}

    def _emit(self, now: float) -> None:
        self.manager.emit_json(self._vessels_msg(now))

    def snapshot(self) -> list[dict]:
        return [self._vessels_msg(time.monotonic())]
