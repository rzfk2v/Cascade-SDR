"""SatDump mode — experimental. Covers everything that doesn't need the binary.

The decode itself is SatDump's; what's ours is choosing the pipeline, building
its command line, spotting products as they land, and refusing to serve
anything outside the output directory.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.modes.satellite import SATELLITES, SatelliteMode


def _mode(gain=40.0, ppm=0) -> SatelliteMode:
    mgr = SimpleNamespace(gain=gain, freq_correction=ppm, center_freq=137.1e6,
                          sample_rate=1.024e6, json_msgs=[])
    mgr.emit_json = mgr.json_msgs.append
    return SatelliteMode(mgr)


def test_every_satellite_is_in_the_137_mhz_band() -> None:
    for s in SATELLITES:
        assert 137e6 <= s["freq"] <= 138e6, s
        assert s["pipeline"] and s["rate"] > 0, s


def test_command_line_carries_pipeline_frequency_and_rate(monkeypatch, tmp_path) -> None:
    m = _mode()
    monkeypatch.setattr(SatelliteMode, "_exe", staticmethod(lambda: "/usr/bin/satdump"))
    cmd = m._cmd(tmp_path)

    assert cmd[1:3] == ["live", m.sat["pipeline"]]
    assert str(tmp_path) in cmd
    assert "--frequency" in cmd and str(int(m.sat["freq"])) in cmd
    assert "--samplerate" in cmd and str(int(m.sat["rate"])) in cmd
    assert "--gain" in cmd and "40" in cmd


def test_auto_gain_is_left_to_satdump(monkeypatch, tmp_path) -> None:
    """'auto' is our UI's word, not a number to pass through."""
    m = _mode(gain="auto")
    monkeypatch.setattr(SatelliteMode, "_exe", staticmethod(lambda: "/usr/bin/satdump"))
    assert "--gain" not in m._cmd(tmp_path)


def test_selecting_another_satellite_retunes_and_asks_for_a_restart() -> None:
    m = _mode()
    other = SATELLITES[1]["id"]
    m.configure({"satellite": other})
    assert m.sat_id == other
    assert m._restart.is_set()


def test_an_unknown_satellite_is_ignored() -> None:
    m = _mode()
    before = m.sat_id
    m.configure({"satellite": "not-a-satellite"})
    assert m.sat_id == before
    assert not m._restart.is_set()


def test_products_are_picked_up_as_they_land(tmp_path, monkeypatch) -> None:
    import app.modes.satellite as sat
    monkeypatch.setattr(sat, "SATELLITE_DIR", tmp_path)
    m = _mode()
    m._out = tmp_path / "pass1"
    (m._out / "MSU-MR").mkdir(parents=True)

    assert m._scan_products() is False           # nothing yet

    img = m._out / "MSU-MR" / "rgb.png"
    img.write_bytes(b"\x89PNG fake")
    assert m._scan_products() is True
    assert m._products[0]["name"] == "rgb.png"
    assert m._products[0]["path"].startswith("pass1/")
    assert m._scan_products() is False           # unchanged -> no re-emit


def test_partial_and_non_image_files_are_skipped(tmp_path, monkeypatch) -> None:
    import app.modes.satellite as sat
    monkeypatch.setattr(sat, "SATELLITE_DIR", tmp_path)
    m = _mode()
    m._out = tmp_path / "pass2"
    m._out.mkdir(parents=True)
    (m._out / "empty.png").write_bytes(b"")      # still being written
    (m._out / "frames.cadu").write_bytes(b"data")

    assert m._scan_products() is False
    assert m._products == []


def test_snapshot_tells_a_new_client_it_is_experimental() -> None:
    m = _mode()
    kinds = [s["type"] for s in m.snapshot()]
    assert kinds == ["sat_config", "sat_status", "sat_products"]
    assert m.snapshot()[0]["experimental"] is True
