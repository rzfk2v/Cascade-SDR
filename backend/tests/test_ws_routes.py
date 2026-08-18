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


def test_satellite_products_endpoint_lists_nothing_by_default() -> None:
    with TestClient(app) as client:
        r = client.get("/api/satellite")
        assert r.status_code == 200
        assert isinstance(r.json()["products"], list)


@pytest.mark.parametrize("path", ["%2e%2e%2fsecrets.png", "pass1%2f..%2f..%2fetc%2fx.png"])
def test_satellite_delete_refuses_to_escape_its_directory(path: str) -> None:
    """The products directory is served publicly; deletes must stay inside it.

    Percent-encoded, because a literal "../" is normalised away by the router
    before the handler ever sees it — this is the form that actually arrives.
    """
    with TestClient(app) as client:
        assert client.delete(f"/api/satellite/{path}").status_code == 400


def test_satellite_delete_refuses_non_images() -> None:
    with TestClient(app) as client:
        assert client.delete("/api/satellite/pass1/frames.cadu").status_code == 400
