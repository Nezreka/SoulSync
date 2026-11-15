import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * #2126: the subtitle search says when the file already has the language.
 * French subs that only live inside the video used to end in "No subtitles
 * found for French, try another language".
 */

const SRC = readFileSync(resolve(process.cwd(), 'static/video/video-subtitles.js'), 'utf8');

type Track = { where: string; bitmap?: boolean };

function note(tracks: Track[], lang = 'fr'): string {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(
    'tracks',
    'lang',
    `function esc(s) { return String(s); }
     function langLabel(c) { return c === 'fr' ? 'French' : c; }
     ${extractFunction('inFileNote', SRC)}
     return inFileNote(tracks, lang);`,
  )(tracks, lang) as string;
}

describe('subtitle search, what the file already has (#2126)', () => {
  it('says nothing when the file has none', () => {
    expect(note([])).toBe('');
  });

  it('says a track inside the file is counted', () => {
    const text = note([{ where: 'embedded', bitmap: false }]);
    expect(text).toContain('French subtitles inside it');
    expect(text).toContain('won’t keep searching');
  });

  it('says a picture track cannot become a separate file', () => {
    expect(note([{ where: 'embedded', bitmap: true }])).toContain('picture subtitles');
  });

  it('a sidecar reads as beside the file', () => {
    expect(note([{ where: 'external' }])).toBe('This file already has French subtitles beside it.');
  });

  it('the empty result uses it instead of try another language', () => {
    expect(SRC).toContain('if (!cands.length && have) {');
  });
});
