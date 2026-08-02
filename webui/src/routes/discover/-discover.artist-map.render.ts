/**
 * Artist Map — the render pipeline.
 *
 * Two layers. A static offscreen BUFFER holds the far field (every bubble too
 * small to read), rendered once and blitted with a pan/zoom transform; a LIVE
 * overlay redraws only the bubbles big enough to see, every frame, so they can
 * scale, bob and ripple. This bounds per-frame work to what you can actually
 * see rather than the full 2000-node world.
 *
 * Transcribed from `webui/static/discover.js` 5875-5916 (sprites),
 * 8447-9237 (buffer, painters, loop, draw, perf) and 9861-9976 (images).
 *
 * Canvas code cannot be compared by return value, so the differential suite runs
 * both this and the vanilla against a RECORDING 2D context and compares the two
 * operation logs — every call and every property assignment, in order.
 */

import {
  type ArtMapIsland,
  type ArtMapNode,
  ARTMAP_LIVE_OVERFLOW_LIMIT,
  artMap,
  artMapIsLiveSize,
  artMapNodeDisplacement,
  artMapNodeImgPx,
  artMapReservedW,
  artMapStepAnimations,
} from './-discover.artist-map';

type Ctx = CanvasRenderingContext2D;

// ── Cached sprites ───────────────────────────────────────────────────────────

/**
 * A cached circular "gloss" sprite (5875-5896) — a soft top-left specular
 * highlight that makes each bubble read as a glassy orb.
 *
 * One radial gradient, rendered once; per-bubble it is a cheap drawImage rather
 * than a per-frame gradient.
 */
export function artMapGlossSprite(): HTMLCanvasElement {
  if (artMap._gloss) return artMap._gloss;
  const S = 128;
  const c = document.createElement('canvas');
  c.width = S;
  c.height = S;
  const cx = c.getContext('2d') as Ctx;
  cx.beginPath();
  cx.arc(S / 2, S / 2, S / 2, 0, Math.PI * 2);
  cx.clip();
  const g = cx.createRadialGradient(S * 0.34, S * 0.28, S * 0.02, S * 0.5, S * 0.5, S * 0.62);
  g.addColorStop(0, 'rgba(255,255,255,0.40)');
  g.addColorStop(0.32, 'rgba(255,255,255,0.10)');
  g.addColorStop(0.6, 'rgba(255,255,255,0.0)');
  cx.fillStyle = g;
  cx.fillRect(0, 0, S, S);
  // A faint inner-bottom shade for roundness
  const g2 = cx.createRadialGradient(S * 0.5, S * 0.78, S * 0.05, S * 0.5, S * 0.5, S * 0.7);
  g2.addColorStop(0, 'rgba(0,0,0,0.18)');
  g2.addColorStop(0.5, 'rgba(0,0,0,0.0)');
  cx.fillStyle = g2;
  cx.fillRect(0, 0, S, S);
  artMap._gloss = c;
  return c;
}

/**
 * A cached soft radial halo per genre hue (5901-5916), drawn behind the focused
 * island so it reads as a glowing place on the water.
 *
 * Cached per hue (there are only ever a handful), so it stays one drawImage per
 * frame rather than a per-frame gradient.
 */
export function artMapHaloSprite(hue: number): HTMLCanvasElement {
  artMap._halos = artMap._halos || {};
  if (artMap._halos[hue]) return artMap._halos[hue];
  const S = 256;
  const c = document.createElement('canvas');
  c.width = S;
  c.height = S;
  const cx = c.getContext('2d') as Ctx;
  const g = cx.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2);
  g.addColorStop(0, `hsla(${hue},75%,55%,0.22)`);
  g.addColorStop(0.45, `hsla(${hue},75%,50%,0.08)`);
  g.addColorStop(1, `hsla(${hue},75%,50%,0)`);
  cx.fillStyle = g;
  cx.fillRect(0, 0, S, S);
  artMap._halos[hue] = c;
  return c;
}

// ── The static buffer ────────────────────────────────────────────────────────

/**
 * Render ALL (non-live) nodes once into the offscreen canvas (8447-8539).
 *
 * Only called on data changes, never on pan/zoom — panning is a blit of what
 * this produced.
 *
 * The buffer's resolution tracks zoom (higher zoom, higher res) but is capped
 * two ways: at 1:1, and at MAX_BUFFER_PX across the world's longest side. The
 * cap only binds on big worlds; small maps stay crisp.
 *
 * `_liveOverflow` is decided BEFORE the draw loop on purpose. If more bubbles
 * would be live than the overlay can draw, the overlay caps out and the buffer
 * would have excluded them — a sparse, half-rendered map. Setting the flag first
 * makes `artMapIsLiveSize` return false throughout, so the buffer bakes the
 * whole crowd instead.
 */
