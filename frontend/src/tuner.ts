// Frequency axis + tuning overlay drawn on top of the waterfall.
//
// Maps screen position <-> RF frequency for the captured band
// [center - rate/2, center + rate/2], renders a frequency scale, a tuned-channel
// cursor with its bandwidth shaded, and handles click-to-tune / drag-to-select.

// Extra receivers (VFO B/C/D) shown as their own tuning cursors.
export type SubVfoMarker = { on: boolean; freq: number; bw: number };
const SUB_VFO_COLORS = ["#58a6ff", "#3fb950", "#d29922"];
const SUB_VFO_LABELS = ["B", "C", "D"];

export class Tuner {
  centerFreq = 100e6;
  sampleRate = 2.4e6;
  tuned = 100e6;
  bandwidth = 200e3;
  active = false; // show the cursor (radio mode)
  subVfos: SubVfoMarker[] = [];
  // Backing stores are devicePixelRatio-scaled (set by layoutCanvases); drawing
  // stays in CSS px via a canvas transform so lines/text render crisp on HiDPI.
  dpr = 1;

  // Display zoom: the visible window as a fraction [viewLo, viewHi] of the
  // captured band. (0,1) = whole band; narrower = zoomed in. Pure display — the
  // dongle still captures the full band.
  viewLo = 0;
  viewHi = 1;

  onTune?: (freqHz: number) => void;        // click
  onSelect?: (loHz: number, hiHz: number) => void;  // drag a range
  onViewChange?: (lo: number, hi: number) => void;  // zoom/pan -> sync renderers

  private octx: CanvasRenderingContext2D;
  private actx: CanvasRenderingContext2D;
  private dragStart: number | null = null; // fraction [0,1]
  private dragCur = 0;
  private panning = false;
  private panStartX = 0;
  private panStartLo = 0;
  // Touch: track active pointers so two fingers pinch-zoom / pan (mouse uses the
  // same handlers, so click-tune, drag-select and shift-pan keep working).
  private pointers = new Map<number, number>(); // pointerId -> canvas fraction
  private pinchDist0 = 0;
  private pinchSpan0 = 1;
  private pinchBand0 = 0;
  private gestureMulti = false;

  constructor(
    private overlay: HTMLCanvasElement,
    private axis: HTMLCanvasElement,
  ) {
    this.octx = overlay.getContext("2d")!;
    this.actx = axis.getContext("2d")!;
    overlay.addEventListener("pointerdown", (e) => this.onPointerDown(e));
    overlay.addEventListener("pointermove", (e) => this.onPointerMove(e));
    overlay.addEventListener("pointerup", (e) => this.onPointerUp(e));
    overlay.addEventListener("pointercancel", (e) => this.onPointerUp(e));
    overlay.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
  }

  setBand(centerFreq: number, sampleRate: number): void {
    const changed = centerFreq !== this.centerFreq || sampleRate !== this.sampleRate;
    this.centerFreq = centerFreq;
    this.sampleRate = sampleRate;
    if (changed) this.resetView(); // a new captured band invalidates the zoom window
    this.drawAxis();
    this.draw();
  }

