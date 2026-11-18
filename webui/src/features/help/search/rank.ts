/**
 * Ranking for help search (plan 4.4): exact title, then prefix, then words,
 * a little typo tolerance, and boosts for this page and what was used
 * recently. Pure functions over pre-normalised text, so the palette can
 * rank the whole index on every key press.
 *
 * Every query word has to land somewhere (title, keywords, sub or body), or
 * the item is out. Where it lands sets the score; the title counts most.
 */

/** [start, end) into the string as shown */
export type Range = [number, number];

export interface Rankable {
  title: string;
  /** shown under or after the title; matched and highlighted */
  sub?: string;
  /** matched, never shown: synonyms, the old names of things */
  keywords?: string;
  /** long text (a docs article): matched last, and gives the snippet */
  body?: string;
}

export interface Prepared {
  title: string;
  titleWords: string[];
  sub: string;
  keywords: string;
  keywordWords: string[];
  body: string;
}

export interface Scored {
  score: number;
  /** the body excerpt the matched words sit in, when the hit was in the body */
  snippet?: string;
}

/** lowercase, accents off, punctuation to spaces; keeps string positions */
export function fold(text: string): string {
  // NFD then dropping marks changes length, so fold one character at a time
  let out = '';
  for (const ch of text) {
    const base = ch.normalize('NFD').replace(/[̀-ͯ]/g, '') || ch;
    const lower = base.toLowerCase();
    // one output character per input code unit, so ranges map straight back
    const one = /[\p{L}\p{N}]/u.test(lower[0] ?? '') ? lower[0] : ' ';
    out += ch.length === 2 ? `${one} ` : one;
  }
  return out;
}

export function words(folded: string): string[] {
  return folded.split(' ').filter(Boolean);
}

export function queryWords(query: string): string[] {
  return words(fold(query.trim())).slice(0, 8);
}

