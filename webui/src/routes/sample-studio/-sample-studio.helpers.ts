/**
 * Sample Studio — pure helpers. No React, no DOM: everything here is
 * unit-testable (-sample-studio.helpers.test.ts).
 */

/** "m:ss.t" — 83.456 -> "1:23.5" */
export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00.0';
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  const whole = Math.floor(s);
  const tenth = Math.floor((s - whole) * 10);
  return `${m}:${String(whole).padStart(2, '0')}.${tenth}`;
}

/** Seconds per beat for a BPM. Null-safe: 0/negative/invalid BPM -> null. */
export function beatInterval(bpm: number | null | undefined): number | null {
  if (typeof bpm !== 'number' || !Number.isFinite(bpm) || bpm <= 0) return null;
  return 60 / bpm;
}

/**
 * Bar/beat position of a time in seconds: { bar (1-based), beat (1-based), beatIndex }.
 * Null when BPM is unknown.
 */
export function barBeatAt(
  seconds: number,
  bpm: number | null | undefined,
  beatsPerBar = 4,
): { bar: number; beat: number; beatIndex: number } | null {
  const interval = beatInterval(bpm);
  if (interval === null) return null;
  const beatIndex = Math.max(0, Math.floor(seconds / interval));
  return {
    bar: Math.floor(beatIndex / beatsPerBar) + 1,
    beat: (beatIndex % beatsPerBar) + 1,
    beatIndex,
  };
}

/** Start time (seconds) of bar N (1-based). Null when BPM is unknown. */
export function barStartTime(
  bar: number,
  bpm: number | null | undefined,
  beatsPerBar = 4,
): number | null {
  const interval = beatInterval(bpm);
  if (interval === null) return null;
  return (bar - 1) * beatsPerBar * interval;
}

/** Total whole bars in a duration. Null when BPM is unknown. */
export function barCount(
  durationS: number | null | undefined,
  bpm: number | null | undefined,
  beatsPerBar = 4,
): number | null {
  const interval = beatInterval(bpm);
  if (interval === null || typeof durationS !== 'number' || durationS <= 0) return null;
  return Math.floor(durationS / (beatsPerBar * interval));
}

/**
 * Slice a peaks array down to the visible time window before downsampling,
 * so a zoomed view renders the zoomed audio instead of the whole track.
 */
export function visiblePeakSlice(
  min: number[],
  max: number[],
  duration: number,
  viewStart: number,
  viewEnd: number,
): { min: number[]; max: number[] } {
  const n = Math.min(min.length, max.length);
  if (n === 0 || duration <= 0) return { min: [], max: [] };
  const i0 = clamp(Math.floor((viewStart / duration) * n), 0, n - 1);
  const i1 = clamp(Math.ceil((viewEnd / duration) * n), i0 + 1, n);
  return { min: min.slice(i0, i1), max: max.slice(i0, i1) };
}

/**
 * Downsample a peaks array to exactly `target` buckets by taking the
 * min-of-mins / max-of-maxes per window. Used when the cached peaks
 * (e.g. 1500 buckets) don't match the canvas pixel width.
 */
export function downsamplePeaks(
  min: number[],
  max: number[],
  target: number,
): { min: number[]; max: number[] } {
  if (target <= 0) return { min: [], max: [] };
  const n = Math.min(min.length, max.length);
  if (n === 0) return { min: [], max: [] };
  if (target >= n) return { min: min.slice(0, n), max: max.slice(0, n) };
  const outMin: number[] = Array.from({ length: target }, () => 0);
  const outMax: number[] = Array.from({ length: target }, () => 0);
  for (let i = 0; i < target; i++) {
    const start = Math.floor((i * n) / target);
    const end = Math.max(start + 1, Math.floor(((i + 1) * n) / target));
    let lo = Infinity;
    let hi = -Infinity;
    for (let j = start; j < end; j++) {
      if (min[j] < lo) lo = min[j];
      if (max[j] > hi) hi = max[j];
    }
    outMin[i] = lo === Infinity ? 0 : lo;
    outMax[i] = hi === -Infinity ? 0 : hi;
  }
  return { min: outMin, max: outMax };
}

