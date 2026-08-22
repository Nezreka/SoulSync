import type { YearInListening, YearTotals } from './-year.types';

import { describeListeningTime } from './-year.helpers';

/**
 * The card studio: a PURE MODEL and a draw pass.
 *
 * Everything about what the card says, how big it is, which art it uses and
 * which layout it takes is decided here, where it can be tested without a
 * rendering context. `drawYearCard` only decides where things sit.
 *
 * WHY THIS IS A STUDIO AND NOT A TEMPLATE — Spotify's Wrapped card is one
 * fixed composition you cannot touch, because they are rendering the same
 * thing for half a billion people. We are rendering it for one person who
 * owns their music, which means we can offer real choices AND use the actual
 * album art off their disk. That is the whole advantage; a single fixed
 * template throws it away.
 */

// ── shape ────────────────────────────────────────────────────────────────────

export const YEAR_CARD_ASPECTS = {
  post: { width: 1080, height: 1350, label: 'Post', ratio: '4:5' },
  square: { width: 1080, height: 1080, label: 'Square', ratio: '1:1' },
  story: { width: 1080, height: 1920, label: 'Story', ratio: '9:16' },
} as const;

export type YearCardAspect = keyof typeof YEAR_CARD_ASPECTS;
export const YEAR_CARD_ASPECT_KEYS = Object.keys(YEAR_CARD_ASPECTS) as YearCardAspect[];

/**
 * The four compositions.
 *
 * They are genuinely different shapes of card, not restyles: `poster` is one
 * hero image, `mosaic` is a wall of covers, `stack` is the balanced default,
 * `minimal` is type only. A "layout" picker whose options all look the same
 * is worse than no picker.
 */
export const YEAR_CARD_LAYOUTS = {
  stack: { label: 'Stack', artSlots: 4, blurb: 'Art grid over your numbers' },
  poster: { label: 'Poster', artSlots: 1, blurb: 'One cover, full bleed' },
  mosaic: { label: 'Mosaic', artSlots: 9, blurb: 'A wall of everything' },
  minimal: { label: 'Minimal', artSlots: 0, blurb: 'Type only, no art' },
} as const;

export type YearCardLayout = keyof typeof YEAR_CARD_LAYOUTS;
export const YEAR_CARD_LAYOUT_KEYS = Object.keys(YEAR_CARD_LAYOUTS) as YearCardLayout[];

// ── palette ──────────────────────────────────────────────────────────────────

export const YEAR_CARD_THEMES = ['midnight', 'ink', 'sunset', 'paper'] as const;
export type YearCardTheme = (typeof YEAR_CARD_THEMES)[number];

export interface YearCardPalette {
  label: string;
  from: string;
  to: string;
  accent: string;
  text: string;
  muted: string;
  /** Behind art tiles, so a blocked image is a tile rather than a hole. */
  tile: string;
  /** Scrim over a full-bleed image so text stays readable on any cover. */
  scrim: string;
}

export const YEAR_CARD_PALETTES: Record<YearCardTheme, YearCardPalette> = {
  midnight: {
    label: 'Midnight',
    from: '#0b0f0c',
    to: '#10231a',
    accent: '#1db954',
    text: '#ffffff',
    muted: 'rgba(255,255,255,0.55)',
    tile: 'rgba(255,255,255,0.07)',
    scrim: 'rgba(6,10,8,0.72)',
  },
  ink: {
    label: 'Ink',
    from: '#111113',
    to: '#000000',
    accent: '#e7e7ea',
    text: '#ffffff',
    muted: 'rgba(255,255,255,0.5)',
    tile: 'rgba(255,255,255,0.06)',
    scrim: 'rgba(0,0,0,0.68)',
  },
  sunset: {
    label: 'Sunset',
    from: '#2a0f2e',
    to: '#7a2233',
    accent: '#ffb057',
    text: '#fff6ef',
    muted: 'rgba(255,246,239,0.6)',
    tile: 'rgba(255,255,255,0.08)',
    scrim: 'rgba(28,8,20,0.66)',
  },
  // A light card exists because every dark card looks the same in a feed.
  paper: {
    label: 'Paper',
    from: '#f6f4ef',
    to: '#e7e2d8',
    accent: '#0f7a3d',
    text: '#14150f',
    muted: 'rgba(20,21,15,0.55)',
    tile: 'rgba(20,21,15,0.08)',
    scrim: 'rgba(246,244,239,0.74)',
  },
};

