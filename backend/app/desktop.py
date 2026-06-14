"""Native desktop wrapper for Cascade SDR.

Runs the FastAPI backend on a local port in a background thread and opens it in
a native OS window via ``pywebview`` — so Cascade SDR launches like a normal
desktop app instead of a terminal + browser tab. The backend still serves the
*built* frontend, so build it once first:

    cd frontend && npm run build

Then launch the desktop app from the backend venv:

    cd backend && ./.venv/bin/python -m app.desktop

``pywebview`` is an optional extra (it pulls a native GUI toolkit); install it
with ``pip install -r requirements-desktop.txt``. The plain browser/server run
(``uvicorn app.main:app``) does not need it.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn

from app.main import app

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"


def _serve() -> None:
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def _wait_until_up(timeout: float = 15.0) -> bool:
    """Poll the status endpoint until the server answers (or we give up)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urlopen(f"{URL}/api/status", timeout=0.5).read()
            return True
        except (URLError, OSError):
            time.sleep(0.1)
    return False


def main() -> None:
    import webview  # imported here so the server-only install doesn't need it

    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if not dist.is_dir():
        raise SystemExit(
            "frontend/dist not found — build the UI first:\n"
            "    cd frontend && npm run build"
        )

    # Backend on a daemon thread; it dies with the process when the window closes.
    threading.Thread(target=_serve, daemon=True).start()
    if not _wait_until_up():
        raise SystemExit("backend did not start in time")

    webview.create_window(
        "Cascade SDR", URL, width=1280, height=820, min_size=(960, 640)
    )
    # pywebview defaults to private mode, which wipes the web view's localStorage
    # on every launch — losing the user's bookmarks, receiver position, and other
    # saved settings. Disable it and point at a persistent directory so they stick
    # across sessions (same idea as a browser profile).
    storage = Path.home() / ".cascade-sdr" / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    webview.start(private_mode=False, storage_path=str(storage))


if __name__ == "__main__":
    main()
