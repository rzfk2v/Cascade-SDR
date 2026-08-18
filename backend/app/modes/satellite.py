"""Satellite imagery via SatDump — **experimental**.

Weather satellites moved on from analog APT: the low-cost 137 MHz slot is now
Meteor-M's **LRPT**, which is QPSK carrying a CCSDS stack (Viterbi, Reed-Solomon,
framing) wrapped around JPEG-compressed imagery. That is a protocol stack rather
than a DSP chain, so — as with ADS-B, AIS and DAB — we don't decode it ourselves:
**SatDump** owns the dongle for the pass and writes finished images to a
directory, and we surface its progress and its products.

Subprocess mode (``owns_device = False``). Unlike the other subprocess modes,
the payload here is *files*: :meth:`run` watches the output directory and reports
images as they appear, so a long pass shows results while it is still going.

Experimental: SatDump's command line has moved between releases, so if a pass
produces nothing, check the exact flags against your installed build first —
``_cmd`` is deliberately the only place they appear.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from pathlib import Path

from app.modes.base import Mode

# Where finished products land. One directory per pass, so a session's images
# stay grouped; override to keep them off the SD card (see CASCADE_RECORDINGS_DIR).
SATELLITE_DIR = Path(os.environ.get("CASCADE_SATELLITE_DIR")
                     or Path(__file__).resolve().parents[2] / "data" / "satellite")

# Satellites we offer. `pipeline` is SatDump's own name for the decode chain, so
# adding HRPT or a geostationary format later is a row here plus a UI entry.
# The 72k and 80k entries are the same satellites at different symbol rates:
# Meteor-M has transmitted at both, and the wrong pipeline simply never locks —
# so if a pass decodes nothing, try the other one before blaming the antenna.
# Rate 1e6 is what SatDump's own pipeline definition expects.
SATELLITES: list[dict] = [
    {"id": "meteor_m2-4_lrpt", "label": "Meteor-M N2-4 · LRPT 72k",
     "pipeline": "meteor_m2-x_lrpt", "freq": 137_100_000.0, "rate": 1_000_000.0},
    {"id": "meteor_m2-3_lrpt", "label": "Meteor-M N2-3 · LRPT 72k",
     "pipeline": "meteor_m2-x_lrpt", "freq": 137_900_000.0, "rate": 1_000_000.0},
    {"id": "meteor_m2-4_lrpt80", "label": "Meteor-M N2-4 · LRPT 80k",
     "pipeline": "meteor_m2-x_lrpt_80k", "freq": 137_100_000.0, "rate": 1_000_000.0},
    {"id": "meteor_m2-3_lrpt80", "label": "Meteor-M N2-3 · LRPT 80k",
     "pipeline": "meteor_m2-x_lrpt_80k", "freq": 137_900_000.0, "rate": 1_000_000.0},
]

PRODUCT_SUFFIXES = (".png", ".jpg", ".jpeg")
POLL_SECONDS = 2.0

# SatDump reports progress on stdout; the wording varies by version, so pull out
# what we can and fall back to showing its last line verbatim.
_SNR = re.compile(r"SNR[^0-9\-]*(-?\d+(?:\.\d+)?)", re.I)
_FRAMES = re.compile(r"(\d+)\s+frames", re.I)
_LOCK = re.compile(r"\b(locked|lock)\b", re.I)


class SatelliteMode(Mode):
    name = "satellite"
    owns_device = False
    default_center_freq = 137_100_000.0    # display only; SatDump tunes itself

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self.sat_id = SATELLITES[0]["id"]
        self._proc: asyncio.subprocess.Process | None = None
        self._restart = asyncio.Event()
        self._out: Path | None = None
        self._products: list[dict] = []
        self._status = "idle"
        self._snr: float | None = None
        self._frames = 0
        self._locked = False

    # --- configuration ------------------------------------------------------
    def configure(self, params: dict) -> None:
        sid = params.get("satellite")
        if sid and any(s["id"] == sid for s in SATELLITES) and sid != self.sat_id:
            self.sat_id = sid
            self._restart.set()          # respawn SatDump on the new pipeline

    @property
    def sat(self) -> dict:
        return next(s for s in SATELLITES if s["id"] == self.sat_id)

    @staticmethod
    def _exe() -> str | None:
        override = os.environ.get("SATDUMP_BIN")
        if override and os.access(override, os.X_OK):
            return override
        return shutil.which("satdump")

    @classmethod
    def _workdir(cls) -> Path | None:
        """Where to run SatDump from, or None to inherit ours.

        The macOS build ships as an .app bundle whose CLI looks for its config
        at a compiled-in ``/usr/local/share/satdump`` that the bundle does not
        use — it exits with "Couldn't load config file!". It does look in the
        working directory, so running it from the bundle's Resources directory
        fixes it. Linux packages install to the prefix they were built with and
        need none of this.
        """
        exe = cls._exe()
        if not exe:
            return None
        real = Path(exe).resolve()
        bundle = real.parent.parent / "Resources"       # <app>/Contents/Resources
        return bundle if (bundle / "pipelines").is_dir() else None

    def _cmd(self, out_dir: Path) -> list[str]:
        """The one place SatDump's command line lives — see the module docstring."""
        sat = self.sat
        cmd = [
            self._exe(), "live", sat["pipeline"], str(out_dir),
            "--source", "rtlsdr",
            "--frequency", str(int(sat["freq"])),
            "--samplerate", str(int(sat["rate"])),
        ]
        if isinstance(self.manager.gain, (int, float)):
            cmd += ["--gain", str(int(self.manager.gain))]
        if self.manager.freq_correction:
            cmd += ["--ppm_correction", str(int(self.manager.freq_correction))]
        return cmd

    # --- messages -----------------------------------------------------------
    def _config_msg(self) -> dict:
        return {
            "type": "sat_config",
            "satellites": [{"id": s["id"], "label": s["label"],
                            "mhz": s["freq"] / 1e6} for s in SATELLITES],
            "satellite": self.sat_id,
            "experimental": True,
        }

    def _status_msg(self, message: str) -> dict:
        return {"type": "sat_status", "message": message, "state": self._status,
                "snr": self._snr, "frames": self._frames, "locked": self._locked}

    def _products_msg(self) -> dict:
        return {"type": "sat_products", "products": self._products}

    def snapshot(self) -> list[dict]:
        return [self._config_msg(), self._status_msg(self._status),
                self._products_msg()]

    # --- lifetime -----------------------------------------------------------
    async def run(self) -> None:
        # Populate the UI before checking for the binary: someone who hasn't
        # installed SatDump yet should still see what the mode offers, and any
        # images from a previous pass.
        self.manager.emit_json(self._config_msg())
        self._scan_existing()
        self.manager.emit_json(self._products_msg())
        if self._exe() is None:
            self.manager.emit_json({
                "type": "error",
                "message": "satdump not found. Install SatDump (see the README).",
            })
            self._status = "missing satdump"
            self.manager.emit_json(self._status_msg(
                "SatDump is not installed — see the README"))
            return
        try:
            while True:
                self._restart.clear()
                await self._run_pass()
                if not self._restart.is_set():
                    break
        finally:
            await self._kill_proc()

    def _scan_existing(self) -> None:
        """List images from earlier passes, so the panel isn't blank on entry."""
        prev, self._out = self._out, SATELLITE_DIR
        self._scan_products()
        self._out = prev

    async def _run_pass(self) -> None:
        sat = self.sat
        self._out = (SATELLITE_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}_{sat['id']}").resolve()
        self._out.mkdir(parents=True, exist_ok=True)
        self._products = []
        self._snr, self._frames, self._locked = None, 0, False
        self._status = "running"

        self._proc = await asyncio.create_subprocess_exec(
            *self._cmd(self._out),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._workdir(),          # see _workdir: macOS .app bundles
        )
        err = self._watch_stderr(self._proc)
        self.manager.emit_json(self._status_msg(
            f"SatDump running · {sat['label']} · {sat['freq']/1e6:.3f} MHz"))

        last_poll = 0.0
        try:
            while not self._restart.is_set():
                if self._proc.returncode is not None:
                    # A pass ending is normal; report what it produced either way.
                    self._status = "finished"
                    self._scan_products()
                    msg = (f"pass finished · {len(self._products)} image(s)"
                           if self._products
                           else self._exit_error("satdump", err))
                    self.manager.emit_json(self._status_msg(msg))
                    self.manager.emit_json(self._products_msg())
                    return
                try:
                    raw = await asyncio.wait_for(self._proc.stdout.readline(), 0.5)
                    if raw:
                        self._on_line(raw.decode(errors="ignore").strip())
                except asyncio.TimeoutError:
                    pass
                now = time.monotonic()
                if now - last_poll >= POLL_SECONDS:
                    last_poll = now
                    if self._scan_products():
                        self.manager.emit_json(self._products_msg())
        finally:
            await self._kill_proc()

    def _on_line(self, line: str) -> None:
        if not line:
            return
        changed = False
        m = _SNR.search(line)
        if m:
            snr = round(float(m.group(1)), 1)
            changed |= snr != self._snr
            self._snr = snr
        m = _FRAMES.search(line)
        if m:
            frames = int(m.group(1))
            changed |= frames != self._frames
            self._frames = frames
        if _LOCK.search(line) and not self._locked:
            self._locked, changed = True, True
        if changed:
            bits = []
            if self._locked:
                bits.append("locked")
            if self._snr is not None:
                bits.append(f"SNR {self._snr:.1f} dB")
            if self._frames:
                bits.append(f"{self._frames:,} frames")
            self.manager.emit_json(self._status_msg(" · ".join(bits) or line[:120]))

    def _scan_products(self) -> bool:
        """Pick up images written so far. True if the list changed."""
        if self._out is None or not self._out.is_dir():
            return False
        found = []
        for p in sorted(self._out.rglob("*")):
            if p.suffix.lower() in PRODUCT_SUFFIXES and p.is_file():
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                if size <= 0:                     # still being written
                    continue
                found.append({"name": p.name,
                              "path": str(p.relative_to(SATELLITE_DIR)),
                              "size": size})
        if found == self._products:
            return False
        self._products = found
        return True

    async def _kill_proc(self) -> None:
        self._cancel_stderr_watch()
        if self._proc is None or self._proc.returncode is not None:
            return
        try:
            # SIGTERM lets SatDump finish writing the products it already has.
            self._proc.terminate()
            await asyncio.wait_for(self._proc.wait(), timeout=8.0)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
