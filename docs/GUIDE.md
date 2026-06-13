# Cascade SDR — User Guide

Everything Cascade SDR can do, how to use it, and things worth trying. For setup
and install, see the [README](../README.md).

> **Hardware: RTL-SDR only.** Cascade SDR is built specifically for **RTL-SDR
> dongles** (RTL2832U with an R820T/R820T2 tuner, ~24–1766 MHz). Other SDRs
> (Airspy, HackRF, SDRplay, etc.) are **not** supported.

> **One tuner, one job.** An RTL-SDR has a single tuner that sees ~2.4 MHz at a
> time, so only one mode runs at once — picking a mode (or a decoder like ADS-B)
> takes over the dongle. Switching modes hands it cleanly to the next.

---

## The screen

- **Left sidebar** — all controls. The panels shown change with the mode.
- **Right display** — the spectrum **scope** (top) + scrolling **waterfall**
  (below) with a **frequency axis**; in ADS-B/AIS it becomes a **map**, in DAB a
  **station list**.
- **Top of sidebar** — connection dot (green = backend connected) and the mode
  tabs: **Idle · Spectrum · Scan · Replay · DAB · ADS-B · AIS · APRS**.
- **Band label** — under the device status, names the service on the current
  frequency (e.g. “FM broadcast”, “Marine VHF”) so you know what you're looking at.

---

## Device panel (always visible)

