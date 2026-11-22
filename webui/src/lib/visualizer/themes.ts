// theme list, palettes and the option clamps. pure.

import type { RGB } from './frame';

export const THEMES = [
  { id: 'barscope', name: 'Barscope', blurb: 'WMP Bars, resurrected' },
  { id: 'scope', name: 'Oscilloscope', blurb: 'WMP Scope phosphor glow' },
  { id: 'waves', name: 'Waves', blurb: 'WMP Waves ribbons' },
  { id: 'plasma', name: 'Plasma', blurb: 'WMP Plasma blobs' },
  { id: 'spikes', name: 'Spikes', blurb: 'WMP Spikes radial burst' },
  { id: 'aurora', name: 'Aurora', blurb: 'Northern-lights drift' },
  { id: 'stardust', name: 'Stardust', blurb: 'Beat-reactive particles' },
  { id: 'vinyl', name: 'Vinyl', blurb: 'Spinning wax, tonearm and all' },
  { id: 'tunnel', name: 'Tunnel', blurb: 'Spectrum wormhole' },
  { id: 'battery', name: 'Battery', blurb: 'WMP Battery sparks rising' },
  { id: 'dotplane', name: 'Dot Plane', blurb: 'WMP dot grid pulsing' },
  { id: 'alchemy', name: 'Alchemy', blurb: 'WMP morphing blobs' },
  { id: 'fountain', name: 'Fountain', blurb: 'Particle fountain, beat-fed' },
  { id: 'kaleido', name: 'Kaleidoscope', blurb: 'Mirrored spectrum bloom' },
  { id: 'warp', name: 'Warp', blurb: 'Starfield at light speed' },
  { id: 'nebula', name: 'Nebula', blurb: 'Drifting deep-space clouds' },
  { id: 'bloom', name: 'Bloom', blurb: 'Beat rings expanding out' },
  { id: 'none', name: 'Off', blurb: 'Just the ambient glow' },
] as const;

export type ThemeId = (typeof THEMES)[number]['id'];

export interface ThemeInfo {
  id: ThemeId;
  name: string;
  blurb: string;
}

export const DEFAULT_THEME: ThemeId = 'aurora';

export const THEME_IDS: readonly ThemeId[] = THEMES.map((t) => t.id);

export function isThemeId(v: unknown): v is ThemeId {
  return typeof v === 'string' && (THEME_IDS as readonly string[]).includes(v);
}

// unknown ids fall back to aurora, like npv2SetTheme
export function normalizeTheme(v: unknown): ThemeId {
  return isThemeId(v) ? v : DEFAULT_THEME;
}

// the theme after `cur`. autocycle passes skipOff so it never lands on Off.
export function nextTheme(cur: ThemeId, skipOff = false): ThemeId {
  const ids = skipOff ? THEME_IDS.filter((id) => id !== 'none') : THEME_IDS;
  if (!ids.length) return cur;
  let idx = ids.indexOf(cur);
  if (idx < 0) idx = 0;
  else idx = (idx + 1) % ids.length;
  return ids[idx];
}

// curated palettes. 'auto' means the album-art palette.
export const PALETTES = {
  ultraviolet: { r: 138, g: 76, b: 246 },
  ocean: { r: 56, g: 150, b: 255 },
  ember: { r: 255, g: 112, b: 67 },
  forest: { r: 52, g: 199, b: 123 },
  rose: { r: 244, g: 63, b: 140 },
  mono: { r: 208, g: 210, b: 216 },
} as const satisfies Record<string, RGB>;

export type PaletteId = 'auto' | keyof typeof PALETTES;

export const PALETTE_LIST: readonly { id: PaletteId; name: string }[] = [
  { id: 'auto', name: 'Album art' },
  { id: 'ultraviolet', name: 'Ultraviolet' },
  { id: 'ocean', name: 'Ocean' },
  { id: 'ember', name: 'Ember' },
  { id: 'forest', name: 'Forest' },
  { id: 'rose', name: 'Rose' },
  { id: 'mono', name: 'Mono' },
];

export type Quality = 'auto' | 'high' | 'balanced' | 'lite';

export interface QualitySpec {
  dpr: number;
  q: number;
}

export function qualitySpec(name: string): QualitySpec {
  switch (name) {
    case 'high':
      return { dpr: 2, q: 1.25 };
    case 'balanced':
      return { dpr: 1.25, q: 0.8 };
    case 'lite':
      return { dpr: 1, q: 0.5 };
    default:
      return { dpr: 1.5, q: 1 };
  }
}

// autocycle interval in seconds. 'track' is -1 (change per track), 0 is off.
export function autocycleSeconds(v: string | number): number {
  if (v === 'track') return -1;
  const n = parseInt(String(v), 10);
  return isFinite(n) && n > 0 ? n : 0;
}

export function clampVizEnergy(v: unknown): number {
  const n = parseFloat(String(v));
  if (!isFinite(n)) return 1;
  return Math.min(1.5, Math.max(0.5, n));
}

export function clampVizDim(v: unknown): number {
  const n = parseFloat(String(v));
  if (!isFinite(n)) return 0;
  return Math.min(0.6, Math.max(0, n));
}
