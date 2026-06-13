// Dipole tuning helper for the RTL-SDR.com dipole antenna kit.
//
// The kit has two telescopic element sets: LARGE (23 cm–1 m) and SMALL
// (5–13 cm), each with ~2 cm of metal hidden inside the base. A half-wave
// dipole wants each leg at ~λ/4; fitting the kit's published resonance
// cheat-sheet gives total_leg_cm ≈ 7125 / f(MHz) (≈0.95 velocity factor,
// including the 2 cm internal). We tell the user which set to use, how far to
// extend each rod (the visible length = total − 2 cm), and the orientation.

const INTERNAL_CM = 2;          // metal hidden inside the element base
const LARGE_MAX = 102;          // 100 cm visible + 2 cm internal
const LARGE_MIN = 25;           // 23 cm visible + 2 cm internal
const SMALL_MAX = 15;           // 13 cm visible + 2 cm internal
const SMALL_MIN = 7;            //  5 cm visible + 2 cm internal

export interface AntennaAdvice {
  set: "large" | "small";
  lengthCm: number;             // visible length to extend EACH rod to
  orientation: string;
  note?: string;
  fMHz: number;
}

function visible(totalCm: number): number {
  return Math.max(0, Math.round(totalCm - INTERNAL_CM));
}

export function antennaAdvice(fMHz: number): AntennaAdvice | null {
  if (!isFinite(fMHz) || fMHz <= 0) return null;
  const target = 7125 / fMHz;   // ideal total leg length (cm), incl. internal

  // Orientation: 137 MHz weather sats want a horizontal "V"; else vertical.
  const orientation =
    fMHz >= 135 && fMHz <= 138
      ? 'horizontal "V" (~120°) for 137 MHz satellites'
      : "vertical (rods straight up & down)";

  let set: "large" | "small";
  let lengthCm: number;
  let note: string | undefined;

  if (target > LARGE_MAX) {
    // below ~70 MHz the kit can't reach a quarter wave
    set = "large";
    lengthCm = visible(LARGE_MAX);
    note = "below ~70 MHz — extend the large rods fully; the kit can't fully resonate this low.";
  } else if (target >= LARGE_MIN) {
    set = "large";
    lengthCm = visible(target);
  } else if (target >= SMALL_MAX) {
    // gap between the two sets (~285–475 MHz): use whichever end is closer
    if (Math.abs(target - LARGE_MIN) <= Math.abs(target - SMALL_MAX)) {
      set = "large";
      lengthCm = visible(LARGE_MIN);     // fully collapsed large
    } else {
      set = "small";
      lengthCm = visible(SMALL_MAX);     // fully extended small
    }
    note = "between the two element sets — closest fit; slightly off resonance.";
  } else if (target >= SMALL_MIN) {
    set = "small";
    lengthCm = visible(target);
  } else {
    set = "small";
    lengthCm = visible(SMALL_MIN);
    note = "above ~1 GHz — collapse the small rods (~5 cm).";
  }

  return { set, lengthCm, orientation, note, fMHz };
}

export function antennaText(fMHz: number): string {
  const a = antennaAdvice(fMHz);
  if (!a) return "";
  const rods = a.set === "large" ? "long (large) rods" : "short (small) rods";
  return `📡 ${rods}, ~${a.lengthCm} cm each · ${a.orientation}` +
    (a.note ? ` — ${a.note}` : "");
}