// ── stats ────────────────────────────────────────────────────────────────────

export type YearCardStatKey = 'plays' | 'minutes' | 'artists' | 'albums' | 'tracks' | 'active_days';

/** Every stat the card CAN show, with how to render it. Order is the order
 *  they appear; the user chooses which, not where. */
export const YEAR_CARD_STAT_DEFS: {
  key: YearCardStatKey;
  label: string;
  format: (totals: YearTotals) => string;
}[] = [
  { key: 'plays', label: 'Plays', format: (t) => (t.plays ?? 0).toLocaleString() },
  { key: 'minutes', label: 'Listening time', format: (t) => describeListeningTime(t.minutes ?? 0) },
  { key: 'artists', label: 'Artists', format: (t) => (t.artists ?? 0).toLocaleString() },
  { key: 'albums', label: 'Albums', format: (t) => (t.albums ?? 0).toLocaleString() },
  { key: 'tracks', label: 'Tracks', format: (t) => (t.tracks ?? 0).toLocaleString() },
  {
    key: 'active_days',
    label: 'Days with music',
    format: (t) => (t.active_days ?? 0).toLocaleString(),
  },
];

export const DEFAULT_YEAR_CARD_STATS: YearCardStatKey[] = [
  'plays',
  'minutes',
  'artists',
  'active_days',
];

/** More than this and the rows get too tight to read at any aspect. */
export const MAX_YEAR_CARD_STATS = 5;

// ── options ──────────────────────────────────────────────────────────────────

export interface YearCardOptions {
  theme: YearCardTheme;
  layout: YearCardLayout;
  aspect: YearCardAspect;
  artwork: boolean;
  stats: YearCardStatKey[];
  showRunnersUp: boolean;
}

export const DEFAULT_YEAR_CARD_OPTIONS: YearCardOptions = {
  theme: 'midnight',
  layout: 'stack',
  aspect: 'post',
  artwork: true,
  stats: DEFAULT_YEAR_CARD_STATS,
  showRunnersUp: true,
};

// ── model ────────────────────────────────────────────────────────────────────

export interface YearCardStat {
  label: string;
  value: string;
}

export interface YearCardModel {
  palette: YearCardPalette;
  layout: YearCardLayout;
  width: number;
  height: number;
  /** Type scale, so a story card is not a post card with more empty space. */
  scale: number;
  period: string;
  titleLines: string[];
  stats: YearCardStat[];
  highlight: { label: string; name: string; sub: string } | null;
  runnersUp: string[];
  artUrls: string[];
  filename: string;
}

/**
 * Build the card.
 *
 * The stat grid is label/value pairs rather than sentences: split, the draw
 * pass can set them in different weights and align the numbers, which is most
 * of what makes a generated card look designed rather than dumped.
 */