| Control | What it does |
|---|---|
| **Center (MHz)** + **Tune dongle** | Sets the hardware center frequency (24–1766 MHz — the R820T tuner's range). |
| **Sample rate (MS/s)** | Capture bandwidth, up to 2.4. Lower = less CPU/USB, narrower view. |
| **Auto gain** / **Gain** | Auto lets the tuner ride gain; uncheck for a manual gain slider. More gain digs out weak signals but can overload near strong ones. |
| **PPM correction** | Corrects the dongle's crystal error so the displayed frequency is accurate. See *Calibration* below. |
| **Bias-T (5 V)** | Feeds 5 V up the coax to power an inline LNA/antenna. **Only enable if you have a powered device** — don't feed a plain antenna. |
| **Record IQ** | Records the raw stream to a `.cu8` file (see *Recording*). |

### 📡 Antenna helper (dipole kit)
Below the band label, a line tells you how to set the **RTL-SDR.com dipole kit**
for the current frequency — it updates live as you type a Center frequency:

- **Which rods**: *long (large)* set (~70–300 MHz) or *short (small)* set
  (~450 MHz–1 GHz). This is the "short or long antenna" choice.
- **Length each**: extend **both** elements equally to the shown cm (≈ a quarter
  wavelength: `length_cm ≈ 7125 / freq_MHz`, minus the 2 cm hidden in the base).
- **Orientation**: **vertical** (rods straight up & down) for almost everything —
  most signals are vertically polarised. For **137 MHz weather satellites** it
  switches the advice to a horizontal **"V" at ~120°**.

Examples: FM 100 MHz → ~69 cm large; marine/AIS 162 MHz → ~42 cm large;
ADS-B 1090 MHz → ~5 cm small (collapsed). Equal lengths and a clean vertical
line matter more than getting the exact cm. The kit can't reach resonance below
~70 MHz (extend the large rods fully and accept reduced performance).

---

## Spectrum (waterfall + listen, in one view)

The core view: a live FFT plus audio. The **scope** shows the instantaneous
spectrum; the **waterfall** scrolls it over time (bright = strong). It opens
**silent** — so it works as a plain band browser — and starts playing the moment
you click a signal. (This is the old Waterfall and Radio modes merged into one.)

- **Click** a signal → tunes + listens to it (no hardware retune; you're picking a
  channel inside the captured band).
- **Drag** across a signal → sets the demod **bandwidth** to match it.
- **Scroll** (wheel) → **zoom** the display into part of the band; **shift-drag** to
  pan; **Zoom out** (top-right) resets. This magnifies what's captured — it doesn't
  retune — so two close signals become easy to separate.
- **Display panel**: **Auto contrast** (or set the floor/ceiling dB by hand to pull
  weak signals out of the noise), **Peak hold** (peaks linger ~1–2 s then fade —
  great for catching brief bursts), and **Averaging** (2–16×) which smooths the
  scope trace so a weak, steady carrier stops dancing in the noise and stands out.

**Try:** tune Center to **100 MHz**, watch FM stations as bright wide blobs, and
click one to listen. Scroll to zoom into a crowded patch. For a faint constant
tone, switch Averaging to 8× and watch it firm up out of the grass.

---

## Scan (wideband panorama)

The dongle can't see more than ~2.4 MHz at once, so Scan **sweeps** across a range
and stitches it into one wide waterfall — for surveying a whole band.

- Set **From/To (MHz)** or pick a **Preset** (FM, Airband, 2 m, 70 cm).
- **Click** a peak → re-centers the dongle there and drops into the Spectrum view.
- **Drag** to zoom into a sub-range; **Zoom out** to widen.
- Wider ranges refresh slower (each 2.4 MHz slice needs its own retune).

**Try:** Preset **FM broadcast 88–108** to see every local station at once, then
click the strongest to listen. Or scan **1080–1100** for ADS-B activity (needs a
1090 antenna).

---

## Replay (play back a recording)

Open **Replay** and click any saved `.cu8` capture: it streams the file back
through the same Spectrum view and demodulators, looping at the end — **no dongle
required**. Everything works as if live: click a signal to listen, drag to set
bandwidth, scroll to zoom, switch demods. Because IQ captures the *whole* 2.4 MHz
band (not just the channel you were on), you can pull out signals you didn't even
notice during the live session.

- The capture's **center frequency and sample rate** are read from its filename, so
  the axis is labelled correctly.
- Record captures with **Record IQ** in the Spectrum view (Device panel).

**Try:** record a minute of the FM band, then in Replay click around different
stations — same recording, any station, any time.

---

## Demodulators (the Radio controls)

After you click a signal in the Spectrum (or Replay) view, pick a **Demod**:

| Demod | Use for |
|---|---|
| **WFM** | FM broadcast radio (wide). |
| **NFM** | Narrow FM voice — ham 2 m/70 cm, marine, PMR446, business radio. |
| **AM** | AM broadcast, **airband** (aircraft voice 118–137 MHz). |
| **USB / LSB** | Single-sideband voice — ham HF/VHF SSB (USB above 10 MHz by convention). |
| **CW** | Morse — plays the tone **and decodes it to text** (see below). |

- Switching demod sets a sensible **bandwidth** you can fine-tune.
- **FM stereo** (WFM): on by default. Broadcast FM plays in stereo when the
  station sends a pilot; a **◖◗ stereo** mark appears on the level meter when
  locked. Weak/noisy signals are noisier in stereo — turn it off for mono if so.
- **FM de-emphasis** (WFM): leave at **50 µs** in Europe; switch to **75 µs** for
  North America/Korea. Wrong setting makes broadcast FM sound dull or harsh.
- **RDS** (WFM): on by default — within a few seconds of tuning a broadcast FM
  station you'll see its **name**, scrolling **radiotext** (song/show), **PI**
  code and **program type** (e.g. "Pop music"). Needs a clean signal; weak/multipath
  stations decode slowly or not at all. Toggle it off to save a little CPU.
- **Volume**, and **Squelch** — raise it until the hiss on an empty channel cuts
  out; the **level meter** shows the channel strength and ▶ (open) / 🔇 (muted).
- **Record audio** → saves what you hear to a **WAV**.
- The tuning **cursor + shaded band** on the scope/waterfall show where and how
  wide you're listening; **drag** across a signal to set the bandwidth to match it.

**Try:** click a local FM station (WFM). Then Center **162 MHz**, **NFM**, and
look for marine voice. Then **AM** around **120 MHz** near an airport for ATC.

### CW decode
Pick **CW**, tune onto a Morse signal so you hear a clean tone. Decoded text scrolls
in the overlay at the bottom of the display. It self-calibrates to the sending
speed after a character or two; clean, steady CW decodes best. **Try:** a ham CW
segment (2 m: ~144.05 MHz; HF needs an upconverter).

---

## DAB (digital radio)

Digital DAB/DAB+ in Band III (~174–240 MHz).

- Pick a **Block** (5A–13F). **Stockholm: 12A, 12C, 12D** (12C = Sveriges Radio).
- The **ensemble's stations** appear on the right — **click one to play it**.
- One block carries many stations; switching block re-tunes the decoder.

**Try:** block **12C** in the Stockholm area → P1–P4, Barn SR, etc.

---

## ADS-B (aircraft map)

Plots aircraft from their 1090 MHz transponders on a map.

- Switch to **ADS-B**; the map shows planes with **heading-rotated icons** and
  **trails**. The **Aircraft list** (top-right) is sorted by distance.
- **Click** a plane or a row → popup with callsign, ICAO, **type** (light/large/
  heavy/rotorcraft…), squawk, altitude, climb, speed, track.
- Set **My location** (lat, lon) in the ADS-B panel for accurate distances.
- Needs a **1090 MHz antenna** for real range; a 1090 LNA on Bias-T helps a lot.

**Try:** if near an airport/flight path, watch trails build as planes move. Click
the nearest one for details.

---

## AIS (ship map)

Plots vessels from their 162 MHz AIS transmissions.

- Switch to **AIS**; ships appear as markers with **trails**; the **Vessels list**
  is sorted by distance.
- **Click** a ship → name, MMSI, **type** (cargo/tanker/passenger/fishing/sailing…),
  speed, course, destination.
- Works with an ordinary VHF antenna near water. **Data stays local** (not
  uploaded anywhere).

**Try:** near a coast/harbour, watch vessels and read their names/types as their
static messages arrive (every few minutes).

---

## APRS (packet-radio stations map)

Plots amateur **APRS** stations heard on **144.800 MHz** (EU). Needs
[`direwolf`](https://github.com/wb2osz/direwolf) installed (`brew install direwolf`).

- Switch to **APRS**; stations appear as dots with their **callsign** and a trail;
  the **Stations list** is sorted by distance.
- **Click** a station → callsign, packet type, speed/course, altitude, comment.
- Reception is direct or via **digipeaters**, so you can hear stations from
  surprisingly far. Beacons are infrequent (minutes apart) — leave it running.
- North America uses **144.390 MHz** — set the Center frequency accordingly.

**Try:** leave it on for a while near any town; mobile stations (cars/handhelds)
and weather stations should trickle in.

---

## Recording

- **Audio (WAV)** — *Record audio* in the Spectrum view; captures what you hear,
  downloads a 48 kHz WAV. Good for saving a catch or a CW/SSB exchange.
- **IQ (.cu8)** — *Record IQ* in the Spectrum view; saves the **raw radio** so you
  can replay/analyse it later — in Cascade's own **Replay** mode, or in gqrx /
  `rtl_sdr` / etc. Files list with download/delete; the name carries the frequency +
  sample rate. **They're big (~290 MB/min)** — delete when done.

**Try:** record 10 s of IQ on a busy band; later you can replay it offline.

---

## Bookmarks & persistence

- **Bookmarks** — save the current frequency (+demod) with a name; click to recall,
  × to delete.
- Your settings (gain, PPM, demod, de-emphasis, volume, squelch, contrast,
  peak-hold, averaging, scan range, location, bookmarks) **persist across reloads**.

---

## Calibration (PPM)

For accurate frequencies (and the best SSB/CW/digital results), correct the crystal
error once: tune a station with a known frequency (a strong FM station, or an
airport's ATIS), and adjust **PPM** until the signal sits exactly on its marked
frequency in the scope. Typical dongles need ~0–60 ppm. It's saved automatically.

---

## Frequency cheat-sheet (Sweden / EU)

| Band | Frequency | Mode |
|---|---|---|
| FM broadcast | 87.5–108 MHz | WFM |
| Airband (ATC) | 118–137 MHz | AM |
| Weather sats (NOAA/Meteor) | ~137 MHz | (image decode external) |
| 2 m ham | 144–146 MHz | NFM / SSB / CW |
| Marine VHF (CH16 156.8) | 156–162 MHz | NFM |
| AIS | 161.975 / 162.025 MHz | AIS mode |
| DAB+ (Stockholm 12C) | 174–240 MHz | DAB mode |
| TETRA / Rakel | 380–400 MHz | (digital) |
| PMR446 | 446.0–446.2 MHz | NFM |
| 70 cm ham | 430–440 MHz | NFM / SSB / CW |
| ADS-B | 1090 MHz | ADS-B mode |

---

## Troubleshooting

- **“No supported devices” / device error** — unplug the dongle and plug it back
  in. Force-quitting a decoder mid-stream can wedge USB; a replug always clears it.
- **Nothing on a band** — it's almost always the **antenna**. The stock whip is
  poor at 1090 MHz (ADS-B) and weak at VHF; a band-appropriate antenna transforms
  results.
- **Audio crackles** — shouldn't, thanks to the jitter buffer; if it does, avoid
  running heavy apps that starve the browser tab.
- **Frequency looks off** — set **PPM** (see Calibration).
- **ADS-B/AIS/DAB say a tool is missing** — install `dump1090` / build
  `AIS-catcher` / `welle-cli` (see the README).
