// Rough band plan for identifying what service occupies a frequency.
// Oriented to ITU Region 1 / Sweden. Ranges are in MHz and intentionally
// approximate — this is for orientation ("what kind of traffic"), not licensing.

interface Band {
  lo: number; // MHz
  hi: number;
  name: string;
}

// Order doesn't matter for span listing; for a point we pick the narrowest match.
const BANDS: Band[] = [
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
  { lo: 161.95, hi: 162.05, name: "Marine AIS" },
  { lo: 174.0, hi: 240.0, name: "DAB / VHF Band III" },
  { lo: 225.0, hi: 400.0, name: "Military air (UHF)" },
  { lo: 380.0, hi: 400.0, name: "TETRA (emergency services)" },
  { lo: 430.0, hi: 440.0, name: "70 cm ham" },
  { lo: 446.0, hi: 446.2, name: "PMR446" },
  { lo: 450.0, hi: 470.0, name: "UHF business/PMR" },
  { lo: 470.0, hi: 694.0, name: "UHF TV (DVB-T)" },
  { lo: 694.0, hi: 790.0, name: "Mobile (700 MHz)" },
  { lo: 791.0, hi: 862.0, name: "Mobile LTE (800 MHz)" },
  { lo: 880.0, hi: 960.0, name: "GSM 900 (mobile)" },
  { lo: 1087.0, hi: 1093.0, name: "ADS-B (Mode S, 1090 MHz)" },
  { lo: 1559.0, hi: 1610.0, name: "GNSS (GPS/Galileo L1)" },
];

// Narrowest band containing a single frequency (most specific service).
export function bandAt(mhz: number): string {
  let best: Band | null = null;
  for (const b of BANDS) {
    if (mhz >= b.lo && mhz <= b.hi) {
      if (!best || b.hi - b.lo < best.hi - best.lo) best = b;
    }
  }
  return best ? best.name : "";
}

// Distinct services overlapping a span (for the wide spectrum / scan view).
export function bandsInSpan(loMhz: number, hiMhz: number): string[] {
  const names: string[] = [];
  for (const b of BANDS) {
    if (b.hi >= loMhz && b.lo <= hiMhz && !names.includes(b.name)) {
      names.push(b.name);
    }
  }
  return names;
}
