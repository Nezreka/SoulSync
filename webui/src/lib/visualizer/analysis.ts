// song-accurate analysis: log bands, spectral-flux beat, idle synth. pure.
//
// a 2048-point fft gets mapped onto 64 log-spaced bands from 40 hz to
// 16 khz, so each band is roughly the same musical interval. beats come
// from spectral flux (how much the spectrum rose since last frame) instead
// of a bass threshold, so a fingerpicked guitar fires as well as a kick.

export const VIZ_BANDS = 64;
// below this is rumble, not music
export const VIZ_FMIN = 40;
// above this is air few speakers play
export const VIZ_FMAX = 16000;
export const VIZ_FFT = 2048;

export type BandRange = readonly [number, number];

// log-spaced band edges over the fft bins. low bands can be narrower than
// one bin, so neighbours may share bins; the mapping stays ordered.
export function logBandRanges(
  sr: number,
  fftSize: number,
  bandCount: number,
  fMin: number,
  fMax: number,
): BandRange[] {
  const binHz = sr / fftSize;
  const maxBin = fftSize / 2 - 1;
  const ratio = Math.pow(fMax / fMin, 1 / bandCount);
  const ranges: BandRange[] = [];
  for (let b = 0; b < bandCount; b++) {
    // bin 0 is dc, start at 1 so silence stays silent
    const lo = Math.min(maxBin, Math.max(1, Math.floor((fMin * Math.pow(ratio, b)) / binHz)));
    const hi = Math.min(
      maxBin,
      Math.max(lo, Math.ceil((fMin * Math.pow(ratio, b + 1)) / binHz) - 1),
    );
    ranges.push([lo, hi]);
  }
  return ranges;
}

// average the raw fft bins into the log bands
export function mapLogBands(
  raw: ArrayLike<number>,
  ranges: readonly BandRange[],
  out: Uint8Array,
): Uint8Array {
  for (let b = 0; b < ranges.length; b++) {
    const lo = ranges[b][0];
    const hi = ranges[b][1];
    let sum = 0;
    let n = 0;
    for (let k = lo; k <= hi && k < raw.length; k++) {
      sum += raw[k];
      n++;
    }
    out[b] = n ? Math.round(sum / n) : 0;
  }
  return out;
}

export interface FluxState {
  prev: Float32Array;
  hist: number[];
  beat: number;
}

export function createFluxState(bands = VIZ_BANDS): FluxState {
  return { prev: new Float32Array(bands), hist: [], beat: 0 };
}

// spectral-flux onset detection. returns the 0..1 beat envelope.
export function fluxBeat(st: FluxState, bands: ArrayLike<number>, dt: number): number {
  if (st.prev.length !== bands.length) st.prev = new Float32Array(bands.length);
  let flux = 0;
  for (let i = 0; i < bands.length; i++) {
    const d = bands[i] - st.prev[i];
    if (d > 0) flux += d;
    st.prev[i] = bands[i];
  }
  flux /= bands.length * 255;
  st.hist.push(flux);
  // ~1.4 s of context at 60 fps
  if (st.hist.length > 86) st.hist.shift();
  let mean = 0;
  for (let h = 0; h < st.hist.length; h++) mean += st.hist[h];
  mean /= Math.max(1, st.hist.length);
  // onset when the rise clearly beats its recent average. the floor keeps
  // digital silence from firing itself.
  if (st.beat < 0.35 && flux > Math.max(0.012, mean * 1.6)) st.beat = 1;
  st.beat = Math.max(0, st.beat - dt * 2.4);
  if (!isFinite(st.beat)) st.beat = 0;
  return st.beat;
}

// gentle low ambient motion for when nothing is playing
export function idleSynth(freq: Uint8Array, wave: Uint8Array, t: number): void {
  for (let i = 0; i < freq.length; i++) {
    const v = 0.5 + 0.5 * Math.sin(t * 0.6 + i * 0.55) * Math.sin(t * 0.23 + i * 0.21);
    freq[i] = Math.round(38 + v * 52);
  }
  for (let i = 0; i < wave.length; i++) {
    wave[i] = Math.round(128 + 26 * Math.sin((i / wave.length) * 6.2832 * 2 + t * 0.8));
  }
}
