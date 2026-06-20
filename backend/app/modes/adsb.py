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
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from app.modes.base import Mode

# A proper airline callsign is an ICAO 3-letter prefix + flight number (e.g.
# "BAW123", "SAS1"). We only look those up: registrations and ad-hoc IDs
# ("N512QS", "SEABC", military tags) have no scheduled route and otherwise
# produce spurious matches in the route database.
CALLSIGN_RE = re.compile(r"^[A-Z]{3}\d{1,4}[A-Z]?$")

# Free flight-route database (callsign -> origin/destination airports). Only
# queried when the user opts in via the "routes" toggle; the route is NOT part
# of the ADS-B signal, so this is the only way to know where a flight is going.
ROUTE_API = "https://api.adsbdb.com/v0/callsign/"
ROUTE_MAX_INFLIGHT = 6   # cap concurrent lookups so the 1 s loop never stalls

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
    default_center_freq = 1_090_000_000.0   # Mode S / ADS-B; display only

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self._proc: asyncio.subprocess.Process | None = None
        self._jsondir: str | None = None
        self._latest = {"type": "aircraft", "aircraft": [], "count": 0, "positioned": 0}
        # Route lookups (opt-in): callsign -> route dict, or None once we've
        # learned the route is unknown (negative-cached to avoid re-querying).
        self._routes_enabled = False
        self._route_cache: dict[str, dict | None] = {}
        self._route_inflight: set[str] = set()
        self._route_tasks: set[asyncio.Task] = set()

    def configure(self, params: dict) -> None:
        if "routes" in params:
            self._routes_enabled = bool(params["routes"])
            self.manager.emit_json(
                {"type": "adsb_config", "routes": self._routes_enabled}
            )

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
            stderr=asyncio.subprocess.PIPE,
        )
        err = self._watch_stderr(self._proc)
        self.manager.emit_json({"type": "adsb_status", "message": "starting dump1090…"})
        path = os.path.join(self._jsondir, "aircraft.json")

        try:
            announced = False
            while True:
                if self._proc.returncode is not None:
                    raise RuntimeError(self._exit_error("dump1090", err))
                await asyncio.sleep(1.0)
                msg = self._read(path)
                if msg is not None:
                    if self._routes_enabled:
                        self._apply_routes()
                        self._schedule_route_lookups()
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
            # Vertical rate: prefer barometric, fall back to geometric (GNSS).
            rate = a.get("baro_rate")
            if rate is None:
                rate = a.get("geom_rate")
            if rate is not None:
                item["vert_rate"] = round(rate)
            if a.get("squawk"):
                item["squawk"] = a["squawk"]
            cat = a.get("category")
            if cat:
                item["type"] = AC_CATEGORY.get(cat, cat)
            # Registration ("r") and ICAO type designator ("t", e.g. B738) come
            # from dump1090-fa's aircraft database when that build is in use.
            reg = (a.get("r") or "").strip()
            if reg:
                item["reg"] = reg
            actype = (a.get("t") or "").strip()
            if actype:
                item["actype"] = actype
            if a.get("messages") is not None:
                item["msgs"] = a["messages"]
            if a.get("seen") is not None:
                item["age"] = round(a["seen"], 1)
            out.append(item)

        positioned = sum(1 for a in out if "lat" in a)
        self._latest = {"type": "aircraft", "aircraft": out,
                        "count": len(out), "positioned": positioned}
        return self._latest

    # --- route lookups (opt-in) --------------------------------------------
    def _apply_routes(self) -> None:
        """Attach cached origin/destination to the current aircraft snapshot."""
        for item in self._latest["aircraft"]:
            r = self._route_cache.get(item.get("flight", ""))
            if r:
                item["origin"] = r["from"]
                item["origin_name"] = r["from_name"]
                item["destination"] = r["to"]
                item["dest_name"] = r["to_name"]
                if r.get("airline"):
                    item["airline"] = r["airline"]

    def _schedule_route_lookups(self) -> None:
        """Fire background fetches for callsigns we haven't resolved yet."""
        for item in self._latest["aircraft"]:
            cs = item.get("flight")
            if not cs or cs in self._route_cache or cs in self._route_inflight:
                continue
            if not CALLSIGN_RE.match(cs):
                continue  # not an airline callsign — no scheduled route
            if len(self._route_inflight) >= ROUTE_MAX_INFLIGHT:
                break
            self._route_inflight.add(cs)
            task = asyncio.create_task(self._fetch_route(cs))
            self._route_tasks.add(task)
            task.add_done_callback(self._route_tasks.discard)

    async def _fetch_route(self, cs: str) -> None:
        try:
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(None, self._http_get_route, cs)
            # res: route dict (found), "" (known-unknown), or None (transient
            # error — leave uncached so we retry on a later tick).
            if res or res == "":
                self._route_cache[cs] = res or None
        finally:
            self._route_inflight.discard(cs)

    @staticmethod
    def _http_get_route(cs: str) -> dict | str | None:
        url = ROUTE_API + urllib.parse.quote(cs)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Cascade-SDR"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as e:
            # 404 (and other 4xx) = unknown callsign; negative-cache it so we
            # don't re-query every tick. 5xx/timeouts fall through as transient.
            return "" if 400 <= e.code < 500 else None
        except Exception:
            return None  # network/timeout — retry later
        fr = payload.get("response")
        if not isinstance(fr, dict):
            return ""  # "unknown callsign"
        fr = fr.get("flightroute")
        if not fr:
            return ""
        o = fr.get("origin") or {}
        d = fr.get("destination") or {}
        airline = (fr.get("airline") or {}).get("name") or ""

        def code(x: dict) -> str:
            return x.get("iata_code") or x.get("icao_code") or "?"

        def place(x: dict) -> str:
            return x.get("municipality") or x.get("name") or ""

        return {"from": code(o), "from_name": place(o),
                "to": code(d), "to_name": place(d), "airline": airline}

    def snapshot(self) -> list[dict]:
        return [self._latest, {"type": "adsb_config", "routes": self._routes_enabled}]
