/**
 * AudioWorklet processor — runs in a dedicated audio thread.
 * Downsamples from the device's native rate (48kHz / 96kHz / 44.1kHz)
 * down to 16kHz mono and posts Int16Array chunks to the main thread.
 *
 * Uses linear interpolation for simplicity; good enough for speech STT.
 */

const TARGET_RATE = 16000;
const CHUNK_SIZE  = 4000;   // 250ms at 16kHz

class AudioResampleProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    // `sampleRate` is the AudioContext sample rate (global in AudioWorkletGlobalScope)
    this.ratio   = sampleRate / TARGET_RATE;
    this.buf     = [];        // float32 accumulator at input rate
    this.needed  = Math.ceil(CHUNK_SIZE * this.ratio);
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel || channel.length === 0) return true;

    // Accumulate input samples
    for (let i = 0; i < channel.length; i++) {
      this.buf.push(channel[i]);
    }

    // Emit chunks whenever we have enough input samples
    while (this.buf.length >= this.needed) {
      const out = new Int16Array(CHUNK_SIZE);
      for (let i = 0; i < CHUNK_SIZE; i++) {
        const srcF = i * this.ratio;
        const lo   = Math.floor(srcF);
        const hi   = Math.min(lo + 1, this.buf.length - 1);
        const frac = srcF - lo;
        const s    = this.buf[lo] * (1 - frac) + this.buf[hi] * frac;
        out[i]     = Math.max(-32768, Math.min(32767, Math.round(s * 32767)));
      }
      // Discard the consumed input samples
      this.buf.splice(0, this.needed);
      this.port.postMessage(out.buffer, [out.buffer]);
    }

    return true;
  }
}

registerProcessor('audio-resample-processor', AudioResampleProcessor);
