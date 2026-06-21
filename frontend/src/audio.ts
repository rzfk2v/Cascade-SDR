// Web Audio playback for streamed PCM. The backend sends interleaved stereo
// int16 frames (mono demods send L = R). We convert to Float32 and feed an
// AudioWorklet (see public/pcm-worklet.js) that resamples + de-interleaves on a
// dedicated audio thread with a drift-compensated jitter buffer.
//
// AudioContext must be created/resumed from a user gesture, so init() is called
// when the user enters the Spectrum/Replay view.
//
// AudioWorklet is only available in a **secure context** (HTTPS or localhost).
// When the app is served over plain HTTP to a LAN IP (e.g. another computer on
// your network), `ctx.audioWorklet` is undefined, so we fall back to the older,
// deprecated-but-insecure-context-friendly ScriptProcessorNode. The fallback
// re-implements the same adaptive resampler here on the main thread.
//
// Resampling (48 kHz backend -> the sound card rate) and clock-drift correction
// both live in the player, not here: the producer (dongle crystal) and consumer
// (sound card crystal) never run at exactly the same rate, so a fractional read
// pointer is trimmed ±~0.5% by a control loop to hold the buffer near a target
// fill. That keeps the cushion rebuilt (no chronic-underrun crackle) and absorbs
// clock drift. See public/pcm-worklet.js for the same logic on the audio thread.

const IN_RATE = 48000;             // backend PCM rate

// Worklet runs on a dedicated audio thread in a secure context (localhost/HTTPS),
// so a small target latency stays glitch-free. The ScriptProcessor fallback only
// runs over plain-HTTP LAN, on the main thread, where bursty WiFi + main-thread
// jank need a deeper cushion.
const WORKLET_TARGET_S = 0.18;
const WORKLET_MAXBUFFER_S = 4.0;
const FALLBACK_TARGET_S = 0.5;     // override via localStorage["cascadeAudioBufferS"]
const FALLBACK_MAXBUFFER_S = 5.0;
const MAX_TRIM = 0.005;            // ±0.5% playback-speed correction
const TRIM_SMOOTH = 0.05;          // per-block low-pass on the trim

function fallbackTargetS(): number {
  const ov = parseFloat(localStorage.getItem("cascadeAudioBufferS") || "");
  return isFinite(ov) && ov >= 0.1 && ov <= 10 ? ov : FALLBACK_TARGET_S;
}

// 4-point, 3rd-order Hermite (Catmull-Rom) interpolation; t in [0,1).
function hermite(xm1: number, x0: number, x1: number, x2: number, t: number): number {
  const c = (x1 - xm1) * 0.5;
  const v = x0 - x1;
  const w = c + v;
  const a = w + v + (x2 - x0) * 0.5;
  const b = w + a;
  return ((a * t - b) * t + c) * t + x0;
}

export class AudioPlayer {
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private script: ScriptProcessorNode | null = null;
  private readonly rate: number;   // backend in-rate (48 kHz)

  // --- ScriptProcessor fallback: ring-buffer resampler (mirrors the worklet) ---
  private ring: Float32Array | null = null;  // interleaved L,R
  private capacity = 0;        // ring size in frames
  private target = 0;          // target fill in frames
  private writeCount = 0;      // total frames ever written
  private readPos = 0;         // float: total frames ever read
  private playing = false;     // false => prebuffering
  private trim = 0;            // smoothed playback-speed trim

  constructor(sampleRate = IN_RATE) {
    this.rate = sampleRate;
  }

  async init(): Promise<void> {
    if (this.ctx) {
      await this.ctx.resume();
      return;
    }
    // Use the browser's native hardware rate; the player resamples to it.
    this.ctx = new AudioContext();

    if (this.ctx.audioWorklet) {
      try {
        await this.ctx.audioWorklet.addModule("/pcm-worklet.js?v=3");
        this.node = new AudioWorkletNode(this.ctx, "pcm-player", {
          outputChannelCount: [2],
          processorOptions: {
            inRate: this.rate,
            target: WORKLET_TARGET_S,
            maxbuffer: WORKLET_MAXBUFFER_S,
            maxTrim: MAX_TRIM,
          },
        });
        this.node.connect(this.ctx.destination);
        await this.ctx.resume();
        return;
      } catch {
        this.node = null;   // secure-context check passed but load failed; fall back
      }
    }

    this.initScriptFallback();
    await this.ctx.resume();
  }

