// NOAA APT image renderer. Each incoming line is 2080 grayscale pixels; we paint
// it onto a full-resolution offscreen canvas (one row per line, growing down) and
// show a scaled view in the visible canvas. The offscreen is what gets saved as a
// PNG, so the download is full quality regardless of the on-screen size.

const LINE_PX = 2080;
const GROW = 600; // grow the offscreen height in chunks of this many lines

export class AptImage {
  private off: HTMLCanvasElement;
  private octx: CanvasRenderingContext2D;
  private rows = 0;
  private vctx: CanvasRenderingContext2D;
  private vw = 0;
  private vh = 0;

  constructor(private canvas: HTMLCanvasElement) {
    this.vctx = canvas.getContext("2d")!;
    this.off = document.createElement("canvas");
    this.off.width = LINE_PX;
    this.off.height = GROW;
    this.octx = this.off.getContext("2d", { willReadFrequently: true })!;
    this.octx.fillStyle = "#000";
    this.octx.fillRect(0, 0, LINE_PX, GROW);
  }

  resize(w: number, h: number): void {
    this.canvas.width = w;
    this.canvas.height = h;
    this.vw = w;
    this.vh = h;
    this.redraw();
  }

  pushLine(line: Uint8Array): void {
    if (this.rows >= this.off.height) this.growOffscreen();
    const img = this.octx.createImageData(LINE_PX, 1);
    for (let x = 0; x < LINE_PX; x++) {
      const v = line[x] ?? 0;
      const o = x * 4;
      img.data[o] = img.data[o + 1] = img.data[o + 2] = v;
      img.data[o + 3] = 255;
    }
    this.octx.putImageData(img, 0, this.rows);
    this.rows++;
    this.redraw();
  }

  private growOffscreen(): void {
    const grown = document.createElement("canvas");
    grown.width = LINE_PX;
    grown.height = this.off.height + GROW;
    const gctx = grown.getContext("2d", { willReadFrequently: true })!;
    gctx.fillStyle = "#000";
    gctx.fillRect(0, 0, grown.width, grown.height);
    gctx.drawImage(this.off, 0, 0);
    this.off = grown;
    this.octx = gctx;
  }

  // Fit the decoded image (full width, latest rows) into the visible canvas.
  private redraw(): void {
    if (!this.vw || !this.vh || this.rows === 0) return;
    this.vctx.fillStyle = "#000";
    this.vctx.fillRect(0, 0, this.vw, this.vh);
    // show as much height as fits at the current scale, anchored to the newest line
    const scale = this.vw / LINE_PX;
    const visibleRows = Math.min(this.rows, Math.ceil(this.vh / scale));
    const srcY = this.rows - visibleRows;
    this.vctx.imageSmoothingEnabled = false;
    this.vctx.drawImage(
      this.off, 0, srcY, LINE_PX, visibleRows,
      0, 0, this.vw, visibleRows * scale,
    );
  }

  hasImage(): boolean {
    return this.rows > 0;
  }

  clear(): void {
    this.rows = 0;
    this.off.height = GROW;
    this.octx.fillStyle = "#000";
    this.octx.fillRect(0, 0, this.off.width, this.off.height);
    if (this.vw) {
      this.vctx.fillStyle = "#000";
      this.vctx.fillRect(0, 0, this.vw, this.vh);
    }
  }

  // PNG data URL of the full-resolution image (trimmed to the decoded rows).
  toPng(): string | null {
    if (this.rows === 0) return null;
    const out = document.createElement("canvas");
    out.width = LINE_PX;
    out.height = this.rows;
    out.getContext("2d")!.drawImage(this.off, 0, 0, LINE_PX, this.rows, 0, 0, LINE_PX, this.rows);
    return out.toDataURL("image/png");
  }
}
