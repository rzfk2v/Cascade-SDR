// Frequency axis + tuning overlay drawn on top of the waterfall.
//
// Maps screen position <-> RF frequency for the captured band
// [center - rate/2, center + rate/2], renders a frequency scale, a tuned-channel
// cursor with its bandwidth shaded, and handles click-to-tune / drag-to-select.

export class Tuner {
  centerFreq = 100e6;
  sampleRate = 2.4e6;
  tuned = 100e6;
  bandwidth = 200e3;
  active = false; // show the cursor (radio mode)

  onTune?: (freqHz: number) => void;
  onBandwidth?: (bwHz: number, centerHz: number) => void;

  private octx: CanvasRenderingContext2D;
  private actx: CanvasRenderingContext2D;
  private dragStart: number | null = null; // fraction [0,1]
  private dragCur = 0;

  constructor(
    private overlay: HTMLCanvasElement,
    private axis: HTMLCanvasElement,
  ) {
    this.octx = overlay.getContext("2d")!;
    this.actx = axis.getContext("2d")!;
    overlay.addEventListener("mousedown", (e) => this.onDown(e));
    overlay.addEventListener("mousemove", (e) => this.onMove(e));
    window.addEventListener("mouseup", (e) => this.onUp(e));
  }

  setBand(centerFreq: number, sampleRate: number): void {
    this.centerFreq = centerFreq;
    this.sampleRate = sampleRate;
    this.drawAxis();
    this.draw();
  }
  setTuned(freqHz: number): void {
    this.tuned = freqHz;
    this.draw();
  }
  setBandwidth(bwHz: number): void {
    this.bandwidth = bwHz;
    this.draw();
  }
  setActive(active: boolean): void {
    this.active = active;
    this.draw();
  }

  // --- mapping -----------------------------------------------------------
  private freqAt(fraction: number): number {
    return this.centerFreq + (fraction - 0.5) * this.sampleRate;
  }
  private fracOf(freqHz: number): number {
    return 0.5 + (freqHz - this.centerFreq) / this.sampleRate;
  }
  private eventFraction(e: MouseEvent): number {
    const r = this.overlay.getBoundingClientRect();
    return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  }

  // --- interaction -------------------------------------------------------
  private onDown(e: MouseEvent): void {
    this.dragStart = this.eventFraction(e);
    this.dragCur = this.dragStart;
  }
  private onMove(e: MouseEvent): void {
    if (this.dragStart == null) return;
    this.dragCur = this.eventFraction(e);
    this.draw();
  }
  private onUp(e: MouseEvent): void {
    if (this.dragStart == null) return;
    const start = this.dragStart;
    const end = this.eventFraction(e);
    this.dragStart = null;
    const f1 = this.freqAt(start);
    const f2 = this.freqAt(end);
    if (Math.abs(end - start) < 0.004) {
      // treat as a click -> tune here
      this.onTune?.(this.freqAt(end));
    } else {
      const center = (f1 + f2) / 2;
      const bw = Math.abs(f2 - f1);
      this.onBandwidth?.(bw, center);
      this.onTune?.(center);
    }
    this.draw();
  }

  // --- rendering ---------------------------------------------------------
  draw(): void {
    const w = this.overlay.width;
    const h = this.overlay.height;
    this.octx.clearRect(0, 0, w, h);

    // live drag selection
    if (this.dragStart != null) {
      const x0 = Math.min(this.dragStart, this.dragCur) * w;
      const x1 = Math.max(this.dragStart, this.dragCur) * w;
      this.octx.fillStyle = "rgba(47,129,247,0.25)";
      this.octx.fillRect(x0, 0, x1 - x0, h);
    }

    if (!this.active) return;

    // tuned channel band
    const cx = this.fracOf(this.tuned) * w;
    const halfPx = (this.bandwidth / this.sampleRate) * w * 0.5;
    this.octx.fillStyle = "rgba(255,255,255,0.12)";
    this.octx.fillRect(cx - halfPx, 0, halfPx * 2, h);
    // center cursor
    this.octx.strokeStyle = "#ff5b5b";
    this.octx.lineWidth = 1.5;
    this.octx.beginPath();
    this.octx.moveTo(cx, 0);
    this.octx.lineTo(cx, h);
    this.octx.stroke();
  }

  drawAxis(): void {
    const w = this.axis.width;
    const h = this.axis.height;
    this.actx.clearRect(0, 0, w, h);
    this.actx.fillStyle = "#8b949e";
    this.actx.font = "11px -apple-system, system-ui, sans-serif";
    this.actx.textBaseline = "top";
    const ticks = 9;
    for (let i = 0; i < ticks; i++) {
      const frac = i / (ticks - 1);
      const x = frac * w;
      const mhz = this.freqAt(frac) / 1e6;
      this.actx.strokeStyle = "#30363d";
      this.actx.beginPath();
      this.actx.moveTo(x, 0);
      this.actx.lineTo(x, 6);
      this.actx.stroke();
      const label = mhz.toFixed(3);
      let tx = x + 3;
      if (i === ticks - 1) tx = x - this.actx.measureText(label).width - 3;
      else if (i === 0) tx = x + 2;
      else tx = x - this.actx.measureText(label).width / 2;
      this.actx.fillText(label, tx, 8);
    }
  }
}
