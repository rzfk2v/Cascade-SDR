// Built-in frequency directory: a browsable, click-to-tune reference of known
// channels so you can see *what* a frequency is for, not just where you landed.
//
// Everything here is from deterministic public channel plans (ITU marine VHF,
// PMR446, IARU Region-1 ham, weather-sat APT, ISM) plus locally-published
// airport frequencies — all legal to bundle and stable over time. Users can
// extend it at runtime with their own list (see the CSV/JSON import in main.ts).
//
// Most categories apply anywhere in ITU Region 1, but the Airband list is the
// author's local one (Stockholm — Arlanda/Bromma). Editing it here changes the
// bundled defaults; users elsewhere can instead import their own list and untick
// "Show built-in lists" to replace these at runtime.

export type DirDemod = "wfm" | "nfm" | "am" | "usb" | "lsb";

export interface FreqEntry {
  mhz: number;
  name: string;
  demod: DirDemod;
  note?: string; // short purpose, shown muted
}

export interface FreqCategory {
  id: string;
  name: string;
  entries: FreqEntry[];
}

// --- Marine VHF (international, ITU) --------------------------------------
// Ship-station transmit frequencies for the simplex/lower leg, which is what an
// SDR ashore receives from vessels. Duplex public-correspondence channels also
// have a coast-station leg (160–161 MHz); AIS sits on the duplex 'B' slots.
const MARINE_NAMES: Record<number, string> = {
  6: "Ship-to-ship (safety)",
  8: "Ship-to-ship",
  9: "Ship-to-ship / marina",
  10: "Ship-to-ship",
  12: "Port operations",
  13: "Bridge-to-bridge (nav safety)",
  14: "Port operations",
  16: "Distress & calling",
  67: "Small-craft safety",
  68: "Marina / port",
  69: "Ship-to-ship",
  70: "DSC (digital calling, data)",
  71: "Marina / port",
  72: "Ship-to-ship",
  73: "Port operations",
  74: "Port operations",
  77: "Ship-to-ship",
};

function marineChannels(): FreqEntry[] {
  const out: FreqEntry[] = [];
  const add = (n: number, mhz: number) => {
    out.push({
      mhz: +mhz.toFixed(4),
      name: `CH${String(n).padStart(2, "0")}`,
      demod: "nfm",
      note: MARINE_NAMES[n],
    });
  };
  // Channels 1–28 (ship leg = 156.050 + (n-1)·0.05) and 60–88 (156.025 + (n-60)·0.05).
  for (let n = 1; n <= 28; n++) add(n, 156.05 + (n - 1) * 0.05);
  for (let n = 60; n <= 88; n++) add(n, 156.025 + (n - 60) * 0.05);
  out.sort((a, b) => a.mhz - b.mhz);
  // AIS rides the duplex 'B' coast slots, away from the simplex ship freqs above.
  out.push({ mhz: 161.975, name: "CH87B · AIS 1", demod: "nfm", note: "vessel positions (use AIS mode)" });
  out.push({ mhz: 162.025, name: "CH88B · AIS 2", demod: "nfm", note: "vessel positions (use AIS mode)" });
  return out;
}

// --- PMR446 (licence-free UHF) -------------------------------------------
function pmr446Channels(): FreqEntry[] {
  const out: FreqEntry[] = [];
  for (let n = 1; n <= 16; n++) {
    out.push({
      mhz: +(446.00625 + (n - 1) * 0.0125).toFixed(5),
      name: `PMR446 ch${n}`,
      demod: "nfm",
      note: n <= 8 ? undefined : "extended channel",
    });
  }
  return out;
}

export const FREQ_DIRECTORY: FreqCategory[] = [
  {
    id: "marine",
    name: "Marine VHF",
    entries: marineChannels(),
  },
  {
    id: "airband",
    name: "Airband (Stockholm)",
    entries: [
      { mhz: 118.5, name: "Arlanda Tower", demod: "am" },
      { mhz: 121.7, name: "Arlanda Ground", demod: "am" },
      { mhz: 119.0, name: "Arlanda ATIS", demod: "am", note: "recorded info" },
      { mhz: 123.75, name: "Arlanda Approach", demod: "am" },
      { mhz: 121.825, name: "Arlanda Clearance", demod: "am" },
      { mhz: 118.1, name: "Bromma Tower", demod: "am" },
      { mhz: 121.6, name: "Bromma Ground", demod: "am" },
      { mhz: 122.45, name: "Bromma ATIS", demod: "am", note: "recorded info" },
      { mhz: 120.15, name: "Bromma Approach", demod: "am" },
      { mhz: 119.4, name: "Bromma AFIS", demod: "am" },
      { mhz: 121.5, name: "Emergency / guard", demod: "am", note: "international air distress" },
    ],
  },
  {
    id: "ham",
    name: "Ham · APRS · satellites",
    entries: [
      { mhz: 144.3, name: "2 m SSB calling", demod: "usb" },
      { mhz: 144.8, name: "APRS", demod: "nfm", note: "packet — use APRS mode to decode" },
      { mhz: 145.5, name: "2 m FM calling", demod: "nfm" },
      { mhz: 145.825, name: "ISS APRS digipeater", demod: "nfm", note: "when overhead" },
      { mhz: 432.2, name: "70 cm SSB calling", demod: "usb" },
      { mhz: 433.5, name: "70 cm FM calling", demod: "nfm" },
      { mhz: 145.8, name: "2 m satellite downlinks", demod: "nfm", note: "145.8–146.0 segment" },
      { mhz: 437.5, name: "70 cm satellite downlinks", demod: "nfm", note: "cubesats, varies" },
    ],
  },
  {
    id: "wxsat",
    name: "Weather sat · ISM",
    entries: [
      { mhz: 137.62, name: "NOAA-15 APT", demod: "wfm", note: "use APT mode to decode" },
      { mhz: 137.9125, name: "NOAA-18 APT", demod: "wfm", note: "use APT mode to decode" },
      { mhz: 137.1, name: "NOAA-19 / Meteor", demod: "wfm", note: "use APT mode to decode" },
      { mhz: 433.92, name: "ISM 433 (sensors/remotes)", demod: "nfm", note: "use ISM mode to decode" },
      { mhz: 868.3, name: "ISM 868 (SRD devices)", demod: "nfm", note: "use ISM mode to decode" },
    ],
  },
  {
    id: "pmr",
    name: "PMR446 (licence-free)",
    entries: pmr446Channels(),
  },
];