export function artMapRebuildBuffer(): void {
  const placed = artMap.placed;
  if (!placed.length) return;

  const visible = placed.filter((n) => (n.opacity || 0) > 0.01);
  if (!visible.length) return;

  // World bounds
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  visible.forEach((n) => {
    minX = Math.min(minX, n.x - n.radius - 10);
    maxX = Math.max(maxX, n.x + n.radius + 10);
    minY = Math.min(minY, n.y - n.radius - 10);
    maxY = Math.max(maxY, n.y + n.radius + 10);
  });

  const bw = maxX - minX;
  const bh = maxY - minY;
  const z = artMap.zoom || 0.1;
  const scale = Math.min(z * 2, 1.0, artMap.MAX_BUFFER_PX / Math.max(bw, bh));

  if (!artMap.offscreen) artMap.offscreen = document.createElement('canvas');
  const oc = artMap.offscreen;
  oc.width = Math.ceil(bw * scale);
  oc.height = Math.ceil(bh * scale);
  const octx = oc.getContext('2d') as Ctx;
  artMap._bufferScale = scale;
  artMap._bufferMinX = minX;
  artMap._bufferMinY = minY;
  // Freeze the live/buffer partition to this build's zoom (see artMapIsLiveSize).
  artMap._liveBuildZoom = artMap.zoom;
  artMap._drawAlphaMul = 1; // the buffer bakes at full alpha; the blit applies the reveal fade

  const bz = artMap.zoom;
  let liveN = 0;
  for (const n of visible) {
    if (!n._isLabel && (n.radius || 0) * bz >= artMap.LIVE_PX) liveN++;
  }
  artMap._liveOverflow = liveN > ARTMAP_LIVE_OVERFLOW_LIMIT;

  octx.scale(scale, scale);
  octx.translate(-minX, -minY);

  // Build node lookup
  if (!artMap._nodeById) {
    artMap._nodeById = {};
    placed.forEach((n) => {
      (artMap._nodeById as Record<string, ArtMapNode>)[n.id as string] = n;
    });
  }

  // Draw edges (connection lines between related nodes)
  if (artMap.edges && artMap.edges.length > 0) {
    octx.lineWidth = 1;
    octx.strokeStyle = 'rgba(138,43,226,0.08)';
    octx.beginPath();
    for (const edge of artMap.edges) {
      const s = (artMap._nodeById as Record<string, ArtMapNode>)[edge.source as string];
      const t = (artMap._nodeById as Record<string, ArtMapNode>)[edge.target as string];
      if (!s || !t || (s.opacity || 0) < 0.05 || (t.opacity || 0) < 0.05) continue;
      octx.moveTo(s.x, s.y);
      octx.lineTo(t.x, t.y);
    }
    octx.stroke();
  }

  // Draw ALL nodes — genre labels first, similar next, watchlist on top.
  const hideSimilar = artMap._hideSimilar || false;
  for (let pass = 0; pass < 3; pass++) {
    for (const n of visible) {
      if (pass === 0 && n._isLabel) {
        /* draw */
      } else if (
        pass === 1 &&
        !n._isLabel &&
        n.type !== 'watchlist' &&
        n.type !== 'center' &&
        n.ring !== 1
      ) {
        /* draw */
      } else if (
        pass === 2 &&
        !n._isLabel &&
        (n.type === 'watchlist' || n.type === 'center' || n.ring === 1)
      ) {
        /* draw */
      } else continue;
      if (hideSimilar && n.type !== 'watchlist' && n.type !== 'center' && !n._isLabel) continue;
      if (artMapIsLiveSize(n)) continue; // big enough to read → drawn live on the overlay
      artMapDrawNodeToBuffer(octx, n, scale);
    }
  }

  octx.globalAlpha = 1;

  artMap.dirty = false;
}

/**
 * Draw a SINGLE node in world coords (8544-8647) — the caller has already
 * applied any scale/translate.
 *
 * Shared by the full rebuild, the incremental image compositor and the live
 * overlay, so a bubble cannot look different depending on which path drew it.
 *
 * Detail is chosen by ON-SCREEN size, not world size: under 2.2px it is a
 * coloured dot, the gloss and the label only appear past 12/13px, and album art
 * shows at nearly every size because the bitmaps are pre-masked to circles and
 * cost one drawImage.
 */
export function artMapDrawNodeToBuffer(octx: Ctx, n: ArtMapNode, scale: number): void {
  const op = n.opacity || 0;
  if (op < 0.01) return;
  const r = n.radius;
  const isW = n.type === 'watchlist' || n.type === 'center';
  // Global fade multiplier (reveal). Lets the whole map fade in cleanly while
  // each painter keeps its own per-element alpha.
  const mul = artMap._drawAlphaMul == null ? 1 : artMap._drawAlphaMul;
  octx.globalAlpha = op * mul;

  // Genre title — a clean floating label above its island (no big bubble).
  if (n._isLabel) {
    const hue = n._hue == null ? 270 : n._hue;
    const titleSize = Math.max(13, n.radius * 0.42);
    const name = (n.name || '').toUpperCase();
    octx.textAlign = 'center';
    octx.textBaseline = 'middle';
    // Soft glow behind the title for legibility over the water.
    octx.globalAlpha = mul;
    octx.font = `800 ${titleSize}px system-ui, sans-serif`;
    octx.shadowColor = `hsla(${hue},70%,12%,0.9)`;
    octx.shadowBlur = titleSize * 0.6;
    octx.fillStyle = `hsla(${hue},85%,82%,0.96)`;
    octx.fillText(name, n.x, n.y);
    octx.shadowBlur = 0;
    // Count subtitle
    octx.globalAlpha = 0.55 * mul;
    octx.font = `600 ${titleSize * 0.42}px system-ui, sans-serif`;
    octx.fillStyle = 'rgba(255,255,255,0.7)';
    octx.fillText(`${n._count || 0} artists`, n.x, n.y + titleSize * 0.85);
    octx.globalAlpha = 1;
    return;
  }

  const rScaled = r * scale;
  const img = artMap.images[n.id as string];

  if (rScaled < 2.2) {
    octx.beginPath();
    octx.arc(n.x, n.y, r, 0, Math.PI * 2);
    octx.fillStyle = isW ? '#6b21a8' : '#2a2a40';
    octx.fill();
    return;
  }

  // Focal glow ring for watchlist/center bubbles
  if (isW && rScaled >= 7) {
    octx.beginPath();
    octx.arc(n.x, n.y, r + 4, 0, Math.PI * 2);
    octx.strokeStyle = 'rgba(138,43,226,0.25)';
    octx.lineWidth = 5;
    octx.stroke();
  }

  // Body — pre-masked circular image (no clip) or a placeholder disc.
  if (img) {
    octx.drawImage(img, n.x - r, n.y - r, r * 2, r * 2);
  } else {
    octx.beginPath();
    octx.arc(n.x, n.y, r, 0, Math.PI * 2);
    octx.fillStyle = isW ? '#1a0a30' : '#141420';
    octx.fill();
  }

  // Glassy specular highlight — only on bubbles big enough to read it; skipping
  // the dense swarm halves per-frame drawImage cost when zoomed in.
  if (rScaled >= 12) {
    octx.drawImage(artMapGlossSprite(), n.x - r, n.y - r, r * 2, r * 2);
  }

  const showLabel = rScaled >= 13;

  // Darken art behind the label so the name stays legible.
  if (showLabel && img) {
    octx.beginPath();
    octx.arc(n.x, n.y, r, 0, Math.PI * 2);
    octx.fillStyle = 'rgba(0,0,0,0.42)';
    octx.fill();
  }

  // Border — tinted with the island's genre hue so clusters read as a family.
  octx.beginPath();
  octx.arc(n.x, n.y, r, 0, Math.PI * 2);
  if (isW) octx.strokeStyle = 'rgba(138,43,226,0.5)';
  else if (n._hue != null) octx.strokeStyle = `hsla(${n._hue},70%,70%,0.22)`;
  else octx.strokeStyle = 'rgba(255,255,255,0.10)';
  octx.lineWidth = isW ? 2 : rScaled >= 7 ? 1 : 0.5;
  octx.stroke();

  if (showLabel) {
    const fontSize = isW ? Math.max(16, r * 0.14) : Math.max(8, r * 0.3);
    octx.font = `${isW ? '700' : '600'} ${fontSize}px system-ui`;
    octx.textAlign = 'center';
    octx.textBaseline = 'middle';
    octx.fillStyle = '#fff';
    const maxC = isW ? 20 : 12;
    const label = n.name.length > maxC ? n.name.substring(0, maxC - 1) + '…' : n.name;
    octx.fillText(label, n.x, n.y);
  }
}

