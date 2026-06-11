"""SDR-Ultra backend entry point.

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

from app.device import DeviceManager
from app.hub import Hub
from app.modes.base import Mode
from app.modes.radio import RadioMode
from app.modes.scan import ScanMode
from app.modes.spectrum import SpectrumMode

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sdr.main")

# Registry of selectable modes. Milestones add entries here (radio, adsb, ais).
MODE_REGISTRY: dict[str, type[Mode]] = {
    SpectrumMode.name: SpectrumMode,
    RadioMode.name: RadioMode,
    ScanMode.name: ScanMode,
}

app = FastAPI(title="SDR-Ultra")
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


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    await hub.register(ws)
    await ws.send_json(manager.status())
    for snap in manager.mode_snapshot():  # sync a reconnecting client's UI
        await ws.send_json(snap)
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
            await ws.send_json({"type": "pong"})
        elif cmd == "status":
            await ws.send_json(manager.status())
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
            )
        elif cmd == "config":
            manager.configure_mode(msg.get("params", {}))
        else:
            await ws.send_json({"type": "error", "message": f"unknown cmd: {cmd}"})
    except Exception as exc:
        await ws.send_json({"type": "error", "message": str(exc)})


# --- static frontend (optional; present after `npm run build`) --------------
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")
