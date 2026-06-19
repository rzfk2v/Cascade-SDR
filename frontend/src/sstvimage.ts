// SSTV image renderer. Each decoded picture announces its size (mode-dependent,
// e.g. 320×256) and then streams one RGB row at a time, top-down. We paint rows
// onto a full-resolution offscreen canvas (what gets saved as PNG) and show the
// whole frame scaled to fit the visible canvas, preserving aspect ratio.

export class SstvImage {
  private off: HTMLCanvasElement;
  private octx: CanvasRenderingContext2D;
  private iw = 0; // image width
  private ih = 0; // image height
  private rows = 0;
  private modeName = "";
  private vctx: CanvasRenderingContext2D;
  private vw = 0;
  private vh = 0;

  constructor(private canvas: HTMLCanvasElement) {
    this.vctx = canvas.getContext("2d")!;
    this.off = document.createElement("canvas");
    this.octx = this.off.getContext("2d", { willReadFrequently: true })!;
  }

  resize(w: number, h: number): void {
    this.canvas.width = w;
    this.canvas.height = h;
    this.vw = w;
    this.vh = h;
    this.redraw();
  }

  // Begin a new image of the given size (called on the backend's sstv_start).
  start(mode: string, width: number, height: number): void {
    this.modeName = mode;
    this.iw = width;
    this.ih = height;
    this.rows = 0;
    this.off.width = width;
    this.off.height = height;
    this.octx.fillStyle = "#000";
    this.octx.fillRect(0, 0, width, height);
    this.redraw();
  }

  pushRow(rgb: Uint8Array): void {
    if (!this.iw || this.rows >= this.ih) return;
    const img = this.octx.createImageData(this.iw, 1);
    for (let x = 0; x < this.iw; x++) {
      const s = x * 3;
      const o = x * 4;
      img.data[o] = rgb[s] ?? 0;
      img.data[o + 1] = rgb[s + 1] ?? 0;
      img.data[o + 2] = rgb[s + 2] ?? 0;
      img.data[o + 3] = 255;
    }
    this.octx.putImageData(img, 0, this.rows);
    this.rows++;
    this.redraw();
  }

  // Fit the whole image into the visible canvas, preserving aspect, centered.
  private redraw(): void {
    if (!this.vw || !this.vh) return;
    this.vctx.fillStyle = "#000";
    this.vctx.fillRect(0, 0, this.vw, this.vh);
    if (!this.iw || !this.ih) return;
    const scale = Math.min(this.vw / this.iw, this.vh / this.ih);
    const dw = this.iw * scale;
    const dh = this.ih * scale;
    const dx = (this.vw - dw) / 2;
    const dy = (this.vh - dh) / 2;
    this.vctx.imageSmoothingEnabled = false;
    this.vctx.drawImage(this.off, 0, 0, this.iw, this.ih, dx, dy, dw, dh);
  }

  modeLabel(): string {
    return this.modeName;
  }

  hasImage(): boolean {
    return this.rows > 0;
  }

  clear(): void {
    this.iw = 0;
    this.ih = 0;
    this.rows = 0;
    this.modeName = "";
    if (this.vw) {
      this.vctx.fillStyle = "#000";
      this.vctx.fillRect(0, 0, this.vw, this.vh);
    }
  }

  // PNG data URL of the full-resolution image (decoded rows only).
  toPng(): string | null {
    if (this.rows === 0) return null;
    const out = document.createElement("canvas");
    out.width = this.iw;
    out.height = this.rows;
    out.getContext("2d")!.drawImage(
      this.off, 0, 0, this.iw, this.rows, 0, 0, this.iw, this.rows,
    );
    return out.toDataURL("image/png");
  }
}
