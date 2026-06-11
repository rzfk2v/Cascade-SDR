// AudioWorklet that plays streamed mono Float32 PCM with a jitter buffer.
//
// SDR audio arrives in bursts over a WebSocket while the main thread is also busy
// drawing the waterfall, so chunk timing is uneven. Playing "just in time" causes
// constant underrun crackle. Instead we keep a small cushion: we don't start (or
// restart) playback until ~PREBUFFER seconds are queued, then drain steadily. On
// underrun we emit one clean silence gap and re-buffer rather than crackling, and
// we cap the queue so latency stays bounded.

const PREBUFFER_S = 0.12; // ~120 ms cushion before playback starts
const MAXBUFFER_S = 0.5;  // drop oldest beyond this to bound latency

class PcmPlayer extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffers = [];        // queued Float32Array chunks
    this.readIndex = 0;       // read offset into buffers[0]
    this.available = 0;       // total unread samples queued
    this.playing = false;     // false => buffering
    this.prebuffer = Math.floor(sampleRate * PREBUFFER_S);
    this.maxbuffer = Math.floor(sampleRate * MAXBUFFER_S);
    this.port.onmessage = (e) => {
      const chunk = e.data;
      this.buffers.push(chunk);
      this.available += chunk.length;
      while (this.available > this.maxbuffer && this.buffers.length > 1) {
        const dropped = this.buffers.shift();
        this.available -= dropped.length - this.readIndex;
        this.readIndex = 0;
      }
    };
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;

    if (!this.playing) {
      if (this.available >= this.prebuffer) this.playing = true;
      else {
        out.fill(0);
        return true;
      }
    }

    for (let i = 0; i < out.length; i++) {
      if (this.available <= 0) {
        // Underrun: finish this render quantum with silence and re-buffer.
        out.fill(0, i);
        this.playing = false;
        break;
      }
      const cur = this.buffers[0];
      out[i] = cur[this.readIndex++];
      this.available--;
      if (this.readIndex >= cur.length) {
        this.buffers.shift();
        this.readIndex = 0;
      }
    }
    return true;
  }
}

registerProcessor('pcm-player', PcmPlayer);
