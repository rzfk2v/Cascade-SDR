// Records the demodulated audio (mono int16 PCM) into a downloadable WAV file,
// entirely client-side. Feed it the same PCM that goes to the player.

export class WavRecorder {
  recording = false;
  private chunks: Int16Array[] = [];
  private total = 0;
  private startedAt = 0;

  constructor(private sampleRate = 48000) {}

  start(): void {
    this.chunks = [];
    this.total = 0;
    this.startedAt = performance.now();
    this.recording = true;
  }

  addPcm(samples: Int16Array): void {
    if (!this.recording) return;
    this.chunks.push(samples.slice()); // copy — the source buffer is transient
    this.total += samples.length;
  }

  elapsedSec(): number {
    return this.recording ? (performance.now() - this.startedAt) / 1000 : 0;
  }

  // Stop and return a WAV Blob (null if nothing was captured).
  stop(): Blob | null {
    this.recording = false;
    if (this.total === 0) return null;
    const pcm = new Int16Array(this.total);
    let o = 0;
    for (const c of this.chunks) {
      pcm.set(c, o);
      o += c.length;
    }
    this.chunks = [];
    return this.toWav(pcm);
  }

  private toWav(pcm: Int16Array): Blob {
    const bytes = pcm.length * 2;
    const buf = new ArrayBuffer(44 + bytes);
    const v = new DataView(buf);
    const wr = (off: number, s: string) => {
      for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i));
    };
    wr(0, "RIFF");
    v.setUint32(4, 36 + bytes, true);
    wr(8, "WAVE");
    wr(12, "fmt ");
    v.setUint32(16, 16, true); // PCM fmt chunk size
    v.setUint16(20, 1, true); // PCM
    v.setUint16(22, 1, true); // mono
    v.setUint32(24, this.sampleRate, true);
    v.setUint32(28, this.sampleRate * 2, true); // byte rate
    v.setUint16(32, 2, true); // block align
    v.setUint16(34, 16, true); // bits/sample
    wr(36, "data");
    v.setUint32(40, bytes, true);
    new Int16Array(buf, 44).set(pcm);
    return new Blob([buf], { type: "audio/wav" });
  }
}

// Trigger a browser download of a Blob.
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
