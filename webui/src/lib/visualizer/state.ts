// per-visualizer scratch: each theme's particles and the low-res soft
// layers. painters read the active store, so two canvases never share
// particles. the Visualizer swaps its own store in around each paint.

import type { VizFrame } from './frame';

import { ease, bin } from './frame';

export interface SoftBuffer {
  c: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
}

export interface VizStore {
  themes: Map<string, unknown>;
  // undefined = not tried yet, null = no offscreen canvas here
  soft: Map<string, SoftBuffer | null>;
}

export function createStore(): VizStore {
  return { themes: new Map(), soft: new Map() };
}

const defaultStore = createStore();
let active: VizStore = defaultStore;

export function activeStore(): VizStore {
  return active;
}

// run fn with `store` as the active store, then put the old one back
export function withStore<T>(store: VizStore, fn: () => T): T {
  const prev = active;
  active = store;
  try {
    return fn();
  } finally {
    active = prev;
  }
}

// drop everything a store holds (particles, offscreen canvases)
export function clearStore(store: VizStore): void {
  for (const buf of store.soft.values()) {
    if (buf) {
      buf.c.width = 0;
      buf.c.height = 0;
    }
  }
  store.soft.clear();
  store.themes.clear();
}

// per-theme mutable scratch, made lazily
export function themeState<T>(id: string, init: () => T): T {
  let st = active.themes.get(id);
  if (st === undefined) {
    st = init();
    active.themes.set(id, st);
  }
  return st as T;
}

// smoothed bins, fast attack and slow release, so motion reads as musical
// instead of frame jitter
export function binS(S: VizFrame, key: string, i: number, count: number): number {
  const target = bin(S, i, count);
  const st = themeState('bins-' + key, () => ({ v: [] as number[] }));
  while (st.v.length < count) st.v.push(0);
  const cur = st.v[i] || 0;
  const k = ease(target > cur ? 0.55 : 0.1, S);
  const nv = cur + (target - cur) * k;
  st.v[i] = nv;
  return nv;
}

export type SoftDraw = (c: CanvasRenderingContext2D, w: number, h: number) => void;

// soft layers at low resolution. clouds and glows are blurry by design, so
// drawing them small and scaling up looks the same and costs far less fill.
// falls back to drawing straight onto ctx when there is no offscreen canvas.
export function soft(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  scale: number,
  key: string,
  draw: SoftDraw,
  mode?: GlobalCompositeOperation,
): void {
  let buf = active.soft.get(key);
  if (buf === undefined) {
    buf = null;
    try {
      if (typeof document !== 'undefined') {
        const c = document.createElement('canvas');
        const cx = typeof c.getContext === 'function' ? c.getContext('2d') : null;
        if (cx) buf = { c, ctx: cx };
      }
    } catch {
      buf = null;
    }
    active.soft.set(key, buf);
  }
  if (!buf || typeof ctx.drawImage !== 'function') {
    draw(ctx, w, h);
    return;
  }
  const bw = Math.max(2, Math.round(w * scale));
  const bh = Math.max(2, Math.round(h * scale));
  if (buf.c.width !== bw || buf.c.height !== bh) {
    buf.c.width = bw;
    buf.c.height = bh;
  }
  buf.ctx.setTransform(1, 0, 0, 1, 0, 0);
  buf.ctx.globalCompositeOperation = 'source-over';
  buf.ctx.globalAlpha = 1;
  buf.ctx.clearRect(0, 0, bw, bh);
  draw(buf.ctx, bw, bh);
  ctx.save();
  if (mode) ctx.globalCompositeOperation = mode;
  // bilinear is plenty for content that is blurry by design
  ctx.imageSmoothingEnabled = true;
  try {
    ctx.imageSmoothingQuality = 'low';
  } catch {
    // old browsers
  }
  ctx.drawImage(buf.c, 0, 0, w, h);
  ctx.restore();
}