export type QualityTier = 'hires' | 'lossless' | 'high' | 'other' | 'unknown';

/**
 * Quality tier from bitrate (kbps) + file extension.
 * Mirrors the design's quality badges: Hi-Res 24-bit / Lossless 16-bit+ / 320 kbps+.
 */
export function qualityTier(
  bitrate: number | null | undefined,
  filePath: string | null | undefined,
): QualityTier {
  const ext = (filePath ?? '').split('.').pop()?.toLowerCase() ?? '';
  const kbps = typeof bitrate === 'number' && Number.isFinite(bitrate) ? bitrate : null;
  if (!ext && kbps === null) return 'unknown';
  // Lossless containers first — extension is the reliable signal.
  if (ext === 'flac' || ext === 'alac' || ext === 'wav' || ext === 'aiff') {
    // "Hi-Res" needs a real bitrate signal: > ~2000 kbps implies >16/44.1.
    if (kbps !== null && kbps > 2000) return 'hires';
    return 'lossless';
  }
  if (kbps !== null) {
    if (kbps >= 320) return 'high';
    return 'other';
  }
  return ext === 'mp3' || ext === 'aac' || ext === 'm4a' || ext === 'ogg' ? 'other' : 'unknown';
}

export const QUALITY_TIER_LABEL: Record<Exclude<QualityTier, 'unknown'>, string> = {
  hires: 'Hi-Res 24-bit',
  lossless: 'Lossless 16-bit+',
  high: '320 kbps+',
  other: 'Standard',
};

/** Tempo bucket label for a BPM, used by the tempo filter. */
export function tempoBucket(
  bpm: number | null | undefined,
): 'slow' | 'mid' | 'fast' | 'fastest' | null {
  if (typeof bpm !== 'number' || !Number.isFinite(bpm) || bpm <= 0) return null;
  if (bpm < 100) return 'slow';
  if (bpm < 120) return 'mid';
  if (bpm < 140) return 'fast';
  return 'fastest';
}

/** Clamp a number into [lo, hi]. */
export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

/**
 * Transient slices of the in/out region: consecutive onset pairs inside the
 * region become slices; the last slice runs to the out-point. When there are
 * no onsets in the region, the whole region is one slice.
 */
export function slicesFromOnsets(
  onsets: number[],
  inPoint: number,
  outPoint: number,
): { start: number; end: number }[] {
  const lo = Math.min(inPoint, outPoint);
  const hi = Math.max(inPoint, outPoint);
  if (!(hi > lo)) return [];
  const inside = onsets.filter((t) => t > lo + 0.005 && t < hi - 0.005).sort((a, b) => a - b);
  if (inside.length === 0) return [{ start: lo, end: hi }];
  const bounds = [lo, ...inside, hi];
  const slices: { start: number; end: number }[] = [];
  for (let i = 0; i < bounds.length - 1; i++) {
    if (bounds[i + 1] - bounds[i] > 0.005) slices.push({ start: bounds[i], end: bounds[i + 1] });
  }
  return slices.length > 0 ? slices : [{ start: lo, end: hi }];
}