export function buildYearCardModel(
  year: YearInListening,
  options: YearCardOptions = DEFAULT_YEAR_CARD_OPTIONS,
): YearCardModel {
  const palette = YEAR_CARD_PALETTES[options.theme] ?? YEAR_CARD_PALETTES.midnight;
  const layout: YearCardLayout = YEAR_CARD_LAYOUTS[options.layout] ? options.layout : 'stack';
  const aspect = YEAR_CARD_ASPECTS[options.aspect] ?? YEAR_CARD_ASPECTS.post;
  const totals: YearTotals = year.totals ?? {
    plays: 0,
    minutes: 0,
    artists: 0,
    albums: 0,
    tracks: 0,
    active_days: 0,
  };

  // Selection is honoured in DEFINITION order, not click order — a card whose
  // rows reshuffle as you tick boxes feels broken rather than configurable.
  const chosen = new Set(options.stats ?? []);
  const stats = YEAR_CARD_STAT_DEFS.filter((d) => chosen.has(d.key))
    .slice(0, MAX_YEAR_CARD_STATS)
    .map((d) => ({ label: d.label, value: d.format(totals) }));

  const artists = year.top_artists ?? [];
  const top = artists[0];
  const track = (year.top_tracks ?? [])[0];

  const highlight = top
    ? {
        label: 'Your number one',
        name: top.name,
        sub: track ? `On repeat: ${track.name}` : `${top.plays.toLocaleString()} plays`,
      }
    : null;

  const runnersUp = options.showRunnersUp ? artists.slice(1, 4).map((a) => a.name) : [];

  // Deduped: the top artist and the top track's album commonly resolve to the
  // same file, and a wall showing one square twice reads as a bug.
  const slots = options.artwork ? YEAR_CARD_LAYOUTS[layout].artSlots : 0;
  const artUrls = slots
    ? Array.from(
        new Set(
          [
            ...artists.map((a) => a.image_url),
            ...(year.top_albums ?? []).map((a) => a.image_url),
            ...(year.top_tracks ?? []).map((t) => t.image_url),
            ...(year.discoveries ?? []).map((d) => d.image_url),
          ].filter((url): url is string => Boolean(url)),
        ),
      ).slice(0, slots)
    : [];

  return {
    palette,
    layout,
    width: aspect.width,
    height: aspect.height,
    // 1350 is the reference height the type was drawn against.
    scale: aspect.height / 1350,
    period: year.period?.label ?? '',
    titleLines: ['Your Year', 'in Listening'],
    stats,
    highlight,
    runnersUp,
    artUrls,
    filename: cardFilename(year, options),
  };
}

function cardFilename(year: YearInListening, options: YearCardOptions): string {
  const label = (year.period?.label ?? '').replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '');
  const stem = label.toLowerCase() || 'in-listening';
  return `soulsync-year-${stem}-${options.layout ?? 'stack'}.png`;
}

/** Toggle a stat, refusing to leave the card with none and capping the max. */
export function toggleCardStat(
  current: YearCardStatKey[],
  key: YearCardStatKey,
): YearCardStatKey[] {
  const has = current.includes(key);
  // A card with zero stats is not a minimal card, it is a broken one.
  if (has && current.length === 1) return current;
  if (has) return current.filter((k) => k !== key);
  if (current.length >= MAX_YEAR_CARD_STATS) return current;
  return [...current, key];
}

// ── draw ─────────────────────────────────────────────────────────────────────

/**
 * Draw the model. `images` is positional against `model.artUrls`; a null entry
 * means that image did not load (or would taint the canvas) and its tile is
 * drawn flat, so a grid never collapses.
 */
export function drawYearCard(
  ctx: CanvasRenderingContext2D,
  model: YearCardModel,
  images: (CanvasImageSource | null)[] = [],
): void {
  const { palette, width: W, height: H } = model;

  const bg = ctx.createLinearGradient(0, 0, W * 0.6, H);
  bg.addColorStop(0, palette.from);
  bg.addColorStop(1, palette.to);
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, W, H);

  ctx.textBaseline = 'alphabetic';
  ctx.textAlign = 'left';

  if (model.layout === 'poster') drawPoster(ctx, model, images);
  else if (model.layout === 'mosaic') drawMosaic(ctx, model, images);
  else drawStack(ctx, model, images); // stack + minimal share a composition
}

const FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif';
const font = (weight: number, size: number, scale: number) =>
  `${weight} ${Math.round(size * scale)}px ${FONT}`;

