/** ranking for help search: title before body, prefixes, typos, recency, highlights */

import { afterEach, describe, expect, it } from 'vitest';

import {
  RECENT_KEY,
  distance,
  fold,
  highlight,
  noteRecent,
  plainText,
  prepare,
  queryWords,
  readRecent,
  recencyBoost,
  score,
  snippetAround,
  type Rankable,
} from './rank';

const rank = (items: Rankable[], query: string) =>
  items
    .map((item) => ({ item, s: score(prepare(item), queryWords(query), item.body).score }))
    .filter((r) => r.s > 0)
    .sort((a, b) => b.s - a.s)
    .map((r) => r.item.title);

afterEach(() => localStorage.removeItem(RECENT_KEY));

describe('folding', () => {
  it('lowercases, drops accents and punctuation, and keeps every position', () => {
    const text = 'Café: Beyoncé’s “Déjà Vu”';
    const folded = fold(text);
    expect(folded).toHaveLength(text.length);
    expect(folded).toBe('cafe  beyonce s  deja vu ');
  });

  it('keeps positions through characters outside the basic plane', () => {
    const text = 'Mix 🎵 Rock';
    expect(fold(text)).toHaveLength(text.length);
    expect(highlight(text, ['rock'])).toEqual([[text.indexOf('Rock'), text.indexOf('Rock') + 4]]);
  });
});

describe('scoring', () => {
  const items: Rankable[] = [
    { title: 'Download path', sub: 'Settings › Library' },
    { title: 'Downloads', sub: 'Every download, live' },
    { title: 'Folder Setup (Downloads & Transfer)', body: 'the download path must be reachable' },
    { title: 'Transfer path', keywords: 'library folder output' },
    { title: 'Docker & Deployment', body: 'map the same host path into both containers' },
  ];

  it('an exact title beats a prefix, a prefix beats a word, a word beats the body', () => {
    expect(rank(items, 'downloads')[0]).toBe('Downloads');
    expect(rank(items, 'download p')[0]).toBe('Download path');
    const path = rank(items, 'path');
    expect(path.slice(0, 2).sort()).toEqual(['Download path', 'Transfer path']);
    expect(path.indexOf('Docker & Deployment')).toBeGreaterThan(path.indexOf('Transfer path'));
  });

  it('every word has to land somewhere', () => {
    expect(rank(items, 'download banana')).toEqual([]);
    expect(rank(items, 'transfer output')).toEqual(['Transfer path']);
  });

  it('forgives a typo in a longer word, never in a short one', () => {
    expect(rank(items, 'dowload')).toContain('Download path');
    expect(rank(items, 'trasnfer')).toContain('Transfer path');
    expect(rank(items, 'pth')).toEqual([]);
  });

  it('a body match gives a snippet around the word', () => {
    const doc = items[4];
    const s = score(prepare(doc), queryWords('containers'), doc.body);
    expect(s.score).toBeGreaterThan(0);
    expect(s.snippet).toContain('containers');
  });

  it('distance counts a swap as one', () => {
    expect(distance('wathc', 'watch', 2)).toBe(1);
    expect(distance('abc', 'xyz', 1)).toBe(2);
  });
});

describe('highlighting', () => {
  it('marks word starts first, and the whole word a typo matched', () => {
    expect(highlight('Quick scan the library', ['scan'])).toEqual([[6, 10]]);
    expect(highlight('Release Scanning', ['scan'])).toEqual([[8, 12]]);
    expect(highlight('Download path', ['dowload'])).toEqual([[0, 8]]);
    expect(highlight('Nothing here', ['scan'])).toEqual([]);
  });

  it('merges overlapping words', () => {
    expect(highlight('Download path', ['down', 'download'])).toEqual([[0, 8]]);
  });
});

describe('snippets and markdown', () => {
  it('cuts around the word at word edges', () => {
    const text = `${'word '.repeat(40)}target ${'tail '.repeat(40)}`.trim();
    const snip = snippetAround(text, fold(text), 'target')!;
    expect(snip.startsWith('…')).toBe(true);
    expect(snip.endsWith('…')).toBe(true);
    expect(snip).toContain('target');
  });

  it('reads markdown as prose', () => {
    expect(
      plainText('## Head\n\n- **Bold** and [a link](#gs-docker)\n```\ncode\n```\n> [!TIP] Hi'),
    ).toBe('Head Bold and a link Hi');
  });
});

describe('recently used', () => {
  it('the latest pick gets the biggest boost, and it fades', () => {
    const now = Date.UTC(2026, 10, 17);
    noteRecent('a', now - 1000);
    noteRecent('b', now);
    const recent = readRecent();
    expect(recent.map((r) => r.id)).toEqual(['b', 'a']);
    expect(recencyBoost('b', recent, now)).toBeGreaterThan(recencyBoost('a', recent, now));
    expect(recencyBoost('b', recent, now + 15 * 86_400_000)).toBe(0);
    expect(recencyBoost('zzz', recent, now)).toBe(0);
  });

  it('a broken store reads as nothing', () => {
    localStorage.setItem(RECENT_KEY, '{nope');
    expect(readRecent()).toEqual([]);
  });
});
