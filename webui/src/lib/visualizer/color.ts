// album-art colours for the visualizer palette. ported from media-player.js
// (npExtractAmbientColor, npHue, npPickAccentColor, npPunchUpColor).

import type { RGB } from './frame';

export type Triple = [number, number, number];

export interface ColorBin {
  r: number;
  g: number;
  b: number;
  n: number;
  w: number;
}

export interface AmbientColors {
  main: RGB;
  accent: RGB;
}

// hue of an rgb colour in degrees, or -1 for a grey
export function hue(r: number, g: number, b: number): number {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  if (d < 12) return -1;
  let h: number;
  if (max === r) h = ((g - b) / d) % 6;
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  h *= 60;
  return h < 0 ? h + 360 : h;
}

// second colour: the strongest bin whose hue sits clearly away from the main
// one and still carries a real share of the cover. a one-colour cover gets a
// close lighter neighbour instead, so the accent stays in harmony with the art.
export function pickAccentColor(bins: readonly ColorBin[], best: ColorBin): Triple {
  const avg = (bin: ColorBin): Triple => [bin.r / bin.n, bin.g / bin.n, bin.b / bin.n];
  const [br, bg, bb] = avg(best);
  const h0 = hue(br, bg, bb);
  let pick: Triple | null = null;
  let pickScore = -1;
  for (const bin of bins) {
    if (bin === best || bin.w < best.w * 0.12) continue;
    const [r, g, b] = avg(bin);
    const h = hue(r, g, b);
    if (h < 0 || h0 < 0) continue;
    const dh = Math.min(Math.abs(h - h0), 360 - Math.abs(h - h0));
    if (dh < 40) continue;
    if (bin.w > pickScore) {
      pickScore = bin.w;
      pick = [r, g, b];
    }
  }
  if (pick) return [Math.round(pick[0]), Math.round(pick[1]), Math.round(pick[2])];
  // analogous fallback: rotate the hue ~35 degrees and lift it a little
  const t = (35 / 360) * 2 * Math.PI;
  const cos = Math.cos(t);
  const sin = Math.sin(t);
  const k = (1 - cos) / 3;
  const q = Math.sqrt(1 / 3) * sin;
  const c = (v: number) => Math.max(0, Math.min(255, Math.round(v * 1.12 + 10)));
  return [
    c(br * (cos + k) + bg * (k - q) + bb * (k + q)),
    c(br * (k + q) + bg * (cos + k) + bb * (k - q)),
    c(br * (k - q) + bg * (k + q) + bb * (cos + k)),
  ];
}

// lift a colour toward vivid (about 1.3x saturation, brightness floor) so
// dark or pastel covers still glow
export function punchUpColor(r: number, g: number, b: number): Triple {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  // grey, leave it
  if (max === min) return [r, g, b];
  const mid = (max + min) / 2;
  const boost = 1.3;
  let nr = Math.round(mid + (r - mid) * boost);
  let ng = Math.round(mid + (g - mid) * boost);
  let nb = Math.round(mid + (b - mid) * boost);
  const bright = (nr + ng + nb) / 3;
  if (bright < 70) {
    const lift = 70 / Math.max(bright, 1);
    nr *= lift;
    ng *= lift;
    nb *= lift;
  }
  const clamp = (v: number) => Math.max(0, Math.min(255, Math.round(v)));
  return [clamp(nr), clamp(ng), clamp(nb)];
}

// dominant vibrant colour from rgba pixels, not a flat average (that muddies
// to grey-brown). coarse 4-bit-per-channel bins, each weighted by
// saturation squared so a punchy accent beats a big dull background.
// returns null when every pixel is transparent, near-black or near-white.
export function extractAmbientColors(data: ArrayLike<number>): AmbientColors | null {
  const bins = new Map<number, ColorBin>();
  // sample every 4th pixel
  for (let i = 0; i + 3 < data.length; i += 16) {
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const a = data[i + 3];
    if (a < 128) continue;
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const brightness = (r + g + b) / 3;
    // skip near-black and near-white
    if (brightness < 24 || brightness > 240) continue;
    const sat = max === 0 ? 0 : (max - min) / max;
    const key = ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4);
    // floor so greys still count a little
    const weight = 0.15 + sat * sat;
    const bin = bins.get(key);
    if (bin) {
      bin.r += r;
      bin.g += g;
      bin.b += b;
      bin.n++;
      bin.w += weight;
    } else bins.set(key, { r, g, b, n: 1, w: weight });
  }
  let best: ColorBin | null = null;
  let bestScore = -1;
  for (const bin of bins.values()) {
    if (bin.w > bestScore) {
      bestScore = bin.w;
      best = bin;
    }
  }
  if (!best) return null;
  const [r, g, b] = punchUpColor(
    Math.round(best.r / best.n),
    Math.round(best.g / best.n),
    Math.round(best.b / best.n),
  );
  const [r2, g2, b2] = punchUpColor(...pickAccentColor(Array.from(bins.values()), best));
  return { main: { r, g, b }, accent: { r: r2, g: g2, b: b2 } };
}

// draw a loaded cover at 64x64 and read its colours. null when the image
// is not loaded, the canvas is tainted (cross-origin), or nothing qualifies.
export function ambientColorsFromImage(img: HTMLImageElement): AmbientColors | null {
  try {
    if (!img.complete || !img.naturalWidth) return null;
    const canvas = document.createElement('canvas');
    canvas.width = 64;
    canvas.height = 64;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.drawImage(img, 0, 0, 64, 64);
    return extractAmbientColors(ctx.getImageData(0, 0, 64, 64).data);
  } catch {
    // cross-origin or canvas error
    return null;
  }
}
