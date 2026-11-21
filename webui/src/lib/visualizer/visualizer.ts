// the theater engine: owns one canvas and its rAF loop. ported from
// npv2VizFrame / npv2ReadAudio / npv2SetTheme in media-player-v2.js.
// it never makes an AudioContext; the caller hands it an analyser
// (fftSize 2048 for song-accurate bands).

import type { BandRange, FluxState } from './analysis';
import type { RGB, VizFrame } from './frame';
import type { TrailSpec } from './painters';
import type { VizStore } from './state';
import type { Quality, ThemeId } from './themes';

import {
  VIZ_FMAX,
  VIZ_FMIN,
  createFluxState,
  fluxBeat,
  idleSynth,
  logBandRanges,
  mapLogBands,
} from './analysis';
import { analogous, createFrame, easeColor, frameFactor } from './frame';
import { PAINTERS, TRAILS } from './painters';
import { clearStore, createStore, withStore } from './state';
import {
  DEFAULT_THEME,
  autocycleSeconds,
  clampVizEnergy,
  nextTheme,
  normalizeTheme,
  qualitySpec,
} from './themes';

export interface VisualizerOptions {
  // the analyser to read each frame, or null for the idle synth
  analyser: () => AnalyserNode | null;
  // false paints the idle synth even when an analyser exists
  playing: () => boolean;
  // told when autocycle moves to another theme
  onThemeChange?: (id: ThemeId) => void;
}

interface Buffer {
  c: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  ready: boolean;
  theme: ThemeId | null;
}

const DEFAULT_PALETTE: RGB = { r: 29, g: 185, b: 84 };

function now(): number {
  return typeof performance !== 'undefined' && typeof performance.now === 'function'
    ? performance.now()
    : Date.now();
}

export class Visualizer {
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D | null;
  private readonly opts: VisualizerOptions;
  private readonly store: VizStore = createStore();
  private readonly S: VizFrame = createFrame();

  private theme: ThemeId = DEFAULT_THEME;
  private quality: Quality = 'auto';
  private energyMul = 1;
  private reduceMotion = false;
  private autocycle = 0;
  private lastCycle = 0;

  private palette: RGB = DEFAULT_PALETTE;
  private palette2: RGB | null = null;
  private palCur: RGB | null = null;
  private pal2Cur: RGB | null = null;
  private art: HTMLImageElement | null = null;

  private raf = 0;
  private last = 0;
  private vizT = 0;
  private blank = false;
  private destroyed = false;

  private trail: Buffer | null = null;
  private fade: Buffer | null = null;
  private fadeA = 0;

  private ranges: BandRange[] | null = null;
  private sr = 0;
  private fft = 0;
  private raw: Uint8Array<ArrayBuffer> | null = null;
  private waveFull: Uint8Array<ArrayBuffer> | null = null;
  private readonly flux: FluxState = createFluxState();

  constructor(canvas: HTMLCanvasElement, opts: VisualizerOptions) {
    this.canvas = canvas;
    this.opts = opts;
    let ctx: CanvasRenderingContext2D | null = null;
    try {
      ctx = canvas.getContext('2d');
    } catch {
      ctx = null;
    }
    this.ctx = ctx;
  }

  get running(): boolean {
    return this.raf !== 0;
  }

  get currentTheme(): ThemeId {
    return this.theme;
  }

  start(): void {
    if (this.raf || this.destroyed) return;
    this.last = now();
    this.raf = requestAnimationFrame(this.tick);
  }

  stop(): void {
    if (this.raf) {
      cancelAnimationFrame(this.raf);
      this.raf = 0;
    }
  }

  destroy(): void {
    this.stop();
    this.destroyed = true;
    clearStore(this.store);
    for (const b of [this.trail, this.fade]) {
      if (b) {
        b.c.width = 0;
        b.c.height = 0;
      }
    }
    this.trail = null;
    this.fade = null;
    this.fadeA = 0;
    this.art = null;
  }

