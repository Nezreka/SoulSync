import { describe, expect, it } from 'vitest';

import { inTitles, titleIdOf } from './browse';

describe('continue watching on a library page', () => {
  const fourK = [{ id: 12 }, { id: 40 }];
  it('keeps only what is in the library', () => {
    expect(inTitles({ href: '/player/movies/12' }, fourK)).toBe(true);
    expect(inTitles({ href: '/player/movies/99' }, fourK)).toBe(false);
    expect(inTitles({ href: '/player/tv/40?season=2' }, fourK)).toBe(true);
    expect(inTitles({}, fourK)).toBe(false);
  });
  it('reads the title from the link', () => {
    expect(titleIdOf({ href: '/player/tv/7?season=1' })).toBe(7);
    expect(titleIdOf({ href: '/player/music/3' })).toBeNull();
  });
});