/**
 * Composite ONE node into the EXISTING buffer without a full rebuild (8655-8673).
 *
 * This is what makes image streaming cheap: when a bitmap arrives, redraw only
 * that node over its placeholder instead of all ~1500. Returns false when there
 * is no buffer yet so the caller can fall back to a full rebuild.
 *
 * A live-layer bubble returns TRUE without drawing — it reads its image fresh
 * each frame, and compositing it here would double-draw.
 *
 * NOTE: this is currently unreachable. `artMapStreamImages` schedules a
 * throttled full rebuild instead, because the per-node composite could miss
 * images that arrived while the buffer was being rebuilt at a new scale. Kept
 * because the vanilla keeps it and the two painters must not drift.
 */
export function artMapCompositeNode(n: ArtMapNode): boolean {
  const oc = artMap.offscreen;
  const scale = artMap._bufferScale;
  if (!oc || scale == null) return false;
  if (artMap._hideSimilar && n.type !== 'watchlist' && n.type !== 'center' && !n._isLabel) {
    return false;
  }
  if ((n.opacity || 0) < 0.01) return false;
  if (artMapIsLiveSize(n)) return true;
  const octx = oc.getContext('2d') as Ctx;
  octx.save();
  octx.scale(scale, scale);
  octx.translate(-(artMap._bufferMinX as number), -(artMap._bufferMinY as number));
  artMapDrawNodeToBuffer(octx, n, scale);
  octx.restore();
  octx.globalAlpha = 1;
  return true;
}

// ── The live overlay ─────────────────────────────────────────────────────────

/** How many live bubbles one frame will draw — far more while blooming (8717). */
export const ARTMAP_LIVE_CAP = 600;
export const ARTMAP_REVEAL_CAP = 2200;

/**
 * Draw every big/near bubble in world space, honouring its animation transform
 * (8699-8734).
 *
 * During the reveal the buffer is bypassed, so this draws EVERY bubble (and the
 * genre titles) so they can all bloom; otherwise only the big ones, with the
 * rest already on screen via the buffer blit. Kept cheap by a viewport cull and
 * a hard cap.
 *
 * `_liveCount` is left at 0 while revealing, which is what parks the ambient
 * loop the moment the bloom ends if nothing is big enough to bob.
 */
export function artMapDrawLiveLayer(ctx: Ctx): void {
  const placed = artMap.placed;
  if (!placed || !placed.length) return;
  const z = artMap.zoom;
  const ox = artMap.offsetX;
  const oy = artMap.offsetY;
  const w = artMap.width;
  const h = artMap.height;
  const margin = 80;

  ctx.save();
  ctx.translate(ox, oy);
  ctx.scale(z, z);
  artMap._drawAlphaMul = 1;

  const revealing = artMap._revealing;
  let drawn = 0;
  const CAP = revealing ? ARTMAP_REVEAL_CAP : ARTMAP_LIVE_CAP;
  for (const n of placed) {
    if (!revealing && !artMapIsLiveSize(n)) continue;
    if (artMap._hideSimilar && n.type !== 'watchlist' && n.type !== 'center' && !n._isLabel) {
      continue;
    }
    // Viewport cull (screen space)
    const sx = ox + n.x * z;
    const sy = oy + n.y * z;
    const rPx = (n.radius || 0) * z;
    if (
      sx + rPx < -margin ||
      sx - rPx > w + margin ||
      sy + rPx < -margin ||
      sy - rPx > h + margin
    ) {
      continue;
    }
    artMapDrawLiveNode(ctx, n);
    if (++drawn >= CAP) break;
  }
  artMap._drawAlphaMul = 1;
  ctx.restore();
  ctx.globalAlpha = 1;
  artMap._liveCount = revealing ? 0 : drawn;
}

/**
 * Tactile hover-pop (8740-8760): redraw the hovered bubble 16% larger with its
 * cover and a bright hue ring, on top of everything.
 *
 * Works even when the bubble lives in the static buffer, so hover always feels
 * responsive. `ctx` is already in world space.
 */
export function artMapDrawHoverPop(ctx: Ctx, n: ArtMapNode): void {
  const r = n.radius;
  const hue = n._hue == null ? 270 : n._hue;
  const s = 1.16;
  const img = artMap.images[n.id as string];
  ctx.save();
  ctx.translate(n.x, n.y);
  ctx.scale(s, s);
  ctx.translate(-n.x, -n.y);
  if (img) {
    ctx.drawImage(img, n.x - r, n.y - r, r * 2, r * 2);
  } else {
    ctx.beginPath();
    ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
    ctx.fillStyle = '#1a0a30';
    ctx.fill();
  }
  ctx.beginPath();
  ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
  ctx.strokeStyle = `hsla(${hue},90%,78%,0.95)`;
  ctx.lineWidth = 2.5 / s;
  ctx.stroke();
  ctx.restore();
  ctx.beginPath();
  ctx.arc(n.x, n.y, r * s + 5, 0, Math.PI * 2);
  ctx.strokeStyle = `hsla(${hue},85%,66%,0.45)`;
  ctx.lineWidth = 3;
  ctx.stroke();
}

