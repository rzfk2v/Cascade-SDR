// Band plan for identifying what service occupies a frequency.
// Oriented to ITU Region 1 / Sweden. Ranges are in MHz and intentionally
// approximate — for orientation ("what kind of traffic"), not licensing.
//
// Entries flagged `c: true` are channel/segment-level detail (e.g. Marine CH16):
// they're returned for a single tuned frequency (bandAt) but kept out of the
// wide-span list (bandsInSpan) so the spectrum/scan label stays uncluttered.

interface Band {
  lo: number; // MHz
  hi: number;
  name: string;
  c?: boolean; // channel-level detail
}

const BANDS: Band[] = [
  // --- broad services ---
  // HF (reachable with an upconverter — Reception ▸ Advanced)
  { lo: 0.148, hi: 0.283, name: "LW broadcast" },
  { lo: 0.526, hi: 1.606, name: "MW broadcast (AM)" },
  { lo: 1.81, hi: 2.0, name: "160 m ham" },
  { lo: 3.5, hi: 3.8, name: "80 m ham" },
  { lo: 5.9, hi: 6.2, name: "49 m SW broadcast" },
  { lo: 7.0, hi: 7.2, name: "40 m ham" },
  { lo: 7.2, hi: 7.45, name: "41 m SW broadcast" },
  { lo: 9.4, hi: 9.9, name: "31 m SW broadcast" },
  { lo: 11.6, hi: 12.1, name: "25 m SW broadcast" },
  { lo: 14.0, hi: 14.35, name: "20 m ham" },
  { lo: 15.1, hi: 15.83, name: "19 m SW broadcast" },
  { lo: 18.068, hi: 18.168, name: "17 m ham" },
  { lo: 21.0, hi: 21.45, name: "15 m ham" },
  { lo: 24.89, hi: 24.99, name: "12 m ham" },
  { lo: 26.965, hi: 27.405, name: "CB radio (27 MHz)" },
  { lo: 28.0, hi: 29.7, name: "10 m ham" },
  { lo: 50.0, hi: 52.0, name: "6 m ham" },
  { lo: 87.5, hi: 108.0, name: "FM broadcast" },
  { lo: 108.0, hi: 117.975, name: "Aviation nav (VOR/ILS)" },
  { lo: 118.0, hi: 136.975, name: "Airband (AM voice)" },
  { lo: 137.0, hi: 138.0, name: "Weather satellites" },
  { lo: 144.0, hi: 146.0, name: "2 m ham" },
  { lo: 146.0, hi: 156.0, name: "VHF land mobile" },
  { lo: 156.0, hi: 162.05, name: "Marine VHF" },
  { lo: 162.05, hi: 174.0, name: "VHF land mobile / pagers" },
  { lo: 174.0, hi: 240.0, name: "DAB / VHF Band III" },
  { lo: 315.0, hi: 390.0, name: "Military air (UHF)" },
  { lo: 380.0, hi: 400.0, name: "TETRA (emergency / Rakel)" },
  { lo: 406.0, hi: 410.0, name: "Sat distress / land mobile" },
  { lo: 430.0, hi: 440.0, name: "70 cm ham" },
  { lo: 440.0, hi: 470.0, name: "UHF business/PMR" },
  { lo: 470.0, hi: 694.0, name: "UHF TV (DVB-T)" },
  { lo: 694.0, hi: 790.0, name: "Mobile (700 MHz)" },
  { lo: 791.0, hi: 862.0, name: "Mobile LTE (800 MHz)" },
  { lo: 863.0, hi: 870.0, name: "SRD / 868 MHz devices" },
  { lo: 880.0, hi: 960.0, name: "GSM 900 (mobile)" },
  { lo: 1087.0, hi: 1093.0, name: "ADS-B (Mode S, 1090 MHz)" },
  { lo: 1452.0, hi: 1492.0, name: "L-band (mobile / DAB-L)" },
  { lo: 1525.0, hi: 1559.0, name: "Inmarsat satellite (down)" },
  { lo: 1559.0, hi: 1610.0, name: "GNSS (GPS/Galileo L1)" },
  { lo: 1610.0, hi: 1626.5, name: "Iridium satellite" },

  // --- channel / segment detail ---
  { lo: 121.45, hi: 121.55, name: "Air emergency 121.5", c: true },
  { lo: 131.7, hi: 131.75, name: "ACARS (aircraft data)", c: true },
  { lo: 137.095, hi: 137.105, name: "NOAA-19 / Meteor APT", c: true },
  { lo: 137.615, hi: 137.625, name: "NOAA-15 APT", c: true },
  { lo: 137.905, hi: 137.92, name: "NOAA-18 / Meteor APT", c: true },
  { lo: 144.79, hi: 144.81, name: "APRS 144.800", c: true },
  { lo: 145.49, hi: 145.51, name: "2 m FM calling 145.500", c: true },
  { lo: 145.8, hi: 146.0, name: "2 m satellite segment", c: true },
  { lo: 156.79, hi: 156.81, name: "Marine CH16 (distress)", c: true },
  { lo: 156.52, hi: 156.53, name: "Marine CH70 (DSC)", c: true },
  { lo: 161.97, hi: 161.98, name: "AIS 1 (CH87B)", c: true },
  { lo: 162.02, hi: 162.03, name: "AIS 2 (CH88B)", c: true },
  { lo: 433.05, hi: 434.79, name: "ISM 433 (remotes/sensors)", c: true },
  { lo: 446.0, hi: 446.2, name: "PMR446", c: true },
  { lo: 1575.0, hi: 1575.84, name: "GPS L1 1575.42", c: true },
];

// Narrowest band containing a single frequency (channel-level preferred).
export function bandAt(mhz: number): string {
  let best: Band | null = null;
  for (const b of BANDS) {
    if (mhz >= b.lo && mhz <= b.hi) {
      if (!best || b.hi - b.lo < best.hi - best.lo) best = b;
    }
  }
  return best ? best.name : "";
}

// Distinct broad services overlapping a span (channels excluded to stay readable).
export function bandsInSpan(loMhz: number, hiMhz: number): string[] {
  const names: string[] = [];
  for (const b of BANDS) {
    if (b.c) continue;
    if (b.hi >= loMhz && b.lo <= hiMhz && !names.includes(b.name)) {
      names.push(b.name);
    }
  }
  return names;
}
