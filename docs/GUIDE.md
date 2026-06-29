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
  (below) with a **frequency axis**; in ADS-B/AIS/APRS it becomes a **map**, in DAB
  a **station list**, in ACARS / ISM / Pager a feed, in APT / SSTV an image.
- **Top of sidebar** — connection dot (green = backend connected) and the mode
  tabs, grouped **Explore** (Idle · Radio · Sweep · Replay · Scanner) and
  **Decode** (DAB · ADS-B · AIS · APRS · ACARS · APT · SSTV · Pager · ISM).
- **Band label** — under the device status, names the service on the current
  frequency (e.g. “FM broadcast”, “Marine VHF”) so you know what you're looking at.
- **On phones / narrow screens** the sidebar collapses behind a **☰ menu** button
  (top-left) so the display gets the full width; tap ☰ to open the controls, then
  the backdrop or any mode button to close it. The aircraft/vessel list becomes a
  bottom sheet.

---

## Controls (sidebar panels)

The sidebar shows only the panels that apply to the current mode.

### Tuning (Radio view)
| Control | What it does |
|---|---|
| **Center / Frequency (MHz)** | Type a frequency and press **Enter** to go there: it re-centers the band **and** tunes/listens to that frequency (the channel cursor follows). Range 24–1766 MHz (R820T). Click elsewhere in the waterfall afterwards to listen off-centre — in Radio mode the field is labelled **Frequency** and tracks the channel you're listening to (so it updates when you click around), while in the Spectrum view it's labelled **Center** and shows the dongle's centre. |
| **Sample rate (MS/s)** | Capture bandwidth, up to 2.4. Lower = less CPU/USB, narrower view. |
| **◀ block / block ▶** | Jump the captured band down/up by one capture width (~2.4 MHz) to walk across the spectrum looking for signals. |

Switching modes **keeps the current band** — listen to a ship on AIS (162 MHz), hit
**Radio**, and you're looking at 162 MHz instead of jumping back to a default.
Decoder modes (ADS-B/AIS/APRS/ACARS/DAB/SSTV/Pager) tune themselves, so they hide Center.

### Reception (all live modes)
| Control | What it does |
|---|---|
| **Auto gain** / **Gain** | Auto lets the tuner ride gain; uncheck for a manual slider. More gain digs out weak signals but can overload near strong ones. For broadcast FM, a manual *high* gain often gives the cleanest audio and best RDS lock. |
| **Advanced ▸ PPM correction** | Corrects the dongle's crystal error so the displayed frequency is accurate. See *Calibration* below. |
| **Advanced ▸ Bias-T (5 V)** | Feeds 5 V up the coax to power an inline LNA/antenna. **Only enable if you have a powered device** — don't feed a plain antenna. |

PPM and Bias-T sit under a collapsible **Advanced** disclosure — they're usually set once.

### Recording (Radio / APT / SSTV)
**Record IQ** captures the raw stream to a `.cu8` file (see *Recording* below).

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

## Radio (waterfall + listen, in one view)

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
- **Reading the scope**: the left edge has a **dB scale** (a line every 10 dB) so you
  can read signal strength at a glance. A dashed red **noise-floor line** marks the
  grass between carriers (with a `noise ≈ … dB` readout), and the **service name**
  for the tuned band (e.g. *70 cm ham*, *Airband*) is printed faintly across it.

**Try:** tune Center to **100 MHz**, watch FM stations as bright wide blobs, and
click one to listen. Scroll to zoom into a crowded patch. For a faint constant
tone, switch Averaging to 8× and watch it firm up out of the grass.

---

## Sweep (wideband panorama)

The dongle can't see more than ~2.4 MHz at once, so Sweep **sweeps** across a range
and stitches it into one wide waterfall — for surveying a whole band.

- Set **From/To (MHz)** or pick a **Preset** (FM, Airband, 2 m, 70 cm).
- **Click** a peak → re-centers the dongle there and drops into the Radio view.
- **Drag** to zoom into a sub-range; **Zoom out** to widen.
- Wider ranges refresh slower (each 2.4 MHz slice needs its own retune).

**Try:** Preset **FM broadcast 88–108** to see every local station at once, then
click the strongest to listen. Or sweep **1080–1100** for ADS-B activity (needs a
1090 antenna).

