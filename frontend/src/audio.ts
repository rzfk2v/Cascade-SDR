// Web Audio playback for streamed PCM. The backend sends interleaved stereo
// int16 frames (mono demods send L = R); we convert to Float32 and feed an
// AudioWorklet (see public/pcm-worklet.js) that de-interleaves to 2 channels.
//
// AudioContext must be created/resumed from a user gesture, so init() is called
// when the user enters the Spectrum/Replay view.

export class AudioPlayer {
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private readonly rate: number;

  constructor(sampleRate = 48000) {
    this.rate = sampleRate;
  }

  async init(): Promise<void> {
    if (this.ctx) {
      await this.ctx.resume();
      return;
    }
    this.ctx = new AudioContext({ sampleRate: this.rate });
    await this.ctx.audioWorklet.addModule("/pcm-worklet.js");
    this.node = new AudioWorkletNode(this.ctx, "pcm-player", {
      outputChannelCount: [2],
    });
    this.node.connect(this.ctx.destination);
  }

  // body is a DataView at the aligned payload (interleaved L,R int16 LE).
  pushInt16(body: DataView): void {
    if (!this.node) return;
    const n = body.byteLength >> 1;             // total samples (L,R interleaved)
    const i16 = new Int16Array(body.buffer, body.byteOffset, n);
    const f32 = new Float32Array(n);
    for (let i = 0; i < n; i++) f32[i] = i16[i] / 32768;
    this.node.port.postMessage(f32, [f32.buffer]);
  }

  async suspend(): Promise<void> {
    await this.ctx?.suspend();
  }
}
