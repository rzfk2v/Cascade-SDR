// AudioWorklet that plays streamed interleaved-stereo Float32 PCM with an
// adaptive, clock-drift-compensated jitter buffer, de-interleaving to the two
// output channels (L,R,L,R,...).
//
// SDR audio is produced at the dongle's 48 kHz (its crystal) and played back at
// the sound card's rate (a *different* crystal). Those clocks differ by tens of
// ppm, so a fixed-rate drain slowly drifts to underflow (gaps) or overflow
// (drops). Worse, the old "play just-in-time, never rebuild the cushion" logic
// lived at the underrun boundary and emitted a silence sliver almost every
// callback -> continuous crackle, regardless of link quality.
//
// Instead we keep a ring buffer and read it with a *fractional* pointer whose
// speed is continuously trimmed (±~0.5%) by a control loop that steers the
// buffer fill toward a target. This single mechanism does three jobs at once:
//   * resamples 48 kHz -> the AudioContext rate (44.1 / 48 / 96 kHz),
//   * compensates clock drift between producer and consumer,
//   * keeps the cushion rebuilt so we never sit starved (no crackle).
// Interpolation is 4-point cubic (Catmull-Rom) for clean output off unity ratio.

const DEFAULT_IN_RATE = 48000;     // backend PCM rate
const DEFAULT_TARGET_S = 0.18;     // target buffer fill (latency) in seconds
const DEFAULT_MAX_S = 4.0;         // ring capacity / overflow ceiling
const DEFAULT_MAX_TRIM = 0.005;    // ±0.5% playback-speed correction (≈8 cents)
const TRIM_SMOOTH = 0.05;          // per-block low-pass on the trim (≈50 ms tc)

// 4-point, 3rd-order Hermite (Catmull-Rom) interpolation; t in [0,1) between x0,x1.
function hermite(xm1, x0, x1, x2, t) {
  const c = (x1 - xm1) * 0.5;
  const v = x0 - x1;
  const w = c + v;
  const a = w + v + (x2 - x0) * 0.5;
  const b = w + a;
  return ((a * t - b) * t + c) * t + x0;
}

class PcmPlayer extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.inRate = opts.inRate > 0 ? opts.inRate : DEFAULT_IN_RATE;
    const targetS = opts.target > 0 ? opts.target : DEFAULT_TARGET_S;
    const maxS = opts.maxbuffer > 0 ? opts.maxbuffer : DEFAULT_MAX_S;
    this.maxTrim = opts.maxTrim > 0 ? opts.maxTrim : DEFAULT_MAX_TRIM;

    this.target = Math.max(1, Math.floor(this.inRate * targetS));        // frames
    this.capacity = Math.max(this.target * 4, Math.floor(this.inRate * maxS));
    this.ring = new Float32Array(this.capacity * 2);  // interleaved L,R
    this.writeCount = 0;    // total frames ever written
    this.readPos = 0;       // float: total frames ever read
    this.playing = false;   // false => prebuffering up to target
    this.trim = 0;          // current smoothed speed trim

    this.port.onmessage = (e) => {
      const chunk = e.data;                 // Float32Array interleaved L,R
      const frames = chunk.length >> 1;
      const cap = this.capacity;
      for (let i = 0; i < frames; i++) {
        const w = ((this.writeCount + i) % cap) * 2;
        this.ring[w] = chunk[i * 2];
        this.ring[w + 1] = chunk[i * 2 + 1];
      }
      this.writeCount += frames;
      // Overflow (reader fell badly behind): drop oldest by jumping readPos.
      const avail = this.writeCount - this.readPos;
      if (avail > cap - 2) this.readPos = this.writeCount - (cap - 2);
    };
  }

  process(_inputs, outputs) {
    const outL = outputs[0][0];
    if (!outL) return true;
    const outR = outputs[0][1] || outL;  // fall back to mono if only 1 channel
    const n = outL.length;
    const cap = this.capacity;
    const ring = this.ring;

    let avail = this.writeCount - this.readPos;
    if (!this.playing) {
      if (avail >= this.target) this.playing = true;
      else { outL.fill(0); if (outR !== outL) outR.fill(0); return true; }
    }

    // Control loop: trim playback speed to steer buffer fill toward target.
    const relErr = (avail - this.target) / this.target;
    const trimTarget = Math.max(-this.maxTrim,
      Math.min(this.maxTrim, relErr * (this.maxTrim / 0.5)));
    this.trim += (trimTarget - this.trim) * TRIM_SMOOTH;
    const step = (this.inRate / sampleRate) * (1 + this.trim);

    for (let i = 0; i < n; i++) {
      const i1 = Math.floor(this.readPos);
      if (i1 + 2 >= this.writeCount) {
        // Underrun: silence the rest, freeze the read pointer, resume next call.
        // The control loop's slow-down keeps refilling toward target.
        for (; i < n; i++) { outL[i] = 0; if (outR !== outL) outR[i] = 0; }
        break;
      }
      const t = this.readPos - i1;
      const aL = ((i1 - 1) % cap + cap) % cap, bL = i1 % cap;
      const cL = (i1 + 1) % cap, dL = (i1 + 2) % cap;
      outL[i] = hermite(ring[aL * 2], ring[bL * 2], ring[cL * 2], ring[dL * 2], t);
      const rv = hermite(ring[aL * 2 + 1], ring[bL * 2 + 1], ring[cL * 2 + 1], ring[dL * 2 + 1], t);
      if (outR !== outL) outR[i] = rv;
      this.readPos += step;
    }
    return true;
  }
}

registerProcessor('pcm-player', PcmPlayer);
