# SDR-Ultra

Your own cross-platform RTL-SDR application. A small **Python backend** owns the
dongle and does the signal processing; a **web frontend** (opened in a browser)
shows the UI, waterfall, audio and maps. The two talk over a WebSocket.

Built to grow feature-by-feature. Status:

| Mode | What it does | Status |
|------|--------------|--------|
| **Waterfall** | Live scrolling spectrogram (FFT) + frequency axis | ✅ working |
| **Scan** | Swept wideband panorama (e.g. whole 88–108 band) to find signals | ✅ working |
| **FM** radio | Click a signal to tune, drag to set bandwidth, audio in browser | ✅ working |
| **AM** radio | Reuses the radio pipeline (envelope detect) | 🚧 basic (refined in M3) |
| **ADS-B** | Aircraft on a Leaflet map (via `dump1090`) | ✅ working* |
| **AIS** | Ships on a map (via `AIS-catcher`) | ⏳ planned (M5) |

\* Needs `dump1090` installed (`brew install dump1090-mutability`) **and a decent
1090 MHz antenna** — the stock whip barely hears ADS-B. The pipeline runs and
plots aircraft when it receives them; with the stock antenna you may see none.

### Using the radio
Open the app, click **Waterfall** to see the band, then **click any signal to
tune it** (this switches to Radio mode and plays audio). **Drag across a signal**
to set its bandwidth. A live **spectrum scope** sits above the waterfall (dBFS vs
frequency) and a **squelch** slider mutes the audio when the channel level falls
below the threshold (the level meter shows the current channel level and ▶/🔇).
Adjust demod (FM/AM), bandwidth and volume in the Radio controls. The dongle
stays on one center frequency and captures ~2.4 MHz; you're selecting channels
*within* that band digitally — use **Tune dongle** to move the captured window.

Audio uses a ~120 ms jitter buffer to stay click-free. The device is read on a
dedicated thread (kept drained at real time) so DSP never starves the USB stream.

### Finding signals with Scan
The dongle only sees ~2.4 MHz at once, so **Scan** sweeps it across a wider range
(set *Scan from/to* in MHz, or pick a **preset** like FM 88–108 or Airband) and
stitches the slices into one wide spectrum + waterfall. **Click any peak** to
re-center the dongle there and drop straight into Radio mode to listen. Wider
ranges sweep more slowly (each ~2.4 MHz slice needs its own retune + capture).

### Zoom, gain, PPM
- **Drag** across the scope/waterfall to zoom: in **Scan** it narrows the swept
  range (e.g. drag into the busy part around 1090 MHz); in **Waterfall** it
  re-centers and narrows the captured band; in **Radio** it sets demod bandwidth.
  Use **Zoom out** (top-right) to widen ×2.
- **Gain** — uncheck *Auto gain* for a manual slider over the device's gain steps.
  High manual gain helps weak signals (e.g. ADS-B); too much overloads.
- **PPM correction** — RTL-SDR crystals are off by tens of ppm; set this so the
  displayed/tuned frequency is accurate (important for narrowband + digital modes).

### Layout
Controls live in the left sidebar; the spectrum scope + waterfall fill the rest of
the window and resize with it.

### ADS-B (aircraft map)
Select **ADS-B**: the backend spawns `dump1090`, reads its BaseStation feed
(TCP 30003), and plots aircraft on an OpenStreetMap map. Switching to another mode
kills `dump1090` and hands the dongle back. **Bias-T** (Device panel) can power a
1090 MHz LNA — strongly recommended for real range. Gain/PPM are passed to dump1090.

> FM **de-emphasis** defaults to 50 µs (Europe). In North America/Korea use 75 µs
> — change `tau_us` in [backend/app/modes/radio.py](backend/app/modes/radio.py)
> (`DeEmphasis(...)`). A UI toggle can be added later.

> A single RTL-SDR has one tuner, so **one mode runs at a time** — you pick what
> the dongle is doing. Add a second dongle later for concurrent modes.

## Architecture

```
RTL-SDR (USB) ──IQ──▶ Python backend (FastAPI)
                        • DeviceManager owns the dongle (one mode at a time)
                        • IQ modes run in a worker thread (read_samples + numpy DSP)
                        • subprocess modes wrap dump1090 / AIS-catcher
                        └─ WebSocket: JSON control + status, binary FFT/audio
                                 │
                                 ▼
                      Web frontend (Vite + TypeScript)
                        • waterfall (canvas)  • Web Audio (planned)
                        • Leaflet map (planned)
```

Key files: [backend/app/device.py](backend/app/device.py) (device ownership +
worker thread), [backend/app/modes/](backend/app/modes/) (one file per mode),
[backend/app/dsp/](backend/app/dsp/) (hand-written DSP),
[frontend/src/](frontend/src/) (UI, WebSocket, renderers).

## Prerequisites (macOS, Apple Silicon)

```bash
brew install rtl-sdr python@3.12 node
```

`rtl-sdr` pulls in `librtlsdr`. Verify the dongle is seen:

```bash
rtl_test        # should print your tuner (e.g. R820T); Ctrl-C to stop
```

> **librtlsdr / pyrtlsdr note:** we pin `pyrtlsdr==0.3.0` and `setuptools<81`
> (see [backend/requirements.txt](backend/requirements.txt)). Newer pyrtlsdr
> hard-binds a symbol the Homebrew `librtlsdr` doesn't export and fails to
> import; 0.3.0 works with the stock library.

## Setup

```bash
# Backend
cd backend
python3.12 -m venv .venv            # or: /opt/homebrew/opt/python@3.12/bin/python3.12
./.venv/bin/pip install -r requirements.txt

# Frontend
cd ../frontend
npm install
```

## Run (development)

Two terminals:

```bash
# 1) backend  (http://localhost:8000)
cd backend && ./.venv/bin/uvicorn app.main:app --reload --port 8000

# 2) frontend (http://localhost:5173 — proxies /api and /ws to the backend)
cd frontend && npm run dev
```

Open **http://localhost:5173**, click **Waterfall**, and you should see the
spectrum. Set the **Center (MHz)** to a strong local FM station and click **Tune**.

## Run (single-process)

Build the frontend once; the backend then serves it directly — no Vite needed:

```bash
cd frontend && npm run build          # outputs frontend/dist/
cd ../backend && ./.venv/bin/uvicorn app.main:app --port 8000
# open http://localhost:8000
```

> Tip: during development, if you edit the WebSocket framing and the browser
> behaves oddly, do a **hard reload** — Vite's HMR can keep a stale module.
> The single-process build above sidesteps this entirely.

## Windows

Same backend + browser. Differences: install the RTL-SDR WinUSB driver with
**Zadig**, and use Windows builds of `dump1090` / `AIS-catcher`. No code changes
expected. (A detailed Windows section will be added alongside M4/M5.)

## Roadmap

See the milestone plan in
`~/.claude/plans/i-have-a-rtl-peaceful-moon.md`. Next up: **M2 — FM radio +
audio** (`RadioMode` + Web Audio playback).