function drawStack(
  ctx: CanvasRenderingContext2D,
  model: YearCardModel,
  images: (CanvasImageSource | null)[],
): void {
  const { palette, width: W, height: H, scale: s } = model;
  const M = Math.round(96 * s);

  ctx.fillStyle = palette.accent;
  ctx.font = font(700, 30, s);
  ctx.fillText(spaced(model.period.toUpperCase()), M, 150 * s);

  ctx.fillStyle = palette.text;
  ctx.font = font(800, 92, s);
  model.titleLines.forEach((line, i) => {
    ctx.fillText(line, M, (268 + i * 100) * s);
  });

  let y = 430 * s;
  if (model.artUrls.length) {
    const gap = Math.round(16 * s);
    const tile = (W - M * 2 - gap) / 2;
    model.artUrls.slice(0, 4).forEach((_, index) => {
      const x = M + (index % 2) * (tile + gap);
      const ty = y + Math.floor(index / 2) * (tile + gap);
      drawTile(ctx, images[index], x, ty, tile, palette, 20 * s);
    });
    const rows = Math.ceil(Math.min(model.artUrls.length, 4) / 2);
    y += rows * (tile + gap) + 34 * s;
  }

  y = drawHighlight(ctx, model, y, M);
  y = drawStatRows(ctx, model, y, M);
  drawFooter(ctx, model, M, H);
}

/** One cover, full bleed, text over a scrim. The most "shareable" of the four. */
function drawPoster(
  ctx: CanvasRenderingContext2D,
  model: YearCardModel,
  images: (CanvasImageSource | null)[],
): void {
  const { palette, width: W, height: H, scale: s } = model;
  const M = Math.round(96 * s);
  const hero = images[0];

  if (hero) {
    // Cover-fit a square source into the card without squashing it.
    const side = Math.max(W, H);
    ctx.drawImage(hero, (W - side) / 2, (H - side) / 2, side, side);
  }

  // Scrim: bottom-heavy so the type sits on solid ground whatever the cover.
  const scrim = ctx.createLinearGradient(0, 0, 0, H);
  scrim.addColorStop(0, hero ? 'rgba(0,0,0,0.15)' : 'rgba(0,0,0,0)');
  scrim.addColorStop(0.42, hero ? 'rgba(0,0,0,0.35)' : 'rgba(0,0,0,0)');
  scrim.addColorStop(1, palette.scrim);
  ctx.fillStyle = scrim;
  ctx.fillRect(0, 0, W, H);

  ctx.fillStyle = palette.accent;
  ctx.font = font(700, 30, s);
  ctx.fillText(spaced(model.period.toUpperCase()), M, 150 * s);

  // Type is bottom-anchored here, not top — a poster reads up from its base.
  const rows = model.stats.length;
  let y = H - M - rows * 74 * s - (model.highlight ? 150 * s : 0);

  ctx.fillStyle = palette.text;
  ctx.font = font(800, 92, s);
  model.titleLines.forEach((line, i) => {
    ctx.fillText(line, M, y - (model.titleLines.length - i) * 100 * s + 40 * s);
  });

  y = drawHighlight(ctx, model, y, M);
  drawStatRows(ctx, model, y, M);
  drawFooter(ctx, model, M, H);
}

/** A wall of covers with a text panel over the lower half. */
function drawMosaic(
  ctx: CanvasRenderingContext2D,
  model: YearCardModel,
  images: (CanvasImageSource | null)[],
): void {
  const { palette, width: W, height: H, scale: s } = model;
  const M = Math.round(80 * s);
  const cols = 3;
  const tile = W / cols;
  const rowsNeeded = Math.ceil(Math.max(model.artUrls.length, 1) / cols);

  for (let i = 0; i < rowsNeeded * cols; i += 1) {
    const x = (i % cols) * tile;
    const ty = Math.floor(i / cols) * tile;
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, ty, tile, tile);
    ctx.clip();
    ctx.fillStyle = palette.tile;
    ctx.fillRect(x, ty, tile, tile);
    const img = images[i];
    if (img) ctx.drawImage(img, x, ty, tile, tile);
    ctx.restore();
  }

  const panelTop = Math.min(rowsNeeded * tile, H * 0.52);
  const panel = ctx.createLinearGradient(0, panelTop - 160 * s, 0, panelTop + 120 * s);
  panel.addColorStop(0, 'rgba(0,0,0,0)');
  panel.addColorStop(1, palette.from);
  ctx.fillStyle = panel;
  ctx.fillRect(0, panelTop - 160 * s, W, 280 * s);
  ctx.fillStyle = palette.from;
  ctx.fillRect(0, panelTop + 118 * s, W, H - panelTop);

  let y = panelTop + 90 * s;
  ctx.fillStyle = palette.accent;
  ctx.font = font(700, 30, s);
  ctx.fillText(spaced(model.period.toUpperCase()), M, y);
  y += 76 * s;

  ctx.fillStyle = palette.text;
  ctx.font = font(800, 76, s);
  ctx.fillText(model.titleLines.join(' '), M, y);
  y += 60 * s;

  y = drawHighlight(ctx, model, y, M);
  drawStatRows(ctx, model, y, M);
  drawFooter(ctx, model, M, H);
}

