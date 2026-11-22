import type { YearInListening } from './-year.types';

import { type YearCardModel, drawYearCard } from './-year.card';
import { buildCalendar, describeListeningTime, formatStoryDate } from './-year.helpers';

/**
 * The story as a video: the same moments as the slides, drawn on a canvas and
 * recorded in real time with MediaRecorder, ending on the keepsake card. The
 * scenes are data (testable without a canvas); the draw pass and the recorder
 * sit beside them.
 */

export type ReelSceneKind =
  | 'title'
  | 'totals'
  | 'artist'
  | 'track'
  | 'genre'
  | 'streak'
  | 'day'
  | 'calendar'
  | 'card';

export interface ReelScene {
  kind: ReelSceneKind;
  kicker: string;
  title: string;
  sub?: string;
  art?: string | null;
  /** a number the title counts up to */
  count?: number;
  ms: number;
}

const SCENE_MS = 2_800;
const CARD_MS = 3_600;

/** The moments this year can fill, in story order. */
export function buildReelScenes(year: YearInListening): ReelScene[] {
  const scenes: ReelScene[] = [];
  const label = year.period?.label ?? '';
  scenes.push({
    kind: 'title',
    kicker: label,
    title: 'Your Year in Listening',
    sub: 'Made with SoulSync',
    ms: SCENE_MS,
  });
  if (!year.has_data || (year.totals?.plays ?? 0) <= 0) {
    scenes.push({ kind: 'card', kicker: '', title: '', ms: CARD_MS });
    return scenes;
  }
  scenes.push({
    kind: 'totals',
    kicker: 'You pressed play',
    title: year.totals.plays.toLocaleString(),
    count: year.totals.plays,
    sub: `times · ${describeListeningTime(year.totals.minutes)} of music`,
    ms: SCENE_MS,
  });
  const artist = year.top_artists?.[0];
  if (artist) {
    scenes.push({
      kind: 'artist',
      kicker: 'Your number one',
      title: artist.name,
      sub: `${artist.plays.toLocaleString()} plays`,
      art: artist.image_url ?? null,
      ms: SCENE_MS,
    });
  }
  const track = year.top_tracks?.[0];
  if (track) {
    scenes.push({
      kind: 'track',
      kicker: 'On repeat',
      title: track.name,
      sub: track.artist ?? undefined,
      art: track.image_url ?? null,
      ms: SCENE_MS,
    });
  }
  const genre = year.top_genres?.[0];
  if (genre) {
    scenes.push({
      kind: 'genre',
      kicker: 'Your sound',
      title: genre.genre,
      sub: `${Math.round(genre.share * 100)}% of what you played`,
      ms: SCENE_MS,
    });
  }
  if ((year.streak?.days ?? 0) >= 2) {
    scenes.push({
      kind: 'streak',
      kicker: 'Your longest streak',
      title: `${year.streak!.days} days`,
      count: year.streak!.days,
      sub: 'in a row with music',
      ms: SCENE_MS,
    });
  }
  const day = formatStoryDate(year.peak_day?.date);
  if (day) {
    scenes.push({
      kind: 'day',
      kicker: 'Your biggest day',
      title: day,
      sub: `${year.peak_day.plays.toLocaleString()} plays${
        year.peak_day.top_artist ? ` · mostly ${year.peak_day.top_artist}` : ''
      }`,
      ms: SCENE_MS,
    });
  }
  if ((year.days ?? []).length) {
    scenes.push({
      kind: 'calendar',
      kicker: 'Every day of it',
      title: `${year.totals.active_days.toLocaleString()} days with music`,
      ms: SCENE_MS,
    });
  }
  scenes.push({ kind: 'card', kicker: '', title: '', ms: CARD_MS });
  return scenes;
}

export function reelDuration(scenes: ReelScene[]): number {
  return scenes.reduce((sum, s) => sum + s.ms, 0);
}

/** Which scene is on screen `t` ms in, and how far through it (0..1). */
export function sceneAt(
  scenes: ReelScene[],
  t: number,
): { index: number; progress: number; local: number } {
  let start = 0;
  for (let i = 0; i < scenes.length; i++) {
    const end = start + scenes[i].ms;
    if (t < end || i === scenes.length - 1) {
      const local = Math.max(0, Math.min(scenes[i].ms, t - start));
      return { index: i, local, progress: scenes[i].ms ? local / scenes[i].ms : 1 };
    }
    start = end;
  }
  return { index: 0, local: 0, progress: 0 };
}

export interface VideoFormat {
  mime: string;
  ext: 'mp4' | 'webm';
}

/** mp4 where the browser records it (Safari, recent Chrome), else webm. */
export function pickVideoFormat(isTypeSupported: (mime: string) => boolean): VideoFormat | null {
  const order: VideoFormat[] = [
    { mime: 'video/mp4;codecs=avc1', ext: 'mp4' },
    { mime: 'video/mp4', ext: 'mp4' },
    { mime: 'video/webm;codecs=vp9,opus', ext: 'webm' },
    { mime: 'video/webm;codecs=vp8,opus', ext: 'webm' },
    { mime: 'video/webm', ext: 'webm' },
  ];
  for (const format of order) {
    try {
      if (isTypeSupported(format.mime)) return format;
    } catch {
      // a browser that throws on a type it doesn't know: try the next
    }
  }
  return null;
}

