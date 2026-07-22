import { describe, expect, it } from 'vitest';

import { libraryV2SearchSchema } from './-library-v2.types';

describe('libraryV2SearchSchema', () => {
  it('accepts a positive album deep-link id', () => {
    expect(libraryV2SearchSchema.parse({ album: '42' }).album).toBe(42);
  });

  it('drops invalid album deep-link ids', () => {
    expect(libraryV2SearchSchema.parse({ album: 'not-an-id' }).album).toBeUndefined();
    expect(libraryV2SearchSchema.parse({ album: '-1' }).album).toBeUndefined();
  });

  it('accepts playlist navigation and rejects invalid playlist ids', () => {
    expect(libraryV2SearchSchema.parse({ section: 'playlists', playlist: '17' })).toMatchObject({
      section: 'playlists',
      playlist: 17,
    });
    expect(libraryV2SearchSchema.parse({ section: 'wat', playlist: '0' })).toMatchObject({
      section: 'artists',
      playlist: undefined,
    });
  });
});
