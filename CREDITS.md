# Credits & third-party licenses

Cascade SDR (© 2026 Jens Engfors) is licensed under **GPL-3.0** (see `LICENSE`).
It builds on the following open-source projects — thanks to their authors.

## Python backend (imported)

| Project | License | Use |
|---|---|---|
| [pyrtlsdr](https://github.com/roger-/pyrtlsdr) | GPL-3.0 | RTL-SDR access (drives Cascade's GPL-3.0 licensing) |
| [librtlsdr](https://github.com/osmocom/rtl-sdr) | GPL-2.0 | RTL-SDR driver |
| [NumPy](https://numpy.org) | BSD-3-Clause | DSP math |
| [SciPy](https://scipy.org) | BSD-3-Clause | filters / signal processing |
| [FastAPI](https://fastapi.tiangolo.com) | MIT | web/WebSocket server |
| [Starlette](https://www.starlette.io) | BSD-3-Clause | ASGI framework |
| [Uvicorn](https://www.uvicorn.org) | BSD-3-Clause | ASGI server |
| [pyais](https://github.com/M0r13n/pyais) | MIT | AIS NMEA decoding |
| [aprslib](https://github.com/rossengeorgiev/aprs-python) | GPL-2.0-or-later | APRS (TNC2) packet parsing |
| [pywebview](https://pywebview.flowrl.com) | BSD-3-Clause | optional native desktop window (`app.desktop`) |

## Web frontend

| Project | License | Use |
|---|---|---|
| [Leaflet](https://leafletjs.com) | BSD-2-Clause | maps (ADS-B / AIS) |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | ODbL (data) | map tiles — "© OpenStreetMap contributors" |
| [Vite](https://vitejs.dev) | MIT | build tooling |
| [TypeScript](https://www.typescriptlang.org) | Apache-2.0 | language/tooling |

## External decoders (run as separate processes; user-installed)

| Tool | License | Mode |
|---|---|---|
| [dump1090-fa](https://github.com/flightaware/dump1090) | GPL-2.0 | ADS-B |
| [AIS-catcher](https://github.com/jvde-github/AIS-catcher) | GPL-3.0 | AIS |
| [direwolf](https://github.com/wb2osz/direwolf) | GPL-2.0+ | APRS (AX.25 soundcard TNC) |
| [rtl_fm](https://github.com/osmocom/rtl-sdr) (rtl-sdr) | GPL-2.0 | FM audio feed for APRS |
| [welle.io](https://github.com/AlbrechtL/welle.io) (`welle-cli`) | GPL-2.0 | DAB |

These tools are **not bundled** — Cascade SDR launches them if you install them
yourself (see the README). They keep their own licenses.

## Map data attribution

Map tiles are © OpenStreetMap contributors, used under the OSM tile usage policy.
The attribution is shown on the map. For heavy or public deployments, use a
dedicated tile provider rather than the public OSM tile server.
