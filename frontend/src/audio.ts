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
const WORKLET_MAXBUFFER_S = 0.6;
const FALLBACK_PREBUFFER_S = 0.70;
const FALLBACK_MAXBUFFER_S = 1.6;

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
    this.ctx = new AudioContext({ sampleRate: this.rate });

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
    this.prebuffer = Math.floor(sr * FALLBACK_PREBUFFER_S) * 2;  // ×2 for the two channels
    this.maxbuffer = Math.floor(sr * FALLBACK_MAXBUFFER_S) * 2;
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
        // Underrun: finish with silence and re-buffer (mirrors the worklet).
        outL.fill(0, i);
        if (outR !== outL) outR.fill(0, i);
        this.playing = false;
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
    const f32 = new Float32Array(n);
    for (let i = 0; i < n; i++) f32[i] = i16[i] / 32768;

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

  async suspend(): Promise<void> {
    await this.ctx?.suspend();
  }
}
