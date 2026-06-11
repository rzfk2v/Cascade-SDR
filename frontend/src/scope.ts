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

  constructor(canvas: HTMLCanvasElement) {
    this.ctx = canvas.getContext("2d")!;
    this.w = canvas.width;
    this.h = canvas.height;
  }

  clear(): void {
    this.last = null;
    this.ctx.clearRect(0, 0, this.w, this.h);
  }

  pushRow(row: Float32Array): void {
    this.last = row;
    this.autoRange(row);
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

    if (!this.last) return;

    // trace
    const n = this.last.length;
    ctx.beginPath();
    for (let x = 0; x < this.w; x++) {
      const bin = Math.min(n - 1, ((x * n) / this.w) | 0);
      const y = this.yOf(this.last[bin]);
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
