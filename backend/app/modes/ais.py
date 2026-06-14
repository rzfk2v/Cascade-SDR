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
import json
import shutil
import time
from pathlib import Path

from pyais import decode as ais_decode

from app.modes.ais_mid import iso2_for_mmsi
from app.modes.base import Mode

UDP_HOST = "127.0.0.1"
UDP_PORT = 10110
STALE_SECONDS = 600.0  # vessels report slowly (anchored/Class B); keep them a while

# Persistent name/note cache. Ship names ride in static messages sent only every
# ~6 min, so each session would otherwise start blank; caching MMSI -> name (and
# a user note) lets known vessels show their name the instant they're heard again.
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "ais_cache.json"
CACHE_FLUSH_SECONDS = 15.0           # coalesce disk writes; flush at most this often
# Permanent ship attributes worth remembering between sessions (not voyage data
# like draught/destination/ETA, which change every trip).
_CACHE_KEYS = ("name", "callsign", "ship_type", "type", "imo", "length", "width")

# AIS navigation-status codes (0–15); 9/10/13 reserved and 15 undefined -> omit.
NAV_STATUS = {
    0: "Under way (engine)", 1: "At anchor", 2: "Not under command",
    3: "Restricted manoeuvrability", 4: "Constrained by draught", 5: "Moored",
    6: "Aground", 7: "Fishing", 8: "Under way (sailing)",
    11: "Towing astern", 12: "Pushing ahead", 14: "AIS-SART / MOB / EPIRB",
}


def _format_eta(d: dict) -> str:
    """Build an ETA string from a type-5 month/day/hour/minute set, if present."""
    mo = d.get("month")
    if not mo or not (1 <= int(mo) <= 12):
        return ""                               # 0/None month = not available
    day = int(d.get("day") or 0)
    hr, mi = int(d.get("hour") or 24), int(d.get("minute") or 60)
    if hr >= 24 or mi >= 60:                     # 24:60 = time not available
        return f"{int(mo):02d}-{day:02d}"
    return f"{int(mo):02d}-{day:02d} {hr:02d}:{mi:02d}"


