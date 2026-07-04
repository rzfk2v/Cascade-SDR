"""DAB / DAB+ mode via welle-cli.

DAB is COFDM digital radio, so (like ADS-B/AIS) we don't decode it ourselves — we
run **welle-cli** (welle.io headless) which owns the dongle, tunes a Band III
*block*, and runs a small web server. We poll its ``/mux.json`` for the ensemble's
station list and forward it to the browser. The browser plays a chosen station
directly from welle-cli's ``/mp3/<sid>`` endpoint (welle-cli decodes on demand).

One block (~1.5 MHz) carries a whole *ensemble* of stations. Changing the block
restarts welle-cli. Subprocess mode (``owns_device = False``).
"""
from __future__ import annotations

import asyncio
import json
import shutil
import urllib.request

from app.modes.base import Mode

WEB_PORT = 7979


class DabMode(Mode):
    name = "dab"
    owns_device = False
    default_center_freq = 227_360_000.0   # Band III block 12C (the default); display only

    def __init__(self, manager) -> None:
        super().__init__(manager)
        self.channel = "12C"
        self._proc: asyncio.subprocess.Process | None = None
        self._restart = asyncio.Event()
        self._latest = self._empty()

    def _empty(self) -> dict:
        return {"type": "dab_ensemble", "channel": self.channel, "ensemble": "",
                "snr": 0.0, "services": [], "web_port": WEB_PORT}

    def configure(self, params: dict) -> None:
        if params.get("channel"):
            ch = str(params["channel"]).strip().upper()
            if ch != self.channel:
                self.channel = ch
                self._latest = self._empty()
                self._restart.set()  # make run() respawn welle-cli on the new block

    @staticmethod
    def _exe() -> str | None:
        return shutil.which("welle-cli")

    async def run(self) -> None:
        if self._exe() is None:
            self.manager.emit_json({
                "type": "error",
                "message": "welle-cli not found. Build welle.io (see README).",
            })
            return
        try:
            while True:  # (re)spawn loop — re-enters when the block changes
                self._restart.clear()
                await self._run_channel()
                if not self._restart.is_set():
                    break  # welle-cli exited and no channel change requested
        finally:
            await self._kill_proc()

    async def _run_channel(self) -> None:
        cmd = [self._exe(), "-c", self.channel, "-w", str(WEB_PORT)]
        if self.manager.gain != "auto":
            cmd += ["-g", str(self.manager.gain)]
        self._proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        err = self._watch_stderr(self._proc)
        self.manager.emit_json({"type": "dab_status",
                                "message": f"tuning block {self.channel}…"})
        loop = asyncio.get_running_loop()
        try:
            while not self._restart.is_set():
                if self._proc.returncode is not None:
                    self.manager.emit_json({
                        "type": "error",
                        "message": self._exit_error(
                            f"welle-cli (block {self.channel})", err),
                    })
                    return
                await asyncio.sleep(1.5)
                data = await loop.run_in_executor(None, self._fetch_mux)
                if data is not None:
                    self._latest = self._parse(data)
                    self.manager.emit_json(self._latest)
        finally:
            await self._kill_proc()

    def _fetch_mux(self) -> dict | None:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{WEB_PORT}/mux.json", timeout=2
            ) as r:
                return json.loads(r.read().decode())
        except Exception:
            return None  # server not up yet / transient

    def _parse(self, data: dict) -> dict:
        services = []
        for s in data.get("services", []):
            label = ""
            lab = s.get("label")
            if isinstance(lab, dict):
                label = (lab.get("label") or "").strip()
            if not label:
                continue
            # audio services have a component with an audio service-component type
            comps = s.get("components", []) or []
            is_audio = any(c.get("ascty") is not None for c in comps)
            if not is_audio:
                continue
            sid = s.get("sid")
            if isinstance(sid, str):
                try:
                    sid = int(sid, 0)   # welle-cli may report "0xE241"-style SIDs
                except ValueError:
                    sid = None
            mp3 = s.get("url_mp3")
            if not mp3 and sid is not None:
                mp3 = f"/mp3/{sid & 0xFFFF:04x}"
            # DLS ("dynamic label": now playing / programme info). welle-cli only
            # decodes it for services it is actually decoding, i.e. the one being
            # streamed — the rest stay empty until played.
            dls = ""
            dl = s.get("dls")
            if isinstance(dl, dict):
                dls = (dl.get("label") or "").strip()
            services.append({"sid": sid, "label": label, "mp3": mp3, "dls": dls})
        ens = ""
        el = data.get("ensemble", {}).get("label")
        if isinstance(el, dict):
            ens = (el.get("label") or "").strip()
        snr = data.get("demodulator", {}).get("snr", 0.0) or 0.0
        return {"type": "dab_ensemble", "channel": self.channel, "ensemble": ens,
                "snr": round(float(snr), 1), "services": services, "web_port": WEB_PORT}

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

    def snapshot(self) -> list[dict]:
        return [self._latest]