  resetView(): void {
    this.viewLo = 0;
    this.viewHi = 1;
    this.onViewChange?.(0, 1);
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
  setSubVfos(v: SubVfoMarker[]): void {
    this.subVfos = v;
    this.draw();
  }

  // --- mapping -----------------------------------------------------------
  // A canvas-x fraction [0,1] maps through the zoom window to a band fraction,
  // then to RF Hz. With no zoom (viewLo=0, viewHi=1) these are the identity.
  private freqAt(canvasFrac: number): number {
    const bandFrac = this.viewLo + canvasFrac * (this.viewHi - this.viewLo);
    return this.centerFreq + (bandFrac - 0.5) * this.sampleRate;
  }
  private fracOf(freqHz: number): number {
    const bandFrac = 0.5 + (freqHz - this.centerFreq) / this.sampleRate;
    return (bandFrac - this.viewLo) / (this.viewHi - this.viewLo);
  }
  private eventFraction(e: { clientX: number }): number {
    const r = this.overlay.getBoundingClientRect();
    return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  }

  // --- interaction -------------------------------------------------------
  private span(): number {
    return this.viewHi - this.viewLo;
  }

  private onPointerDown(e: PointerEvent): void {
    this.overlay.setPointerCapture(e.pointerId);
    const f = this.eventFraction(e);
    this.pointers.set(e.pointerId, f);
    if (this.pointers.size === 2) {
      // second finger down: start a pinch/pan, abandoning any single-finger drag
      this.dragStart = null;
      this.panning = false;
      this.gestureMulti = true;
      this.beginPinch();
      return;
    }
    if (this.pointers.size > 2) return;
    this.gestureMulti = false;
    // shift-drag pans the zoom window (only meaningful when zoomed in)
    if (e.shiftKey && this.span() < 0.999) {
      this.panning = true;
      this.panStartX = f;
      this.panStartLo = this.viewLo;
      return;
    }
    this.dragStart = f;
    this.dragCur = f;
  }
  private onPointerMove(e: PointerEvent): void {
    if (!this.pointers.has(e.pointerId)) return;
    this.pointers.set(e.pointerId, this.eventFraction(e));
    if (this.pointers.size >= 2) {
      this.handlePinch();
      return;
    }
    if (this.panning) {
      const span = this.span();
      let lo = this.panStartLo - (this.pointers.get(e.pointerId)! - this.panStartX) * span;
      lo = Math.min(1 - span, Math.max(0, lo));
      this.viewLo = lo;
      this.viewHi = lo + span;
      this.onViewChange?.(this.viewLo, this.viewHi);
      this.drawAxis();
      this.draw();
      return;
    }
    if (this.dragStart == null) return;
    this.dragCur = this.pointers.get(e.pointerId)!;
    this.draw();
  }
  private onPointerUp(e: PointerEvent): void {
    this.pointers.delete(e.pointerId);
    try {
      this.overlay.releasePointerCapture(e.pointerId);
    } catch {
      /* pointer already released */
    }
    if (this.gestureMulti) {
      // tail of a pinch/pan — don't tune or select; reset once all fingers lift
      this.dragStart = null;
      this.panning = false;
      if (this.pointers.size === 0) this.gestureMulti = false;
      return;
    }
    if (this.panning) {
      this.panning = false;
      return;
    }
    if (this.dragStart == null) return;
    const start = this.dragStart;
    const end = this.eventFraction(e);
    this.dragStart = null;
    const f1 = this.freqAt(start);
    const f2 = this.freqAt(end);
    if (Math.abs(end - start) < 0.004) {
      this.onTune?.(this.freqAt(end));            // tap -> tune
    } else {
      this.onSelect?.(Math.min(f1, f2), Math.max(f1, f2));  // drag -> select range
    }
    this.draw();
  }

  // --- two-finger pinch-zoom + pan (touch) -------------------------------
  private twoPointers(): [number, number] {
    const it = this.pointers.values();
    return [it.next().value as number, it.next().value as number];
  }
  private beginPinch(): void {
    const [a, b] = this.twoPointers();
    const mid = (a + b) / 2;
    this.pinchDist0 = Math.max(0.01, Math.abs(a - b));
    this.pinchSpan0 = this.span();
    this.pinchBand0 = this.viewLo + mid * this.pinchSpan0; // band frac under midpoint
  }
  private handlePinch(): void {
    const [a, b] = this.twoPointers();
    const mid = (a + b) / 2;
    const dist = Math.max(0.01, Math.abs(a - b));
    const span = Math.min(1, Math.max(0.02, this.pinchSpan0 * (this.pinchDist0 / dist)));
    let lo = this.pinchBand0 - mid * span; // keep the start band point under the live midpoint
    lo = Math.min(1 - span, Math.max(0, lo));
    this.viewLo = lo;
    this.viewHi = lo + span;
    this.onViewChange?.(this.viewLo, this.viewHi);
    this.drawAxis();
    this.draw();
  }

  private onWheel(e: WheelEvent): void {
    e.preventDefault();
    const cf = this.eventFraction(e);                       // cursor, canvas frac
    const anchor = this.viewLo + cf * this.span();          // band frac under cursor
    const factor = e.deltaY < 0 ? 0.8 : 1.25;               // in : out
    let span = Math.min(1, Math.max(0.02, this.span() * factor)); // cap at ~50×
    let lo = anchor - cf * span;                            // keep anchor fixed
    let hi = lo + span;
    if (lo < 0) { lo = 0; hi = span; }
    if (hi > 1) { hi = 1; lo = 1 - span; }
    this.viewLo = lo;
    this.viewHi = hi;
    this.onViewChange?.(lo, hi);
    this.drawAxis();
    this.draw();
  }

  // --- rendering ---------------------------------------------------------
  draw(): void {
    const w = this.overlay.width / this.dpr;
    const h = this.overlay.height / this.dpr;
    this.octx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.octx.clearRect(0, 0, w, h);

    // live drag selection
    if (this.dragStart != null) {
      const x0 = Math.min(this.dragStart, this.dragCur) * w;
      const x1 = Math.max(this.dragStart, this.dragCur) * w;
      this.octx.fillStyle = "rgba(47,129,247,0.25)";
      this.octx.fillRect(x0, 0, x1 - x0, h);
    }

    if (!this.active) return;

    // px per Hz through the zoom window, so the shaded bandwidth stays true
    // to the axis when zoomed in
    const pxPerHz = w / (this.sampleRate * this.span());

    // tuned channel band
    const cx = this.fracOf(this.tuned) * w;
    const halfPx = this.bandwidth * pxPerHz * 0.5;
    this.octx.fillStyle = "rgba(255,255,255,0.12)";
    this.octx.fillRect(cx - halfPx, 0, halfPx * 2, h);
    // center cursor
    this.octx.strokeStyle = "#ff5b5b";
    this.octx.lineWidth = 1.5;
    this.octx.beginPath();
    this.octx.moveTo(cx, 0);
    this.octx.lineTo(cx, h);
    this.octx.stroke();

    // extra receivers (VFO B/C/D): a coloured cursor + band + letter each
    this.octx.font = "bold 11px -apple-system, system-ui, sans-serif";
    this.subVfos.forEach((v, i) => {
      if (!v.on) return;
      const x = this.fracOf(v.freq) * w;
      if (x < -10 || x > w + 10) return;
      const half = v.bw * pxPerHz * 0.5;
      const color = SUB_VFO_COLORS[i] || "#8b949e";
      this.octx.save();
      this.octx.globalAlpha = 0.14;
      this.octx.fillStyle = color;
      this.octx.fillRect(x - half, 0, half * 2, h);
      this.octx.restore();
      this.octx.strokeStyle = color;
      this.octx.lineWidth = 1.5;
      this.octx.beginPath();
      this.octx.moveTo(x, 0);
      this.octx.lineTo(x, h);
      this.octx.stroke();
      this.octx.fillStyle = color;
      this.octx.fillText(SUB_VFO_LABELS[i] || "?", x + 4, 14);
    });
  }

  drawAxis(): void {
    const w = this.axis.width / this.dpr;
    const h = this.axis.height / this.dpr;
    this.actx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.actx.clearRect(0, 0, w, h);
    this.actx.fillStyle = "#8b949e";
    this.actx.font = "11px -apple-system, system-ui, sans-serif";
    this.actx.textBaseline = "top";
    // adapt tick count to width so MHz labels (~55 px each) don't overlap
    const ticks = Math.max(3, Math.min(13, Math.floor(w / 80) + 1));
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
