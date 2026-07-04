// Scrolling waterfall renderer.
//
// Each incoming FFT row (float32 dBFS, length = fft_size) is colour-mapped and
// drawn as a thin line at the top; the existing image scrolls down by one row.
// This "blit the canvas onto itself shifted by one row" trick is cheap and
// smooth. On HiDPI screens the canvas backing store is device-pixel sized and
// each FFT row is `rowH` (≈ devicePixelRatio) pixels tall, so the scroll speed
// in CSS pixels stays the same while the horizontal resolution doubles.

export class Waterfall {
  private ctx: CanvasRenderingContext2D;
  private w: number;
  private h: number;
  private rowH = 1; // device-pixel height of one FFT row
  private rowImage: ImageData;
  // dBFS range mapped to the colour ramp. Auto-adjusts slowly toward the signal,
  // unless the user sets a manual range.
  private floor = -90;
  private ceil = -20;
  private auto = true;
  // visible fraction of the FFT (display zoom); (0,1) = whole band
  private viewLo = 0;
  private viewHi = 1;

  constructor(private canvas: HTMLCanvasElement) {
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("2D canvas unavailable");
    this.ctx = ctx;
    this.w = canvas.width;
    this.h = canvas.height;
    this.rowImage = this.ctx.createImageData(Math.max(1, this.w), 1);
    this.ctx.fillStyle = "#000";
    this.ctx.fillRect(0, 0, this.w, this.h);
  }

  // Resize the backing store to a new pixel size (history is cleared).
  // `rowH` is the device-pixel height of one FFT row (≈ devicePixelRatio).
  resize(w: number, h: number, rowH = 1): void {
    this.canvas.width = w;
    this.canvas.height = h;
    this.w = w;
    this.h = h;
    this.rowH = Math.max(1, Math.round(rowH));
    this.rowImage = this.ctx.createImageData(Math.max(1, w), this.rowH);
    this.clear();
  }

  pushRow(row: Float32Array): void {
    // Scroll everything down by one row.
    const rh = Math.min(this.rowH, this.h);
    this.ctx.drawImage(this.canvas, 0, 0, this.w, this.h - rh, 0, rh, this.w, this.h - rh);

    this.autoRange(row);
    const span = Math.max(1, this.ceil - this.floor);
    const px = this.rowImage.data;
    const n = row.length;
    const vspan = this.viewHi - this.viewLo;
    for (let x = 0; x < this.w; x++) {
      // Map the pixel column to its span of FFT bins and take the max over that
      // span, so narrow signals stay visible when there are more bins than
      // pixels (nearest-bin sampling would skip bins and drop narrow carriers).
      const b0 = Math.min(n - 1, ((this.viewLo + (x / this.w) * vspan) * n) | 0);
      const b1 = Math.min(n, Math.max(b0 + 1, ((this.viewLo + ((x + 1) / this.w) * vspan) * n) | 0));
      let v = row[b0];
      for (let b = b0 + 1; b < b1; b++) if (row[b] > v) v = row[b];
      let t = (v - this.floor) / span;
      t = t < 0 ? 0 : t > 1 ? 1 : t;
      const c = ((t * 255) | 0) * 3;
      const o = x * 4;
      px[o] = LUT[c];
      px[o + 1] = LUT[c + 1];
      px[o + 2] = LUT[c + 2];
      px[o + 3] = 255;
    }
    // Replicate the colour-mapped line into the remaining rowH-1 lines.
    const stride = this.w * 4;
    for (let k = 1; k < rh; k++) px.copyWithin(k * stride, 0, stride);
    this.ctx.putImageData(this.rowImage, 0, 0);
  }

  // Display zoom: show only [lo,hi] of the FFT. History can't be re-mapped, so
  // clear and rebuild from the new mapping.
  setView(lo: number, hi: number): void {
    this.viewLo = lo;
    this.viewHi = hi;
    this.clear();
  }

  // Manual contrast: fix the colour-map dB window. Pass auto=true to resume
  // automatic ranging.
  setRange(floor: number, ceil: number, auto: boolean): void {
    this.auto = auto;
    if (!auto) {
      this.floor = Math.min(floor, ceil - 1);
      this.ceil = Math.max(ceil, floor + 1);
    }
  }

  private autoRange(row: Float32Array): void {
    if (!this.auto) return;
    // Percentile-based ranging: the ~30th percentile sits in the noise "grass"
    // and the ~98th near the strongest wanted signals, so a single strong
    // carrier can't drag the ceiling up and wash out the rest of the band
    // (raw min/max ranging did exactly that). Smoothed so colours adapt to
    // gain/antenna changes without flicker.
    const n = row.length;
    const stride = Math.max(1, (n / 512) | 0);
    const samp: number[] = [];
    for (let i = 0; i < n; i += stride) samp.push(row[i]);
    samp.sort((a, b) => a - b);
    const lo = samp[(samp.length * 0.3) | 0];
    const hi = samp[Math.min(samp.length - 1, (samp.length * 0.98) | 0)];
    if (!isFinite(lo) || !isFinite(hi)) return;
    const a = 0.02;
    this.floor += a * (lo - 4 - this.floor);
    this.ceil += a * (Math.max(hi, lo + 20) + 8 - this.ceil);
  }

  clear(): void {
    this.ctx.fillStyle = "#000";
    this.ctx.fillRect(0, 0, this.w, this.h);
  }
}

// Perceptual-ish blue->cyan->yellow->red ramp (à la classic SDR waterfalls).
function colormap(t: number): [number, number, number] {
  const stops: [number, number, number, number][] = [
    [0.0, 0, 0, 30],
    [0.25, 0, 60, 170],
    [0.5, 0, 200, 200],
    [0.75, 240, 220, 40],
    [1.0, 240, 40, 20],
  ];
  for (let i = 1; i < stops.length; i++) {
    if (t <= stops[i][0]) {
      const [t0, r0, g0, b0] = stops[i - 1];
      const [t1, r1, g1, b1] = stops[i];
      const f = (t - t0) / (t1 - t0);
      return [r0 + f * (r1 - r0), g0 + f * (g1 - g0), b0 + f * (b1 - b0)].map(Math.round) as [
        number,
        number,
        number,
      ];
    }
  }
  return [240, 40, 20];
}

// 256-entry RGB lookup of the ramp, precomputed once — pushRow runs per pixel
// per row, and interpolating there costs allocations + GC on the same thread
// that feeds the ScriptProcessor audio fallback.
const LUT = new Uint8Array(256 * 3);
for (let i = 0; i < 256; i++) {
  const [r, g, b] = colormap(i / 255);
  LUT[i * 3] = r;
  LUT[i * 3 + 1] = g;
  LUT[i * 3 + 2] = b;
}