/**
 * Draw one live bubble with its animation transform (8766-8793).
 *
 * `aScale` scales about the node centre, `aAlpha` folds into the global draw
 * multiplier. Steady state adds ambient buoyancy plus any ripple shove; the
 * reveal has its own motion (the surfacing rise) and skips both.
 *
 * Reuses the shared node painter, so a bubble is identical to its baked form
 * once it settles.
 */
export function artMapDrawLiveNode(ctx: Ctx, n: ArtMapNode): void {
  const sc = n.aScale == null ? 1 : n.aScale;
  if (sc <= 0.001) return;
  const baseMul = artMap._drawAlphaMul == null ? 1 : artMap._drawAlphaMul;
  artMap._drawAlphaMul = baseMul * (n.aAlpha == null ? 1 : n.aAlpha);
  let ox = 0;
  let oy = 0;
  if (artMap._revealing) {
    if (n._revealRise) oy += n._revealRise; // surfacing rise during the bloom
  } else {
    if (n._bobAmp) oy += Math.sin((artMap._now || 0) * 0.0016 + (n._bobPhase || 0)) * n._bobAmp;
    const disp = artMapNodeDisplacement(n);
    if (disp) {
      ox += disp.dx;
      oy += disp.dy;
    }
  }
  if (sc !== 1 || ox || oy) {
    ctx.save();
    ctx.translate(n.x + ox, n.y + oy);
    ctx.scale(sc, sc);
    ctx.translate(-n.x, -n.y);
    artMapDrawNodeToBuffer(ctx, n, artMap.zoom);
    ctx.restore();
  } else {
    artMapDrawNodeToBuffer(ctx, n, artMap.zoom);
  }
  artMap._drawAlphaMul = baseMul;
  ctx.globalAlpha = 1;
}

// ── The animation loop ───────────────────────────────────────────────────────

/**
 * The rAF loop (8799-8826). Runs only while something is animating and idles
 * otherwise, so a still map costs nothing.
 *
 * Drawing is capped at ~30fps: the bloom, ripples and ambient bob all read fine
 * at 30, and halving the redraws keeps a 1800-bubble map smooth. A pending
 * buffer rebuild always draws regardless, so the throttle can never skip the
 * frame that bakes the map after the reveal ends.
 */
export function artMapStartLoop(): void {
  const a = artMap._anim;
  if (a.running) return;
  a.running = true;
  a.last = performance.now();
  const tick = (t: number) => {
    if (!a.running) return;
    artMap._now = t;
    const more = artMapStepAnimations(t);
    if (artMap.dirty || t - (a._lastDraw || 0) >= 31) {
      artMapDraw(); // sets artMap._liveCount
      a._lastDraw = t;
    }
    const keep = more || (artMap._ambient && (artMap._liveCount || 0) > 0 && !document.hidden);
    if (keep) {
      a.raf = requestAnimationFrame(tick);
    } else {
      a.running = false;
      a.raf = null;
    }
  };
  a.raf = requestAnimationFrame(tick);
}

/**
 * (Re)start the ambient loop if buoyancy is on and it is not already running
 * (8830-8832) — called after the reveal and on zoom/pan so bob resumes when
 * bubbles come into view.
 */
export function artMapEnsureAmbient(): void {
  if (artMap._ambient && !artMap._anim.running && !document.hidden) artMapStartLoop();
}

/** Reveal timings (8895) — island stagger, radial spread, per-node duration. */
export const ARTMAP_REVEAL = { ISL_STAGGER: 145, RADIAL_MS: 430, NODE_DUR: 470 };

/**
 * Kick off the ripple-bloom reveal (8884-8912).
 *
 * Each island blooms in turn; within an island, bubbles surface outward from the
 * centre like a drop hitting water, with a ripple ring expanding behind them.
 * The whole map renders on the live layer while this runs so every bubble can
 * animate, and bakes into the buffer at the end.
 *
 * A genre LABEL is matched to its island by NAME (it carries no `_island`), and
 * is delayed a further 90ms so the title lands after its bubbles.
 */
export function artMapBeginReveal(): void {
  const t0 = performance.now();
  artMap._revealT0 = t0;
  artMap._revealing = true;
  artMap._ambient = true; // keep the loop alive afterwards for buoyancy
  artMap._fieldAlpha = 1; // the buffer is bypassed while revealing

  const islands = artMap._islands || [];
  const islByName: Record<string, ArtMapIsland> = {};
  islands.forEach((isl, i) => {
    isl._order = i;
    islByName[isl.name] = isl;
  });

  const { ISL_STAGGER, RADIAL_MS, NODE_DUR } = ARTMAP_REVEAL;
  for (const n of artMap.placed) {
    n.aScale = 0;
    n.aAlpha = 0;
    const isl = islByName[n._island as string] || (n._isLabel ? islByName[n.name] : null);
    const order = isl ? (isl._order as number) : 0;
    let radial = 0;
    if (isl && isl.r > 0) radial = Math.min(1, Math.hypot(n.x - isl.cx, n.y - isl.cy) / isl.r);
    n._revealAt = t0 + order * ISL_STAGGER + radial * RADIAL_MS + (n._isLabel ? 90 : 0);
    n._revealDur = NODE_DUR;
  }

  artMap._ripples = islands.map((isl) => ({
    cx: isl.cx,
    cy: isl.cy,
    hue: isl.hue,
    maxR: isl.r * 1.45,
    t0: t0 + (isl._order as number) * ISL_STAGGER,
    dur: 1150,
  }));

  artMapStartLoop();
}

/**
 * Bloom ONE island (6116-6135) — the per-island version used when navigating
 * between genres, rather than the whole-map reveal.
 *
 * Only currently-visible bubbles bloom, and the stagger is a continuous radial
 * ramp plus a deterministic per-bubble jitter, so they surface organically
 * instead of in visible rings. Bubbles also rise 1.15 radii into place.
 */
