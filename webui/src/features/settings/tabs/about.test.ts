/**
 * What each pill is for (about.ts): every pill on both sides has its line,
 * and each line is short, plain and in sentence case.
 */

import { describe, expect, it } from 'vitest';

import { TABS } from '../nav';
import { ABOUT, aboutOf } from './about';

const LIBRARY_KINDS: Record<string, string[]> = {
  folders: ['music', 'audiobooks', 'podcasts', 'video'],
  naming: ['music', 'audiobooks', 'podcasts', 'video'],
  tags: ['music', 'audiobooks', 'video'],
  import: ['music', 'audiobooks', 'podcasts', 'video'],
};

describe('what each pill is for', () => {
  it('every pill has its line; a Library pill has one for each kind', () => {
    for (const tab of TABS) {
      // People has its own home page, with its own summary
      if (tab.key === 'people') continue;
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

  it("a kind falls back to its pill's line; an unknown pill has none", () => {
    expect(aboutOf('downloads', 'sources', 'video')).toBe(
      'Where downloads come from, and in what order.',
    );
    expect(aboutOf('library', 'naming', 'video')).toBe(
      'How movie and episode files and folders are named.',
    );
    expect(aboutOf('library', 'folders')).toBeNull();
    expect(aboutOf('nope', 'nothing')).toBeNull();
  });
});
