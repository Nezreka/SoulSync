/**
 * What each pill is for (about.ts): every pill on both sides has its line,
 * and each line is short, plain and in sentence case.
 */

import { describe, expect, it } from 'vitest';

import { TABS, tabDef } from '../nav';
import { ABOUT, aboutOf } from './about';

const STOP = new Set(
  (
    'the and what where which how from with that this they your into for are you its can ' +
    'soulsync them their each every'
  ).split(' '),
);

/** plurals, -ed and -ing off, then a doubled last letter and a final e:
 *  skips / skipped, tags / tagged and live / lives each fold to one word */
function stem(word: string): string {
  let w = word.replace(/(ing|ed|es|s)$/, '');
  w = w.replace(/([b-df-hj-np-tv-z])\1$/, '$1');
  return w.length > 3 ? w.replace(/e$/, '') : w;
}

/** words that say the same thing here: a pill line that swaps one for
 *  the other still repeats its subtitle ("the chain that tries them" is
 *  "in what order", "sign in" is "the accounts") */
const SAME = [
  'order chain tries first next rank',
  'source come',
  'skip filter',
  'account sign login',
  'name naming',
];
const CONCEPT = new Map<string, string>();
for (const group of SAME) {
  const [head, ...rest] = group.split(' ');
  for (const w of [head, ...rest]) CONCEPT.set(stem(w), stem(head));
}

/** the share of a line's words (folded as above) its tab's subtitle
 *  already says */
function echoes(line: string, sub: string): number {
  const words = (text: string) =>
    new Set(
      (text.toLowerCase().match(/[a-z]+/g) ?? [])
        .filter((w) => w.length > 2 && !STOP.has(w))
        .map((w) => CONCEPT.get(stem(w)) ?? stem(w)),
    );
  const mine = words(line);
  const theirs = words(sub);
  return [...mine].filter((w) => theirs.has(w)).length / Math.max(1, mine.size);
}

const LIBRARY_KINDS: Record<string, string[]> = {
  folders: ['music', 'audiobooks', 'podcasts', 'video'],
  naming: ['music', 'audiobooks', 'podcasts', 'video'],
  tags: ['music', 'audiobooks', 'video'],
  import: ['music', 'audiobooks', 'podcasts', 'video'],
};

describe('what each pill is for', () => {
  it('every pill has its line; a Library pill has one for each kind', () => {
    for (const tab of TABS) {
      // a tab with one view says what it is for in its subtitle
      if (tab.views.length === 1) continue;
      for (const view of tab.views) {
        const kinds = tab.key === 'library' ? LIBRARY_KINDS[view.key] : undefined;
        if (kinds)
          for (const kind of kinds)
            expect(aboutOf(tab.key, view.key, kind), `${tab.key}/${view.key}/${kind}`).toBeTruthy();
        else expect(aboutOf(tab.key, view.key), `${tab.key}/${view.key}`).toBeTruthy();
      }
    }
  });

  it('each is one short plain line: 6 to 12 words, sentence case, no em dash', () => {
    for (const [key, line] of Object.entries(ABOUT)) {
      const words = line.split(/\s+/).length;
      expect(words, `${key}: ${line}`).toBeGreaterThanOrEqual(6);
      expect(words, `${key}: ${line}`).toBeLessThanOrEqual(12);
      expect(line, key).toMatch(
        /^[A-Z][^A-Z]*(?:(?:SoulSync|MetaSync|TV|NFO|M3U|YouTube|Import|Now)[^A-Z]*)*\.$/,
      );
      expect(line, key).not.toContain('—');
    }
  });

  it('a tab with one view has no line: its subtitle is enough', () => {
    for (const tab of TABS.filter((t) => t.views.length === 1))
      expect(
        Object.keys(ABOUT).filter((key) => key.startsWith(`${tab.key}/`)),
        tab.key,
      ).toEqual([]);
  });

  it("no pill line repeats its tab's subtitle", () => {
    for (const [key, line] of Object.entries(ABOUT)) {
      const tab = TABS.find((t) => t.key === key.split('/')[0]);
      expect(tab, key).toBeTruthy();
      expect(echoes(line, tab!.sub), `${key}: "${line}" echoes "${tab!.sub}"`).toBeLessThan(0.4);
    }
    // the check bites on every line that said its subtitle again, in other words too
    const old: Array<[Parameters<typeof tabDef>[0], string]> = [
      ['downloads', 'Where downloads come from, and in what order.'],
      ['downloads', 'The sources SoulSync searches, and the chain that tries them.'],
      ['downloads', 'Which files a download skips, and which one wins.'],
      ['connections', 'Pick the media server and sign in to each service.'],
    ];
    for (const [tab, line] of old)
      expect(echoes(line, tabDef(tab).sub), line).toBeGreaterThanOrEqual(0.4);
  });

  it('folds word forms and same-meaning words before comparing', () => {
    expect(stem('skipped')).toBe(stem('skips'));
    expect(stem('tagged')).toBe(stem('tags'));
    expect(stem('lives')).toBe(stem('live'));
    expect(echoes('The chain that tries them.', 'In what order.')).toBe(1);
    expect(echoes('Sign in.', 'The accounts.')).toBe(1);
  });

  it("a kind falls back to its pill's line; an unknown pill has none", () => {
    expect(aboutOf('downloads', 'sources', 'video')).toBe(
      'Connect each one, tune its settings, and test them all.',
    );
    expect(aboutOf('library', 'naming', 'video')).toBe(
      'The pattern every movie and episode file follows.',
    );
    expect(aboutOf('library', 'folders')).toBeNull();
    expect(aboutOf('nope', 'nothing')).toBeNull();
  });
});
