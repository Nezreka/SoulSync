/**
 * how the header search ranks what it finds. everything is matched on a
 * folded form (no accents, no punctuation, "&" is "and"), so "amelie"
 * finds "Amélie", "spiderman" finds "Spider-Man", and "jay z" finds
 * "JAY-Z". a leading "the" never matters.
 *
 * best to worst: the whole title, the start of the title, the start of a
 * word, all of the words anywhere, a run inside, then a typo or two away.
 * a year in the query ("alien 1979") counts toward the title that has it.
 */

export interface Query {
  /** the folded words without the year */
  text: string;
  words: string[];
  year: number | null;
}

/** lowercase, accents off, punctuation to spaces, "&" to "and", no leading "the" */
export function fold(value: string | null | undefined): string {
  if (!value) return '';
  return value
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/&/g, ' and ')
    .replace(/['’`]/g, '')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim()
    .replace(/^the /, '');
}

/** the same text with its spaces gone: "spider man" matches "spiderman" */
const squash = (value: string) => value.replace(/ /g, '');

export function parseQuery(raw: string): Query {
  const folded = fold(raw);
  const match = /(?:^| )((?:18|19|20)\d\d)$/.exec(folded);
  const year = match && folded !== match[1] ? Number(match[1]) : null;
  const text = year ? folded.slice(0, match!.index).trim() : folded;
  return { text, words: text ? text.split(' ') : [], year };
}

/** levenshtein with an early exit once it's past max */
export function editDistance(a: string, b: string, max = 2): number {
  if (Math.abs(a.length - b.length) > max) return max + 1;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const row = [i];
    let best = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      row[j] = Math.min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + cost);
      best = Math.min(best, row[j]);
    }
    if (best > max) return max + 1;
    prev = row;
  }
  return prev[b.length];
}

/** a word in the query is "close" to a word in the title: a typo away */
function closeWord(q: string, words: string[]): boolean {
  if (q.length < 4) return false;
  const max = q.length >= 8 ? 2 : 1;
  return words.some(
    (w) =>
      editDistance(q, w, max) <= max ||
      (w.length > q.length && editDistance(q, w.slice(0, q.length), 1) <= 1),
  );
}

/**
 * how well one field matches, 0 for no match. 100 is the whole title.
 */
export function scoreText(value: string | null | undefined, q: Query): number {
  if (!q.text) return 0;
  const t = fold(value);
  if (!t) return 0;
  if (t === q.text || squash(t) === squash(q.text)) return 100;
  if (t.startsWith(q.text)) return 80;
  const words = t.split(' ');
  if (words.some((w) => w.startsWith(q.text))) return 65;
  if (q.words.length > 1 && q.words.every((qw) => words.some((w) => w.startsWith(qw)))) return 55;
  if (t.includes(q.text) || squash(t).includes(squash(q.text))) return 45;
  if (q.words.every((qw) => words.some((w) => w.startsWith(qw)) || closeWord(qw, words))) return 25;
  return 0;
}

/**
 * a hit's score from its fields: the main one counts in full, the others
 * (an artist on an album, a channel on a video) a little less. a matching
 * year lifts it; a different year for a query that names one sinks it.
 */
export function scoreHit(
  q: Query,
  main: string | null | undefined,
  others: (string | null | undefined)[] = [],
  year?: number | string | null,
): number {
  let best = scoreText(main, q);
  for (const other of others) best = Math.max(best, scoreText(other, q) * 0.7);
  if (!best) return 0;
  // among equals, the shorter title is the closer one ("Alien" before "Alien Nation")
  best -= Math.min(fold(main).length / 200, 0.9);
  if (q.year) {
    const y = Number(year);
    if (y === q.year) best += 30;
    else if (y) best -= 20;
  }
  return best;
}

/** best first; ties keep their order (the caller's own ranking: popularity, recency) */
export function rank<T>(items: readonly T[], score: (item: T) => number, limit = Infinity): T[] {
  return items
    .map((item, i) => ({ item, i, s: score(item) }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s || a.i - b.i)
    .slice(0, limit)
    .map((x) => x.item);
}