export function canRecordVideo(): boolean {
  return (
    typeof MediaRecorder !== 'undefined' &&
    typeof HTMLCanvasElement !== 'undefined' &&
    typeof HTMLCanvasElement.prototype.captureStream === 'function' &&
    pickVideoFormat((m) => MediaRecorder.isTypeSupported(m)) !== null
  );
}

export function videoFilename(cardFilename: string, ext: string): string {
  return cardFilename.replace(/\.png$/i, '') + `.${ext}`;
}

// ── draw ─────────────────────────────────────────────────────────────────────

const ease = (t: number) => 1 - (1 - Math.max(0, Math.min(1, t))) ** 3;

function wrap(ctx: CanvasRenderingContext2D, text: string, width: number, max = 2): string[] {
  const words = text.split(/\s+/);
  const lines: string[] = [];
  let line = '';
  for (const word of words) {
    const probe = line ? `${line} ${word}` : word;
    if (ctx.measureText(probe).width > width && line) {
      lines.push(line);
      line = word;
      if (lines.length === max - 1) break;
    } else {
      line = probe;
    }
  }
  const rest = words.slice(lines.join(' ').split(/\s+/).filter(Boolean).length).join(' ');
  if (rest) lines.push(rest);
  if (lines.length > max) lines.length = max;
  const last = lines[lines.length - 1] ?? '';
  if (ctx.measureText(last).width > width) {
    let cut = last;
    while (cut.length > 1 && ctx.measureText(`${cut}…`).width > width) cut = cut.slice(0, -1);
    lines[lines.length - 1] = `${cut}…`;
  }
  return lines;
}

/**
 * One frame. `images` maps art urls to loaded images (null when one would not
 * load or would taint the canvas). The card scene draws the keepsake card
 * itself, so the video ends exactly where the story does.
 */
export function drawReelFrame(
  ctx: CanvasRenderingContext2D,
  card: YearCardModel,
  year: YearInListening,
  scenes: ReelScene[],
  t: number,
  images: Map<string, CanvasImageSource | null>,
  cardImages: (CanvasImageSource | null)[],
): void {
  const { width: W, height: H, palette } = card;
  const s = card.scale;
  const { index, progress, local } = sceneAt(scenes, t);
  const scene = scenes[index];

  if (scene.kind === 'card') {
    ctx.clearRect(0, 0, W, H);
    drawYearCard(ctx, card, cardImages);
    // fade in from the scene before
    const veil = 1 - ease(local / 500);
    if (veil > 0) {
      ctx.fillStyle = palette.from;
      ctx.globalAlpha = veil;
      ctx.fillRect(0, 0, W, H);
      ctx.globalAlpha = 1;
    }
    return;
  }

  const bg = ctx.createLinearGradient(0, 0, W * 0.7, H);
  bg.addColorStop(0, palette.from);
  bg.addColorStop(1, palette.to);
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, W, H);

  // a slow glow that drifts across the whole reel
  const drift = t / 1000;
  const gx = W * (0.5 + 0.35 * Math.sin(drift * 0.6));
  const gy = H * (0.35 + 0.2 * Math.cos(drift * 0.45));
  const glow = ctx.createRadialGradient(gx, gy, 0, gx, gy, W * 0.8);
  glow.addColorStop(0, palette.accent);
  glow.addColorStop(1, 'transparent');
  ctx.globalAlpha = 0.18;
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, W, H);
  ctx.globalAlpha = 1;

  // in over the first 600ms, out over the last 400ms
  const enter = ease(local / 600);
  const exit = scene.ms - local < 400 ? (scene.ms - local) / 400 : 1;
  const alpha = Math.max(0, Math.min(enter, exit));
  const rise = (1 - enter) * 40 * s;
  ctx.globalAlpha = alpha;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'alphabetic';

  let y = H * 0.36;
  const art = scene.art ? images.get(scene.art) : null;
  if (art) {
    const size = Math.min(W * 0.52, H * 0.34);
    const ax = (W - size) / 2;
    const ay = H * 0.16 + rise;
    ctx.save();
    ctx.beginPath();
    if (scene.kind === 'artist') ctx.arc(W / 2, ay + size / 2, size / 2, 0, Math.PI * 2);
    else ctx.roundRect(ax, ay, size, size, 28 * s);
    ctx.clip();
    ctx.drawImage(art, ax, ay, size, size);
    ctx.restore();
    y = ay + size + 90 * s;
  }

  ctx.fillStyle = palette.accent;
  ctx.font = `700 ${Math.round(30 * s)}px Inter, system-ui, sans-serif`;
  if (scene.kicker) ctx.fillText(scene.kicker.toUpperCase(), W / 2, y + rise);
  y += 92 * s;

  ctx.fillStyle = palette.text;
  let title = scene.title;
  if (scene.count !== undefined) {
    const shown = Math.round(scene.count * ease(progress * 1.6));
    title = scene.kind === 'streak' ? `${shown} days` : shown.toLocaleString();
  }
  const big = scene.kind === 'totals' || scene.kind === 'streak' ? 150 : 92;
  ctx.font = `800 ${Math.round(big * s)}px Inter, system-ui, sans-serif`;
  for (const line of wrap(ctx, title, W * 0.84)) {
    ctx.fillText(line, W / 2, y + rise);
    y += big * 1.08 * s;
  }

  if (scene.sub) {
    ctx.fillStyle = palette.muted;
    ctx.font = `500 ${Math.round(36 * s)}px Inter, system-ui, sans-serif`;
    for (const line of wrap(ctx, scene.sub, W * 0.82)) {
      ctx.fillText(line, W / 2, y + 10 * s + rise);
      y += 46 * s;
    }
  }

  if (scene.kind === 'calendar') {
    drawCalendar(ctx, year, W, H, s, progress, palette.accent, palette.tile);
  }
  ctx.globalAlpha = 1;
}

