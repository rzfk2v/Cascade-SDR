// Scrolling waterfall renderer.
//
// Each incoming FFT row (float32 dBFS, length = fft_size) is colour-mapped and
// drawn as a 1px line at the top; the existing image scrolls down by 1px. This
// "blit the canvas onto itself shifted by one row" trick is cheap and smooth.

export class Waterfall {
  private ctx: CanvasRenderingContext2D;
  private w: number;
  private h: number;
  private rowImage: ImageData;
  // dBFS range mapped to the colour ramp. Auto-adjusts slowly toward the signal.
  private floor = -90;
  private ceil = -20;

  constructor(private canvas: HTMLCanvasElement) {
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) throw new Error("2D canvas unavailable");
    this.ctx = ctx;
    this.w = canvas.width;
    this.h = canvas.height;
    this.rowImage = this.ctx.createImageData(this.w, 1);
    this.ctx.fillStyle = "#000";
    this.ctx.fillRect(0, 0, this.w, this.h);
  }

  pushRow(row: Float32Array): void {
    // Scroll everything down by one pixel.
    this.ctx.drawImage(this.canvas, 0, 0, this.w, this.h - 1, 0, 1, this.w, this.h - 1);

    this.autoRange(row);
    const span = Math.max(1, this.ceil - this.floor);
    const px = this.rowImage.data;
    const n = row.length;
    for (let x = 0; x < this.w; x++) {
      // Map canvas column -> FFT bin.
      const bin = Math.min(n - 1, (x * n / this.w) | 0);
      const norm = Math.min(1, Math.max(0, (row[bin] - this.floor) / span));
      const [r, g, b] = colormap(norm);
      const o = x * 4;
      px[o] = r;
      px[o + 1] = g;
      px[o + 2] = b;
      px[o + 3] = 255;
    }
    this.ctx.putImageData(this.rowImage, 0, 0);
  }

  private autoRange(row: Float32Array): void {
    // Track a slow min/max so the colours adapt to gain/antenna without flicker.
    let mn = Infinity;
    let mx = -Infinity;
    for (let i = 0; i < row.length; i++) {
      const v = row[i];
      if (v < mn) mn = v;
      if (v > mx) mx = v;
    }
    if (!isFinite(mn) || !isFinite(mx)) return;
    const a = 0.02;
    this.floor += a * (mn - 3 - this.floor);
    this.ceil += a * (mx + 3 - this.ceil);
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
