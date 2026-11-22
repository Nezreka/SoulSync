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

/** "1:02:03" or "4:05" */
export function clock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}
