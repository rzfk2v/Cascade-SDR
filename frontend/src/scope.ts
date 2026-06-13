// Real-time spectrum line scope drawn above the waterfall. Renders the most
// recent FFT row as a trace (dBFS vs frequency) with a light dB grid. Shares the
// same horizontal frequency mapping as the waterfall/overlay.

export class SpectrumScope {
  private ctx: CanvasRenderingContext2D;
  private w: number;
  private h: number;
  private floor = -90;
  private ceil = -10;
  private last: Float32Array | null = null;
  private disp: Float32Array | null = null; // what we draw (raw, or averaged)
  private avg: Float32Array | null = null;
  private avgN = 1; // 1 = averaging off; N = exponential mean over ~N rows
  private peak: Float32Array | null = null;
  private peakHold = false;
  private peakDecayDbPerSec = 24; // peaks linger then fade over ~1-2 s
  private lastPeakTime = 0;
  private viewLo = 0; // visible fraction of the FFT (display zoom)
  private viewHi = 1;

  constructor(private canvas: HTMLCanvasElement) {
    this.ctx = canvas.getContext("2d")!;
    this.w = canvas.width;
    this.h = canvas.height;
  }

  resize(w: number, h: number): void {
    this.canvas.width = w;
    this.canvas.height = h;
    this.w = w;
    this.h = h;
    this.draw();
  }

  clear(): void {
    this.last = null;
    this.disp = null;
    this.avg = null;
    this.peak = null;
    this.ctx.clearRect(0, 0, this.w, this.h);
  }

  setPeakHold(on: boolean): void {
    this.peakHold = on;
    this.peak = null; // reset accumulation
    this.lastPeakTime = 0;
  }

  setAveraging(n: number): void {
    this.avgN = n >= 1 ? n : 1;
    this.avg = null; // restart the running mean
  }

  setView(lo: number, hi: number): void {
    this.viewLo = lo;
    this.viewHi = hi;
    this.draw();
  }

  pushRow(row: Float32Array): void {
    this.last = row;
    if (this.peakHold) {
      const now = performance.now();
      const dt = this.lastPeakTime ? (now - this.lastPeakTime) / 1000 : 0;
      this.lastPeakTime = now;
      const drop = this.peakDecayDbPerSec * dt; // dB this peak decays since last row
      if (!this.peak || this.peak.length !== row.length) {
        this.peak = row.slice();
      } else {
        for (let i = 0; i < row.length; i++) {
          // hold the max, but let it sag so transients show briefly then fade
          const decayed = this.peak[i] - drop;
          this.peak[i] = row[i] > decayed ? row[i] : decayed;
        }
      }
    }
    // spectrum averaging: exponential running mean over ~avgN rows, smooths
    // the noise floor so weak/steady carriers stand out (peaks still tracked raw)
    if (this.avgN > 1) {
      const a = 1 / this.avgN;
      if (!this.avg || this.avg.length !== row.length) {
        this.avg = row.slice();
      } else {
        for (let i = 0; i < row.length; i++) this.avg[i] += a * (row[i] - this.avg[i]);
      }
      this.disp = this.avg;
    } else {
      this.disp = row;
    }
    this.autoRange(this.disp);
    this.draw();
  }

  private autoRange(row: Float32Array): void {
    let mn = Infinity;
    let mx = -Infinity;
    for (let i = 0; i < row.length; i++) {
      const v = row[i];
      if (v < mn) mn = v;
      if (v > mx) mx = v;
    }
    if (!isFinite(mn) || !isFinite(mx)) return;
    const a = 0.05;
    this.floor += a * (mn - 5 - this.floor);
    this.ceil += a * (mx + 8 - this.ceil);
  }

  private yOf(db: number): number {
    const span = Math.max(1, this.ceil - this.floor);
    const t = (db - this.floor) / span; // 0 bottom .. 1 top
    return this.h - t * this.h;
  }

  draw(): void {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.w, this.h);

    // dB grid (horizontal lines every ~20 dB)
    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.lineWidth = 1;
    ctx.fillStyle = "#5b6470";
    ctx.font = "10px -apple-system, system-ui, sans-serif";
    const step = 20;
    const start = Math.ceil(this.floor / step) * step;
    for (let db = start; db < this.ceil; db += step) {
      const y = this.yOf(db);
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(this.w, y);
      ctx.stroke();
      ctx.fillText(`${db}`, 2, y - 2);
    }

    const vspan = this.viewHi - this.viewLo;

    // peak-hold trace (behind the live trace)
    if (this.peak) {
      const pn = this.peak.length;
      ctx.beginPath();
      for (let x = 0; x < this.w; x++) {
        const frac = this.viewLo + (x / this.w) * vspan;
        const bin = Math.min(pn - 1, (frac * pn) | 0);
        const y = this.yOf(this.peak[bin]);
        if (x === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = "rgba(248,81,73,0.7)";
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    const trace = this.disp ?? this.last;
    if (!trace) return;

    // trace
    const n = trace.length;
    ctx.beginPath();
    for (let x = 0; x < this.w; x++) {
      const frac = this.viewLo + (x / this.w) * vspan;
      const bin = Math.min(n - 1, (frac * n) | 0);
      const y = this.yOf(trace[bin]);
      if (x === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = "#4dd0e1";
    ctx.lineWidth = 1.25;
    ctx.stroke();
    // soft fill under the trace
    ctx.lineTo(this.w, this.h);
    ctx.lineTo(0, this.h);
    ctx.closePath();
    ctx.fillStyle = "rgba(77,208,225,0.08)";
    ctx.fill();
  }
}