export function artMapBloomIsland(isl: ArtMapIsland): void {
  const t0 = performance.now();
  artMap._revealing = true;
  artMap._ambient = true;
  for (const n of artMap.placed) {
    if ((n.opacity || 0) < 0.01) continue;
    n.aScale = 0;
    n.aAlpha = 0;
    let radial = 0;
    if (isl.r > 0) radial = Math.min(1, Math.hypot(n.x - isl.cx, n.y - isl.cy) / isl.r);
    const jitter = ((Math.abs(((n.id as number) | 0) * 1103515245 + 12345) % 1000) / 1000) * 200;
    n._revealAt = t0 + radial * 300 + jitter;
    n._revealDur = 560;
    n._riseAmp = (n.radius || 20) * 1.15; // bubbles rise up into place (surfacing)
    n._revealRise = n._riseAmp;
  }
  artMap._ripples = [{ cx: isl.cx, cy: isl.cy, hue: isl.hue, maxR: isl.r * 1.45, t0, dur: 1100 }];
  artMapStartLoop();
}

/**
 * Draw the expanding water-ripple rings (8917-8937).
 *
 * Cheap stroked arcs, hue-tinted, fading as they grow, in world space with a
 * screen-constant line width (hence the `/ z`).
 */
export function artMapDrawRipples(ctx: Ctx): void {
  const rip = artMap._ripples;
  if (!rip || !rip.length) return;
  const t = performance.now();
  const z = artMap.zoom;
  ctx.save();
  ctx.translate(artMap.offsetX, artMap.offsetY);
  ctx.scale(z, z);
  for (const r of rip) {
    const p = (t - r.t0) / r.dur;
    if (p < 0 || p > 1) continue;
    const radius = r.maxR * (0.08 + 0.92 * (1 - Math.pow(1 - p, 2)));
    const alpha = (1 - p) * 0.55;
    ctx.beginPath();
    ctx.arc(r.cx, r.cy, radius, 0, Math.PI * 2);
    ctx.strokeStyle = `hsla(${r.hue},85%,72%,${alpha})`;
    ctx.lineWidth = (1.5 + 6 * (1 - p)) / z; // ~constant on screen
    ctx.stroke();
  }
  ctx.restore();
}

/**
 * Emit a click/tap ripple at a world point (8965-8974) — a fading ring plus a
 * radial shove of nearby bubbles.
 *
 * Unlike the reveal's rings this one carries `push`, which is what makes the
 * surrounding bubbles heave outward and settle back.
 */
export function artMapEmitRipple(wx: number, wy: number, hue?: number | null): void {
  if (!artMap._ripples) artMap._ripples = [];
  const WR = artMap.WATCHLIST_R;
  artMap._ripples.push({
    cx: wx,
    cy: wy,
    hue: hue == null ? 270 : hue,
    maxR: WR * 2.6,
    t0: performance.now(),
    dur: 900,
    push: WR * 0.22,
    width: WR * 0.6,
  });
  artMapStartLoop(); // animate the ripple (guards against double-start)
}

// ── The frame ────────────────────────────────────────────────────────────────

/**
 * Request a draw (8976-8984).
 *
 * Coalesces every request into a single rAF, so a burst of mousemove/pan/
 * animation calls never draws more than once per frame.
 */
export function artMapRender(): void {
  if (artMap._rafPending) return;
  artMap._rafPending = requestAnimationFrame(() => {
    artMap._rafPending = null;
    artMapDraw();
  });
}

/**
 * Paint one frame (8986-9188).
 *
 * Background, cached vignette, the focused island's halo, the buffer blit (or
 * nothing while revealing), the live overlay, ripples, and finally the hover
 * constellation.
 *
 * The constellation is the expensive part, so its connected set is cached by
 * node id rather than recomputed per frame, and its edges are stroked twice
 * (wide faint halo, then crisp core) instead of using shadowBlur or a per-frame
 * gradient — those were the original hover-lag culprits.
 */
