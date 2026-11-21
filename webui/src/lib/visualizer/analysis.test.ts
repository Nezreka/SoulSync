import {
  VIZ_BANDS,
  VIZ_FFT,
  VIZ_FMAX,
  VIZ_FMIN,
  createFluxState,
  fluxBeat,
  idleSynth,
  logBandRanges,
  mapLogBands,
} from './analysis';
import { createFrame, frameFactor, ease, analogous, easeColor } from './frame';
import {
  autocycleSeconds,
  clampVizDim,
  clampVizEnergy,
  nextTheme,
  normalizeTheme,
  qualitySpec,
} from './themes';

describe('log bands', () => {
  const ranges = logBandRanges(48000, VIZ_FFT, VIZ_BANDS, VIZ_FMIN, VIZ_FMAX);

  it('makes one ordered, gap-free range per band, skipping dc', () => {
    expect(ranges).toHaveLength(VIZ_BANDS);
    expect(ranges[0][0]).toBeGreaterThanOrEqual(1);
    for (let b = 0; b < ranges.length; b++) {
      expect(ranges[b][1]).toBeGreaterThanOrEqual(ranges[b][0]);
      expect(ranges[b][1]).toBeLessThanOrEqual(VIZ_FFT / 2 - 1);
      if (b > 0) {
        expect(ranges[b][0]).toBeGreaterThanOrEqual(ranges[b - 1][0]);
        expect(ranges[b][0]).toBeLessThanOrEqual(ranges[b - 1][1] + 1);
      }
    }
  });

  it('maps a rising raw spectrum to monotonic bands', () => {
    const raw = Uint8Array.from({ length: VIZ_FFT / 2 }, (_, i) => Math.min(255, i >> 1));
    const out = mapLogBands(raw, ranges, new Uint8Array(VIZ_BANDS));
    for (let b = 1; b < out.length; b++) expect(out[b]).toBeGreaterThanOrEqual(out[b - 1]);
    expect(out[VIZ_BANDS - 1]).toBeGreaterThan(out[0]);
  });

  it('silence maps to zero', () => {
    const out = mapLogBands(new Uint8Array(1024), ranges, new Uint8Array(VIZ_BANDS));
    expect(Array.from(out).every((v) => v === 0)).toBe(true);
  });
});

describe('flux beat', () => {
  it('stays quiet on a steady spectrum and fires on an onset', () => {
    const st = createFluxState();
    const steady = new Uint8Array(VIZ_BANDS).fill(90);
    // the first frame from zero is itself a rise; let it decay away
    for (let i = 0; i < 60; i++) fluxBeat(st, steady, 1 / 60);
    expect(st.beat).toBe(0);
    const hit = new Uint8Array(VIZ_BANDS).fill(220);
    const b = fluxBeat(st, hit, 1 / 60);
    expect(b).toBeGreaterThan(0.9);
    // decays after
    let last = b;
    for (let i = 0; i < 5; i++) {
      const v = fluxBeat(st, hit, 1 / 60);
      expect(v).toBeLessThan(last);
      last = v;
    }
  });

  it('digital silence never fires', () => {
    const st = createFluxState();
    const zero = new Uint8Array(VIZ_BANDS);
    for (let i = 0; i < 100; i++) expect(fluxBeat(st, zero, 1 / 60)).toBe(0);
  });

  it('resizes its state for any band count', () => {
    const st = createFluxState();
    expect(() => fluxBeat(st, new Uint8Array(8), 1 / 60)).not.toThrow();
    expect(st.prev).toHaveLength(8);
  });
});

describe('idle synth', () => {
  it('stays gentle and low', () => {
    const S = createFrame();
    idleSynth(S.freq, S.wave, 3.2);
    for (const v of S.freq) {
      expect(v).toBeGreaterThanOrEqual(38);
      expect(v).toBeLessThanOrEqual(90);
    }
    for (const v of S.wave) expect(Math.abs(v - 128)).toBeLessThanOrEqual(26);
  });
});

describe('frame helpers + options', () => {
  it('frame factor follows dt and defaults to one frame', () => {
    expect(frameFactor({ ...createFrame(), dt: 1 / 60 })).toBeCloseTo(1);
    expect(frameFactor({ ...createFrame(), dt: 1 / 120 })).toBeCloseTo(0.5);
    expect(frameFactor({ ...createFrame(), dt: 0 })).toBe(1);
    expect(ease(0.5, { ...createFrame(), dt: 1 / 30 })).toBeCloseTo(0.75);
  });

  it('analogous colour stays in range and eases', () => {
    const a = analogous({ r: 29, g: 185, b: 84 });
    for (const v of [a.r, a.g, a.b]) {
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(255);
    }
    const S = { ...createFrame(), dt: 1 / 60 };
    expect(easeColor(null, a, S)).toEqual(a);
    const mid = easeColor({ r: 0, g: 0, b: 0 }, { r: 100, g: 100, b: 100 }, S);
    expect(mid.r).toBeCloseTo(6);
  });

  it('options clamp like the vanilla player', () => {
    expect(qualitySpec('high')).toEqual({ dpr: 2, q: 1.25 });
    expect(qualitySpec('whatever')).toEqual({ dpr: 1.5, q: 1 });
    expect(clampVizEnergy(9)).toBe(1.5);
    expect(clampVizEnergy('x')).toBe(1);
    expect(clampVizDim(-1)).toBe(0);
    expect(autocycleSeconds('track')).toBe(-1);
    expect(autocycleSeconds('60')).toBe(60);
    expect(autocycleSeconds('off')).toBe(0);
    expect(normalizeTheme('nope')).toBe('aurora');
    expect(nextTheme('bloom', true)).toBe('barscope');
    expect(nextTheme('bloom')).toBe('none');
  });
});
