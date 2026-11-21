// the 17 theater painters, transcribed from NPV2_PAINT in media-player-v2.js
// (generated once from it, then typed). every painter gets (ctx, w, h, S).
// keep the math in step with the vanilla source: same look, just typed.

import type { VizFrame } from './frame';
import type { ThemeId } from './themes';

import {
  beatAmp,
  bin,
  css,
  css2,
  ease,
  frameFactor,
  onset,
  pal2Of,
  palA,
  qOf,
  scopeTrace,
  spawn as spawnCount,
} from './frame';
import { binS, soft, themeState } from './state';

export type Painter = (ctx: CanvasRenderingContext2D, w: number, h: number, S: VizFrame) => void;

export interface TrailSpec {
  keep: number;
  zoom?: number;
  spin?: number;
}

// real persistence: these themes see the previous frame drawn back under the
// new one, faded by `keep` (per 60 fps frame) and nudged by zoom / spin
export const TRAILS: Partial<Record<ThemeId, TrailSpec>> = {
  scope: { keep: 0.8 },
  spikes: { keep: 0.7, zoom: 0.012, spin: 0.003 },
  warp: { keep: 0.74, zoom: 0.02 },
  fountain: { keep: 0.68 },
  bloom: { keep: 0.7, zoom: 0.008 },
  tunnel: { keep: 0.55, zoom: 0.03 },
};

interface Pt {
  x: number;
  y: number;
}
interface PlasmaSpark {
  a: number;
  r: number;
  sp: number;
  life: number;
}
interface Ring {
  r: number;
  x: number;
  y: number;
}
interface Comet {
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number;
}
interface Streak {
  a: number;
  z: number;
  sp: number;
}
interface Ember {
  x: number;
  y: number;
  vy: number;
  vx: number;
  life: number;
  decay: number;
  sz: number;
  far: boolean;
  ph: number;
}
interface Arc {
  pts: Pt[] | null;
  life: number;
  ambient: boolean;
}
interface Drop {
  x: number;
  y: number;
  px: number;
  py: number;
  vx: number;
  vy: number;
  life: number;
  decay: number;
  sz: number;
}
interface Splash {
  x: number;
  y: number;
  vx: number;
  life: number;
}
interface Jump {
  a: number;
  z: number;
  life: number;
}
interface BloomRing {
  r: number;
  w: number;
  rot: number;
  petals: number;
  accent: boolean;
  gentle?: boolean;
}
interface BloomSpark {
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number;
  accent: boolean;
}