export function artMapDraw(): void {
  const _t0 = artMap._perf ? performance.now() : 0;
  if (!artMap._anim.running) artMap._now = performance.now(); // keep bob current on on-demand draws
  const ctx = artMap.ctx as Ctx;
  const w = artMap.width;
  const h = artMap.height;

  ctx.fillStyle = '#0a0a14';
  ctx.fillRect(0, 0, w, h);

  // Premium backdrop: a soft central glow fading to a dark vignette. Cached and
  // only rebuilt on resize, so it stays one cheap fillRect.
  if (!artMap._bgGrad || artMap._bgW !== w || artMap._bgH !== h) {
    const g = ctx.createRadialGradient(
      w / 2,
      h * 0.42,
      Math.min(w, h) * 0.12,
      w / 2,
      h / 2,
      Math.max(w, h) * 0.78,
    );
    g.addColorStop(0, 'rgba(46,34,78,0.40)');
    g.addColorStop(0.5, 'rgba(16,12,28,0.0)');
    g.addColorStop(1, 'rgba(0,0,0,0.55)');
    artMap._bgGrad = g;
    artMap._bgW = w;
    artMap._bgH = h;
  }
  ctx.fillStyle = artMap._bgGrad as CanvasGradient;
  ctx.fillRect(0, 0, w, h);

  const z = artMap.zoom;

  // Soft genre-hued halo behind the focused island (one-island mode).
  if (artMap._oneIsland && artMap._islands && artMap._islands.length) {
    const isl = artMap._islands[artMap._focusIdx || 0];
    if (isl) {
      const hr = isl.r * 2.5 * z;
      const hsx = artMap.offsetX + isl.cx * z;
      const hsy = artMap.offsetY + isl.cy * z;
      ctx.drawImage(artMapHaloSprite(isl.hue), hsx - hr, hsy - hr, hr * 2, hr * 2);
    }
  }

  // While the ripple-bloom reveal runs, bypass the static buffer entirely and
  // let the live layer draw every bubble. The buffer is (re)built once at the end.
  if (!artMap._revealing) {
    if (artMap.dirty || !artMap.offscreen) {
      const _rt = artMap._perf ? performance.now() : 0;
      artMapRebuildBuffer();
      if (artMap._perf) artMap._rebuildMs = performance.now() - _rt;
    }
    if (artMap.offscreen) {
      const oc = artMap.offscreen;
      const s = artMap._bufferScale as number;
      const mx = artMap._bufferMinX as number;
      const my = artMap._bufferMinY as number;
      const fieldAlpha = artMap._fieldAlpha == null ? 1 : artMap._fieldAlpha;
      if (fieldAlpha < 0.999) ctx.globalAlpha = fieldAlpha;
      ctx.drawImage(
        oc,
        artMap.offsetX + mx * z,
        artMap.offsetY + my * z,
        (oc.width * z) / s,
        (oc.height * z) / s,
      );
      ctx.globalAlpha = 1;
    }
  }

  artMapDrawLiveLayer(ctx);
  artMapDrawRipples(ctx);

  // ── Interactive overlay (drawn on the main canvas, not the buffer) ──
  const cFade = artMap._constellationFade || 0;
  if (cFade > 0 && (artMap.hoveredNode || artMap._constellationCache)) {
    const n =
      artMap.hoveredNode ||
      (artMap._constellationCache
        ? (artMap._nodeById || {})[artMap._constellationCache.nodeId as string]
        : null);
    if (!n) {
      artMap._constellationFade = 0;
      artMap._constellationCache = null;
    }
    if (n) {
      ctx.save();
      ctx.translate(artMap.offsetX, artMap.offsetY);
      ctx.scale(z, z);

      // Cache the connected-node lookup (don't recompute every frame).
      const cache = artMap._constellationCache;
      if (!cache || cache.nodeId !== n.id) {
        const connectedIds = new Set<unknown>();
        if (n.type === 'watchlist') {
          for (const e of artMap.edges) {
            if (e.source === n.id) connectedIds.add(e.target);
          }
        } else {
          const sourceIds = new Set<unknown>();
          for (const e of artMap.edges) {
            if (e.target === n.id) sourceIds.add(e.source);
          }
          for (const sid of sourceIds) {
            connectedIds.add(sid);
            for (const e of artMap.edges) {
              if (e.source === sid) connectedIds.add(e.target);
            }
          }
        }
        const nById = artMap._nodeById || {};
        artMap._constellationCache = {
          nodeId: n.id,
          nodes: [n, ...[...connectedIds].map((id) => nById[id as string]).filter(Boolean)],
        };
      }

      const highlightNodes = (artMap._constellationCache as { nodes: ArtMapNode[] }).nodes;

      if (highlightNodes.length > 1) {
        // Semi-transparent dark overlay on the entire visible area
        ctx.save();
        ctx.resetTransform();
        ctx.globalAlpha = 0.6 * cFade;
        ctx.fillStyle = '#0a0a14';
        ctx.fillRect(
          0,
          0,
          (artMap.canvas as HTMLCanvasElement).width,
          (artMap.canvas as HTMLCanvasElement).height,
        );
        ctx.globalAlpha = 1;
        ctx.restore();

        // Connection lines — build the path ONCE, then two cheap strokes.
        ctx.lineCap = 'round';
        ctx.beginPath();
        for (const cn of highlightNodes) {
          if (cn === n) continue;
          ctx.moveTo(n.x, n.y);
          ctx.lineTo(cn.x, cn.y);
        }
        ctx.strokeStyle = `rgba(168,85,247,${0.18 * cFade})`;
        ctx.lineWidth = 6;
        ctx.stroke();
        ctx.strokeStyle = `rgba(201,150,255,${0.6 * cFade})`;
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // Redraw highlighted nodes on top
        ctx.globalAlpha = cFade;
        for (const hn of highlightNodes) {
          const r = hn.radius;
          const isW = hn.type === 'watchlist';
          const isHov = hn === n;

          // Glow
          if (isHov) {
            ctx.beginPath();
            ctx.arc(hn.x, hn.y, r + 8, 0, Math.PI * 2);
            ctx.strokeStyle = 'rgba(138,43,226,0.4)';
            ctx.lineWidth = 6;
            ctx.stroke();
          }

          // Circle + image — pre-masked, so no per-frame clip.
          const img = artMap.images[hn.id as string];
          if (img) {
            ctx.drawImage(img, hn.x - r, hn.y - r, r * 2, r * 2);
            ctx.beginPath();
            ctx.arc(hn.x, hn.y, r, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(0,0,0,0.35)'; // keep the name legible over art
            ctx.fill();
          } else {
            ctx.beginPath();
            ctx.arc(hn.x, hn.y, r, 0, Math.PI * 2);
            ctx.fillStyle = isW ? '#1a0a30' : '#141420';
            ctx.fill();
          }

          // Border
          ctx.beginPath();
          ctx.arc(hn.x, hn.y, r, 0, Math.PI * 2);
          ctx.strokeStyle = isHov
            ? 'rgba(255,255,255,0.7)'
            : isW
              ? 'rgba(138,43,226,0.5)'
              : 'rgba(255,255,255,0.3)';
          ctx.lineWidth = isHov ? 3 : 1.5;
          ctx.stroke();

          // Name
          const fontSize = isW ? Math.max(14, r * 0.14) : Math.max(8, r * 0.3);
          ctx.font = `${isW ? '700' : '600'} ${fontSize}px system-ui`;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillStyle = '#fff';
          const maxC = isW ? 20 : 12;
          const label = hn.name.length > maxC ? hn.name.substring(0, maxC - 1) + '…' : hn.name;
          ctx.fillText(label, hn.x, hn.y);
        }
        ctx.globalAlpha = 1;
      } else {
        // Single node, no connections — pop the hovered bubble.
        artMapDrawHoverPop(ctx, n);
      }

      ctx.restore();
    }
  } else if (artMap.hoveredNode && !artMap._constellationActive) {
    // Pre-constellation: instant tactile pop on the hovered bubble.
    ctx.save();
    ctx.translate(artMap.offsetX, artMap.offsetY);
    ctx.scale(z, z);
    artMapDrawHoverPop(ctx, artMap.hoveredNode);
    ctx.restore();
  }

  if (artMap._perf) artMapDrawPerf(ctx, _t0);
}

/**
 * Fade the constellation in and out (9354-9368).
 *
 * Fading IN steps 0.08 a frame, OUT steps 0.1 — deliberately asymmetric, so it
 * lights up a touch more gently than it leaves. The cache is only dropped once
 * the fade reaches zero, so a re-hover of the same node reuses it.
 */
export function artMapAnimateConstellation(): void {
  if (artMap._constellationActive && (artMap._constellationFade ?? 0) < 1) {
    artMap._constellationFade = Math.min(1, ((artMap._constellationFade ?? 0) || 0) + 0.08);
    artMapRender();
    requestAnimationFrame(artMapAnimateConstellation);
  } else if (!artMap._constellationActive && (artMap._constellationFade ?? 0) > 0) {
    artMap._constellationFade = Math.max(0, (artMap._constellationFade ?? 0) - 0.1);
    artMapRender();
    if ((artMap._constellationFade ?? 0) > 0) {
      requestAnimationFrame(artMapAnimateConstellation);
    } else {
      artMap._constellationCache = null;
    }
  }
}

// ── The perf HUD ─────────────────────────────────────────────────────────────

export const ARTMAP_PERF_URL = '/api/discover/artist-map/perf';

/** The HUD's payload + lines (9192-9237), separated from the drawing. */
export function artMapPerfReport(drawMs: number, fps: number) {
  const oc = artMap.offscreen;
  return {
    payload: {
      nodes: artMap.placed.length,
      edges: (artMap.edges || []).length,
      buffer: oc ? oc.width + 'x' + oc.height : '-',
      scale: +(artMap._bufferScale || 0).toFixed(3),
      zoom: +artMap.zoom.toFixed(3),
      rebuildMs: +(artMap._rebuildMs || 0).toFixed(1),
      drawMs: +drawMs.toFixed(1),
      fps,
    },
    lines: [
      `nodes ${artMap.placed.length}   edges ${(artMap.edges || []).length}`,
      `buffer ${oc ? oc.width + '×' + oc.height : '—'}   scale ${(artMap._bufferScale || 0).toFixed(3)}`,
      `zoom ${artMap.zoom.toFixed(3)}`,
      `rebuild ${(artMap._rebuildMs || 0).toFixed(1)}ms   draw ${drawMs.toFixed(1)}ms`,
      `~${fps} fps (while interacting)`,
    ],
  };
}

/**
 * The 'd' overlay (9192-9237). Shows where frame time goes so the real
 * bottleneck (buffer rebuild on zoom vs blit on pan) is measured rather than
 * guessed.
 *
 * The numbers are also POSTed to app.log about 1.5x a second, because on-canvas
 * text cannot be copied — least of all mid-lag, which is exactly when it matters.
 */
export function artMapDrawPerf(ctx: Ctx, t0: number): void {
  const drawMs = performance.now() - t0;
  const now = performance.now();
  const dt = artMap._lastPerfTs ? now - artMap._lastPerfTs : 0;
  artMap._lastPerfTs = now;
  const fps = dt > 0 ? Math.round(1000 / dt) : 0;

  if (!artMap._perfPostTs || now - artMap._perfPostTs > 700) {
    artMap._perfPostTs = now;
    try {
      void fetch(ARTMAP_PERF_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(artMapPerfReport(drawMs, fps).payload),
      }).catch(() => {});
    } catch {
      /* ignore */
    }
  }

  const lines = artMapPerfReport(drawMs, fps).lines;
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0); // device pixels, ignore the dpr scale
  ctx.font = '12px monospace';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  const pad = 8;
  const lh = 16;
  ctx.fillStyle = 'rgba(0,0,0,0.72)';
  ctx.fillRect(10, 10, 270, lines.length * lh + pad * 2);
  ctx.fillStyle = '#7CFC00';
  lines.forEach((l, i) => ctx.fillText(l, 10 + pad, 10 + pad + i * lh));
  ctx.restore();
}