---

## Scanner (monitor channels, stop on activity)

A channel scanner: it steps through a preset's channels and **stops on the first
one carrying a transmission**, plays it, then resumes a few seconds after it goes
quiet — like a marine/PMR scanner.

- Pick a **Preset**: **Marine VHF** (Ch 16 + ship-to-ship + Swedish leisure/fishing
  channels), **PMR446**, or **Airband** (AM). Data-only channels (DSC 70, AIS) are
  excluded — there's nothing to listen to.
- The channel grid shows every channel with a live **signal bar**; the active one
  turns green and the one it's parked on is highlighted.
- **Squelch (dB over noise)** — how far above the noise floor counts as a signal.
  Use the bars to set it: if a call you want makes a bar rise but it doesn't stop,
  lower squelch; if it keeps stopping on noise, raise it.
- **Volume** for the parked audio. It resumes scanning ~3 s after a channel falls
  silent.
- **Priority** — pick a channel (e.g. Marine **Ch 16**) and the scanner jumps to it
  the moment it's active, even while parked on another channel, then returns to
  normal scanning when it goes quiet. "Off" disables it.
- **Customize channels** — expand it to build your own list: edit a channel's
  label / frequency / demod (NFM or AM), reorder with **↑ ↓**, remove with **×**,
  **+ Add channel**, then **Save** under a name. Saved presets appear under
  *Custom* in the dropdown (saving with a custom preset's own name updates it; a
  built-in can't be overwritten — save under a new name). **Delete this preset**
  removes a custom one. Presets persist on the backend
  (`backend/data/scanner_custom.json`).
- Because the dongle sees only ~2.4 MHz at once, wide presets are covered in a few
  capture blocks — but the marine simplex channels all sit within ~1.4 MHz, so
  they're watched at the same time. (Priority pre-emption works while parked on a
  channel sharing the priority channel's capture window — all built-in presets do.)

**Try:** Scanner → **Marine VHF** near a harbour and leave it running; it parks and
plays whenever someone keys up (Ch 16 is the most active to confirm it works).
Needs a VHF/marine antenna.

---

## Replay (play back a recording)

Open **Replay** and click any saved `.cu8` capture: it streams the file back
through the same Radio view and demodulators, looping at the end — **no dongle
required**. Everything works as if live: click a signal to listen, drag to set
bandwidth, scroll to zoom, switch demods. Because IQ captures the *whole* 2.4 MHz
band (not just the channel you were on), you can pull out signals you didn't even
notice during the live session.

- The capture's **center frequency and sample rate** are read from its filename, so
  the axis is labelled correctly.
- Record captures with **Record IQ** in the Radio view (Recording panel).

**Try:** record a minute of the FM band, then in Replay click around different
stations — same recording, any station, any time.

---

## Demodulators (the Radio controls)

After you click a signal in the Radio (or Replay) view, pick a **Demod**:

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

- Switch to **ADS-B**; the map shows planes as **icons that point the way they're
  heading**, sized by aircraft class — narrowbody/small, **widebody** (bigger), and
  the **A380** (biggest); **rotorcraft get a helicopter icon**. Each leaves a
  **trail**. The **Aircraft list** (top-right) is sorted by distance.
- The list shows a **climb/descent arrow** next to each altitude — **▲ green**
  climbing, **▼ red** descending, **– grey** level (small deadband, so it doesn't
  flicker at cruise).
- **Click** a plane on the map or a row in the list to **follow** it — the map
  re-centres on it and keeps it centred as it moves (drag the map to stop
  following). The popup shows callsign, ICAO, **registration** and **model** (when
  your dump1090 build ships an aircraft database), **category** (light/large/heavy/
  rotorcraft…), squawk, altitude, climb (with the same arrow), speed, track.
- **Route, airline and tail number** are *not* in the ADS-B signal. Tick **"Look up
  route + tail number"** in the ADS-B controls to fetch them from **adsbdb.com** —
  the route/airline by callsign, and the **registration (tail #) + operator** by
  ICAO hex (this fills the tail number even when your dump1090 build has no aircraft
  database). The popup then shows airline, **From / To**, **Tail #** and **Operator**.
  It's **off by default**: leaving it off keeps ADS-B fully offline (nothing leaves
  your machine). Results are cached. The route is the *last-known* route for that
  flight number, so it can occasionally be a stale/return leg — check the airline
  name if a route looks wrong.
- Needs a **1090 MHz antenna** for real range; a 1090 LNA on Bias-T helps a lot.
- See **Map options** below to set your location (for distances) and toggle trails.

**Try:** if near an airport/flight path, watch trails build as planes move. Click
the nearest one for details.

---

## AIS (ship map)

Plots vessels from their 162 MHz AIS transmissions.

- Switch to **AIS**; ships appear as markers **sized by the vessel's length** (a
  dinghy is a dot, a tanker is large), **colour-coded by ship type** the way
  MarineTraffic does it (cargo green, tanker red, passenger blue, high-speed teal,
  sailing/pleasure purple, fishing brown, …), and tagged with a **country flag**
  from the MMSI. A vessel that's **underway** shows an **arrow pointing to its
  heading**; one that's **moored** (or hasn't reported a heading) shows a circle.
  Open **Vessel colours** in the AIS panel for the legend. Trails show recent
  movement.