  setTheme(id: string): void {
    const next = normalizeTheme(id);
    // crossfade: keep the outgoing theme's last frame and fade it out over
    // the new one instead of a hard cut
    if (next !== this.theme && this.raf && this.theme !== 'none') {
      const b = this.buffer('fade', this.canvas.width, this.canvas.height);
      if (b) {
        try {
          b.ctx.globalCompositeOperation = 'copy';
          b.ctx.drawImage(this.canvas, 0, 0);
          b.ctx.globalCompositeOperation = 'source-over';
          this.fadeA = 1;
        } catch {
          this.fadeA = 0;
        }
      }
    }
    this.theme = next;
    this.blank = false;
    // autocycle restarts from a manual change
    this.lastCycle = this.vizT;
  }

  // the next theme in the list. autocycle passes skipOff.
  cycleTheme(skipOff = false): ThemeId {
    this.setTheme(nextTheme(this.theme, skipOff));
    return this.theme;
  }

  // main cover colour, plus its second colour when there is one. both ease
  // toward the target over ~1s. no accent means a close neighbour of main.
  setPalette(main: RGB, accent?: RGB | null): void {
    this.palette = { r: main.r, g: main.g, b: main.b };
    this.palette2 = accent ? { r: accent.r, g: accent.g, b: accent.b } : null;
  }

  setArt(img: HTMLImageElement | null): void {
    this.art = img;
  }

  setTitle(s: string): void {
    this.S.title = s || '';
  }

  setQuality(q: Quality): void {
    this.quality = q;
  }

  // 0.5..1.5 visual responsiveness
  setEnergy(n: number): void {
    this.energyMul = clampVizEnergy(n);
  }

  setReducedMotion(b: boolean): void {
    this.reduceMotion = !!b;
  }

  // seconds between automatic theme changes. 0, 'off' or 'track' turn the
  // timer off ('track' means the caller cycles on track change).
  setAutocycle(v: number | string): void {
    this.autocycle = autocycleSeconds(v);
    this.lastCycle = this.vizT;
  }

  private tick = (ts: number): void => {
    this.raf = requestAnimationFrame(this.tick);
    const ctx = this.ctx;
    if (!ctx) return;
    if (this.theme === 'none') {
      // the css ambient glow carries it; leave a clear canvas behind
      if (!this.blank) {
        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this.fadeA = 0;
        this.blank = true;
      }
      return;
    }
    if (typeof document !== 'undefined' && document.hidden) return;
    const dt = Math.min(0.1, (ts - this.last) / 1000 || 0.016);
    this.last = ts;
    // reduce motion slows the visual clock; painters read S.t
    this.vizT += dt * (this.reduceMotion ? 0.3 : 1);
    this.sizeCanvas();
    const w = this.canvas.width;
    const h = this.canvas.height;
    const S = this.S;
    this.readAudio(this.vizT, dt);
    S.energy = Math.min(1, S.energy * this.energyMul);
    if (this.reduceMotion) S.energy = Math.min(S.energy, 0.45);
    S.t = this.vizT;
    const pal2 = this.palette2 || analogous(this.palette);
    this.palCur = easeColor(this.palCur, this.palette, S);
    this.pal2Cur = easeColor(this.pal2Cur, pal2, S);
    S.pal = this.palCur;
    S.pal2 = this.pal2Cur;
    const art = this.art;
    S.art = art && art.complete && art.naturalWidth > 0 ? art : null;
    if (this.autocycle > 0 && this.vizT - this.lastCycle >= this.autocycle) {
      const id = this.cycleTheme(true);
      this.opts.onThemeChange?.(id);
    }
    const theme = this.theme;
    const paint = PAINTERS[theme];
    ctx.clearRect(0, 0, w, h);
    const trail = TRAILS[theme];
    if (trail) this.drawTrail(ctx, w, h, trail);
    try {
      withStore(this.store, () => paint(ctx, w, h, S));
    } catch {
      // a bad frame is not a broken player
    }
    if (trail) this.keepTrail(w, h);
    // the previous theme's last frame fades out on top of the new one
    if (this.fadeA > 0 && this.fade) {
      ctx.save();
      ctx.globalAlpha = this.fadeA;
      ctx.drawImage(this.fade.c, 0, 0, w, h);
      ctx.restore();
      this.fadeA = Math.max(0, this.fadeA - S.dt / 0.8);
    }
  };