function drawCalendar(
  ctx: CanvasRenderingContext2D,
  year: YearInListening,
  W: number,
  H: number,
  s: number,
  progress: number,
  accent: string,
  tile: string,
) {
  const cal = buildCalendar(year.days ?? [], year.period.start, year.period.end);
  const cols = cal.weeks.length;
  if (!cols) return;
  const gap = 4 * s;
  const cell = Math.min((W * 0.88 - gap * (cols - 1)) / cols, 22 * s);
  const gridW = cols * cell + (cols - 1) * gap;
  const x0 = (W - gridW) / 2;
  const y0 = H * 0.58;
  const shown = Math.ceil(cols * ease(progress * 1.4));
  cal.weeks.forEach((week, wi) => {
    if (wi >= shown) return;
    week.forEach((c, di) => {
      if (!c.inYear) return;
      ctx.fillStyle = c.level ? accent : tile;
      ctx.globalAlpha = c.level ? 0.25 + 0.1875 * c.level : 1;
      ctx.beginPath();
      ctx.roundRect(x0 + wi * (cell + gap), y0 + di * (cell + gap), cell, cell, 3 * s);
      ctx.fill();
    });
  });
  ctx.globalAlpha = 1;
}

// ── record ───────────────────────────────────────────────────────────────────

export interface RecordOptions {
  card: YearCardModel;
  year: YearInListening;
  scenes: ReelScene[];
  images: Map<string, CanvasImageSource | null>;
  cardImages: (CanvasImageSource | null)[];
  /** the soundtrack's audio, mixed in when given */
  audio?: MediaStream | null;
  onProgress?: (fraction: number) => void;
  signal?: AbortSignal;
}

/** Record the reel in real time and hand back the file. */
export function recordReel(opts: RecordOptions): Promise<{ blob: Blob; ext: string }> {
  return new Promise((resolve, reject) => {
    const format = pickVideoFormat((m) => MediaRecorder.isTypeSupported(m));
    if (!format) {
      reject(new Error('This browser cannot record video'));
      return;
    }
    const canvas = document.createElement('canvas');
    canvas.width = opts.card.width;
    canvas.height = opts.card.height;
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      reject(new Error('No canvas'));
      return;
    }
    const stream = canvas.captureStream(30);
    for (const track of opts.audio?.getAudioTracks() ?? []) stream.addTrack(track);
    const recorder = new MediaRecorder(stream, {
      mimeType: format.mime,
      videoBitsPerSecond: 8_000_000,
    });
    const chunks: Blob[] = [];
    recorder.ondataavailable = (e) => {
      if (e.data.size) chunks.push(e.data);
    };
    recorder.onstop = () => {
      for (const t of stream.getTracks()) t.stop();
      if (opts.signal?.aborted) {
        reject(new DOMException('Cancelled', 'AbortError'));
        return;
      }
      resolve({ blob: new Blob(chunks, { type: format.mime.split(';')[0] }), ext: format.ext });
    };
    const total = reelDuration(opts.scenes);
    let start: number | null = null;
    const tick = (now: number) => {
      if (opts.signal?.aborted) {
        recorder.stop();
        return;
      }
      if (start === null) start = now;
      const t = now - start;
      drawReelFrame(
        ctx,
        opts.card,
        opts.year,
        opts.scenes,
        Math.min(t, total),
        opts.images,
        opts.cardImages,
      );
      opts.onProgress?.(Math.min(1, t / total));
      if (t < total) requestAnimationFrame(tick);
      else recorder.stop();
    };
    // the first frame before recording starts, so the file never opens black
    drawReelFrame(ctx, opts.card, opts.year, opts.scenes, 0, opts.images, opts.cardImages);
    recorder.start(250);
    requestAnimationFrame(tick);
  });
}