/** Auto-suggested chop name: "Track Title · 0:12 chop" style. */
export function suggestChopName(trackTitle: string | null | undefined, startS: number): string {
  const title = (trackTitle || 'Untitled').trim() || 'Untitled';
  const total = Math.max(0, Math.round(startS));
  const stamp = `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
  return `${title} · ${stamp} chop`;
}

export interface SuggestedChop {
  start: number;
  end: number;
  /** "Bars 24–25" when BPM is known, otherwise "0:12.0–0:16.5". */
  label: string;
  score: number;
}

export interface SuggestChopsOptions {
  bpm: number | null | undefined;
  durationS: number | null | undefined;
  peaks: { min: number[]; max: number[] } | null | undefined;
  onsets: number[];
  count?: number;
  /** Musical unit per suggestion when BPM is known. */
  barsPerChop?: number;
}

/** Mean peak amplitude of the [t0, t1) window; 1 when peaks are missing. */
function windowEnergy(
  peaks: { min: number[]; max: number[] } | null | undefined,
  durationS: number,
  t0: number,
  t1: number,
): number {
  const n = Math.min(peaks?.min.length ?? 0, peaks?.max.length ?? 0);
  if (!peaks || n === 0 || durationS <= 0 || !(t1 > t0)) return 1;
  const i0 = clamp(Math.floor((t0 / durationS) * n), 0, n - 1);
  const i1 = clamp(Math.ceil((t1 / durationS) * n), i0 + 1, n);
  let sum = 0;
  for (let i = i0; i < i1; i++) {
    const lo = Math.abs(peaks.min[i]);
    const hi = Math.abs(peaks.max[i]);
    sum += Math.max(lo, hi);
  }
  return sum / (i1 - i0);
}

function onsetsInWindow(onsets: number[], t0: number, t1: number): number {
  let c = 0;
  for (const t of onsets) if (t >= t0 && t < t1) c++;
  return c;
}

/**
 * Smart default chops: the most promising regions of the track, scored by
 * loudness × transient density. With a known BPM these are musical units
 * (2-bar phrases on the bar grid); without BPM they fall back to loud
 * time windows. The full transient firehose stays available behind
 * "show all" — this is the curated default a first-time user sees.
 *
 * Deterministic: same input -> same output, sorted by score descending.
 */
export function suggestChops(opts: SuggestChopsOptions): SuggestedChop[] {
  const { bpm, peaks, onsets } = opts;
  const durationS = opts.durationS ?? 0;
  const count = Math.max(1, Math.min(opts.count ?? 8, 32));
  const barsPerChop = Math.max(1, opts.barsPerChop ?? 2);
  if (!(durationS > 0)) return [];

  interface Candidate extends SuggestedChop {
    key: number;
  }
  const candidates: Candidate[] = [];
  const interval = beatInterval(bpm);

  if (interval !== null) {
    const barLen = 4 * interval;
    const nBars = Math.floor(durationS / barLen);
    for (let b = 1; b <= nBars; b++) {
      const start = (b - 1) * barLen;
      const end = Math.min(start + barsPerChop * barLen, durationS);
      if (end - start < barLen * 0.5) continue;
      const lastBar = Math.min(b + barsPerChop - 1, nBars);
      const energy = windowEnergy(peaks, durationS, start, end);
      const density = onsetsInWindow(onsets, start, end);
      candidates.push({
        start,
        end,
        label: `Bars ${b}–${lastBar}`,
        score: energy * (1 + density / 4),
        key: b,
      });
    }
  } else {
    // No tempo: score loud windows of a few seconds each.
    const windowS = Math.max(2, durationS / (count * 2));
    const n = Math.floor(durationS / windowS);
    for (let w = 0; w < n; w++) {
      const start = w * windowS;
      const end = Math.min(start + windowS, durationS);
      const energy = windowEnergy(peaks, durationS, start, end);
      const density = onsetsInWindow(onsets, start, end);
      candidates.push({
        start,
        end,
        label: `${formatTime(start)}–${formatTime(end)}`,
        score: energy * (1 + density / 4),
        key: w,
      });
    }
  }

  // Greedy top-N with no overlaps (bar-granular when BPM is known).
  candidates.sort((a, z) => z.score - a.score);
  const picked: Candidate[] = [];
  for (const c of candidates) {
    if (picked.length >= count) break;
    const overlaps = picked.some((p) =>
      interval !== null
        ? Math.abs(p.key - c.key) < barsPerChop
        : !(c.end <= p.start || c.start >= p.end),
    );
    if (!overlaps) picked.push(c);
  }
  return picked.map(({ start, end, label, score }) => ({ start, end, label, score }));
}