  private sizeCanvas(): void {
    const c = this.canvas;
    const spec = qualitySpec(this.quality);
    const dpr = Math.min(spec.dpr, (typeof window !== 'undefined' && window.devicePixelRatio) || 1);
    const w = Math.max(2, Math.floor(c.clientWidth * dpr));
    const h = Math.max(2, Math.floor(c.clientHeight * dpr));
    if (c.width !== w || c.height !== h) {
      c.width = w;
      c.height = h;
    }
    this.S.q = spec.q;
  }

  private readAudio(t: number, dtIn: number): void {
    const S = this.S;
    const dt = isFinite(dtIn) && dtIn > 0 ? dtIn : 0.016;
    let idle = true;
    const prevBeat = S.beat || 0;
    try {
      const an = this.opts.playing() ? this.opts.analyser() : null;
      if (an) {
        // song-accurate path: the fft mapped to log bands
        const sr = an.context.sampleRate || 48000;
        const fft = an.fftSize;
        if (!this.ranges || !this.raw || this.sr !== sr || this.fft !== fft) {
          this.ranges = logBandRanges(sr, fft, S.freq.length, VIZ_FMIN, VIZ_FMAX);
          this.sr = sr;
          this.fft = fft;
          this.raw = new Uint8Array(an.frequencyBinCount);
        }
        an.getByteFrequencyData(this.raw);
        an.getByteTimeDomainData(S.wave);
        if (!this.waveFull || this.waveFull.length !== fft) this.waveFull = new Uint8Array(fft);
        an.getByteTimeDomainData(this.waveFull);
        S.waveFull = this.waveFull;
        mapLogBands(this.raw, this.ranges, S.freq);
        let sum = 0;
        for (let i = 0; i < S.freq.length; i++) sum += S.freq[i];
        S.energy = sum / S.freq.length / 255;
        S.beat = fluxBeat(this.flux, S.freq, dt);
        idle = false;
      }
    } catch {
      // analyser unavailable, idle synth below
    }
    if (idle) {
      idleSynth(S.freq, S.wave, t);
      S.energy = 0.22;
      S.beat = Math.max(0, (S.beat || 0) - dt * 2.4);
      // the scope falls back to the synthesized wave
      S.waveFull = null;
    }
    // a beat only ever jumps up when it fires; everything else decays
    S.onset = !idle && S.beat > prevBeat + 1e-6;
    S.dt = dt;
    S.idle = idle;
  }

  private buffer(slot: 'trail' | 'fade', w: number, h: number): Buffer | null {
    let b = this[slot];
    if (!b) {
      try {
        const c = document.createElement('canvas');
        const cx = c.getContext('2d');
        if (!cx) return null;
        b = { c, ctx: cx, ready: false, theme: null };
        this[slot] = b;
      } catch {
        return null;
      }
    }
    if (b.c.width !== w || b.c.height !== h) {
      b.c.width = w;
      b.c.height = h;
      b.ready = false;
    }
    return b;
  }

  private drawTrail(ctx: CanvasRenderingContext2D, w: number, h: number, trail: TrailSpec): void {
    const b = this.buffer('trail', w, h);
    if (!b || !b.ready || b.theme !== this.theme) return;
    const f = frameFactor(this.S);
    ctx.save();
    ctx.globalAlpha = Math.pow(trail.keep, f);
    ctx.translate(w / 2, h / 2);
    // reduce motion keeps the fade but drops the zoom / spin feedback
    if (!this.reduceMotion) {
      const z = Math.pow(1 + (trail.zoom || 0), f);
      ctx.scale(z, z);
      if (trail.spin) ctx.rotate(trail.spin * f);
    }
    ctx.drawImage(b.c, -w / 2, -h / 2);
    ctx.restore();
  }

  private keepTrail(w: number, h: number): void {
    const b = this.buffer('trail', w, h);
    if (!b) return;
    b.ctx.globalCompositeOperation = 'copy';
    b.ctx.drawImage(this.canvas, 0, 0);
    b.ctx.globalCompositeOperation = 'source-over';
    b.ready = true;
    b.theme = this.theme;
  }
}