// ── Camera animation ─────────────────────────────────────────────────────────

/** How long a toolbar zoom/fit takes to land (8400). */
/**
 * Focus one genre island (6082-6113): clamp + remember the index, show ONLY
 * that island's bubbles (labels stay hidden — the nav bar already names the
 * genre), and frame it in the space LEFT of the info panel.
 *
 * The vanilla ends by rewriting the island nav and the panel (6105-6106);
 * here those are React state, so the FOCUSED ISLAND is returned for the
 * caller to feed them. `bloom: false` re-renders statically instead.
 */
export function artMapFocusIsland(
  idx: number,
  opts: { bloom?: boolean } = {},
): ArtMapIsland | null {
  const islands = artMap._islands || [];
  if (!islands.length) return null;
  const clamped = Math.max(0, Math.min(islands.length - 1, idx));
  artMap._focusIdx = clamped;
  const isl = islands[clamped];

  for (const n of artMap.placed) {
    n.opacity = !n._isLabel && n._island === isl.name ? 1 : 0;
  }

  // ~80% framing in the width the panel leaves free (6097-6102).
  const usableW = Math.max(200, artMap.width - artMapReservedW());
  const span = isl.r * 2.3 + 120;
  const z = Math.min(usableW / span, artMap.height / span, 1.2);
  artMap.zoom = z;
  artMap.offsetX = usableW / 2 - isl.cx * z;
  artMap.offsetY = artMap.height / 2 - isl.cy * z;

  if (opts.bloom !== false) {
    artMapBloomIsland(isl);
  } else {
    artMap.dirty = true;
    artMapRender();
  }
  return isl;
}

/**
 * Step to the prev/next island, WRAPPING (6139-6146) — and refusing entirely
 * below two islands, so the arrows are inert rather than re-blooming the only
 * island on every press.
 */
export function artMapIslandNavStep(dir: number): ArtMapIsland | null {
  const islands = artMap._islands || [];
  if (islands.length < 2) return null;
  let idx = (artMap._focusIdx || 0) + dir;
  if (idx < 0) idx = islands.length - 1;
  if (idx >= islands.length) idx = 0;
  return artMapFocusIsland(idx, { bloom: true });
}

export const ARTMAP_CAMERA_MS = 250;

/**
 * Ease the camera to a target over 250ms (8395-8420).
 *
 * Every intermediate frame is a BLIT only; the buffer is marked dirty and
 * rebuilt once, at the final zoom, so a zoom animation never pays for N
 * rebuilds.
 */