- The **Vessels list** has **sortable columns** — click **Vessel / Spd / Crs / Dist**
  to sort by that column (click again to reverse).
- **Click** a ship → a full readout: name, MMSI, **flag/country**, IMO, callsign,
  **type** (cargo/tanker/passenger/fishing/sailing…), **navigation status**,
  **size (length × width)**, draught, speed, course, heading, rate of turn,
  destination and ETA — whatever that vessel has broadcast.
- **Notes**: click a vessel, type a note in the AIS panel, **Save** — it's stored
  against that MMSI and flagged with a 📝 in the list.
- **Name cache**: names and your notes are remembered between sessions, so a known
  ship shows up named the instant it's heard again, instead of waiting minutes for
  its next static (name) message.
- Works with an ordinary VHF antenna near water. **Data stays local** (not uploaded).
- See **Map options** below to set your location (for distances) and toggle trails.

**Try:** near a coast/harbour, sort by **Spd** to spot what's moving, and click a
big marker to read its dimensions and destination.

---

## APRS (packet-radio stations map)

Plots amateur **APRS** stations heard on **144.800 MHz** (EU). Needs
[`direwolf`](https://github.com/wb2osz/direwolf) installed (`brew install direwolf`).

- Switch to **APRS**; stations appear as dots with their **callsign** and a trail;
  the **Stations list** is sorted by distance.
- **Click** a station → callsign, packet type, speed/course, altitude, comment.
- A **Packets** feed in the APRS panel shows each decoded packet as it's received
  — **messages** (with their recipient), **status**, **weather**, and position
  **comments** — newest first, with the raw packet as a fallback. This is the
  place to actually *read* APRS traffic, as opposed to the aggregated map. (Note:
  most RF traffic is position/weather beacons; addressed messages are uncommon.)
- Reception is direct or via **digipeaters**, so you can hear stations from
  surprisingly far. Beacons are infrequent (minutes apart) — leave it running.
- North America uses **144.390 MHz** — set the Center frequency accordingly.
- See **Map options** below to set your location (for distances) and toggle trails.

**Try:** leave it on for a while near any town; mobile stations (cars/handhelds)
and weather stations should trickle in.

---

## Map options (ADS-B · AIS · APRS)

Shared controls for all three map modes:

- **My location** (lat, lon) — the reference point for **distance/bearing** in the
  lists, also shown as a small **blue dot** on the map so you can confirm it's
  right. Click **📍 Use my location** to fill it from the browser's geolocation
  (works in a browser; in the desktop app, type it in — either way it's saved).
  Defaults to central Stockholm; set it to where your antenna is.
- **Show tracks** — toggle the movement trails on/off (useful when the map is busy).

Both persist across sessions.

---

## ACARS (aircraft data feed)

Shows **ACARS** messages — short text/data from aircraft on ~131 MHz AM. Needs
[`acarsdec`](https://github.com/TLeconte/acarsdec) built from source (see README).

- Switch to **ACARS**; decoded messages stream into a **log** (newest on top),
  each with time, **flight / registration**, label, frequency, and the message text.
- No map — ACARS doesn't carry position; it's a live feed.
- Watches **131.725 / 131.525 / 131.825 MHz** (EU) at once. North America centres
  on **131.550** — see the README to change channels.

**Try:** near an airport, leave it running; you'll catch weather requests, position
reports, and ops messages as aircraft pass.

---

## APT (NOAA weather-satellite images)

Decodes **NOAA APT** — a live grayscale image from a polar weather satellite as it
passes overhead. Hand-written decoder, no external tool.

- Switch to **APT**, pick the satellite (**NOAA 15** 137.620, **18** 137.9125,
  **19** 137.100 MHz). The image builds **top-down at 2 lines/s** during the pass.
- **Save PNG** downloads the full-resolution image; **Clear** restarts.
- **Two ways to capture** (you chose both):
  - *Live* — watch it draw during the pass.
  - *Record then decode* — **Record IQ** during the pass, then in **Replay** tick
    **"Decode as APT image"** and play it back.

**You need a pass + the right antenna.** Check pass times (gpredict / n2yo.com),
and set the dipole kit to a horizontal **"V" (~120°)** with elements ~53 cm (the
antenna helper shows this when you're on 137 MHz). The stock whip barely works.

**Try:** find the next NOAA pass for your location, start APT a minute before,
and watch the coastline scroll in. (Meteor-M LRPT is digital and not supported.)

---

## SSTV (slow-scan TV images)

Decodes **SSTV** — pictures sent as audio tones over the radio. Hand-written
decoder, no external tool.

- Switch to **SSTV**; it listens on **144.500 MHz** (the 2 m calling frequency,
  NBFM) and decodes any transmission it hears. The **mode is auto-detected** from
  the VIS header — **Martin M1/M2**, **Scottie S1/S2/DX**, **Robot 36/72**, and
  **PD 50/90/120/160/180** are supported.
- The picture builds **top-down** over ~1–2 min. **Save PNG** downloads the
  full-resolution image; **Clear** restarts.
- For **HF SSTV** (e.g. 14.230 MHz USB — needs an HF upconverter for an RTL-SDR),
  open **Radio**, switch demod to **USB**, tune the signal, and toggle **SSTV**
  on — the same decoder runs. Record IQ to decode a transmission again in Replay.

> The tone's instantaneous frequency carries the picture (1500 Hz = black …
> 2300 Hz = white, 1200 Hz = line sync). The YUV modes (Robot, PD) carry luma
> plus colour-difference channels, converted back to RGB on decode.

---

## Pager (POCSAG/FLEX)

Shows **pager messages** — POCSAG (512/1200/2400 baud) and FLEX. The backend pipes
`rtl_fm` into [`multimon-ng`](https://github.com/EliasOenal/multimon-ng)
(`brew install multimon-ng`).

- Switch to **Pager**, pick a **channel** — **DAPNET 439.9875** (the amateur-radio
  POCSAG network) plus common EU/VHF POCSAG frequencies. Decoded messages stream
  into a **feed** (newest on top) with the protocol, address/capcode and text.
- No map — pages don't carry position; it's a live feed.

> What's on the air, and whether you may listen, **varies by country**. Use this
> for the amateur DAPNET network and other lawful, unencrypted traffic.

---

## ISM (315–915 MHz devices)

Decodes the **433.92 MHz ISM band** — weather stations, soil/pool sensors,
**TPMS** tyre-pressure monitors, door/window contacts, remotes, energy meters.
Needs [`rtl_433`](https://github.com/merbanan/rtl_433) (`brew install rtl_433`).

- Switch to **ISM**; the view groups decodes **by device** — one card per
  transmitter (**model · id · channel**) with a hit count, **last-seen** time and
  signal level.
- Each numeric reading (temperature, humidity, pressure, wind, rain, TPMS
  pressure…) gets a live **sparkline** of its trend, with the current value and
  the min–max range. Non-numeric fields (battery, type, status) and brand-new
  readings show as plain chips until they have history.
- Devices and their trends are **cached on disk** (`backend/data/ism_cache.json`),
  so they reappear when you come back to the tab or restart the backend.
- **Filter by type** with the dropdown (top-right): pick a model to show only that
  sensor, or *All types*. The choice is remembered.
- **Remove a device** with the **×** on its card — drops it from the view *and*
  the cached history. (A device that's still transmitting reappears on its next
  beacon; use the type filter to hide ones you simply don't want to watch.)
- **Band** selector: switch between **315 / 433.92 / 868.3 / 915 MHz**. Picking a
  band relaunches `rtl_433` on that frequency. 433.92 and 868.3 are the EU bands;
  315 and 915 are common in the Americas.
- No map — these are short one-way beacons; it's a live per-device feed.
- A short whip is plenty (λ/4 ≈ 17 cm at 433 MHz; shorter for 868/915). Gain/PPM
  are passed to `rtl_433`.

**Try:** leave it running for a minute (devices beacon periodically, busiest in
the evening) and watch your neighbourhood's weather sensors and car TPMS appear.

---

## Recording

- **Audio (WAV)** — *Record audio* in the Radio view; captures what you hear,
  downloads a 48 kHz WAV. Good for saving a catch or a CW/SSB exchange.
- **IQ (.cu8)** — *Record IQ* in the Radio view; saves the **raw radio** so you
  can replay/analyse it later — in Cascade's own **Replay** mode, or in gqrx /
  `rtl_sdr` / etc. Files list with download/delete; the name carries the frequency +
  sample rate. **They're big (~290 MB/min)** — delete when done. To keep them off
  the boot disk (e.g. on a Pi), set **`CASCADE_RECORDINGS_DIR`** to a USB drive or
  an NFS-mounted NAS folder; on a Pi, record over Ethernet so a WiFi stall can't
  drop samples.

**Try:** record 10 s of IQ on a busy band; later you can replay it offline.

---

## Frequency directory

- A **built-in, click-to-tune reference** of known channels, so you can see *what*
  a frequency is for instead of guessing. Click any row to jump there (it switches
  to **Radio** with a sensible demodulator — AM for airband, NFM for marine/PMR, …).
- Grouped into **Marine VHF** (full ITU channel table, ship-station frequencies),
  **Airband** (Stockholm Arlanda & Bromma tower/ground/ATIS/approach + 121.5 guard),
  **Ham · APRS · satellites** (calling frequencies, APRS, ISS, sat segments),
  **Weather sat · ISM** (NOAA/Meteor APT, 433/868 devices), and **PMR446**.
- **Search** by name or MHz to filter across every category at once.

> **These are the author's lists.** The marine, ham, weather-sat, ISM and PMR446
> entries are standard international (ITU / IARU Region-1) plans that apply anywhere
> in Europe, but the **airband list is specific to Stockholm** (Arlanda & Bromma) —
> it won't match your local airports. Replace it with your own:
>
> - **Import my own list** — load a `CSV` (`name,mhz,demod`) or `JSON`
>   (`[{"name","mhz","demod"}]`) file. Demod is optional (nfm/wfm/am/usb/lsb). It's
>   saved in your browser and shown as **My list** at the top; **Clear my list**
>   removes it.
> - Untick **Show built-in lists** to hide the bundled (Stockholm/Region-1) channels
>   and use *only* your imported list.
> - To change the bundled defaults permanently, edit `frontend/src/frequencies.ts`
>   and rebuild.
>
> Channels that map to a decoder (AIS, APRS, APT, ISM) are tagged — switch to that
> mode to actually decode them.

## Bookmarks & persistence

- **Bookmarks** — save the current frequency (+demod) with a name; click to recall,
  × to delete.
- Your settings (gain, PPM, demod, FM stereo, de-emphasis, volume, squelch,
  contrast, peak-hold, averaging, sweep range, location, show-tracks, bookmarks)
  **persist across reloads**.
- AIS **vessel names and your notes** are cached on the backend, so they survive
  restarts and reappear the moment a known ship is heard again.

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
| ISM sensors / TPMS | 433.92 · 868.3 MHz (also 315/915) | ISM mode |
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
- **ADS-B/AIS/DAB/ISM say a tool is missing** — install `dump1090` /
  `rtl_433` (both `brew install`) or build `AIS-catcher` / `welle-cli` (see the
  README).
