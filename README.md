# Cascade SDR

A cross-platform RTL-SDR application. A small **Python backend** owns the dongle
and does the signal processing; a **web frontend** (opened in a browser) shows the
UI, waterfall, audio and maps. The two talk over a WebSocket.

📖 **New here? See the [User Guide](docs/GUIDE.md)** — how to use every mode, plus
things to try. Status:

| Mode | What it does | Status |
|------|--------------|--------|
| **Waterfall** | Live scrolling spectrogram (FFT) + frequency axis | ✅ working |
| **Scan** | Swept wideband panorama (e.g. whole 88–108 band) to find signals | ✅ working |
| **Radio** | WFM / NFM / AM / USB / LSB; click-to-tune, squelch, audio in browser | ✅ working |
| **DAB** | DAB/DAB+ digital radio — ensemble station list + playback (via `welle-cli`) | ✅ working† |
| **ADS-B** | Aircraft on a Leaflet map (via `dump1090`) | ✅ working* |
| **AIS** | Ships on a Leaflet map (via `AIS-catcher`) | ✅ working** |

\* Needs `dump1090` installed (`brew install dump1090-mutability`) **and a decent
1090 MHz antenna** — the stock whip barely hears ADS-B. The pipeline runs and
plots aircraft when it receives them; with the stock antenna you may see none.

\*\* Needs `AIS-catcher` (build from source, see below). AIS (162 MHz) works with a
normal VHF/whip antenna near water. **By default we pass `-X off` so your received
data is NOT uploaded to the aiscatcher.org community feed.**

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

### Demodulators
Radio supports **WFM** (broadcast), **NFM** (narrow — ham/marine/PMR voice),
**AM** (carrier-normalised), **USB/LSB** (SSB, with AGC), and **CW** (Morse —
plays the tone and **decodes it to text** in an overlay; best on clean signals,
self-calibrates after a character or so). Switching demod sets a sensible default
bandwidth you can then fine-tune.

### Recording
- **Audio**: the *Record audio* button (Radio controls) captures what you're
  hearing to a **WAV** download.
- **IQ**: *Record IQ* (Device panel, Waterfall/Radio modes) writes the raw stream
  to a standard **.cu8** file (replayable in rtl_sdr/gqrx/etc.), listed with
  download/delete; the filename carries the center frequency and sample rate.

### Display, bookmarks, persistence
- **Band label**: under the device status it names the service(s) on the current
  frequency range (FM broadcast, Airband, Marine VHF, 2 m/70 cm ham, TETRA, DAB,
  ADS-B, …) so you know what kind of traffic to expect (EU/Sweden band plan).
- **Display** panel: **Auto contrast** (or manual floor/ceiling dB) for the
  waterfall, and **Peak hold** on the spectrum scope — peaks linger then fade over
  ~1–2 s so brief/bursty signals flash and are easy to spot.
- **Bookmarks**: save the current frequency (+ demod) with a name; click to recall,
  × to delete.
- **Settings persist** across reloads (gain, PPM, bias-T, demod, volume, squelch,
  contrast, peak-hold, scan range, receiver location, bookmarks) via localStorage.

### Layout
Controls live in the left sidebar; the spectrum scope + waterfall fill the rest of
the window and resize with it.

### ADS-B (aircraft map)
Select **ADS-B**: the backend spawns `dump1090`, reads its BaseStation feed
(TCP 30003), and plots aircraft on an OpenStreetMap map. Switching to another mode
kills `dump1090` and hands the dongle back. **Bias-T** (Device panel) can power a
1090 MHz LNA — strongly recommended for real range. Gain/PPM are passed to dump1090.
Click a plane or a row in the **Aircraft** list for full detail (callsign, ICAO,
**type/category** — light/small/large/heavy/rotorcraft, squawk, altitude, climb,
speed, track); the list is sorted by distance from your location (set it in the
ADS-B panel). Each aircraft also draws a **track trail** as it moves.

### DAB radio (†)
Select **DAB**, pick a **Band III block** (5A–13F). The backend runs `welle-cli`,
which tunes the block and decodes the **ensemble**; the station list appears on the
right — click a station to play it (the browser streams it from welle-cli). One
block carries many stations. Switching modes stops welle-cli and frees the dongle.

`welle-cli` isn't in Homebrew, so build it once:

```bash
brew install cmake fftw faad2 mpg123 libsamplerate lame
git clone --depth 1 https://github.com/AlbrechtL/welle.io.git ~/.local/src/welle.io
cd ~/.local/src/welle.io && mkdir build && cd build
export CPLUS_INCLUDE_PATH="$(xcrun --show-sdk-path)/usr/include/c++/v1"   # macOS CLT libc++
cmake .. -DBUILD_WELLE_IO=OFF -DBUILD_WELLE_CLI=ON -DRTLSDR=ON -DCMAKE_POLICY_VERSION_MINIMUM=3.5
make -j4 welle-cli && cp welle-cli /opt/homebrew/bin/welle-cli
```

> Needs a Band III antenna. Verified live in Stockholm: block **12C** = the
> **SR STOCKHOLM** ensemble (P1–P4, etc.).

### AIS (ships map)
Select **AIS**: the backend spawns `AIS-catcher` (listening on the 162 MHz marine
channels), receives NMEA over UDP, decodes it with `pyais`, and plots vessels on
the map with a sortable **Vessels** list (name/MMSI, speed, course, distance).
AIS works with an ordinary VHF/whip antenna if you're near water. Vessel popups
show the **ship type** (cargo/tanker/passenger/fishing/sailing/…) once a static
message arrives, and each vessel draws a **track trail** as it moves.

`AIS-catcher` isn't in Homebrew, so build it from source once:

```bash
brew install cmake
git clone --depth 1 https://github.com/jvde-github/AIS-catcher.git ~/.local/src/AIS-catcher
cd ~/.local/src/AIS-catcher && mkdir build && cd build
# On recent macOS the CLT libc++ headers can be incomplete; point clang at the SDK's:
export CPLUS_INCLUDE_PATH="$(xcrun --show-sdk-path)/usr/include/c++/v1"
cmake .. && make -j4
cp AIS-catcher /opt/homebrew/bin/AIS-catcher
```

> Privacy: AIS-catcher shares received data to aiscatcher.org **by default**. We
> launch it with `-X off` so nothing leaves your machine.

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
