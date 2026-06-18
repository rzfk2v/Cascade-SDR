// Web Audio playback for streamed PCM. The backend sends interleaved stereo
// int16 frames (mono demods send L = R); we convert to Float32 and feed an
// AudioWorklet (see public/pcm-worklet.js) that de-interleaves to 2 channels.
//
// AudioContext must be created/resumed from a user gesture, so init() is called
// when the user enters the Spectrum/Replay view.
//
// AudioWorklet is only available in a **secure context** (HTTPS or localhost).
// When the app is served over plain HTTP to a LAN IP (e.g. another computer on
// your network), `ctx.audioWorklet` is undefined, so we fall back to the older,
// deprecated-but-insecure-context-friendly ScriptProcessorNode. The fallback
// re-implements the worklet's jitter buffer here on the main thread.

// The buffer is sized to the playback path, so we don't add latency where it
// isn't needed. The AudioWorklet only runs in a secure context (localhost /
// HTTPS) and plays on a dedicated audio thread, so a small cushion is plenty
// and keeps latency low. The ScriptProcessor fallback only runs over plain-HTTP
// (a LAN IP) and on the main thread, where network jitter + TCP retransmit
// stalls need a much deeper cushion to stay glitch-free.
const WORKLET_PREBUFFER_S = 0.20;
const WORKLET_MAXBUFFER_S = 3.0;
// Plain-HTTP LAN clients see bursty WiFi delivery. The fallback uses a short
// initial prebuffer (0.5 s) before starting play; after any gap/underrun it
// outputs silence seamlessly and resumes the moment data returns — no
// multi-second re-buffering wait. Tunable via localStorage["cascadeAudioBufferS"].
const FALLBACK_PREBUFFER_S = 0.5;

function fallbackPrebufferS(): number {
  const ov = parseFloat(localStorage.getItem("cascadeAudioBufferS") || "");
  return isFinite(ov) && ov >= 0.1 && ov <= 10 ? ov : FALLBACK_PREBUFFER_S;
}

export class AudioPlayer {
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private script: ScriptProcessorNode | null = null;
  private readonly rate: number;

  // --- ScriptProcessor fallback state (counts in interleaved samples) ---
  private buffers: Float32Array[] = [];  // queued interleaved Float32 (L,R,...)
  private readIndex = 0;                  // read offset into buffers[0]
  private available = 0;                  // total unread interleaved samples
  private playing = false;                // false => buffering
  private prebuffer = 0;
  private maxbuffer = 0;

  constructor(sampleRate = 48000) {
    this.rate = sampleRate;
  }

  async init(): Promise<void> {
    if (this.ctx) {
      await this.ctx.resume();
      return;
    }
    // Use the browser's native hardware rate — avoids double resampling when
    // the hardware doesn't run at 48 kHz. pushInt16 resamples as needed.
    this.ctx = new AudioContext();

    if (this.ctx.audioWorklet) {
      try {
        await this.ctx.audioWorklet.addModule("/pcm-worklet.js");
        this.node = new AudioWorkletNode(this.ctx, "pcm-player", {
          outputChannelCount: [2],
          processorOptions: {
            prebuffer: WORKLET_PREBUFFER_S,
            maxbuffer: WORKLET_MAXBUFFER_S,
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
    const sr = this.ctx!.sampleRate;
    const pre = fallbackPrebufferS();
    this.prebuffer = Math.floor(sr * pre) * 2;   // ×2 for the two channels
    this.maxbuffer = Math.floor(sr * 5.0) * 2;   // 5 s ceiling absorbs WiFi bursts
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

    if (!this.playing) {
      if (this.available >= this.prebuffer) {
        this.playing = true;
      } else {
        outL.fill(0);
        if (outR !== outL) outR.fill(0);
        return;
      }
    }

    for (let i = 0; i < outL.length; i++) {
      if (this.available < 2) {
        // Underrun: play silence and resume immediately when data returns.
        // Don't reset to re-buffering state — that causes multi-second pauses.
        outL.fill(0, i);
        if (outR !== outL) outR.fill(0, i);
        break;
      }
      let cur = this.buffers[0];
      outL[i] = cur[this.readIndex++];
      if (this.readIndex >= cur.length) { this.buffers.shift(); this.readIndex = 0; cur = this.buffers[0]; }
      outR[i] = cur[this.readIndex++];
      if (this.readIndex >= cur.length) { this.buffers.shift(); this.readIndex = 0; }
      this.available -= 2;
    }
  }

  // body is a DataView at the aligned payload (interleaved L,R int16 LE).
  pushInt16(body: DataView): void {
    if (!this.node && !this.script) return;
    const n = body.byteLength >> 1;             // total samples (L,R interleaved)
    const i16 = new Int16Array(body.buffer, body.byteOffset, n);
    let f32 = new Float32Array(n);
    for (let i = 0; i < n; i++) f32[i] = i16[i] / 32768;

    // Resample if the AudioContext settled at a different rate than the backend's
    // 48 kHz (e.g. macOS may run at 44.1 or 96 kHz depending on the audio device).
    if (this.ctx!.sampleRate !== this.rate) {
      f32 = this.resampleStereo(f32, this.rate, this.ctx!.sampleRate);
    }

    if (this.node) {
      this.node.port.postMessage(f32, [f32.buffer]);
      return;
    }
    // ScriptProcessor fallback: enqueue and bound latency (mirrors the worklet).
    this.buffers.push(f32);
    this.available += f32.length;
    while (this.available > this.maxbuffer && this.buffers.length > 1) {
      const dropped = this.buffers.shift()!;
      this.available -= dropped.length - this.readIndex;
      this.readIndex = 0;
    }
  }

  // Linear-interpolation stereo resample (interleaved L,R input/output).
  private resampleStereo(input: Float32Array, fromRate: number, toRate: number): Float32Array<ArrayBuffer> {
    const inFrames  = input.length >> 1;
    const outFrames = Math.round(inFrames * toRate / fromRate);
    const out = new Float32Array(outFrames * 2);
    for (let i = 0; i < outFrames; i++) {
      const src  = i * fromRate / toRate;
      const lo   = Math.floor(src);
      const frac = src - lo;
      const a = Math.min(lo * 2, input.length - 2);
      const b = Math.min(a + 2, input.length - 2);
      out[i * 2]     = input[a]     + (input[b]     - input[a])     * frac;
      out[i * 2 + 1] = input[a + 1] + (input[b + 1] - input[a + 1]) * frac;
    }
    return out;
  }

  async suspend(): Promise<void> {
    await this.ctx?.suspend();
  }
}
