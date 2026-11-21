// mini player glow: the same cover colours and analysis at a whisper. two
// soft glows breathe with the bass and a faint spectrum runs along the
// bottom edge. ported from npv2PaintMini.

import type { RGB, VizFrame } from './frame';

import { beatAmp, bin, palA } from './frame';

export function paintMini(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  S: VizFrame,
  pal: RGB,
  pal2: RGB,
): void {
  const t = S.t || 0;
  const beat = beatAmp(S);
  const bass = S.freq.length ? (S.freq[0] + S.freq[1] + S.freq[2] + S.freq[3]) / 4 / 255 : 0.3;
  const lift = Math.min(1, bass * 0.8 + beat * 0.5);
  ctx.clearRect(0, 0, w, h);
  const glow = (x: number, y: number, r: number, p: RGB, a: number) => {
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, palA(p, a.toFixed(3)));
    g.addColorStop(1, palA(p, 0));
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
  };
  glow(
    w * (0.24 + 0.08 * Math.sin(t * 0.35)),
    h * 0.55,
    h * (1.3 + 0.4 * lift),
    pal,
    0.22 + 0.26 * lift,
  );
  glow(
    w * (0.78 + 0.07 * Math.cos(t * 0.28)),
    h * 0.45,
    h * (1.1 + 0.3 * lift),
    pal2,
    0.16 + 0.16 * lift,
  );
  // spectrum whisper along the bottom edge
  const n = 48;
  const bw = w / n;
  for (let i = 0; i < n; i++) {
    const v = bin(S, i, n);
    const bh = 1.5 + v * h * 0.16;
    ctx.fillStyle = 'rgba(255,255,255,' + (0.05 + v * 0.14).toFixed(3) + ')';
    ctx.fillRect(i * bw + 1, h - bh, Math.max(1, bw - 2), bh);
  }
}