  // Older ScriptProcessorNode path for insecure contexts (plain-HTTP LAN access).
  private initScriptFallback(): void {
    this.target = Math.max(1, Math.floor(this.rate * fallbackTargetS()));
    this.capacity = Math.max(this.target * 4, Math.floor(this.rate * FALLBACK_MAXBUFFER_S));
    this.ring = new Float32Array(this.capacity * 2);
    // 0 input channels (we synthesise), 2 output channels. 4096 keeps callbacks
    // sparse so the main thread's waterfall drawing doesn't starve playback.
    this.script = this.ctx!.createScriptProcessor(4096, 0, 2);
    this.script.onaudioprocess = (e) => this.renderFallback(e);
    this.script.connect(this.ctx!.destination);
  }

  private renderFallback(e: AudioProcessingEvent): void {
    const out = e.outputBuffer;
    const outL = out.getChannelData(0);
    const outR = out.numberOfChannels > 1 ? out.getChannelData(1) : outL;
    const n = outL.length;
    const cap = this.capacity;
    const ring = this.ring!;

    const avail = this.writeCount - this.readPos;
    if (!this.playing) {
      if (avail >= this.target) {
        this.playing = true;
      } else {
        outL.fill(0);
        if (outR !== outL) outR.fill(0);
        return;
      }
    }

    // Control loop: trim playback speed to steer buffer fill toward target.
    const relErr = (avail - this.target) / this.target;
    const trimTarget = Math.max(-MAX_TRIM, Math.min(MAX_TRIM, relErr * (MAX_TRIM / 0.5)));
    this.trim += (trimTarget - this.trim) * TRIM_SMOOTH;
    const step = (this.rate / this.ctx!.sampleRate) * (1 + this.trim);

    for (let i = 0; i < n; i++) {
      const i1 = Math.floor(this.readPos);
      if (i1 + 2 >= this.writeCount) {
        // Underrun: silence the rest, freeze the read pointer, resume next call.
        for (; i < n; i++) { outL[i] = 0; if (outR !== outL) outR[i] = 0; }
        break;
      }
      const t = this.readPos - i1;
      const a = ((i1 - 1) % cap + cap) % cap, b = i1 % cap;
      const c = (i1 + 1) % cap, d = (i1 + 2) % cap;
      outL[i] = hermite(ring[a * 2], ring[b * 2], ring[c * 2], ring[d * 2], t);
      const rv = hermite(ring[a * 2 + 1], ring[b * 2 + 1], ring[c * 2 + 1], ring[d * 2 + 1], t);
      if (outR !== outL) outR[i] = rv;
      this.readPos += step;
    }
  }

  // body is a DataView at the aligned payload (interleaved L,R int16 LE).
  pushInt16(body: DataView): void {
    if (!this.node && !this.script) return;
    const n = body.byteLength >> 1;             // total samples (L,R interleaved)
    const i16 = new Int16Array(body.buffer, body.byteOffset, n);
    const f32 = new Float32Array(n);
    for (let i = 0; i < n; i++) f32[i] = i16[i] / 32768;

    if (this.node) {
      // Hand off to the audio thread; it resamples + drift-corrects. Transfer
      // the buffer to avoid a copy.
      this.node.port.postMessage(f32, [f32.buffer]);
      return;
    }
    // ScriptProcessor fallback: write into the ring buffer.
    const frames = n >> 1;
    const cap = this.capacity;
    const ring = this.ring!;
    for (let i = 0; i < frames; i++) {
      const w = ((this.writeCount + i) % cap) * 2;
      ring[w] = f32[i * 2];
      ring[w + 1] = f32[i * 2 + 1];
    }
    this.writeCount += frames;
    const avail = this.writeCount - this.readPos;
    if (avail > cap - 2) this.readPos = this.writeCount - (cap - 2);
  }

  async suspend(): Promise<void> {
    await this.ctx?.suspend();
  }
}
