"""Websocket routing: /ws is the only endpoint; stray paths are refused cleanly.

Regression test for GitHub issue #2, where a websocket upgrade to a path other
than /ws fell through to the StaticFiles catch-all mount and crashed the
handshake with an ASGI AssertionError (logged as a 500).
"""
import pytest
from starlette.testclient import TestClient

from app.main import app


def test_ws_connects_and_reports_status() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "status"


@pytest.mark.parametrize("path", ["/ws/", "/wss", "/sdr/ws", "/"])
def test_stray_ws_paths_are_refused_not_500(path: str) -> None:
    with TestClient(app) as client:
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect(path):
                pass
        # A clean refusal, not the StaticFiles assertion crash.
        assert not isinstance(exc_info.value, AssertionError)
