"""Cascade SDR backend entry point.

Serves:
  * ``/ws``        — WebSocket: JSON commands in, JSON status + binary streams out.
  * ``/api/status``— one-shot status (handy for health checks / curl).
  * ``/``          — the built frontend, if present (``frontend/dist``).

Run during development with:
    uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from fastapi import HTTPException

from app.device import RECORDINGS_DIR, DeviceManager
from app.hub import Hub
from app.modes.adsb import AdsbMode
from app.modes.ais import AisMode
from app.modes.acars import AcarsMode
from app.modes.aprs import AprsMode
from app.modes.apt import AptMode
from app.modes.base import Mode
from app.modes.dab import DabMode
from app.modes.ism import IsmMode
from app.modes.pager import PagerMode
from app.modes.radio import RadioMode
from app.modes.replay import ReplayMode
from app.modes.scan import ScanMode
from app.modes.scanner import ScannerMode
from app.modes.spectrum import SpectrumMode
from app.modes.sstv import SstvMode

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sdr.main")

# Registry of selectable modes. Milestones add entries here (radio, adsb, ais).
MODE_REGISTRY: dict[str, type[Mode]] = {
    SpectrumMode.name: SpectrumMode,
    RadioMode.name: RadioMode,
    ReplayMode.name: ReplayMode,
    ScanMode.name: ScanMode,
    ScannerMode.name: ScannerMode,
    AdsbMode.name: AdsbMode,
    AisMode.name: AisMode,
    AprsMode.name: AprsMode,
    AcarsMode.name: AcarsMode,
    AptMode.name: AptMode,
    SstvMode.name: SstvMode,
    PagerMode.name: PagerMode,
    DabMode.name: DabMode,
    IsmMode.name: IsmMode,
}

app = FastAPI(title="Cascade SDR")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local dev: Vite dev server on a different port
    allow_methods=["*"],
    allow_headers=["*"],
)

hub = Hub()
manager = DeviceManager(hub)


@app.get("/api/status")
async def api_status() -> dict:
    return manager.status()


@app.get("/api/modes")
async def api_modes() -> dict:
    return {"modes": list(MODE_REGISTRY.keys())}


@app.get("/api/recordings")
async def api_recordings() -> dict:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(RECORDINGS_DIR.glob("*.cu8"), key=lambda x: x.stat().st_mtime,
                    reverse=True):
        st = p.stat()
        items.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime})
    return {"recordings": items, "recording": manager.recording}


@app.delete("/api/recordings/{name}")
async def delete_recording(name: str) -> dict:
    # guard against path traversal — only allow plain .cu8 names in the dir
    target = (RECORDINGS_DIR / name).resolve()
    if target.parent != RECORDINGS_DIR.resolve() or target.suffix != ".cu8":
        raise HTTPException(status_code=400, detail="bad name")
    target.unlink(missing_ok=True)
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    await hub.register(ws)
    # Per-client sends go through the hub queue: the sender task is the only
    # writer on the socket, so these can't interleave with broadcast frames.
    await hub.send_json(ws, manager.status())
    for snap in manager.mode_snapshot():  # sync a reconnecting client's UI
        await hub.send_json(ws, snap)
    try:
        while True:
            msg = await ws.receive_json()
            await handle_command(ws, msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("websocket error")
    finally:
        await hub.unregister(ws)


async def handle_command(ws: WebSocket, msg: dict) -> None:
    cmd = msg.get("cmd")
    try:
        if cmd == "ping":
            await hub.send_json(ws, {"type": "pong"})
        elif cmd == "status":
            await hub.send_json(ws, manager.status())
        elif cmd == "set_mode":
            await manager.set_mode(msg["mode"], MODE_REGISTRY)
        elif cmd == "start":
            await manager.start()
        elif cmd == "stop":
            await manager.stop()
        elif cmd == "tune":
            await manager.retune(
                center_freq=msg.get("center_freq"),
                sample_rate=msg.get("sample_rate"),
                gain=msg.get("gain"),
                ppm=msg.get("ppm"),
                bias_tee=msg.get("bias_tee"),
                converter_on=msg.get("converter_on"),
                converter_hz=msg.get("converter_hz"),
                direct_sampling=msg.get("direct_sampling"),
                tuner_bw=msg.get("tuner_bw"),
                rtl_agc=msg.get("rtl_agc"),
            )
        elif cmd == "config":
            manager.configure_mode(msg.get("params", {}))
        elif cmd == "record":
            if msg.get("action") == "start":
                res = manager.record_start()
                await hub.broadcast_json(
                    {"type": "rec_status", "recording": manager.recording, **res}
                )
            else:
                name = manager.record_stop()
                await hub.broadcast_json(
                    {"type": "rec_status", "recording": False, "stopped": name}
                )
        else:
            await hub.send_json(ws, {"type": "error", "message": f"unknown cmd: {cmd}"})
    except Exception as exc:
        await hub.send_json(ws, {"type": "error", "message": str(exc)})


# --- IQ recordings download (mounted before the catch-all frontend) ---------
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/recordings", StaticFiles(directory=str(RECORDINGS_DIR)), name="recordings")

# --- static frontend (optional; present after `npm run build`) --------------
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")
