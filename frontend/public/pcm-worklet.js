// AudioWorklet that plays streamed interleaved-stereo Float32 PCM with a jitter
// buffer, de-interleaving to the two output channels (L,R,L,R,...).
//
// SDR audio arrives in bursts over a WebSocket while the main thread is also busy
// drawing the waterfall, so chunk timing is uneven. Playing "just in time" causes
// constant underrun crackle. Instead we keep a small cushion: we don't start
// playback until ~PREBUFFER seconds are queued, then drain steadily. On underrun
// we play silence and resume the instant data returns (no re-buffering wait).
//
// Latency stays bounded by a watermark: after a network stall, TCP delivers the
// missed audio in one burst, and since producer and consumer both run at the
// same rate that backlog would otherwise sit in the queue *forever* as added
// latency. When the queue exceeds WATERMARK we trim it back down to just above
// the prebuffer — one audible skip forward, right after a stall that already
// glitched, instead of seconds of permanent lag.

// Defaults if the page doesn't pass sizes via processorOptions.
const DEFAULT_PREBUFFER_S = 0.20;
const DEFAULT_WATERMARK_S = 0.75;
const TRIM_MARGIN_S = 0.15; // trim back down to prebuffer + this margin

class PcmPlayer extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    const preS = opts.prebuffer > 0 ? opts.prebuffer : DEFAULT_PREBUFFER_S;
    const wmS = opts.watermark > 0 ? opts.watermark : DEFAULT_WATERMARK_S;
    this.buffers = [];        // queued interleaved Float32Array chunks (L,R,...)
    this.readIndex = 0;       // read offset into buffers[0] (in samples)
    this.available = 0;       // total unread samples queued (interleaved)
    this.playing = false;     // false => buffering
    // counts are in interleaved samples, so ×2 for the two channels
    this.prebuffer = Math.floor(sampleRate * preS) * 2;
    this.watermark = Math.floor(sampleRate * wmS) * 2;
    this.trimto = this.prebuffer + Math.floor(sampleRate * TRIM_MARGIN_S) * 2;
    this.port.onmessage = (e) => {
      const chunk = e.data;
      this.buffers.push(chunk);
      this.available += chunk.length;
      if (this.available > this.watermark) {
        while (this.available > this.trimto && this.buffers.length > 1) {
          const dropped = this.buffers.shift();
          this.available -= dropped.length - this.readIndex;
          this.readIndex = 0;
        }
      }
    };
  }

  process(_inputs, outputs) {
    const outL = outputs[0][0];
    if (!outL) return true;
    const outR = outputs[0][1] || outL; // fall back to mono if only 1 channel

    if (!this.playing) {
      if (this.available >= this.prebuffer) this.playing = true;
      else {
        outL.fill(0);
        if (outR !== outL) outR.fill(0);
        return true;
      }
    }

    for (let i = 0; i < outL.length; i++) {
      if (this.available < 2) {
        // Underrun: play silence and resume immediately when data returns.
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
    return true;
  }
}

registerProcessor('pcm-player', PcmPlayer);