export const PAINTERS: Record<ThemeId, Painter> = {
  // --- WMP Bars: mirrored bars firing from the center line ----------------
  barscope(ctx, w, h, S) {
    const st = themeState('barscope', () => ({ peaks: [] as number[] }));
    const N = 56;
    const cy = h * 0.52;
    const maxH = h * 0.42;
    const beat = beatAmp(S);
    while (st.peaks.length < N) st.peaks.push(0);
    // faint backdrop wash so the bars sit in something
    const wash = ctx.createLinearGradient(0, 0, 0, h);
    wash.addColorStop(0, css(S, 0));
    wash.addColorStop(0.52, css(S, 0.1 + S.energy * 0.08));
    wash.addColorStop(1, css(S, 0));
    ctx.fillStyle = wash;
    ctx.fillRect(0, 0, w, h);
    const bw = w / N;
    for (let i = 0; i < N; i++) {
      const v = binS(S, 'barscope', i, N);
      st.peaks[i] = Math.max(v, st.peaks[i] - 0.008 * frameFactor(S));
      const bh = Math.max(2, v * maxH);
      const x = i * bw + bw * 0.18;
      const ww = bw * 0.64;
      // bar body: palette core, accent + near-white at the tips
      const g = ctx.createLinearGradient(0, cy - bh, 0, cy + bh);
      g.addColorStop(0, css2(S, 0.95));
      g.addColorStop(0.42, css(S, 0.85));
      g.addColorStop(1, css(S, 0.1));
      ctx.fillStyle = g;
      if (ctx.roundRect) {
        ctx.beginPath();
        ctx.roundRect(x, cy - bh, ww, bh * 2, Math.min(ww * 0.4, 4));
        ctx.fill();
      } else {
        ctx.fillRect(x, cy - bh, ww, bh * 2);
      }
      // bright tip edges catch the light
      ctx.fillStyle = 'rgba(255,255,255,' + (0.25 + v * 0.55).toFixed(3) + ')';
      ctx.fillRect(x, cy - bh, ww, 1.5);
      ctx.fillRect(x, cy + bh - 1.5, ww, 1.5);
      // soft mirror shimmer below the line — sells the "stage"
      const sh = Math.min(bh * 0.5, h * 0.1);
      const mg = ctx.createLinearGradient(0, cy + bh, 0, cy + bh + sh);
      mg.addColorStop(0, css(S, (0.22 * v).toFixed(3)));
      mg.addColorStop(1, css(S, 0));
      ctx.fillStyle = mg;
      ctx.fillRect(x, cy + bh, ww, sh);
      // falling peak cap, pure WMP — flashes on the beat
      const py = st.peaks[i] * maxH;
      ctx.fillStyle =
        'rgba(255,255,255,' + (0.22 + st.peaks[i] * 0.45 + beat * 0.3).toFixed(3) + ')';
      ctx.fillRect(x, cy - py - 2, ww, 2);
      ctx.fillRect(x, cy + py, ww, 2);
    }
    ctx.fillStyle = 'rgba(255,255,255,' + (0.1 + beat * 0.15).toFixed(3) + ')';
    ctx.fillRect(0, cy - 0.5, w, 1);
  },

  // --- WMP Scope: phosphor waveform ---------------------------------------
  // A real oscilloscope: the whole 2048-sample window, triggered on a rising
  // zero crossing so the wave stands still, resampled to a smooth trace.
  // The afterglow is real persistence (NPV2_TRAILS), not shifted copies.
  scope(ctx, w, h, S) {
    const cy = h * 0.52;
    const beat = beatAmp(S);
    const st = themeState('scope', () => ({ gain: 1, pts: null as Float32Array | null }));
    // light backdrop: the trail system fades the old trace underneath
    ctx.fillStyle = 'rgba(2,6,4,0.30)';
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = 'rgba(255,255,255,0.05)';
    ctx.lineWidth = 1;
    for (let gy = 0.2; gy < 1; gy += 0.2) {
      ctx.beginPath();
      ctx.moveTo(0, h * gy);
      ctx.lineTo(w, h * gy);
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.moveTo(w / 2, 0);
    ctx.lineTo(w / 2, h);
    ctx.stroke();
    // corner ticks frame the scope face
    ctx.strokeStyle = 'rgba(255,255,255,0.14)';
    const tk = 14;
    [
      [8, 8, 1, 1],
      [w - 8, 8, -1, 1],
      [8, h - 8, 1, -1],
      [w - 8, h - 8, -1, -1],
    ].forEach(([tx, ty, sx, sy]) => {
      ctx.beginPath();
      ctx.moveTo(tx, ty + tk * sy);
      ctx.lineTo(tx, ty);
      ctx.lineTo(tx + tk * sx, ty);
      ctx.stroke();
    });
    const src = S.waveFull && S.waveFull.length > 64 ? S.waveFull : S.wave;
    const n = Math.max(64, Math.round(384 * qOf(S)));
    if (!st.pts || st.pts.length !== n) st.pts = new Float32Array(n);
    const pts = st.pts;
    scopeTrace(src, pts);
    // auto-gain: normalize to the observed peak so the trace always
    // fills the scope face, quiet track or loud, like a real scope
    let peak = 0;
    for (let i = 0; i < n; i++) {
      const d = Math.abs(pts[i]);
      if (d > peak) peak = d;
    }
    const tgt = Math.min((0.72 + S.energy * 0.28) / Math.max(peak * 2, 0.12), 6);
    st.gain += (tgt - st.gain) * ease(tgt > st.gain ? 0.25 : 0.04, S);
    const amp = h * 0.3 * st.gain;
    const trace = (alpha: number, width: number, glow: number) => {
      ctx.beginPath();
      for (let i = 0; i < n; i++) {
        const x = (i / (n - 1)) * w;
        const y = cy - pts[i] * amp;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = css(S, alpha);
      ctx.lineWidth = width;
      ctx.lineJoin = 'round';
      ctx.shadowColor = css(S, 0.8);
      ctx.shadowBlur = glow;
      ctx.stroke();
      ctx.shadowBlur = 0;
    };
    trace(0.16, 8, 0); // soft phosphor halo
    trace(0.9, 2, 14 + beat * 10); // hot core with bloom
    ctx.strokeStyle = 'rgba(255,255,255,' + (0.35 + beat * 0.4).toFixed(3) + ')';
    ctx.lineWidth = 0.8;
    ctx.stroke(); // white-hot filament on the core
    if (beat > 0.05) {
      ctx.fillStyle = 'rgba(255,255,255,' + (beat * 0.8).toFixed(3) + ')';
      ctx.beginPath();
      ctx.arc(6, cy, 2 + beat * 3, 0, 6.2832);
      ctx.fill();
    }
  },

  // --- WMP Waves: layered translucent ribbons ------------------------------
  waves(ctx, w, h, S) {
    const beat = beatAmp(S);
    // deep water: dark top falling into a palette-tinted floor
    const deep = ctx.createLinearGradient(0, 0, 0, h);
    deep.addColorStop(0, '#02040a');
    deep.addColorStop(0.55, '#04060f');
    deep.addColorStop(1, css(S, 0.1 + S.energy * 0.1));
    ctx.fillStyle = deep;
    ctx.fillRect(0, 0, w, h);
    const layers = [
      { amp: 0.1, speed: 0.9, alpha: 0.16, yOff: 0.38, accent: true },
      { amp: 0.16, speed: 0.7, alpha: 0.3, yOff: 0.46, accent: false },
      { amp: 0.22, speed: 0.45, alpha: 0.22, yOff: 0.56, accent: true },
      { amp: 0.13, speed: 1.15, alpha: 0.4, yOff: 0.64, accent: false },
    ];
    layers.forEach((L, li) => {
      const e = (0.35 + S.energy * 1.4) * (1 + beat * 0.25);
      const steps = 90;
      const pts = [];
      for (let i = 0; i <= steps; i++) {
        const x = (i / steps) * w;
        const ph = i * 0.09 + S.t * L.speed * (li % 2 ? -1 : 1) * 2;
        const mod = 0.6 + 0.4 * bin(S, i % 32, 32);
        const y =
          h * L.yOff +
          Math.sin(ph) * h * L.amp * e * mod +
          Math.sin(ph * 2.7 + li * 1.7) * h * L.amp * 0.35 * e;
        pts.push([x, y]);
      }
      // the front ribbon gets a filled body, not just a stroke
      if (li === 3) {
        const fg = ctx.createLinearGradient(0, h * (L.yOff - L.amp), 0, h);
        fg.addColorStop(0, L.accent ? css2(S, 0) : css(S, 0));
        fg.addColorStop(0.45, L.accent ? css2(S, L.alpha * 0.55) : css(S, L.alpha * 0.55));
        fg.addColorStop(1, L.accent ? css2(S, 0.02) : css(S, 0.02));
        ctx.beginPath();
        pts.forEach(([x, y], i) => {
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.lineTo(w, h);
        ctx.lineTo(0, h);
        ctx.closePath();
        ctx.fillStyle = fg;
        ctx.fill();
      }
      ctx.beginPath();
      pts.forEach(([x, y], i) => {
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = L.accent ? css2(S, L.alpha) : css(S, L.alpha);
      ctx.lineWidth = 30 - li * 6;
      ctx.lineCap = 'round';
      if (li === 3) {
        ctx.shadowColor = css(S, 0.7);
        ctx.shadowBlur = 14;
      }
      ctx.stroke();
      ctx.shadowBlur = 0;
      // crest foam on the front ribbon
      if (li === 3) {
        ctx.beginPath();
        pts.forEach(([x, y], i) => {
          if (i === 0) ctx.moveTo(x, y - 8);
          else ctx.lineTo(x, y - 8);
        });
        ctx.strokeStyle = 'rgba(255,255,255,' + (0.1 + S.energy * 0.12).toFixed(3) + ')';
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
    });
  },

  // --- WMP Plasma: drifting energy blobs, additive --------------------------
  plasma(ctx, w, h, S) {
    const st = themeState('plasma', () => ({
      seeds: [0.7, 1.9, 3.1, 4.4, 5.6, 0.3, 2.5, 5.1],
      sparks: [] as PlasmaSpark[],
      p: [] as (Pt | undefined)[],
    }));
    const beat = beatAmp(S);
    ctx.fillStyle = '#04040c';
    ctx.fillRect(0, 0, w, h);
    const R = Math.max(w, h) * 0.3;
    // blob positions first (full-res coordinates), shared by the soft
    // layer and the filaments
    const blobs = st.seeds.map((sd, i) => {
      const sp = 0.22 + i * 0.06;
      const x = w / 2 + Math.cos(S.t * sp + sd * 2.1) * w * 0.3 * (0.5 + S.energy * 0.7);
      const y = h / 2 + Math.sin(S.t * sp * 1.3 + sd * 3.7) * h * 0.28;
      const r =
        R *
        (0.55 + 0.45 * binS(S, 'plasma', Math.min(i * 5, 31), 32) + S.energy * 0.25) *
        (1 + beat * 0.35);
      st.p[i] = { x, y };
      return { sd, i, x, y, r, accent: i % 3 === 2 };
    });
    const rotA = Math.sin(S.t * 0.05) * 0.35;
    // the blobs are pure glow: draw them at a third of the size
    soft(
      ctx,
      w,
      h,
      0.34,
      'plasma',
      (c, sw, sh) => {
        const k = sw / w;
        c.globalCompositeOperation = 'lighter';
        c.translate(sw / 2, sh / 2);
        c.rotate(rotA);
        c.translate(-sw / 2, -sh / 2);
        for (const bl of blobs) {
          const r = bl.r * k;
          const rx = r * (1 + 0.28 * Math.sin(S.t * 0.6 + bl.sd * 4));
          const ry = r * (1 - 0.22 * Math.sin(S.t * 0.6 + bl.sd * 4));
          const tilt = Math.sin(S.t * 0.3 + bl.sd) * 0.8;
          const col = bl.accent ? css2(S, 0.3) : css(S, 0.34);
          const clear = bl.accent ? css2(S, 0) : css(S, 0);
          c.save();
          c.translate(bl.x * k, bl.y * k);
          c.rotate(tilt);
          c.scale(rx / r, ry / r);
          const g = c.createRadialGradient(0, 0, 0, 0, 0, r);
          g.addColorStop(0, col);
          g.addColorStop(1, clear);
          c.fillStyle = g;
          c.beginPath();
          c.arc(0, 0, r, 0, 6.2832);
          c.fill();
          const core = bl.accent ? css2(S, 0.55 + beat * 0.3) : css(S, 0.55 + beat * 0.3);
          const cg = c.createRadialGradient(0, 0, 0, 0, 0, r * 0.38);
          cg.addColorStop(0, core);
          cg.addColorStop(1, clear);
          c.fillStyle = cg;
          c.beginPath();
          c.arc(0, 0, r * 0.38, 0, 6.2832);
          c.fill();
          c.restore();
        }
      },
      'lighter',
    );
    ctx.globalCompositeOperation = 'lighter';
    ctx.save();
    ctx.translate(w / 2, h / 2);
    ctx.rotate(rotA);
    ctx.translate(-w / 2, -h / 2);
    // electric filaments between near neighbors — curved arcs that bow
    // and breathe; straight lines read as a constellation, not plasma
    ctx.lineWidth = 1.2;
    for (let i = 0; i < st.seeds.length; i++) {
      for (let j = i + 1; j < st.seeds.length; j++) {
        const a = st.p[i],
          b = st.p[j];
        if (!a || !b) continue;
        const dx = b.x - a.x,
          dy = b.y - a.y;
        const d = Math.hypot(dx, dy);
        if (d > R * 2.2 || d < 4) continue;
        const al = (1 - d / (R * 2.2)) * (0.1 + beat * 0.22);
        const bow = d * 0.28 * Math.sin(S.t * 1.1 + i * 2.1 + j * 1.3);
        const nx = -dy / d,
          ny = dx / d;
        ctx.strokeStyle = (i + j) % 3 === 0 ? css2(S, al.toFixed(3)) : css(S, al.toFixed(3));
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.quadraticCurveTo((a.x + b.x) / 2 + nx * bow, (a.y + b.y) / 2 + ny * bow, b.x, b.y);
        ctx.stroke();
      }
    }
    ctx.restore();
    // bright sparks orbiting the blob field — tiny, fast, alive
    if (st.sparks.length < 14 && spawnCount(0.1 + S.energy * 0.15, S) > 0) {
      const a = Math.random() * 6.2832;
      st.sparks.push({
        a,
        r: Math.max(w, h) * (0.18 + Math.random() * 0.22),
        sp: 0.6 + Math.random() * 1.4,
        life: 1,
      });
    }
    for (let i = st.sparks.length - 1; i >= 0; i--) {
      const sp = st.sparks[i];
      sp.a += sp.sp * 0.016 * frameFactor(S);
      sp.life -= 0.012 * frameFactor(S);
      if (sp.life <= 0) {
        st.sparks.splice(i, 1);
        continue;
      }
      const sx = w / 2 + Math.cos(sp.a) * sp.r;
      const sy = h / 2 + Math.sin(sp.a) * sp.r * 0.8;
      ctx.fillStyle = 'rgba(255,255,255,' + (sp.life * 0.7).toFixed(3) + ')';
      ctx.beginPath();
      ctx.arc(sx, sy, 1.4, 0, 6.2832);
      ctx.fill();
    }
    ctx.globalCompositeOperation = 'source-over';
    // vignette to keep the edges cinematic
    const vg = ctx.createRadialGradient(
      w / 2,
      h / 2,
      Math.min(w, h) * 0.35,
      w / 2,
      h / 2,
      Math.max(w, h) * 0.75,
    );
    vg.addColorStop(0, 'rgba(0,0,0,0)');
    vg.addColorStop(1, 'rgba(0,0,0,0.45)');
    ctx.fillStyle = vg;
    ctx.fillRect(0, 0, w, h);
  },

  // --- WMP Spikes: radial burst ---------------------------------------------
  spikes(ctx, w, h, S) {
    const cx = w / 2,
      cy = h / 2;
    const N = 110;
    const base = Math.min(w, h) * 0.14;
    const maxL = Math.min(w, h) * 0.46;
    const beat = beatAmp(S);
    ctx.fillStyle = 'rgba(3,3,8,0.6)';
    ctx.fillRect(0, 0, w, h);
    // faint outer guide ring
    ctx.strokeStyle = css(S, 0.12);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, base + maxL, 0, 6.2832);
    ctx.stroke();
    // ghost ring: shorter, fainter spikes counter-rotating behind the main burst
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(-S.t * 0.06);
    ctx.fillStyle = css(S, 0.1);
    for (let i = 0; i < N; i += 2) {
      const v = binS(S, 'spikes', (i + N / 2) % N, N);
      const len = base + (0.3 + v * 0.7) * maxL * 0.55 * (0.6 + S.energy * 0.6);
      const a = (i / N) * 6.2832;
      const halfW = 0.016;
      ctx.beginPath();
      ctx.moveTo(Math.cos(a - halfW) * base, Math.sin(a - halfW) * base);
      ctx.lineTo(Math.cos(a) * len, Math.sin(a) * len);
      ctx.lineTo(Math.cos(a + halfW) * base, Math.sin(a + halfW) * base);
      ctx.closePath();
      ctx.fill();
    }
    ctx.restore();
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(S.t * 0.1 + beat * 0.15);
    for (let i = 0; i < N; i++) {
      const v = binS(S, 'spikes', i, N);
      // generous floor: the burst reads even on quiet passages, not
      // just when the music is hot
      const len = base + (0.3 + v * 0.7) * maxL * (0.6 + S.energy * 0.6);
      const a = (i / N) * 6.2832;
      const hot = v > 0.75;
      // tapered spike: a triangle reads as a burst, a line reads as a chart
      const halfW = hot ? 0.02 : 0.014;
      const ca = Math.cos(a),
        sa = Math.sin(a);
      ctx.fillStyle = hot ? css2(S, 0.9) : css(S, 0.22 + v * 0.6);
      ctx.beginPath();
      ctx.moveTo(ca * base - sa * halfW * base, sa * base + ca * halfW * base);
      ctx.lineTo(ca * len, sa * len);
      ctx.lineTo(ca * base + sa * halfW * base, sa * base - ca * halfW * base);
      ctx.closePath();
      ctx.fill();
      if (v > 0.55) {
        ctx.fillStyle = 'rgba(255,255,255,' + (v * 0.5).toFixed(3) + ')';
        ctx.beginPath();
        ctx.arc(ca * len, sa * len, 1.6, 0, 6.2832);
        ctx.fill();
      }
    }
    ctx.restore();
    // core: solid disc + beat shockwave ring
    const coreR = base * (0.7 + beat * 0.5);
    const cg = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR * 2);
    cg.addColorStop(0, 'rgba(255,255,255,0.85)');
    cg.addColorStop(0.4, css(S, 0.7));
    cg.addColorStop(1, css(S, 0));
    ctx.fillStyle = cg;
    ctx.beginPath();
    ctx.arc(cx, cy, coreR * 2, 0, 6.2832);
    ctx.fill();
    if (beat > 0.1) {
      ctx.strokeStyle = css(S, (beat * 0.6).toFixed(3));
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(cx, cy, base + (1 - beat) * maxL * 0.9, 0, 6.2832);
      ctx.stroke();
    }
  },

  // --- Aurora: starfield + drifting light ribbons ---------------------------
  aurora(ctx, w, h, S) {
    const st = themeState('aurora', () => {
      const stars = [];
      for (let i = 0; i < 150; i++) {
        stars.push({
          x: Math.random(),
          y: Math.random() * 0.75,
          r: Math.random() * 1.4 + 0.3,
          p: Math.random() * 6.28,
          far: Math.random() < 0.4,
        });
      }
      const s = {
        stars,
        shoot: 0,
        shootX: 0,
        shootY: 0,
        nextShoot: 4,
        rayJ: [] as number[][],
        rayT: [] as number[][],
      };
      for (let ri = 0; ri < 3; ri++) {
        s.rayJ.push([]);
        s.rayT.push([]);
        for (let k = 0; k < 8; k++) {
          s.rayJ[ri][k] = Math.random();
          s.rayT[ri][k] = Math.random();
        }
      }
      return s;
    });
    const beat = beatAmp(S);
    // night-sky gradient
    const sky = ctx.createLinearGradient(0, 0, 0, h);
    sky.addColorStop(0, '#02030a');
    sky.addColorStop(0.6, '#060818');
    sky.addColorStop(1, '#0a0a18');
    ctx.fillStyle = sky;
    ctx.fillRect(0, 0, w, h);
    for (const s of st.stars) {
      const tw = 0.25 + 0.55 * (0.5 + 0.5 * Math.sin(S.t * (s.far ? 0.7 : 1.4) + s.p));
      ctx.fillStyle = 'rgba(255,255,255,' + (tw * (s.far ? 0.3 : 0.55)).toFixed(3) + ')';
      ctx.beginPath();
      ctx.arc(s.x * w, s.y * h, s.r, 0, 6.2832);
      ctx.fill();
    }
    // occasional shooting star
    st.nextShoot -= 0.016 * frameFactor(S);
    if (st.nextShoot <= 0 && st.shoot <= 0) {
      st.shoot = 1;
      st.shootX = Math.random() * w * 0.7 + w * 0.15;
      st.shootY = Math.random() * h * 0.25;
      st.nextShoot = 5 + Math.random() * 8;
    }
    if (st.shoot > 0) {
      st.shoot -= 0.03 * frameFactor(S);
      const sx = st.shootX + (1 - st.shoot) * w * 0.18;
      const sy = st.shootY + (1 - st.shoot) * h * 0.1;
      const tg = ctx.createLinearGradient(sx, sy, sx - w * 0.12, sy - h * 0.07);
      tg.addColorStop(0, 'rgba(255,255,255,' + (st.shoot * 0.9).toFixed(3) + ')');
      tg.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.strokeStyle = tg;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(sx - w * 0.12, sy - h * 0.07);
      ctx.stroke();
    }
    const ribbons = [
      { y: 0.28, amp: 0.09, speed: 0.35, alpha: 0.3, accent: false },
      { y: 0.42, amp: 0.13, speed: 0.22, alpha: 0.24, accent: true },
      { y: 0.55, amp: 0.08, speed: 0.5, alpha: 0.16, accent: false },
    ];
    ribbons.forEach((R, ri) => {
      const pal = R.accent ? pal2Of(S) : S && S.pal;
      const grad = ctx.createLinearGradient(0, h * (R.y - R.amp * 2), 0, h * (R.y + R.amp * 2));
      const aMid = (R.alpha * (0.5 + S.energy) * (1 + beat * 0.5)).toFixed(3);
      const aTop = (R.alpha * 0.3 * (0.5 + S.energy)).toFixed(3);
      // real auroras burn brightest along the sharp lower border and
      // diffuse upward — skew the gradient to match
      grad.addColorStop(0, palA(pal, 0));
      grad.addColorStop(0.45, palA(pal, aTop));
      grad.addColorStop(0.82, palA(pal, aMid));
      grad.addColorStop(1, palA(pal, 0));
      ctx.fillStyle = grad;
      ctx.beginPath();
      const steps = 70;
      const pts = [];
      for (let i = 0; i <= steps; i++) {
        const x = (i / steps) * w;
        // The melody shapes the curtain: smoothed spectrum across the
        // sky, so a bright synth line visibly lifts the edge where it
        // sits in the mix and a bass drop lets it sink.
        const sv = binS(S, 'aurora' + ri, i, steps);
        const y =
          h * R.y +
          Math.sin(i * 0.11 + S.t * R.speed * 2 + ri * 2.1) * h * R.amp * (0.6 + S.energy * 0.8) +
          Math.sin(i * 0.031 - S.t * R.speed) * h * R.amp * 0.5 -
          (sv - 0.3) * h * 0.2;
        pts.push([x, y]);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.lineTo(w, h * (R.y + R.amp * 3));
      ctx.lineTo(0, h * (R.y + R.amp * 3));
      ctx.closePath();
      ctx.fill();
      // aurora rays: thin strands rising off the curtain, each with its
      // own jitter, tilt and breathing — the detail that makes it
      // read as a real aurora instead of a picket fence
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      const rays = 8;
      for (let k = 0; k < rays; k++) {
        const fx = (k + 0.5) / rays + st.rayJ[ri][k] * 0.04;
        const py = pts[Math.min(steps, Math.max(0, Math.round(fx * steps)))][1];
        const breathe = 0.7 + 0.3 * Math.sin(S.t * 1.3 + k * 2.4 + ri * 1.7);
        // Rays grow where the spectrum is hot at their position.
        const rv = binS(S, 'aurora-ray' + ri, k, rays);
        const rayLen =
          h * (0.08 + R.amp * 2.4) * breathe * (0.75 + st.rayJ[ri][k] * 0.5) * (0.55 + rv * 0.9);
        const tilt = (st.rayT[ri][k] - 0.5) * rayLen * 0.35;
        const rg = ctx.createLinearGradient(0, py, 0, py - rayLen);
        rg.addColorStop(0, palA(pal, (R.alpha * 0.9 * breathe).toFixed(3)));
        rg.addColorStop(1, palA(pal, 0));
        ctx.fillStyle = rg;
        const rw = Math.max(2, w * 0.003 + R.amp * w * (0.008 + st.rayJ[ri][k] * 0.014));
        const rx = fx * w;
        ctx.beginPath();
        ctx.moveTo(rx - rw / 2, py);
        ctx.lineTo(rx - rw / 4 + tilt, py - rayLen);
        ctx.lineTo(rx + rw / 4 + tilt, py - rayLen);
        ctx.lineTo(rx + rw / 2, py);
        ctx.closePath();
        ctx.fill();
      }
      ctx.restore();
    });
    // horizon glow
    const hg = ctx.createLinearGradient(0, h * 0.75, 0, h);
    hg.addColorStop(0, css(S, 0));
    hg.addColorStop(1, css(S, 0.16 + S.energy * 0.1));
    ctx.fillStyle = hg;
    ctx.fillRect(0, h * 0.75, w, h * 0.25);
  },

  // --- Stardust: beat-reactive particle field --------------------------------
  stardust(ctx, w, h, S) {
    const q = qOf(S);
    const st = themeState('stardust', () => {
      const ps = [];
      for (let i = 0; i < 300; i++) {
        const hero = Math.random() < 0.08;
        ps.push({
          x: Math.random(),
          y: Math.random(),
          vx: (Math.random() - 0.5) * 0.0006,
          vy: (Math.random() - 0.5) * 0.0006,
          r: hero ? 2.2 + Math.random() * 1.6 : Math.random() * 1.8 + 0.4,
          p: Math.random() * 6.28,
          depth: 0.3 + Math.random() * 0.7,
          tint: hero ? (Math.random() < 0.5 ? 1 : 2) : 0,
        });
      }
      return { ps, rings: [] as Ring[], comets: [] as Comet[], idleComet: 8 };
    });
    const beat = beatAmp(S);
    ctx.fillStyle = '#030309';
    ctx.fillRect(0, 0, w, h);
    // faint nebula wash so the field has depth, not void
    const wash = (fx: number, fy: number, fr: number, accent: boolean, al: string) => {
      const g = ctx.createRadialGradient(w * fx, h * fy, 0, w * fx, h * fy, Math.max(w, h) * fr);
      g.addColorStop(0, accent ? css2(S, al) : css(S, al));
      g.addColorStop(1, accent ? css2(S, 0) : css(S, 0));
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);
    };
    wash(0.25 + 0.1 * Math.sin(S.t * 0.05), 0.35, 0.55, false, (0.1 + S.energy * 0.08).toFixed(3));
    wash(0.75 + 0.1 * Math.cos(S.t * 0.04), 0.65, 0.6, true, (0.08 + S.energy * 0.07).toFixed(3));
    // beat shockwave
    const f = frameFactor(S);
    if (onset(S) && st.rings.length < 3) {
      st.rings.push({ r: 20, x: 0.3 + Math.random() * 0.4, y: 0.3 + Math.random() * 0.4 });
    }
    for (let i = st.rings.length - 1; i >= 0; i--) {
      const rg = st.rings[i];
      rg.r += 9 * q * f;
      const a = Math.max(0, 0.5 - rg.r / (Math.max(w, h) * 0.6));
      if (a <= 0) {
        st.rings.splice(i, 1);
        continue;
      }
      ctx.strokeStyle = css(S, a.toFixed(3));
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(rg.x * w, rg.y * h, rg.r, 0, 6.2832);
      ctx.stroke();
    }
    for (const p of st.ps) {
      const sp = (1 + S.energy * 3 + beat * 5) * p.depth * f;
      p.x = (p.x + p.vx * sp + 1) % 1;
      p.y = (p.y + p.vy * sp + 1) % 1;
      const tw = 0.3 + 0.7 * (0.5 + 0.5 * Math.sin(S.t * 2 * p.depth + p.p));
      const a = ((0.45 + S.energy * 0.55) * tw + beat * 0.35 * p.depth) * (0.4 + p.depth * 0.6);
      const rr = p.r * (1 + S.energy * 1.2) * p.depth;
      // palette tint for the hero stars — defensive: the test harness
      // builds frames without palettes
      const p1 = (S && S.pal) || { r: 120, g: 80, b: 200 };
      const p2 = (S && S.pal2) || { r: 120, g: 80, b: 200 };
      const col =
        p.tint === 0
          ? '255,255,255'
          : p.tint === 1
            ? p1.r + ',' + p1.g + ',' + p1.b
            : p2.r + ',' + p2.g + ',' + p2.b;
      ctx.fillStyle = 'rgba(' + col + ',' + Math.min(1, a).toFixed(3) + ')';
      ctx.beginPath();
      ctx.arc(p.x * w, p.y * h, rr, 0, 6.2832);
      ctx.fill();
      // the brightest few get a cross sparkle
      if (p.r > 1.9 && tw > 0.85) {
        ctx.strokeStyle = 'rgba(' + col + ',' + (a * 0.5).toFixed(3) + ')';
        ctx.lineWidth = 1;
        const L = rr * 4;
        ctx.beginPath();
        ctx.moveTo(p.x * w - L, p.y * h);
        ctx.lineTo(p.x * w + L, p.y * h);
        ctx.moveTo(p.x * w, p.y * h - L);
        ctx.lineTo(p.x * w, p.y * h + L);
        ctx.stroke();
      }
    }
    // comets: bright heads with fading trails, born on strong beats —
    // plus a slow ambient one so the trail system shows in quiet passages
    st.idleComet -= 0.016 * f;
    if (st.idleComet <= 0 && st.comets.length < 2) {
      const p = st.ps[Math.floor(Math.random() * st.ps.length)];
      const sp = 0.004 + Math.random() * 0.004;
      const ang = Math.random() * 6.2832;
      st.comets.push({ x: p.x, y: p.y, vx: Math.cos(ang) * sp, vy: Math.sin(ang) * sp, life: 1 });
      st.idleComet = 9 + Math.random() * 6;
    }
    if (onset(S) && st.comets.length < 4 && Math.random() < 0.6) {
      const p = st.ps[Math.floor(Math.random() * st.ps.length)];
      const sp = 0.006 + Math.random() * 0.008;
      const ang = Math.random() * 6.2832;
      st.comets.push({ x: p.x, y: p.y, vx: Math.cos(ang) * sp, vy: Math.sin(ang) * sp, life: 1 });
    }
    for (let i = st.comets.length - 1; i >= 0; i--) {
      const c = st.comets[i];
      c.x = (c.x + c.vx * f + 1) % 1;
      c.y = (c.y + c.vy * f + 1) % 1;
      c.life -= 0.014 * f;
      if (c.life <= 0) {
        st.comets.splice(i, 1);
        continue;
      }
      for (let k = 0; k < 7; k++) {
        const tx = (((c.x - c.vx * k * 2) % 1) + 1) % 1;
        const ty = (((c.y - c.vy * k * 2) % 1) + 1) % 1;
        const a = c.life * 0.8 * Math.pow(1 - k / 7, 1.8);
        ctx.fillStyle = k === 0 ? 'rgba(255,255,255,' + a.toFixed(3) + ')' : css(S, a.toFixed(3));
        ctx.beginPath();
        ctx.arc(tx * w, ty * h, (k === 0 ? 2.4 : 1.8) * (1 - k / 8), 0, 6.2832);
        ctx.fill();
      }
    }
    // palette-tinted motes drifting against the flow
    for (let i = 0; i < 26; i++) {
      const p = st.ps[(i * 7) % st.ps.length];
      ctx.fillStyle = i % 3 === 0 ? css2(S, 0.5) : css(S, 0.5);
      ctx.beginPath();
      ctx.arc(p.x * w, p.y * h, p.r * 2.1, 0, 6.2832);
      ctx.fill();
    }
  },

  // --- Vinyl: spinning wax ----------------------------------------------------
  vinyl(ctx, w, h, S) {
    const cx = w / 2,
      cy = h / 2 + Math.sin(S.t * 0.8) * 3;
    const R = Math.min(w, h) * 0.34;
    const st = themeState('vinyl', () => ({ rot: 0 }));
    const beat = beatAmp(S);
    if (!S.idle) st.rot += (0.008 + S.energy * 0.014) * frameFactor(S);
    else st.rot += 0.002 * frameFactor(S);
    // drop shadow
    ctx.fillStyle = 'rgba(0,0,0,0.5)';
    ctx.beginPath();
    ctx.ellipse(cx, cy + R * 1.02, R * 1.02, R * 0.12, 0, 0, 6.2832);
    ctx.fill();
    // record body
    const body = ctx.createRadialGradient(cx, cy, R * 0.1, cx, cy, R);
    body.addColorStop(0, '#0a0a0a');
    body.addColorStop(0.85, '#101010');
    body.addColorStop(1, '#1e1e1e');
    ctx.fillStyle = body;
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, 6.2832);
    ctx.fill();
    // edge highlight
    ctx.strokeStyle = 'rgba(255,255,255,0.14)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(cx, cy, R - 1, 0, 6.2832);
    ctx.stroke();
    // grooves
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,0.055)';
    ctx.lineWidth = 1;
    for (let r = R * 0.36; r < R * 0.96; r += 5) {
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, 6.2832);
      ctx.stroke();
    }
    // energy highlight arc sweeping the grooves — kept whisper-quiet so
    // it reads as light on wax, not a scratch
    const hl = st.rot % 6.2832;
    ctx.strokeStyle = css(S, (0.1 + S.energy * 0.18).toFixed(3));
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(cx, cy, R * 0.66, hl, hl + 1.1);
    ctx.stroke();
    // light sweep that rides the energy (rotates around the record center)
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(st.rot);
    const sweep = ctx.createLinearGradient(-R, -R, R, R);
    sweep.addColorStop(0.42, 'rgba(255,255,255,0)');
    sweep.addColorStop(
      0.5,
      'rgba(255,255,255,' + (0.05 + S.energy * 0.1 + beat * 0.06).toFixed(3) + ')',
    );
    sweep.addColorStop(0.58, 'rgba(255,255,255,0)');
    ctx.fillStyle = sweep;
    ctx.beginPath();
    ctx.arc(0, 0, R, 0, 6.2832);
    ctx.fill();
    ctx.restore();
    ctx.restore();
    // dead-wax: the smooth ring between label and grooves
    ctx.strokeStyle = 'rgba(255,255,255,0.08)';
    ctx.lineWidth = Math.max(2, R * 0.02);
    ctx.beginPath();
    ctx.arc(cx, cy, R * 0.345, 0, 6.2832);
    ctx.stroke();
    // label: the actual album cover, spinning with the record. falls back
    // to a palette disc + the title's initial while the art loads
    const LR = R * 0.32;
    const art = S.art;
    if (art && typeof ctx.drawImage === 'function') {
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(st.rot);
      ctx.beginPath();
      ctx.arc(0, 0, LR, 0, 6.2832);
      ctx.clip();
      ctx.drawImage(art, -LR, -LR, LR * 2, LR * 2);
      // pressed-paper sheen so it sits ON the wax, not over it
      const sheen = ctx.createRadialGradient(-LR * 0.3, -LR * 0.35, 0, 0, 0, LR);
      sheen.addColorStop(0, 'rgba(255,255,255,0.10)');
      sheen.addColorStop(1, 'rgba(0,0,0,0.28)');
      ctx.fillStyle = sheen;
      ctx.fillRect(-LR, -LR, LR * 2, LR * 2);
      ctx.restore();
    } else {
      ctx.fillStyle = css(S, 0.92);
      ctx.beginPath();
      ctx.arc(cx, cy, LR, 0, 6.2832);
      ctx.fill();
      ctx.fillStyle = 'rgba(0,0,0,0.78)';
      ctx.font = '700 ' + Math.round(R * 0.22) + 'px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const initial = (S.title || '♪').trim().charAt(0).toUpperCase() || '♪';
      ctx.fillText(initial, cx, cy + 1);
    }
    ctx.strokeStyle = 'rgba(0,0,0,0.35)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(cx, cy, LR, 0, 6.2832);
    ctx.stroke();
    // spindle
    ctx.fillStyle = '#d8d8d8';
    ctx.beginPath();
    ctx.arc(cx, cy, R * 0.035, 0, 6.2832);
    ctx.fill();
    // tonearm: pivots and tracks inward as the record plays
    const ax = cx + R * 1.18,
      ay = cy - R * 1.02;
    const trackK = 0.42 - (0.5 + 0.5 * Math.sin(S.t * 0.045)) * 0.16 - S.energy * 0.02;
    const tipX = cx + R * trackK,
      tipY = cy - R * 0.3 * (0.9 + trackK * 0.25);
    ctx.strokeStyle = 'rgba(0,0,0,0.4)';
    ctx.lineWidth = Math.max(4, R * 0.045);
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(ax + 3, ay + 4);
    ctx.lineTo(tipX + 3, tipY + 4);
    ctx.stroke();
    ctx.strokeStyle = 'rgba(220,220,220,0.8)';
    ctx.lineWidth = Math.max(3, R * 0.03);
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(tipX, tipY);
    ctx.stroke();
    ctx.fillStyle = 'rgba(220,220,220,0.9)';
    ctx.beginPath();
    ctx.arc(ax, ay, R * 0.07, 0, 6.2832);
    ctx.fill();
    ctx.fillStyle = 'rgba(120,120,120,0.9)';
    ctx.beginPath();
    ctx.arc(ax, ay, R * 0.028, 0, 6.2832);
    ctx.fill();
    // floor reflection: squashed palette sheen under the wax
    const refl = ctx.createLinearGradient(0, cy + R, 0, cy + R * 1.7);
    refl.addColorStop(0, css(S, 0.16 + S.energy * 0.1));
    refl.addColorStop(1, css(S, 0));
    ctx.fillStyle = refl;
    ctx.beginPath();
    ctx.ellipse(cx, cy + R * 1.28, R * 0.95, R * 0.22, 0, 0, 6.2832);
    ctx.fill();
  },

  // --- Tunnel: spectrum wormhole ----------------------------------------------
  tunnel(ctx, w, h, S) {
    const cx = w / 2,
      cy = h / 2;
    const st = themeState('tunnel', () => ({ z: 0, streaks: [] as Streak[] }));
    const beat = beatAmp(S);
    const f = frameFactor(S);
    st.z = (st.z + (0.012 + S.energy * 0.03 + beat * 0.05) * f) % 1;
    // translucent: the trail feedback (NPV2_TRAILS) streaks the rings outward
    ctx.fillStyle = 'rgba(2,2,8,0.62)';
    ctx.fillRect(0, 0, w, h);
    // core glow the rings fly out of
    const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.min(w, h) * 0.16);
    core.addColorStop(0, 'rgba(255,255,255,' + (0.25 + beat * 0.5).toFixed(3) + ')');
    core.addColorStop(0.5, css(S, 0.35 + beat * 0.3));
    core.addColorStop(1, css(S, 0));
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, Math.min(w, h) * 0.16, 0, 6.2832);
    ctx.fill();
    const RINGS = 22,
      SEGS = 42;
    for (let r = 0; r < RINGS; r++) {
      const z = (r / RINGS + st.z) % 1; // 0 = far, 1 = near
      const rad = Math.pow(z, 2.2) * Math.min(w, h) * 0.52 + 8;
      const alpha = Math.pow(z, 1.6) * 0.85;
      for (let sgi = 0; sgi < SEGS; sgi++) {
        const v = binS(S, 'tunnel', sgi, SEGS);
        const a0 = (sgi / SEGS) * 6.2832 + S.t * 0.05 * z;
        const a1 = ((sgi + 0.72) / SEGS) * 6.2832 + S.t * 0.05 * z;
        const rr = rad * (0.92 + v * 0.35);
        const accent = (r + sgi) % 7 === 0;
        ctx.strokeStyle = accent
          ? css2(S, (alpha * (0.25 + v * 0.75)).toFixed(3))
          : css(S, (alpha * (0.25 + v * 0.75)).toFixed(3));
        ctx.lineWidth = 1 + z * 3.2;
        ctx.beginPath();
        ctx.arc(cx, cy, rr, a0, a1);
        ctx.stroke();
      }
    }
    // speed streaks: bright radial lines rushing toward the viewer
    let born = spawnCount(0.06 + S.energy * 0.1, S) + (onset(S) ? 6 : 0);
    while (born-- > 0 && st.streaks.length < 26) {
      st.streaks.push({ a: Math.random() * 6.2832, z: 0.02, sp: 0.05 + Math.random() * 0.06 });
    }
    ctx.lineCap = 'round';
    for (let i = st.streaks.length - 1; i >= 0; i--) {
      const sk = st.streaks[i];
      sk.z += sk.sp * (0.5 + S.energy) * f;
      if (sk.z >= 1) {
        st.streaks.splice(i, 1);
        continue;
      }
      const r0 = Math.pow(sk.z, 2.2) * Math.min(w, h) * 0.52 + 8;
      const r1 = Math.pow(Math.min(1, sk.z + 0.09), 2.2) * Math.min(w, h) * 0.52 + 8;
      const a = Math.pow(sk.z, 1.6);
      ctx.strokeStyle = css(S, (a * 0.7).toFixed(3));
      ctx.lineWidth = 1 + sk.z * 3;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(sk.a) * r0, cy + Math.sin(sk.a) * r0);
      ctx.lineTo(cx + Math.cos(sk.a) * r1, cy + Math.sin(sk.a) * r1);
      ctx.stroke();
    }
  },

  // --- Off ----------------------------------------------------------------------
  none() {
    /* the CSS ambient glow carries the vibe */
  },

  // --- WMP Battery: sparks rise, beat kicks burst them -------------------------
  battery(ctx, w, h, S) {
    const q = qOf(S);
    const st = themeState('battery', () => ({
      parts: [] as Ember[],
      arcs: [] as Arc[],
      seed: Math.random() * 100,
      idleArc: 7,
    }));
    const beat = beatAmp(S);
    const cx = w / 2,
      horizon = h * 0.78;
    // full clear: no ghost bleed from the previous theme
    ctx.fillStyle = '#04060e';
    ctx.fillRect(0, 0, w, h);
    // faint depth haze above the horizon
    const haze = ctx.createLinearGradient(0, horizon - h * 0.25, 0, horizon);
    haze.addColorStop(0, css(S, 0));
    haze.addColorStop(1, css(S, (0.2 + S.energy * 0.18).toFixed(3)));
    ctx.fillStyle = haze;
    ctx.fillRect(0, horizon - h * 0.25, w, h * 0.25);
    // smoke wisps: large soft blobs rising slowly, barely there — depth
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (let wi = 0; wi < 3; wi++) {
      const wsd = st.seed + wi * 47.3;
      const wx = w * (0.3 + 0.4 * (0.5 + 0.5 * Math.sin(S.t * 0.07 + wsd)));
      const wy = horizon - h * (0.08 + 0.05 * Math.sin(S.t * 0.11 + wsd * 2));
      const wr = w * (0.1 + 0.03 * Math.sin(S.t * 0.09 + wsd * 3));
      const wg = ctx.createRadialGradient(wx, wy, 0, wx, wy, wr);
      wg.addColorStop(0, css(S, (0.05 + S.energy * 0.03).toFixed(3)));
      wg.addColorStop(1, css(S, 0));
      ctx.fillStyle = wg;
      ctx.beginPath();
      ctx.arc(wx, wy, wr, 0, 6.2832);
      ctx.fill();
    }
    ctx.restore();
    // rising sparks, two depth layers
    const f = frameFactor(S);
    const spawn =
      spawnCount((4 + Math.floor(S.energy * 10)) * q, S) + (onset(S) ? Math.round(160 * q) : 0);
    for (let i = 0; i < spawn; i++) {
      if (st.parts.length > Math.round(460 * q)) break;
      const far = Math.random() < 0.45;
      const big = Math.random() < 0.06;
      st.parts.push({
        x: cx + (Math.random() - 0.5) * w * 0.62,
        y: horizon + Math.random() * 6,
        vy: -(h * (0.22 + Math.random() * 0.5)) * (0.35 + S.energy),
        vx: (Math.random() - 0.5) * w * 0.05,
        life: 1,
        decay: 0.005 + Math.random() * 0.011,
        sz: far ? 0.7 + Math.random() * 1.1 : (big ? 3.2 : 1) + Math.random() * 2.4,
        far: far,
        ph: Math.random() * 6.28,
      });
    }
    for (let i = st.parts.length - 1; i >= 0; i--) {
      const p = st.parts[i];
      p.x += (p.vx + Math.sin(S.t * 2.6 + p.ph + p.y * 0.008) * 0.7) * f;
      p.y += p.vy * 0.016 * f;
      p.life -= p.decay * (1 + S.energy * 2) * f;
      if (p.life <= 0 || p.y < -12) {
        st.parts.splice(i, 1);
        continue;
      }
      const a = Math.min(1, p.life * 1.5) * (p.far ? 0.45 : 1);
      // ember ramp: white-hot core -> accent -> palette as it cools
      ctx.fillStyle =
        p.life > 0.66
          ? 'rgba(255,255,255,' + (a * 0.95).toFixed(3) + ')'
          : p.life > 0.33
            ? css2(S, (a * 0.9).toFixed(3))
            : css(S, (a * 0.9).toFixed(3));
      const r = p.sz * (0.5 + p.life * 0.9);
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, 0, 6.2832);
      ctx.fill();
      // motion trail on near sparks: streaks read as rising, dots read as noise
      if (!p.far && p.life > 0.25) {
        ctx.strokeStyle = ctx.fillStyle;
        ctx.globalAlpha = 0.35 * Math.min(1, p.life);
        ctx.lineWidth = Math.max(1, r * 0.6);
        ctx.beginPath();
        ctx.moveTo(p.x, p.y + r * 0.5);
        ctx.lineTo(p.x - p.vx * 1.5, p.y - p.vy * 0.055);
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
      // hot core dot on near sparks
      if (!p.far && p.life > 0.5 && r > 1.6) {
        ctx.fillStyle = 'rgba(255,255,255,' + (a * 0.8).toFixed(3) + ')';
        ctx.beginPath();
        ctx.arc(p.x, p.y, r * 0.35, 0, 6.2832);
        ctx.fill();
      }
    }
    // electric arcs: jagged discharge rising from the horizon — on hard
    // beats, plus a slow ambient one so the sky isn't empty in quiet parts
    st.idleArc -= 0.016 * f;
    if (st.idleArc <= 0 && st.arcs.length < 2) {
      st.idleArc = 8 + Math.random() * 6;
      st.arcs.push({ pts: null, life: 1, ambient: true });
    }
    if (onset(S) && st.arcs.length < 4) {
      st.arcs.push({ pts: null, life: 1, ambient: false });
    }
    for (let i = st.arcs.length - 1; i >= 0; i--) {
      const arc = st.arcs[i];
      if (!arc.pts) {
        const segs = 7 + Math.floor(Math.random() * 5);
        const pts = [{ x: cx + (Math.random() - 0.5) * w * 0.5, y: horizon }];
        for (let s2 = 1; s2 <= segs; s2++) {
          const prev = pts[s2 - 1];
          pts.push({
            x: prev.x + (Math.random() - 0.5) * w * 0.035,
            y: horizon - (h * (arc.ambient ? 0.22 : 0.34) * s2) / segs,
          });
        }
        arc.pts = pts;
      }
    }
    ctx.lineWidth = Math.max(1, w * 0.0016);
    for (let i = st.arcs.length - 1; i >= 0; i--) {
      const arc = st.arcs[i];
      arc.life -= 0.09 * f;
      if (arc.life <= 0) {
        st.arcs.splice(i, 1);
        continue;
      }
      const apts = arc.pts;
      if (!apts) continue;
      ctx.strokeStyle = 'rgba(255,255,255,' + (arc.life * 0.85).toFixed(3) + ')';
      ctx.beginPath();
      ctx.moveTo(apts[0].x, apts[0].y);
      for (let s2 = 1; s2 < apts.length; s2++) ctx.lineTo(apts[s2].x, apts[s2].y);
      ctx.stroke();
      ctx.strokeStyle = css2(S, (arc.life * 0.5).toFixed(3));
      ctx.lineWidth = Math.max(2.5, w * 0.004);
      ctx.stroke();
      ctx.lineWidth = Math.max(1, w * 0.0016);
    }
    // horizon: crisp line + tight glow + reflection shimmer
    const flick = 0.35 + S.energy * 0.35 + Math.sin(S.t * 9 + st.seed) * 0.05 + beat * 0.3;
    const g = ctx.createLinearGradient(0, horizon - 26, 0, horizon + 40);
    g.addColorStop(0, css(S, 0));
    g.addColorStop(0.62, css(S, Math.max(0, flick * 0.5).toFixed(3)));
    g.addColorStop(0.72, 'rgba(255,255,255,' + Math.max(0, flick * 0.55).toFixed(3) + ')');
    g.addColorStop(0.82, css(S, Math.max(0, flick * 0.4).toFixed(3)));
    g.addColorStop(1, css(S, 0));
    ctx.fillStyle = g;
    ctx.fillRect(0, horizon - 26, w, 66);
  },

  // --- WMP Dot Plane: grid of dots breathing with the spectrum ------------------
  // Pseudo-3D: the grid recedes toward a horizon, dots swell and throw
  // light-pillars with the music — a field, not a spreadsheet.
  dotplane(ctx, w, h, S) {
    const cols = 28,
      rows = 16;
    const cw = w / cols;
    const beat = beatAmp(S);
    // a real 3D spectrogram: the near row is now, each row behind it is
    // a moment earlier, so the music scrolls away into the distance
    const hst = themeState('dotplane-hist', () => ({ rows: [] as Float32Array[], acc: 0 }));
    const now = new Float32Array(cols);
    for (let gx = 0; gx < cols; gx++) now[gx] = binS(S, 'dotplane', gx, cols);
    hst.acc += S && typeof S.dt === 'number' && S.dt > 0 ? S.dt : 0.016;
    if (!hst.rows.length || hst.acc >= 0.06) {
      hst.acc = 0;
      hst.rows.unshift(now);
      if (hst.rows.length > rows) hst.rows.length = rows;
    } else {
      hst.rows[0] = now;
    }
    ctx.fillStyle = '#040409';
    ctx.fillRect(0, 0, w, h);
    // floor glow under the horizon
    const horizon = h * 0.34;
    const fg = ctx.createLinearGradient(0, horizon, 0, h);
    fg.addColorStop(0, css(S, 0));
    fg.addColorStop(1, css(S, 0.12 + S.energy * 0.08));
    ctx.fillStyle = fg;
    ctx.fillRect(0, horizon, w, h - horizon);
    // crisp horizon line — where the far dots converge
    ctx.fillStyle = css(S, (0.25 + S.energy * 0.2).toFixed(3));
    ctx.fillRect(0, horizon - 0.75, w, 1.5);
    const bcx = w / 2;
    for (let gy = 0; gy < rows; gy++) {
      const p = gy / (rows - 1); // 0 = far, 1 = near
      const y = horizon + (h * 1.02 - horizon) * Math.pow(p, 1.9);
      const sc = 0.22 + 0.78 * Math.pow(p, 1.6); // perspective scale
      const maxR = Math.min(cw, h / rows) * 0.42 * sc;
      for (let gx = 0; gx < cols; gx++) {
        const rowNow = hst.rows[Math.min(hst.rows.length - 1, rows - 1 - gy)];
        const v = rowNow ? rowNow[gx] : 0;
        // far columns converge toward center for the perspective read
        const x = bcx + (gx * cw + cw / 2 - bcx) * (0.45 + 0.55 * p);
        // beat wavefront radiating from the center, in depth space
        const d = Math.hypot((x - bcx) / w, (y - h / 2) / h);
        const wave = 0.5 + 0.5 * Math.sin(S.t * 1.7 + gx * 0.35 + gy * 0.5 - d * 6 + beat * 4);
        const r = Math.max(0.5, (v * 0.75 + wave * 0.25) * maxR * 1.15);
        const a = 0.22 + v * 0.7;
        const hot = v > 0.8;
        // light pillar rising off loud dots — the money detail
        if (v > 0.3) {
          const ph = v * h * 0.16 * sc;
          const pg = ctx.createLinearGradient(0, y, 0, y - ph);
          pg.addColorStop(0, hot ? css2(S, (a * 0.5).toFixed(3)) : css(S, (a * 0.45).toFixed(3)));
          pg.addColorStop(1, css(S, 0));
          ctx.fillStyle = pg;
          ctx.fillRect(x - r * 0.45, y - ph, r * 0.9, ph);
        }
        ctx.fillStyle = hot ? css2(S, Math.min(1, a + 0.2).toFixed(3)) : css(S, a.toFixed(3));
        ctx.beginPath();
        ctx.arc(x, y, Math.min(r, maxR * 1.15), 0, 6.2832);
        ctx.fill();
      }
    }
  },

  // --- WMP Alchemy: slow morphing metaball blobs --------------------------------
  alchemy(ctx, w, h, S) {
    const st = themeState('alchemy', () => ({ seed: Math.random() * 100 }));
    const beat = beatAmp(S);
    const blobs = Math.max(4, Math.round(7 * qOf(S)));
    ctx.fillStyle = 'rgba(3,5,12,0.35)';
    ctx.fillRect(0, 0, w, h);
    const pts: { x: number; y: number; r: number; accent: boolean }[] = [];
    soft(
      ctx,
      w,
      h,
      0.34,
      'alchemy',
      (c, sw) => {
        const k = sw / w;
        c.globalCompositeOperation = 'lighter';
        for (let i = 0; i < blobs; i++) {
          const accent = i % 3 === 2;
          const v = binS(S, 'alchemy', i, blobs);
          const px = st.seed + i * 13.7;
          const x = w * (0.5 + 0.38 * Math.sin(S.t * 0.21 + px) * Math.sin(S.t * 0.13 + px * 2));
          const y = h * (0.5 + 0.36 * Math.cos(S.t * 0.17 + px * 1.3));
          const r = Math.min(w, h) * (0.1 + v * 0.22 + S.energy * 0.06) * (1 + beat * 0.3);
          pts.push({ x, y, r, accent });
          const pal = accent ? pal2Of(S) : S && S.pal;
          const g = c.createRadialGradient(x * k, y * k, 0, x * k, y * k, r * k);
          g.addColorStop(0, palA(pal, 0.5));
          g.addColorStop(0.6, palA(pal, 0.2));
          g.addColorStop(1, palA(pal, 0));
          c.fillStyle = g;
          c.beginPath();
          c.arc(x * k, y * k, r * k, 0, 6.2832);
          c.fill();
        }
        // bridges: soft glows where blobs near each other, so they read as
        // one merging fluid instead of separate circles
        for (let i = 0; i < pts.length; i++) {
          for (let j = i + 1; j < pts.length; j++) {
            const a = pts[i],
              b = pts[j];
            const d = Math.hypot(a.x - b.x, a.y - b.y);
            const touch = (a.r + b.r) * 1.25;
            if (d > touch || d < 1) continue;
            const mx = (a.x + b.x) / 2,
              my = (a.y + b.y) / 2;
            const mr = Math.min(a.r, b.r) * 0.7 * (1 - (d / touch) * 0.5);
            const pal = a.accent || b.accent ? pal2Of(S) : S && S.pal;
            const al = 0.35 * (1 - d / touch);
            const bg = c.createRadialGradient(mx * k, my * k, 0, mx * k, my * k, mr * k);
            bg.addColorStop(0, palA(pal, al.toFixed(3)));
            bg.addColorStop(1, palA(pal, 0));
            c.fillStyle = bg;
            c.beginPath();
            c.arc(mx * k, my * k, mr * k, 0, 6.2832);
            c.fill();
          }
        }
      },
      'lighter',
    );
  },

  // --- Particle fountain: beat-fed spray from the bottom ------------------------
  fountain(ctx, w, h, S) {
    const q = qOf(S);
    const st = themeState('fountain', () => ({ parts: [] as Drop[], sparks: [] as Splash[] }));
    const beat = beatAmp(S);
    const bass = bin(S, 0, 8) * 0.6 + bin(S, 1, 8) * 0.4;
    const cx = w / 2;
    const f = frameFactor(S);
    const spawn =
      spawnCount((2 + Math.floor(bass * 10)) * q, S) + (onset(S) ? Math.round(160 * q) : 0);
    for (let i = 0; i < spawn && st.parts.length < Math.round(700 * q); i++) {
      const ang = -Math.PI / 2 + (Math.random() - 0.5) * 1.15;
      // velocity tuned so the spray crests around mid-screen even in
      // quiet passages: apex = v^2/2g with g = 0.9h/s^2, so v ≈ 0.95h
      const sp = h * (0.7 + Math.random() * 0.8) * (0.7 + bass * 0.9) * (1 + beat * 0.5);
      st.parts.push({
        x: cx + (Math.random() - 0.5) * w * 0.04,
        y: h * 0.98,
        px: 0,
        py: 0,
        vx: Math.cos(ang) * sp,
        vy: Math.sin(ang) * sp,
        life: 1,
        decay: 0.008 + Math.random() * 0.012,
        sz: 1.5 + Math.random() * 2.5,
      });
      const p = st.parts[st.parts.length - 1];
      p.px = p.x;
      p.py = p.y;
    }
    ctx.fillStyle = 'rgba(3,5,12,0.30)';
    ctx.fillRect(0, 0, w, h);
    ctx.lineCap = 'round';
    for (let i = st.parts.length - 1; i >= 0; i--) {
      const p = st.parts[i];
      p.px = p.x;
      p.py = p.y;
      const dt = 0.016 * f;
      p.vy += h * 0.9 * dt; // gravity
      p.vx += Math.sin(S.t * 1.5 + p.y * 0.01) * 0.35 * f; // wind
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.life -= p.decay * f;
      if (p.life <= 0 || p.y > h + 10) {
        // landing splash: two quick horizontal sparks
        if (p.y > h * 0.88 && st.sparks.length < 120) {
          for (let k = 0; k < 2; k++) {
            st.sparks.push({
              x: p.x,
              y: h * 0.985,
              vx: (Math.random() - 0.5) * w * 0.35,
              life: 0.5 + Math.random() * 0.4,
            });
          }
        }
        st.parts.splice(i, 1);
        continue;
      }
      const a = Math.min(1, p.life) * 0.85;
      // white-hot head, palette tail
      ctx.strokeStyle =
        p.life > 0.7 ? 'rgba(255,255,255,' + a.toFixed(3) + ')' : css(S, a.toFixed(3));
      ctx.lineWidth = p.sz;
      ctx.beginPath();
      ctx.moveTo(p.px, p.py);
      ctx.lineTo(p.x, p.y);
      ctx.stroke();
    }
    // splash sparks skitter along the floor
    for (let i = st.sparks.length - 1; i >= 0; i--) {
      const sp = st.sparks[i];
      sp.x += sp.vx * 0.016 * f;
      sp.vx *= Math.pow(0.96, f);
      sp.life -= 0.03 * f;
      if (sp.life <= 0) {
        st.sparks.splice(i, 1);
        continue;
      }
      ctx.fillStyle = css(S, (sp.life * 0.9).toFixed(3));
      ctx.beginPath();
      ctx.arc(sp.x, sp.y, 1.5, 0, 6.2832);
      ctx.fill();
    }
    // splash glow where the spray lands
    const sg = ctx.createLinearGradient(0, h * 0.9, 0, h);
    sg.addColorStop(0, css(S, 0));
    sg.addColorStop(1, css(S, (0.25 + bass * 0.4 + beat * 0.2).toFixed(3)));
    ctx.fillStyle = sg;
    ctx.fillRect(0, h * 0.9, w, h * 0.1);
  },

  // --- Kaleidoscope: mirrored spectrum wedges ------------------------------------
  kaleido(ctx, w, h, S) {
    const cx = w / 2,
      cy = h / 2;
    const beat = beatAmp(S);
    const st = themeState('kaleido', () => ({ dir: 1, cool: 0 }));
    // flip the spin on a beat, at most every 2s. it used to test
    // beat > 0.9, true for several frames per beat, so it flipped an even
    // number of times and mostly never visibly reversed
    st.cool -= S && typeof S.dt === 'number' && S.dt > 0 ? S.dt : 0.016;
    if (onset(S) && st.cool <= 0) {
      st.dir *= -1;
      st.cool = 2;
    }
    const bass = bin(S, 0, 8);
    const R = Math.min(w, h) * 0.46 * (1 + bass * 0.06);
    const rot = S.t * 0.12 * st.dir;
    const pulse = 1 + beat * 0.1;
    // deep backdrop + soft glow bed so the jewels bloom on darkness
    ctx.fillStyle = 'rgba(2,3,9,0.55)';
    ctx.fillRect(0, 0, w, h);
    const kg = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.5);
    kg.addColorStop(0, css(S, (0.12 + bass * 0.1).toFixed(3)));
    kg.addColorStop(1, css(S, 0));
    ctx.fillStyle = kg;
    ctx.fillRect(0, 0, w, h);

    // Jeweled spiral mandala: each wedge holds a spiral arm of glowing
    // dots — radius = frequency (bass near the core, treble near the
    // rim), size/brightness = the smoothed band value — mirrored around
    // the circle for true kaleidoscope symmetry. A second, dimmer
    // counter-rotating layer interleaves for depth.
    const p1 = S.pal,
      p2 = pal2Of(S);
    const segs = 12,
      bands = 30;
    const wedge = (Math.PI * 2) / segs;
    // color ramp palette -> accent across the spectrum, premixed once
    const ramp: string[] = [];
    for (let i = 0; i < bands; i++) {
      const t = i / (bands - 1);
      ramp.push(
        'rgba(' +
          Math.round(p1.r + (p2.r - p1.r) * t) +
          ',' +
          Math.round(p1.g + (p2.g - p1.g) * t) +
          ',' +
          Math.round(p1.b + (p2.b - p1.b) * t) +
          ',',
      );
    }
    ctx.save();
    ctx.translate(cx, cy);
    ctx.globalCompositeOperation = 'lighter';
    for (let layer = 0; layer < 2; layer++) {
      const lrot = layer === 0 ? rot : -rot * 1.6;
      const lr = layer === 0 ? 1 : 0.82; // inner layer sits slightly in
      const ldim = layer === 0 ? 1 : 0.45;
      for (let sgm = 0; sgm < segs; sgm++) {
        ctx.save();
        ctx.rotate(lrot + (sgm / segs) * Math.PI * 2);
        if (sgm % 2 === 1) ctx.scale(1, -1); // mirror
        let px = 0,
          py = 0,
          pv = 0;
        for (let i = 0; i < bands; i++) {
          const v = binS(S, 'kaleido' + layer, i, bands);
          // the music bends the spiral: hot bands swing outward
          const ang =
            (i / bands) * wedge * 0.92 + Math.sin(S.t * 0.9 + i * 0.55 + layer * 2.1) * 0.05 * v;
          const rr = (0.1 + 0.84 * (i / bands)) * R * pulse * lr;
          const x = Math.cos(ang) * rr,
            y = Math.sin(ang) * rr;
          // filament connecting the jewels along the arm
          if (i > 0 && (pv > 0.03 || v > 0.03)) {
            const fa = Math.min(pv, v) * 0.5 * ldim * (1 + beat * 0.4);
            if (fa > 0.02) {
              ctx.strokeStyle = ramp[i] + fa.toFixed(3) + ')';
              ctx.lineWidth = 1 + Math.min(pv, v) * 3;
              ctx.beginPath();
              ctx.moveTo(px, py);
              ctx.lineTo(x, y);
              ctx.stroke();
            }
          }
          if (v > 0.03) {
            let a = (0.15 + v * 0.85) * ldim * (1 + beat * 0.5);
            if (a > 1) a = 1;
            const jr = (0.8 + v * 3.4) * (layer === 0 ? 1 : 0.7);
            // halo + jewel core; hottest go white-hot
            ctx.fillStyle = (v > 0.72 ? 'rgba(255,255,255,' : ramp[i]) + (a * 0.2).toFixed(3) + ')';
            ctx.beginPath();
            ctx.arc(x, y, jr * 2.4, 0, 6.2832);
            ctx.fill();
            ctx.fillStyle = (v > 0.72 ? 'rgba(255,255,255,' : ramp[i]) + a.toFixed(3) + ')';
            ctx.beginPath();
            ctx.arc(x, y, jr, 0, 6.2832);
            ctx.fill();
            // pinpoint sparkle on the brightest jewels
            if (v > 0.6 && layer === 0) {
              const tw = 0.5 + 0.5 * Math.sin(S.t * 4 + i * 1.7 + sgm);
              ctx.fillStyle = 'rgba(255,255,255,' + (tw * v * 0.85).toFixed(3) + ')';
              ctx.beginPath();
              ctx.arc(x, y, jr * 0.45, 0, 6.2832);
              ctx.fill();
            }
          }
          px = x;
          py = y;
          pv = v;
        }
        ctx.restore();
      }
    }
    ctx.restore();
    // rim of beat-pulsing accent jewels framing the mandala
    ctx.save();
    ctx.translate(cx, cy);
    ctx.globalCompositeOperation = 'lighter';
    ctx.rotate(-rot * 0.6);
    const jr2 = 2 + beat * 5;
    for (let sgm = 0; sgm < segs; sgm++) {
      const a = (sgm / segs) * Math.PI * 2;
      const x = Math.cos(a) * R * 1.02 * pulse,
        y = Math.sin(a) * R * 1.02 * pulse;
      ctx.fillStyle = css2(S, (0.25 + beat * 0.65).toFixed(3));
      ctx.beginPath();
      ctx.arc(x, y, jr2 * 2.2, 0, 6.2832);
      ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,' + (0.35 + beat * 0.5).toFixed(3) + ')';
      ctx.beginPath();
      ctx.arc(x, y, jr2 * 0.8, 0, 6.2832);
      ctx.fill();
    }
    ctx.restore();
    // breathing luminous core
    const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 0.2 * pulse);
    core.addColorStop(0, 'rgba(255,255,255,' + (0.55 + bass * 0.35).toFixed(3) + ')');
    core.addColorStop(0.4, css(S, (0.45 + bass * 0.3).toFixed(3)));
    core.addColorStop(1, css(S, 0));
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, R * 0.2 * pulse, 0, 6.2832);
    ctx.fill();
  },

  // --- Warp: starfield rushing past, speed tied to energy -------------------------
  warp(ctx, w, h, S) {
    const st = themeState('warp', () => ({
      stars: Array.from({ length: 320 }, () => ({
        x: Math.random() * 2 - 1,
        y: Math.random() * 2 - 1,
        z: Math.random(),
        tw: Math.random() * 6.28,
      })),
      jumps: [] as Jump[],
    }));
    const cx = w / 2,
      cy = h / 2;
    const beat = beatAmp(S);
    const speed = 0.008 + S.energy * 0.05 + beat * 0.03 + (S.idle ? 0.004 : 0);
    ctx.fillStyle = 'rgba(2,3,9,0.42)';
    ctx.fillRect(0, 0, w, h);
    // palette nebula wash behind the streaks, with a hot core at the
    // vanishing point so the flight direction reads
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.min(w, h) * 0.5);
    g.addColorStop(0, css(S, (0.34 + beat * 0.25).toFixed(3)));
    g.addColorStop(0.25, css(S, (0.16 + beat * 0.1).toFixed(3)));
    g.addColorStop(1, css(S, 0));
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
    // perspective: k keeps stars on-screen for most of their flight —
    // they only leave the frame as they whip past the camera
    const proj = (zx: number, zy: number, z: number): [number, number] => {
      const k = 0.75 / (z * 1.6 + 0.35);
      return [cx + zx * k * cx, cy + zy * k * cy];
    };
    for (const s of st.stars) {
      s.z -= speed * (0.4 + s.z) * frameFactor(S);
      if (s.z <= 0.02) {
        s.x = Math.random() * 2 - 1;
        s.y = Math.random() * 2 - 1;
        s.z = 1;
      }
      const [sx, sy] = proj(s.x, s.y, s.z);
      const [px, py] = proj(s.x, s.y, s.z + speed * 3);
      const bright = 1 - s.z;
      const near = bright > 0.75;
      // far stars twinkle instead of just streaking
      const twk = near ? 1 : 0.6 + 0.4 * Math.sin(S.t * 3 + s.tw);
      ctx.strokeStyle = near
        ? css2(S, (bright * 0.9).toFixed(3))
        : 'rgba(255,255,255,' + (bright * 0.95 * twk).toFixed(3) + ')';
      ctx.lineWidth = Math.max(1, bright * 2.4);
      ctx.beginPath();
      ctx.moveTo(px, py);
      ctx.lineTo(sx, sy);
      ctx.stroke();
    }
    // jump streaks: on hard beats a few stars punch through extra long
    if (onset(S) && st.jumps.length < 7) {
      for (let k = 0; k < 3; k++) {
        st.jumps.push({ a: Math.random() * 6.2832, z: 0.25 + Math.random() * 0.3, life: 1 });
      }
    }
    for (let i = st.jumps.length - 1; i >= 0; i--) {
      const j = st.jumps[i];
      j.z -= 0.05 * frameFactor(S);
      j.life -= 0.05 * frameFactor(S);
      if (j.z <= 0.03 || j.life <= 0) {
        st.jumps.splice(i, 1);
        continue;
      }
      const r0 = (1 / j.z) * Math.min(w, h) * 0.5;
      const r1 = (1 / Math.min(1, j.z + 0.25)) * Math.min(w, h) * 0.5;
      const gg = ctx.createLinearGradient(
        cx + Math.cos(j.a) * r1,
        cy + Math.sin(j.a) * r1,
        cx + Math.cos(j.a) * r0,
        cy + Math.sin(j.a) * r0,
      );
      gg.addColorStop(0, css2(S, (j.life * 0.85).toFixed(3)));
      gg.addColorStop(1, css2(S, 0));
      ctx.strokeStyle = gg;
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(j.a) * r1, cy + Math.sin(j.a) * r1);
      ctx.lineTo(cx + Math.cos(j.a) * r0, cy + Math.sin(j.a) * r0);
      ctx.stroke();
    }
    // jump flash on the beat
    if (beat > 0.15) {
      const fg = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.min(w, h) * 0.3);
      fg.addColorStop(0, 'rgba(255,255,255,' + (beat * 0.35).toFixed(3) + ')');
      fg.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = fg;
      ctx.fillRect(0, 0, w, h);
    }
  },

  // --- Nebula: slow deep-space clouds ----------------------------------------------
  nebula(ctx, w, h, S) {
    const st = themeState('nebula', () => ({ seed: Math.random() * 40, spark: 0, sx: 0, sy: 0 }));
    const beat = beatAmp(S);
    ctx.fillStyle = '#020309';
    ctx.fillRect(0, 0, w, h);
    const layers = [
      { n: 5, sp: 0.05, al: 0.44, sz: 0.42, accent: false },
      { n: 4, sp: 0.07, al: 0.34, sz: 0.34, accent: true },
      { n: 7, sp: 0.09, al: 0.3, sz: 0.3, accent: false },
      { n: 9, sp: 0.14, al: 0.24, sz: 0.2, accent: true },
    ];
    // ~25 screen-sized glows: pure softness, so a third of the size
    soft(ctx, w, h, 0.3, 'nebula', (c, sw, sh) => {
      for (const L of layers) {
        for (let i = 0; i < L.n; i++) {
          const px = st.seed + i * 31.7 + L.sp * 57 + (L.accent ? 91 : 0);
          const x = sw * (0.5 + 0.45 * Math.sin(S.t * L.sp + px));
          const y = sh * (0.5 + 0.45 * Math.cos(S.t * L.sp * 0.8 + px * 1.7));
          const r =
            Math.min(sw, sh) * L.sz * (0.8 + 0.4 * Math.sin(S.t * 0.3 + px)) * (1 + beat * 0.2);
          const v = binS(S, 'nebula', i, L.n);
          const pal = L.accent ? pal2Of(S) : S && S.pal;
          const aMid = (L.al * (0.6 + v) * (1 + beat * 0.8)).toFixed(3);
          const g = c.createRadialGradient(x, y, 0, x, y, r);
          g.addColorStop(0, palA(pal, aMid));
          g.addColorStop(1, palA(pal, 0));
          c.fillStyle = g;
          c.beginPath();
          c.arc(x, y, r, 0, 6.2832);
          c.fill();
        }
      }
    });
    // filament wisps: short curved streams inside the clouds, drifting
    // slowly. Real nebulas are all filaments — this is the texture the
    // eye expects; kept short and bowed so they never read as scratches.
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (let fi = 0; fi < 5; fi++) {
      const wsd = st.seed + fi * 31.7;
      const wob = Math.sin(S.t * 0.1 + wsd) * 0.15;
      const x0 = w * (0.2 + 0.6 * (0.5 + 0.5 * Math.sin(wsd * 1.7)));
      const y0 = h * (0.25 + 0.5 * (0.5 + 0.5 * Math.cos(wsd * 2.3)));
      const len = Math.max(w, h) * (0.35 + 0.15 * Math.sin(wsd));
      const ang = wob + wsd;
      const dx = Math.cos(ang),
        dy = Math.sin(ang);
      const pal = fi % 2 ? pal2Of(S) : S && S.pal;
      const wg = ctx.createLinearGradient(x0, y0, x0 + dx * len, y0 + dy * len);
      const wa = (0.04 + binS(S, 'nebula-w', fi, 5) * 0.08 + beat * 0.04).toFixed(3);
      wg.addColorStop(0, palA(pal, 0));
      wg.addColorStop(0.5, palA(pal, wa));
      wg.addColorStop(1, palA(pal, 0));
      ctx.strokeStyle = wg;
      ctx.lineWidth = 1.5 + binS(S, 'nebula-w', fi, 5) * 4;
      ctx.lineCap = 'round';
      const bow = 90 * Math.sin(S.t * 0.2 + wsd);
      ctx.beginPath();
      ctx.moveTo(x0, y0);
      ctx.quadraticCurveTo(
        x0 + dx * len * 0.5 - dy * bow,
        y0 + dy * len * 0.5 + dx * bow,
        x0 + dx * len,
        y0 + dy * len,
      );
      ctx.stroke();
    }
    ctx.restore();
    // two depths of stars
    const sst = themeState('nebula-stars', () => ({
      pts: Array.from({ length: 150 }, () => ({
        x: Math.random(),
        y: Math.random(),
        p: Math.random() * 6.28,
        far: Math.random() < 0.5,
      })),
    }));
    for (const p of sst.pts) {
      const tw = 0.25 + 0.55 * (0.5 + 0.5 * Math.sin(S.t * (p.far ? 0.8 : 1.4) + p.p));
      ctx.fillStyle = 'rgba(255,255,255,' + (tw * (p.far ? 0.4 : 0.8)).toFixed(3) + ')';
      const sz = p.far ? 1.2 : 1.8;
      ctx.fillRect(p.x * w, p.y * h, sz, sz);
    }
    // occasional bright star with a cross sparkle
    st.spark -= 0.016 * frameFactor(S);
    if (st.spark <= 0) {
      st.spark = 4 + Math.random() * 7;
      st.sx = Math.random();
      st.sy = Math.random() * 0.8;
    }
    if (st.spark > 0 && st.spark < 1.2) {
      const a = Math.min(1, st.spark);
      const x = st.sx * w,
        y = st.sy * h;
      ctx.strokeStyle = 'rgba(255,255,255,' + (a * 0.8).toFixed(3) + ')';
      ctx.lineWidth = 1.5;
      const L = 9 * a + 2;
      ctx.beginPath();
      ctx.moveTo(x - L, y);
      ctx.lineTo(x + L, y);
      ctx.moveTo(x, y - L);
      ctx.lineTo(x, y + L);
      ctx.stroke();
      ctx.fillStyle = 'rgba(255,255,255,' + (a * 0.9).toFixed(3) + ')';
      ctx.beginPath();
      ctx.arc(x, y, 2, 0, 6.2832);
      ctx.fill();
    }
  },

  // --- Bloom: beat rings expanding from center --------------------------------------
  bloom(ctx, w, h, S) {
    const q = qOf(S);
    const st = themeState('bloom', () => ({
      rings: [] as BloomRing[],
      idleT: 0,
      sparks: [] as BloomSpark[],
    }));
    const cx = w / 2,
      cy = h / 2;
    const beat = beatAmp(S);
    const bass = bin(S, 0, 10) * 0.7 + bin(S, 1, 10) * 0.3;
    const f = frameFactor(S);
    if (onset(S) && st.rings.length < Math.round(26 * q)) {
      // staggered echo: three rings per beat, petals offset
      for (let k = 0; k < 3; k++) {
        st.rings.push({
          r: 8 - k * 7,
          w: 3 + bass * 5,
          rot: Math.random() * 6.28,
          petals: 10 + k * 4,
          accent: k === 1,
        });
      }
    }
    // idle breathing: a soft ring every few seconds even with no beats
    st.idleT += 0.016 * f;
    if (st.idleT > 2.5) {
      st.idleT = 0;
      st.rings.push({ r: 8, w: 2, rot: 0, petals: 12, accent: false, gentle: true });
    }
    ctx.fillStyle = 'rgba(3,4,10,0.4)';
    ctx.fillRect(0, 0, w, h);
    const maxR = Math.hypot(w, h) / 2;
    // ambient rosette: layered petals always turning, so the frame is never empty
    const baseR = Math.min(w, h) * 0.16 * (1 + bass * 0.35 + Math.sin(S.t * 1.4) * 0.04);
    for (let layer = 0; layer < 3; layer++) {
      const petals = 8 + layer * 4;
      const lr = baseR * (1 + layer * 0.55);
      const rot = S.t * (0.1 + layer * 0.05) * (layer % 2 ? -1 : 1);
      const la = 0.28 - layer * 0.055 + bass * 0.15;
      const lineCol = layer === 1 ? css2(S, la.toFixed(3)) : css(S, la.toFixed(3));
      const fillCol = layer === 1 ? css2(S, (la * 0.3).toFixed(3)) : css(S, (la * 0.3).toFixed(3));
      const seg = 6.2832 / petals;
      for (let p = 0; p < petals; p++) {
        // petal: luminous gradient body (bright heart, soft fade) plus outline, long axis radial
        const a0 = rot + p * seg;
        const px = cx + Math.cos(a0) * lr,
          py = cy + Math.sin(a0) * lr;
        const pr = lr * 0.3;
        const pg = ctx.createRadialGradient(px, py, 0, px, py, pr * 1.15);
        pg.addColorStop(0, lineCol);
        pg.addColorStop(0.5, fillCol);
        pg.addColorStop(1, css(S, 0));
        ctx.fillStyle = pg;
        ctx.beginPath();
        ctx.ellipse(px, py, pr, lr * 0.17, a0, 0, 6.2832);
        ctx.fill();
        ctx.strokeStyle = lineCol;
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.ellipse(px, py, pr, lr * 0.17, a0, 0, 6.2832);
        ctx.stroke();
      }
    }
    for (let i = st.rings.length - 1; i >= 0; i--) {
      const rg = st.rings[i];
      rg.r += (4 + bass * 14) * 0.9 * (rg.gentle ? 0.6 : 1) * f;
      rg.rot += 0.004 * (i % 2 ? 1 : -1) * f;
      const a = Math.max(0, 1 - rg.r / maxR);
      if (a <= 0) {
        st.rings.splice(i, 1);
        continue;
      }
      const alpha = (a * (rg.gentle ? 0.45 : 0.9)).toFixed(3);
      ctx.strokeStyle = rg.accent ? css2(S, alpha) : css(S, alpha);
      ctx.lineWidth = rg.w * a + 0.8;
      // petal ring: arc segments with gaps, slowly counter-rotating
      const seg = 6.2832 / rg.petals;
      for (let p = 0; p < rg.petals; p++) {
        const a0 = rg.rot + p * seg;
        ctx.beginPath();
        ctx.arc(cx, cy, rg.r, a0, a0 + seg * 0.62);
        ctx.stroke();
        // shed sparkles off the petal tips while the ring is young
        if (rg.r < maxR * 0.55 && Math.random() < 0.06 * f && st.sparks.length < 140) {
          const ta = a0 + seg * 0.31;
          const sp = 30 + Math.random() * 60;
          st.sparks.push({
            x: cx + Math.cos(ta) * rg.r,
            y: cy + Math.sin(ta) * rg.r,
            vx: Math.cos(ta) * sp,
            vy: Math.sin(ta) * sp,
            life: 1,
            accent: rg.accent,
          });
        }
      }
    }
    // sparkles drift outward and die
    for (let i = st.sparks.length - 1; i >= 0; i--) {
      const sp = st.sparks[i];
      sp.x += sp.vx * 0.016 * f;
      sp.y += sp.vy * 0.016 * f;
      sp.vx *= Math.pow(0.985, f);
      sp.vy *= Math.pow(0.985, f);
      sp.life -= 0.014 * f;
      if (sp.life <= 0) {
        st.sparks.splice(i, 1);
        continue;
      }
      ctx.fillStyle = sp.accent
        ? css2(S, (sp.life * 0.9).toFixed(3))
        : css(S, (sp.life * 0.9).toFixed(3));
      ctx.beginPath();
      ctx.arc(sp.x, sp.y, 2.0, 0, 6.2832);
      ctx.fill();
    }
    // breathing core
    const core = 26 + bass * 60 + Math.sin(S.t * 2.2) * 8 + beat * 22;
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, core * 2.4);
    g.addColorStop(0, 'rgba(255,255,255,' + (0.35 + bass * 0.4).toFixed(3) + ')');
    g.addColorStop(0.4, css(S, 0.5));
    g.addColorStop(1, css(S, 0));
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(cx, cy, core * 2.4, 0, 6.2832);
    ctx.fill();
  },
};
