/**
 * What each pill is for (about.ts): every pill on both sides has its line,
 * and each line is short, plain and in sentence case.
 */

import { describe, expect, it } from 'vitest';

import { TABS, tabDef } from '../nav';
import { ABOUT, aboutOf } from './about';

const STOP = new Set(
  'the and what where which how from with that this they your into for are you its can'.split(' '),
);

/** the share of a line's words (plurals and -ed/-ing folded) its tab's
 *  subtitle already says */
function echoes(line: string, sub: string): number {
  const words = (text: string) =>
    new Set(
      (text.toLowerCase().match(/[a-z]+/g) ?? [])
        .filter((w) => w.length > 2 && !STOP.has(w))
        .map((w) => w.replace(/(ing|ed|es|s)$/, '')),
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
      expect(echoes(line, tab!.sub), `${key}: "${line}" echoes "${tab!.sub}"`).toBeLessThan(0.5);
    }
    // the check bites: the old Downloads > Sources line repeated its subtitle
    expect(
      echoes('Where downloads come from, and in what order.', tabDef('downloads').sub),
    ).toBeGreaterThanOrEqual(0.5);
  });

  it("a kind falls back to its pill's line; an unknown pill has none", () => {
    expect(aboutOf('downloads', 'sources', 'video')).toBe(
      'The sources SoulSync searches, and the chain that tries them.',
    );
    expect(aboutOf('library', 'naming', 'video')).toBe(
      'The pattern every movie and episode file follows.',
    );
    expect(aboutOf('library', 'folders')).toBeNull();
    expect(aboutOf('nope', 'nothing')).toBeNull();
  });
});
