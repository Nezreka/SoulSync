/**
 * a long item's own clock. an audiobook made of several files plays one
 * file at a time, but its position, chapters and progress are on the
 * book's clock; these turn one into the other. a single file is one part
 * starting at 0, so the same code runs for everything.
 */

import type { Chapter, Part } from './types';

/** the part a book position falls in, and how far into it */
export function locate(parts: readonly Part[], at: number): { index: number; offset: number } {
  if (!parts.length) return { index: 0, offset: Math.max(0, at) };
  const t = Math.max(0, at);
  for (let i = parts.length - 1; i >= 0; i--) {
    if (t >= parts[i].start) {
      const offset = Math.min(t - parts[i].start, parts[i].duration || Infinity);
      return { index: i, offset };
    }
  }
  return { index: 0, offset: 0 };
}

/** a position inside a part, on the book's clock */
export function bookTime(parts: readonly Part[], index: number, offset: number): number {
  return (parts[index]?.start ?? 0) + Math.max(0, offset);
}

/** the chapter playing at a position (-1 before the first) */
export function chapterAt(chapters: readonly Chapter[], at: number): number {
  let found = -1;
  for (let i = 0; i < chapters.length; i++) {
    if (chapters[i].start <= at + 0.25) found = i;
    else break;
  }
  return found;
}

/** where "next chapter" goes; null after the last */
export function nextChapterStart(chapters: readonly Chapter[], at: number): number | null {
  const i = chapterAt(chapters, at);
  return chapters[i + 1]?.start ?? null;
}

/**
 * where "previous chapter" goes: the start of this one when it's been
 * playing a few seconds, else the one before (the way a cd player works)
 */
export function previousChapterStart(chapters: readonly Chapter[], at: number, grace = 3): number {
  const i = chapterAt(chapters, at);
  if (i < 0) return 0;
  if (at - chapters[i].start > grace) return chapters[i].start;
  return chapters[Math.max(0, i - 1)]?.start ?? 0;
}

/** a span the server found (intro, credits, recap), from its best source */
export interface Marker {
  kind: 'intro' | 'recap' | 'credits' | 'preview' | 'commercial';
  start: number;
  end: number;
  /** user | sidecar | detected | plex | jellyfin | chapter */
  source: string;
  label?: string;
  confidence?: number;
  /** a scene after the credits: skipping the credits lands on it */
  post_credits?: { start: number; end: number } | null;
}

/** something the file marks as skippable, and where skipping lands */
export interface Skippable {
  kind: 'intro' | 'recap' | 'credits' | 'preview';
  label: string;
  to: number;
  /** where the span starts (a "watch it" undo goes back here) */
  from: number;
  /** a scene after the credits, worth a hint */
  postCredits?: { start: number; end: number } | null;
}

const LABELS: Record<Skippable['kind'], string> = {
  recap: 'Skip recap',
  intro: 'Skip intro',
  credits: 'Skip credits',
  preview: 'Skip preview',
};

const SKIP_NAMES: [Skippable['kind'], string, RegExp][] = [
  ['recap', 'Skip recap', /^(recap|previously(\s+on\b.*)?)$/i],
  [
    'intro',
    'Skip intro',
    /^(intro(duction)?|opening( credits| titles?)?|op|main titles?|title sequence|theme song)$/i,
  ],
  ['credits', 'Skip credits', /^((end(ing)? |closing )?credits|ending|ed|outro|end titles?)$/i],
];

/** the marker playing at a position (half a second early counts), or null */
export function markerAt(markers: readonly Marker[] | undefined, at: number): Marker | null {
  for (const m of markers ?? []) {
    if (m.kind === 'commercial') continue;
    if (at >= m.start - 0.5 && at < m.end) return m;
  }
  return null;
}

/**
 * the "Skip intro" a video offers. the server's markers first (an edit, an
 * .edl, soulsync's own detection, plex or jellyfin, chapter names, in that
 * order); a file with none falls back to its own chapter names: a chapter
 * called Intro, Opening, Recap or Credits can be jumped past, to where the
 * next chapter starts. a chapter that runs longer than ten minutes isn't
 * an intro whatever it is called. credits go to the scene after them when
 * there is one, else the end (so the next episode comes up). nothing is
 * offered in a span's last two seconds.
 */
export function skippableAt(
  chapters: readonly Chapter[],
  at: number,
  duration: number,
  markers?: readonly Marker[],
): Skippable | null {
  if (markers?.length) {
    const m = markerAt(markers, at);
    if (!m) return null;
    const kind = m.kind as Skippable['kind'];
    const post = kind === 'credits' ? (m.post_credits ?? null) : null;
    const to = post ? post.start : Math.min(m.end, duration || m.end);
    if (!(to > at + 2)) return null;
    return { kind, label: m.label ?? LABELS[kind], to, from: m.start, postCredits: post };
  }
  const i = chapterAt(chapters, at);
  if (i < 0) return null;
  const name = chapters[i].title.trim();
  const found = SKIP_NAMES.find(([, , re]) => re.test(name));
  if (!found) return null;
  const to = chapters[i + 1]?.start ?? duration;
  if (!(to > at + 2) || to - chapters[i].start > 600) return null;
  return { kind: found[0], label: found[1], to, from: chapters[i].start };
}

/** where the credits start, when a marker says so (watched and up next go by it) */
export function creditsStart(markers: readonly Marker[] | undefined): number | null {
  const c = (markers ?? []).find((m) => m.kind === 'credits');
  return c ? c.start : null;
}

/** "1:02:03" or "4:05" */
export function clock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}