function drawHighlight(
  ctx: CanvasRenderingContext2D,
  model: YearCardModel,
  yIn: number,
  M: number,
): number {
  if (!model.highlight) return yIn;
  const { palette, width: W, scale: s } = model;
  let y = yIn;

  ctx.textAlign = 'left';
  ctx.fillStyle = palette.muted;
  ctx.font = font(600, 26, s);
  ctx.fillText(spaced(model.highlight.label.toUpperCase()), M, y);
  y += 52 * s;

  ctx.fillStyle = palette.text;
  ctx.font = font(800, 54, s);
  ctx.fillText(ellipsize(ctx, model.highlight.name, W - M * 2), M, y);
  y += 44 * s;

  ctx.fillStyle = palette.muted;
  ctx.font = font(500, 30, s);
  ctx.fillText(ellipsize(ctx, model.highlight.sub, W - M * 2), M, y);
  y += 30 * s;

  if (model.runnersUp.length) {
    y += 34 * s;
    ctx.fillStyle = palette.muted;
    ctx.font = font(500, 27, s);
    ctx.fillText(ellipsize(ctx, model.runnersUp.join('  ·  '), W - M * 2), M, y);
  }
  return y + 46 * s;
}

/** Labels left, values right, a hairline under each — the aligned numbers are
 *  what stop this reading as a dumped list. */
function drawStatRows(
  ctx: CanvasRenderingContext2D,
  model: YearCardModel,
  yIn: number,
  M: number,
): number {
  const { palette, width: W, scale: s } = model;
  let y = yIn;
  model.stats.forEach((stat) => {
    ctx.fillStyle = palette.muted;
    ctx.font = font(500, 30, s);
    ctx.textAlign = 'left';
    ctx.fillText(stat.label, M, y);

    ctx.fillStyle = palette.text;
    ctx.font = font(700, 34, s);
    ctx.textAlign = 'right';
    ctx.fillText(stat.value, W - M, y);

    ctx.textAlign = 'left';
    ctx.strokeStyle = palette.tile;
    ctx.lineWidth = Math.max(1, 2 * s);
    ctx.beginPath();
    ctx.moveTo(M, y + 22 * s);
    ctx.lineTo(W - M, y + 22 * s);
    ctx.stroke();
    y += 74 * s;
  });
  return y;
}

function drawFooter(
  ctx: CanvasRenderingContext2D,
  model: YearCardModel,
  M: number,
  H: number,
): void {
  ctx.fillStyle = model.palette.accent;
  ctx.font = font(700, 30, model.scale);
  ctx.textAlign = 'left';
  ctx.fillText('SoulSync', M, H - 82 * model.scale);
}

function drawTile(
  ctx: CanvasRenderingContext2D,
  img: CanvasImageSource | null | undefined,
  x: number,
  y: number,
  size: number,
  palette: YearCardPalette,
  radius: number,
): void {
  ctx.save();
  roundedRect(ctx, x, y, size, size, radius);
  ctx.clip();
  ctx.fillStyle = palette.tile;
  ctx.fillRect(x, y, size, size);
  if (img) ctx.drawImage(img, x, y, size, size);
  ctx.restore();
}

function roundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** Letter-spacing for the small-caps rows — canvas letterSpacing is not
 *  available everywhere we run, so it is done by hand. */
function spaced(text: string): string {
  return text.split('').join(' ');
}

/** A long artist name must not run off the card. */
function ellipsize(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let out = text;
  while (out.length > 1 && ctx.measureText(`${out}…`).width > maxWidth) {
    out = out.slice(0, -1);
  }
  return `${out}…`;
}