export function artMapAnimateTo(targetZoom: number, targetOX: number, targetOY: number): void {
  if (artMap._animating) cancelAnimationFrame(artMap._animating);
  const startZoom = artMap.zoom;
  const startOX = artMap.offsetX;
  const startOY = artMap.offsetY;
  const duration = ARTMAP_CAMERA_MS;
  const start = performance.now();

  function step(now: number) {
    const t = Math.min(1, (now - start) / duration);
    // Ease out cubic
    const e = 1 - Math.pow(1 - t, 3);
    artMap.zoom = startZoom + (targetZoom - startZoom) * e;
    artMap.offsetX = startOX + (targetOX - startOX) * e;
    artMap.offsetY = startOY + (targetOY - startOY) * e;
    artMapRender(); // blit only, no rebuild
    if (t < 1) {
      artMap._animating = requestAnimationFrame(step);
    } else {
      artMap._animating = null;
      artMap.dirty = true;
      artMapRender(); // rebuild at the final zoom level
    }
  }
  artMap._animating = requestAnimationFrame(step);
}

// ── Images ───────────────────────────────────────────────────────────────────

/**
 * Pre-mask a decoded bitmap into a CIRCLE once, at load time (9882-9900).
 *
 * The map then draws bubbles with a plain drawImage instead of a per-frame
 * `ctx.clip()` per bubble. Clipping is one of the most expensive canvas ops and,
 * at hundreds of visible bubbles per frame, was the live-layer stutter. Done
 * once here it is free forever after.
 *
 * The source ImageBitmap is closed afterwards — the canvas is what we keep.
 */
export function artMapCircleMask(src: CanvasImageSource | null): CanvasImageSource | null {
  if (!src) return null;
  const w = (src as { width?: number }).width || 0;
  if (!w) return src;
  try {
    const c = document.createElement('canvas');
    c.width = w;
    c.height = w;
    const cx = c.getContext('2d') as Ctx;
    cx.beginPath();
    cx.arc(w / 2, w / 2, w / 2, 0, Math.PI * 2);
    cx.closePath();
    cx.clip();
    cx.drawImage(src, 0, 0, w, w);
    const closable = src as { close?: () => void };
    if (closable.close) closable.close(); // free the ImageBitmap; we keep the canvas
    return c;
  } catch {
    return src; // fall back to the raw bitmap (the draw path still clips defensively)
  }
}

/** Decode a blob straight to a small circular avatar (9864-9875). */
export function artMapDecodeSmall(
  blob: Blob | null,
  px: number,
): Promise<CanvasImageSource | null> {
  if (!blob) return Promise.resolve(null);
  const d = Math.min(384, Math.max(112, Math.round(px || 144)));
  try {
    return createImageBitmap(blob, { resizeWidth: d, resizeHeight: d, resizeQuality: 'high' })
      .then(artMapCircleMask)
      .catch(() =>
        createImageBitmap(blob)
          .then(artMapCircleMask)
          .catch(() => null),
      );
  } catch {
    return createImageBitmap(blob)
      .then(artMapCircleMask)
      .catch(() => null);
  }
}

/**
 * Load one node image (9902-9914).
 *
 * A direct CORS fetch first — zero server load, and it works for Spotify,
 * iTunes and Discogs. The server proxy is the fallback for CDNs that send no
 * CORS headers.
 */
export function artMapLoadImage(url: string, px: number): Promise<CanvasImageSource | null> {
  return fetch(url, { mode: 'cors' })
    .then((r) => (r.ok ? r.blob() : Promise.reject(new Error('not ok'))))
    .then((b) => artMapDecodeSmall(b, px))
    .catch(() =>
      fetch('/api/image-proxy?url=' + encodeURIComponent(url))
        .then((r) => (r.ok ? r.blob() : null))
        .then((b) => artMapDecodeSmall(b, px))
        .catch(() => null),
    );
}

/** How many image fetches run at once, and how long redraws are coalesced. */
export const ARTMAP_IMAGE_CONCURRENCY = 24;
export const ARTMAP_REDRAW_THROTTLE_MS = 200;

/**
 * Stream node images in the background WITHOUT blocking the first paint
 * (9930-9976).
 *
 * The map is drawn immediately with placeholder circles and stays fully
 * interactive while images fill in. Biggest nodes are fetched first, since that
 * is where the eye lands. A load token makes opening another map cancel this
 * stream, so stale bitmaps are dropped rather than painted into the new world.
 *
 * Redraws are coalesced into ~200ms waves and do a FULL rebuild rather than a
 * per-node composite: the per-map buffer is small now (one focused island, or a
 * small explore map), and a full rebuild is guaranteed to pick up every cached
 * image. That is what makes streamed art appear on its own instead of only after
 * a manual zoom forced a rebuild.
 *
 * A bitmap for a HIDDEN bubble is still cached but triggers no redraw — you have
 * not navigated to that island yet.
 */
export function artMapStreamImages(
  imgNodes: ArtMapNode[],
  concurrent = ARTMAP_IMAGE_CONCURRENCY,
): void {
  const token = (artMap._loadToken = (artMap._loadToken || 0) + 1);
  const queue = imgNodes
    .filter((n) => n.image_url)
    .slice()
    .sort((a, b) => (b.radius || 0) - (a.radius || 0));
  let idx = 0;
  let inFlight = 0;
  let redrawPending = false;

  const scheduleRedraw = () => {
    if (redrawPending || token !== artMap._loadToken) return;
    redrawPending = true;
    setTimeout(() => {
      redrawPending = false;
      if (token !== artMap._loadToken) return;
      artMap.dirty = true;
      artMapRender();
      artMapEnsureAmbient();
    }, ARTMAP_REDRAW_THROTTLE_MS);
  };

  function pump() {
    if (token !== artMap._loadToken) return; // a newer map took over
    while (inFlight < concurrent && idx < queue.length) {
      const n = queue[idx++];
      if (artMap.images[n.id as string]) continue;
      inFlight++;
      void artMapLoadImage(n.image_url, artMapNodeImgPx(n))
        .then((bmp) => {
          if (bmp && token === artMap._loadToken) {
            artMap.images[n.id as string] = bmp;
            if ((n.opacity || 0) < 0.01) return;
            scheduleRedraw();
          }
        })
        .finally(() => {
          inFlight--;
          pump();
        });
    }
  }
  pump();
}
