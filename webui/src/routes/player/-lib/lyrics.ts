/**
 * synced lyrics (LRC): the lines and their times, and which line is being
 * sung. pure. a line with several stamps (a chorus) becomes one entry per
 * stamp so a seek lands right; metadata tags ([ti:], [ar:]) aren't lyrics.
 */

export interface LyricLine {
  time: number;
  text: string;
}

const STAMP = /\[(\d+):(\d+(?:\.\d+)?)\]/g;

export function parseLrc(synced: string | null | undefined): LyricLine[] {
  const out: LyricLine[] = [];
  if (!synced) return out;
  for (const raw of synced.split(/\r?\n/)) {
    const stamps: number[] = [];
    for (const m of raw.matchAll(STAMP)) {
      const minutes = Number.parseInt(m[1], 10);
      const seconds = Number.parseFloat(m[2]);
      if (Number.isFinite(minutes) && Number.isFinite(seconds)) stamps.push(minutes * 60 + seconds);
    }
    if (!stamps.length) continue;
    const text = raw.replace(STAMP, '').trim();
    for (const time of stamps) out.push({ time, text });
  }
  return out.sort((a, b) => a.time - b.time);
}

/** the line being sung at ``t`` (the last one started), or -1 before the first */
export function activeLine(lines: readonly LyricLine[], t: number): number {
  let lo = 0;
  let hi = lines.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].time <= t) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}

/** plain lyrics as stanzas: blank lines split them */
export function stanzas(plain: string | null | undefined): string[][] {
  if (!plain) return [];
  return plain
    .split(/\r?\n\s*\r?\n/)
    .map((block) =>
      block
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter(Boolean),
    )
    .filter((block) => block.length > 0);
}