/** body text is markdown: drop the markup so a snippet reads as prose */
export function plainText(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^:::.*$/gm, ' ')
    .replace(/^>\s?\[![A-Z]+\]/gm, ' ')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*[-*]\s+/gm, '')
    .replace(/^\s*\d+[.)]\s+/gm, '')
    .replace(/^\|?[\s:|-]+\|?$/gm, ' ')
    .replace(/[*`>|_]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export function prepare(item: Rankable): Prepared {
  const title = fold(item.title);
  const keywords = fold(`${item.keywords ?? ''}`);
  return {
    title,
    titleWords: words(title),
    sub: fold(item.sub ?? ''),
    keywords,
    keywordWords: words(keywords),
    body: item.body ? fold(item.body) : '',
  };
}

/** Damerau-Levenshtein, capped: anything past `max` is max + 1 */
export function distance(a: string, b: string, max: number): number {
  if (Math.abs(a.length - b.length) > max) return max + 1;
  const prev2: number[] = [];
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i += 1) {
    const cur = [i];
    let best = i;
    for (let j = 1; j <= b.length; j += 1) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
        v = Math.min(v, prev2[j - 2] + 1);
      }
      cur[j] = v;
      if (v < best) best = v;
    }
    if (best > max) return max + 1;
    prev2.splice(0, prev2.length, ...prev);
    prev = cur;
  }
  return prev[b.length];
}

/** how many typos a word of this length may carry */
export function allowedTypos(word: string): number {
  if (word.length >= 8) return 2;
  if (word.length >= 4) return 1;
  return 0;
}

/** a word typed with a slip: "dowload", "wathclist", "jellyfn" */
function fuzzyWord(q: string, candidates: string[]): boolean {
  const max = allowedTypos(q);
  if (!max) return false;
  return candidates.some((w) => {
    if (w.length < 3) return false;
    // a prefix with a slip in it counts too: "dowl" for "download"
    const head = w.length > q.length ? w.slice(0, q.length) : w;
    return distance(q, head, max) <= max || distance(q, w, max) <= max;
  });
}

const SNIPPET_BEFORE = 36;
const SNIPPET_LENGTH = 120;

/** the bit of body text around the first matched word, cut at word edges */
export function snippetAround(original: string, folded: string, word: string): string | undefined {
  const at = findWordStart(folded, word);
  if (at < 0) return undefined;
  let start = Math.max(0, at - SNIPPET_BEFORE);
  if (start > 0) {
    const space = original.indexOf(' ', start);
    if (space > -1 && space < at) start = space + 1;
  }
  let end = Math.min(original.length, start + SNIPPET_LENGTH);
  if (end < original.length) {
    const space = original.lastIndexOf(' ', end);
    if (space > at) end = space;
  }
  const text = original.slice(start, end).trim();
  return `${start > 0 ? '…' : ''}${text}${end < original.length ? '…' : ''}`;
}

/** how often a word starts a word in folded text, counted up to 9 */
function occurrences(folded: string, word: string): number {
  let n = 0;
  let from = 0;
  while (n < 9) {
    const at = folded.indexOf(word, from);
    if (at < 0) break;
    if (at === 0 || folded[at - 1] === ' ') n += 1;
    from = at + word.length;
  }
  return n;
}

/** index of `word` at a word start in folded text, else anywhere, else -1 */
function findWordStart(folded: string, word: string): number {
  let from = 0;
  for (;;) {
    const at = folded.indexOf(word, from);
    if (at < 0) break;
    if (at === 0 || folded[at - 1] === ' ') return at;
    from = at + 1;
  }
  return folded.indexOf(word);
}

/**
 * Score one item. 0 means it does not match. `body` is the original (not
 * folded) body text, used only for the snippet.
 */
export function score(prep: Prepared, q: string[], body?: string): Scored {
  if (!q.length) return { score: 0 };
  const joined = q.join(' ');
  let total = 0;
  let snippetWord: string | null = null;

  // the whole query against the title
  if (prep.title.trim() === joined) total += 1000;
  else if (prep.title.startsWith(joined)) total += 600;
  else if (prep.title.includes(` ${joined}`)) total += 350;
  else if (prep.title.includes(joined)) total += 200;

  for (const [i, word] of q.entries()) {
    const last = i === q.length - 1;
    let best = 0;
    if (prep.titleWords.includes(word)) best = 120;
    else if (prep.titleWords.some((w) => w.startsWith(word))) best = last ? 100 : 70;
    else if (word.length >= 3 && prep.title.includes(word)) best = 45;
    const inTitle = best > 0;
    if (best < 60) {
      if (prep.keywordWords.includes(word)) best = Math.max(best, 60);
      else if (prep.keywordWords.some((w) => w.startsWith(word))) best = Math.max(best, 45);
    }
    if (best < 40 && prep.sub) {
      if (words(prep.sub).some((w) => w.startsWith(word))) best = Math.max(best, 35);
      else if (word.length >= 3 && prep.sub.includes(word)) best = Math.max(best, 20);
    }
    if (best < 30 && fuzzyWord(word, prep.titleWords)) best = 30;
    if (best < 20 && fuzzyWord(word, prep.keywordWords)) best = 20;
    if (prep.body && word.length >= 2 && !inTitle) {
      const at = findWordStart(prep.body, word);
      if (at > -1) {
        if (best < 12) best = at === 0 || prep.body[at - 1] === ' ' ? 12 : 6;
        // a guide that keeps coming back to the word is about it
        best += Math.min(18, occurrences(prep.body, word) * 2);
        // the row shows where it matched when the title doesn't say
        snippetWord ??= word;
      }
    }
    // every word has to land somewhere
    if (!best) return { score: 0 };
    total += best;
  }

  // shorter titles win a tie: "Logs" over "Logs from the last day"
  total += Math.max(0, 20 - prep.titleWords.length * 2);
  const snippet = snippetWord && body ? snippetAround(body, prep.body, snippetWord) : undefined;
  return { score: total, snippet };
}

/**
 * The ranges of `text` to highlight for the query: each word where it
 * starts a word first, else where it sits inside one. A typo match lights
 * the whole word it matched.
 */
export function highlight(text: string, q: string[]): Range[] {
  if (!text || !q.length) return [];
  const folded = fold(text);
  const textWords = wordSpans(folded);
  const ranges: Range[] = [];
  for (const word of q) {
    let found = false;
    for (const [start, end] of textWords) {
      if (folded.startsWith(word, start) && start + word.length <= end) {
        ranges.push([start, start + word.length]);
        found = true;
      }
    }
    if (found) continue;
    if (word.length >= 3) {
      let from = 0;
      for (;;) {
        const at = folded.indexOf(word, from);
        if (at < 0) break;
        ranges.push([at, at + word.length]);
        found = true;
        from = at + word.length;
      }
    }
    if (found) continue;
    const max = allowedTypos(word);
    if (!max) continue;
    for (const [start, end] of textWords) {
      const w = folded.slice(start, end);
      if (w.length < 3) continue;
      const head = w.length > word.length ? w.slice(0, word.length) : w;
      if (distance(word, head, max) <= max || distance(word, w, max) <= max) {
        ranges.push([start, end]);
      }
    }
  }
  return merge(ranges);
}

function wordSpans(folded: string): Range[] {
  const spans: Range[] = [];
  let start = -1;
  for (let i = 0; i <= folded.length; i += 1) {
    const space = i === folded.length || folded[i] === ' ';
    if (space && start > -1) {
      spans.push([start, i]);
      start = -1;
    } else if (!space && start < 0) start = i;
  }
  return spans;
}

function merge(ranges: Range[]): Range[] {
  const sorted = ranges.slice().sort((a, b) => a[0] - b[0]);
  const out: Range[] = [];
  for (const r of sorted) {
    const last = out[out.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else out.push([r[0], r[1]]);
  }
  return out;
}

// ── recently used ───────────────────────────────────────────────────────

export const RECENT_KEY = 'ss_help_recent';
const RECENT_MAX = 12;

export interface RecentEntry {
  id: string;
  at: number;
}

export function readRecent(): RecentEntry[] {
  try {
    const raw = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]') as unknown;
    if (!Array.isArray(raw)) return [];
    return raw
      .filter(
        (r): r is RecentEntry =>
          !!r &&
          typeof (r as RecentEntry).id === 'string' &&
          typeof (r as RecentEntry).at === 'number',
      )
      .slice(0, RECENT_MAX);
  } catch {
    return [];
  }
}

export function noteRecent(id: string, now = Date.now()): void {
  const next = [{ id, at: now }, ...readRecent().filter((r) => r.id !== id)].slice(0, RECENT_MAX);
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* storage blocked: nothing is remembered, nothing breaks */
  }
}

const DAY = 86_400_000;

/** a boost that fades over a fortnight; the most recent pick counts most */
export function recencyBoost(id: string, recent: RecentEntry[], now = Date.now()): number {
  const i = recent.findIndex((r) => r.id === id);
  if (i < 0) return 0;
  const age = Math.max(0, now - recent[i].at);
  const fade = Math.max(0, 1 - age / (14 * DAY));
  return Math.round((60 - i * 4) * fade);
}
