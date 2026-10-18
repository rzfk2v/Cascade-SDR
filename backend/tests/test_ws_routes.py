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


def test_recording_delete_removes_only_a_listed_recording(tmp_path, monkeypatch) -> None:
    """Deletes look the name up among the listed recordings; nothing the client
    sends is used as a path, so traversal has nothing to work with."""
    import app.main as main
    monkeypatch.setattr(main, "RECORDINGS_DIR", tmp_path)
    keep = tmp_path.parent / "outside.cu8"
    keep.write_bytes(b"x")
    (tmp_path / "iq_1_100Hz_2400000sps.cu8").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    with TestClient(app) as client:
        # The router splits an encoded "/" into two segments, so this one may
        # not even reach the handler (405 from the static mount) — refused
        # either way, and the file outside is checked below.
        assert client.delete("/api/recordings/..%2Foutside.cu8").status_code in (400, 404, 405)
        assert client.delete("/api/recordings/..%5Coutside.cu8").status_code == 400
        assert client.delete("/api/recordings/notes.txt").status_code == 400
        assert client.delete("/api/recordings/missing.cu8").status_code == 400
        assert client.delete("/api/recordings/iq_1_100Hz_2400000sps.cu8").status_code == 200
    assert not (tmp_path / "iq_1_100Hz_2400000sps.cu8").exists()
    assert keep.exists() and (tmp_path / "notes.txt").exists()


def test_satellite_delete_removes_a_listed_image_in_a_subfolder(tmp_path, monkeypatch) -> None:
    import app.main as main
    monkeypatch.setattr(main, "SATELLITE_DIR", tmp_path)
    (tmp_path / "pass1").mkdir()
    (tmp_path / "pass1" / "msu_mr.png").write_bytes(b"x")
    with TestClient(app) as client:
        listed = client.get("/api/satellite").json()["products"]
        assert [p["path"] for p in listed] == ["pass1/msu_mr.png"]
        assert client.delete("/api/satellite/pass1/msu_mr.png").status_code == 200
    assert not (tmp_path / "pass1" / "msu_mr.png").exists()
