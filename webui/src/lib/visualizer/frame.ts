// the frame every painter reads, plus the small helpers they share.
// ported from media-player-v2.js (npv2Q, npv2BeatAmp, npv2Css, npv2F, ...).

export interface RGB {
  r: number;
  g: number;
  b: number;
}

export interface VizFrame {
  // 64 log-spaced bands, 40 Hz to 16 kHz, 0..255
  freq: Uint8Array<ArrayBuffer>;
  // 64 time-domain samples, 0..255
  wave: Uint8Array<ArrayBuffer>;
  // the whole analyser window for the scope, null when idle
  waveFull: Uint8Array | null;
  // 0..1 overall level
  energy: number;
  // 0..1 beat envelope from spectral flux
  beat: number;
  // true only on the frame a beat fires
  onset: boolean;
  // visual clock in seconds
  t: number;
  // this frame's seconds
  dt: number;
  // no real audio, painting the idle synth
  idle: boolean;
  // quality factor for particle counts
  q: number;
  pal: RGB;
  pal2: RGB;
  // the loaded cover, for the vinyl label
  art: HTMLImageElement | null;
  title: string;
}

export type Alpha = number | string;

export function createFrame(): VizFrame {
  return {
    freq: new Uint8Array(64),
    wave: new Uint8Array(64),
    waveFull: null,
    energy: 0,
    beat: 0,
    onset: false,
    t: 0,
    dt: 0.016,
    idle: true,
    q: 1,
    pal: { r: 29, g: 185, b: 84 },
    pal2: { r: 185, g: 84, b: 29 },
    art: null,
    title: '',
  };
}

const FALLBACK_PAL2: RGB = { r: 160, g: 140, b: 255 };
const FALLBACK_PAL: RGB = { r: 120, g: 80, b: 200 };

function finiteRgb(p: RGB | null | undefined): p is RGB {
  return !!p && isFinite(p.r) && isFinite(p.g) && isFinite(p.b);
}

export function qOf(S: VizFrame): number {
  const q = S.q;
  return typeof q === 'number' && isFinite(q) && q > 0 ? q : 1;
}

export function beatAmp(S: VizFrame): number {
  const b = S.beat;
  return typeof b === 'number' && isFinite(b) && b > 0 ? Math.min(1, b) : 0;
}

export function pal2Of(S: VizFrame): RGB {
  return finiteRgb(S.pal2) ? S.pal2 : FALLBACK_PAL2;
}

export function css(S: VizFrame, alpha: Alpha): string {
  return 'rgba(' + S.pal.r + ',' + S.pal.g + ',' + S.pal.b + ',' + alpha + ')';
}

export function css2(S: VizFrame, alpha: Alpha): string {
  const p = pal2Of(S);
  return (
    'rgba(' + Math.round(p.r) + ',' + Math.round(p.g) + ',' + Math.round(p.b) + ',' + alpha + ')'
  );
}

// a palette colour as rgba() at any alpha
export function palA(p: RGB | null | undefined, alpha: Alpha): string {
  const c = finiteRgb(p) ? p : FALLBACK_PAL;
  return (
    'rgba(' + Math.round(c.r) + ',' + Math.round(c.g) + ',' + Math.round(c.b) + ',' + alpha + ')'
  );
}

// how many 60 fps frames this frame stands for, so motion runs the same
// speed on a 60, 120 or 144 hz screen. 1 when there is no dt.
export function frameFactor(S: VizFrame): number {
  const dt = S.dt;
  return typeof dt === 'number' && isFinite(dt) && dt > 0 ? Math.min(6, dt * 60) : 1;
}

// per-frame easing factor k (tuned at 60 fps) for this frame's dt
export function ease(k: number, S: VizFrame): number {
  return 1 - Math.pow(1 - k, frameFactor(S));
}

// true only on the frame a beat fires
export function onset(S: VizFrame): boolean {
  return S.onset === true;
}

// probabilistic spawn count for `perFrame` items at this frame's dt
export function spawn(perFrame: number, S: VizFrame): number {
  const n = Math.max(0, perFrame) * frameFactor(S);
  const whole = Math.floor(n);
  return whole + (Math.random() < n - whole ? 1 : 0);
}

// average a slice of the bands into `count` segments. the start is
// proportional so more segments than bands still spread across the spectrum.
export function bin(S: VizFrame, i: number, count: number): number {
  const n = S.freq.length;
  const per = Math.max(1, Math.floor(n / count));
  let sum = 0;
  const start = Math.min(n - per, Math.floor((i * n) / count));
  for (let b = 0; b < per && start + b < n; b++) sum += S.freq[start + b];
  return sum / per / 255;
}

// scope trace: trigger on the first rising zero crossing (with a little
// hysteresis) so the wave stands still, then resample half the window
// into `out` as -1..1
export function scopeTrace(src: Uint8Array | null, out: Float32Array): Float32Array {
  const n = src ? src.length : 0;
  if (!src || !n) {
    out.fill(0);
    return out;
  }
  const half = Math.max(2, Math.floor(n / 2));
  let start = 0;
  let armed = false;
  for (let i = 0; i < half; i++) {
    if (src[i] < 124) armed = true;
    else if (armed && src[i] >= 128) {
      start = i;
      break;
    }
  }
  const span = Math.min(n - start, half);
  for (let p = 0; p < out.length; p++) {
    const idx = start + Math.floor((p * span) / out.length);
    out[p] = (src[Math.min(n - 1, idx)] - 128) / 128;
  }
  return out;
}

// a close neighbour of a colour (hue rotated ~35 degrees, lifted), for
// when there is no second cover colour
export function analogous(p: RGB): RGB {
  const t = (35 / 360) * 2 * Math.PI;
  const cos = Math.cos(t);
  const sin = Math.sin(t);
  const k = (1 - cos) / 3;
  const q = Math.sqrt(1 / 3) * sin;
  const c = (v: number) => Math.max(0, Math.min(255, Math.round(v * 1.12 + 10)));
  return {
    r: c(p.r * (cos + k) + p.g * (k - q) + p.b * (k + q)),
    g: c(p.r * (k + q) + p.g * (cos + k) + p.b * (k - q)),
    b: c(p.r * (k - q) + p.g * (k + q) + p.b * (cos + k)),
  };
}

// ease a colour toward its target over ~1s
export function easeColor(cur: RGB | null, target: RGB, S: VizFrame): RGB {
  if (!cur) return { r: target.r, g: target.g, b: target.b };
  const k = ease(0.06, S);
  return {
    r: cur.r + (target.r - cur.r) * k,
    g: cur.g + (target.g - cur.g) * k,
    b: cur.b + (target.b - cur.b) * k,
  };
}