def _load_cache() -> dict[str, dict]:
    try:
        data = json.loads(CACHE_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def ship_type_label(code: int) -> str:
    """Map an AIS ship-and-cargo-type code (0-99) to a readable category."""
    if code in (30,):
        return "Fishing"
    if code in (31, 32, 52):
        return "Tug/Towing"
    if code == 33:
        return "Dredger"
    if code == 35:
        return "Military"
    if code == 36:
        return "Sailing"
    if code == 37:
        return "Pleasure craft"
    if 40 <= code <= 49:
        return "High-speed craft"
    if code == 50:
        return "Pilot vessel"
    if code == 51:
        return "Search & rescue"
    if code == 53:
        return "Port tender"
    if code == 55:
        return "Law enforcement"
    if 60 <= code <= 69:
        return "Passenger"
    if 70 <= code <= 79:
        return "Cargo"
    if 80 <= code <= 89:
        return "Tanker"
    if 90 <= code <= 99:
        return "Other"
    return ""


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
    default_center_freq = 162_000_000.0   # marine AIS (161.975 / 162.025); display only

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self.vessels: dict[str, dict] = {}
        self._frags: dict[tuple, dict] = {}  # multipart reassembly buffer
        self._proc: asyncio.subprocess.Process | None = None
        self._cache = _load_cache()          # MMSI -> {name, callsign, ..., comment}
        self._cache_dirty = False
        self._last_flush = 0.0

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
            stderr=asyncio.subprocess.PIPE,
        )
        err = self._watch_stderr(self._proc)
        self.manager.emit_json({"type": "ais_status", "message": "AIS-catcher running"})

        try:
            last_emit = 0.0
            while True:
                if self._proc.returncode is not None:
                    raise RuntimeError(self._exit_error("AIS-catcher", err))
                await asyncio.sleep(0.25)
                now = time.monotonic()
                if now - last_emit >= 1.0:
                    last_emit = now
                    self._prune(now)
                    self._emit(now)
                    if self._cache_dirty and now - self._last_flush >= CACHE_FLUSH_SECONDS:
                        self._flush_cache()
        finally:
            self._flush_cache()          # persist anything learned this session
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

    # --- name / note cache --------------------------------------------------
    def _enrich_from_cache(self, key: str, v: dict) -> None:
        """Fill a vessel's name/type/note from the cache without overwriting
        anything already learned on-air this session."""
        cached = self._cache.get(key)
        if not cached:
            return
        for k in _CACHE_KEYS:
            if k in cached and k not in v:
                v[k] = cached[k]
        if cached.get("comment"):
            v["comment"] = cached["comment"]

    def _learn(self, key: str, v: dict) -> None:
        """Persist a vessel's static fields into the cache (notes are untouched)."""
        entry = self._cache.setdefault(key, {})
        for k in _CACHE_KEYS:
            if k in v and entry.get(k) != v[k]:
                entry[k] = v[k]
                self._cache_dirty = True

    def _flush_cache(self) -> None:
        if not self._cache_dirty:
            return
        self._last_flush = time.monotonic()
        self._cache_dirty = False
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = CACHE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cache))
            tmp.replace(CACHE_PATH)       # atomic so a crash can't corrupt it
        except Exception:
            pass

    def configure(self, params: dict) -> None:
        """Client commands. ``set_comment``: save/clear a user note for an MMSI."""
        c = params.get("set_comment")
        if isinstance(c, dict) and c.get("mmsi") is not None:
            key = str(c["mmsi"])
            text = str(c.get("text", "")).strip()[:200]
            entry = self._cache.setdefault(key, {})
            if text:
                entry["comment"] = text
            else:
                entry.pop("comment", None)
            self._cache_dirty = True
            self._flush_cache()           # user action -> save immediately
            v = self.vessels.get(key)
            if v is not None:
                if text:
                    v["comment"] = text
                else:
                    v.pop("comment", None)
            self._emit(time.monotonic())  # reflect it in the UI right away

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
            # enum_as_int -> plain ints for status/turn/ship_type (no enum objects)
            d = ais_decode(*parts).asdict(enum_as_int=True)
        except Exception:
            return
        mmsi = d.get("mmsi")
        if not mmsi:
            return
        key = str(mmsi)
        v = self.vessels.setdefault(key, {"mmsi": mmsi})
        self._enrich_from_cache(key, v)   # show cached name/note without waiting on-air
        v["t"] = time.monotonic()

        # --- dynamic (position) fields: types 1/2/3/18/19/27 ----------------
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
        st = d.get("status")
        if isinstance(st, int) and st in NAV_STATUS:
            v["status"] = NAV_STATUS[st]
        turn = d.get("turn")
        if isinstance(turn, (int, float)) and -127 <= turn <= 127:
            v["turn"] = round(turn)        # rate of turn, °/min (±127 = hard over)

        # --- static / voyage fields: types 5 / 19 / 24 ----------------------
        # (dimensions + name can ride on the class-B position type 19 too, so we
        # read by field presence rather than gating on the message type)
        learned = False
        name = (d.get("shipname") or "").strip()
        if name:
            v["name"] = name; learned = True
        code = d.get("ship_type")
        if isinstance(code, int) and code:
            v["ship_type"] = code
            label = ship_type_label(code)
            if label:
                v["type"] = label
            learned = True
        cs = (d.get("callsign") or "").strip()
        if cs:
            v["callsign"] = cs; learned = True
        if d.get("imo"):
            v["imo"] = d["imo"]; learned = True
        bow, stern = d.get("to_bow"), d.get("to_stern")
        if bow is not None and stern is not None and (bow or stern):
            v["length"] = int(bow) + int(stern); learned = True
        port, star = d.get("to_port"), d.get("to_starboard")
        if port is not None and star is not None and (port or star):
            v["width"] = int(port) + int(star); learned = True
        dr = d.get("draught")
        if dr:
            v["draught"] = round(float(dr), 1)        # voyage-specific, not cached
        dest = (d.get("destination") or "").strip()
        if dest:
            v["dest"] = dest                          # voyage-specific, not cached
        eta = _format_eta(d)
        if eta:
            v["eta"] = eta                            # voyage-specific, not cached

        if learned:
            self._learn(key, v)   # remember the ship's identity for next session

    def _prune(self, now: float) -> None:
        for k in [k for k, v in self.vessels.items() if now - v["t"] > STALE_SECONDS]:
            del self.vessels[k]

    def _vessels_msg(self, now: float) -> dict:
        out = []
        for v in self.vessels.values():
            item = {"mmsi": v["mmsi"], "age": round(now - v["t"], 1)}
            for k in ("name", "lat", "lon", "speed", "course", "heading",
                      "ship_type", "type", "callsign", "dest", "comment",
                      "status", "turn", "imo", "length", "width", "draught", "eta"):
                if k in v:
                    item[k] = v[k]
            iso2 = iso2_for_mmsi(v["mmsi"])   # country from the MMSI's MID
            if iso2:
                item["country"] = iso2
            out.append(item)
        positioned = sum(1 for v in out if "lat" in v)
        return {"type": "vessels", "vessels": out,
                "count": len(out), "positioned": positioned}

    def _emit(self, now: float) -> None:
        self.manager.emit_json(self._vessels_msg(now))

    def snapshot(self) -> list[dict]:
        return [self._vessels_msg(time.monotonic())]
