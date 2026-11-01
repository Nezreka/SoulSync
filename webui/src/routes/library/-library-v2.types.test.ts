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

  it('falls back to artists for an unknown section', () => {
    expect(libraryV2SearchSchema.parse({ section: 'playlists' })).toMatchObject({
      section: 'artists',
    });
  });

  it('opens the songs section, with its own filters and sort', () => {
    expect(
      libraryV2SearchSchema.parse({
        section: 'songs',
        dupes: '1',
        quality: 'lossless',
        songSort: 'album',
      }),
    ).toMatchObject({ section: 'songs', dupes: true, quality: 'lossless', songSort: 'album' });
  });

  it('defaults the old page filters to off', () => {
    expect(libraryV2SearchSchema.parse({})).toMatchObject({
      letter: 'all',
      source: '',
      quality: '',
      dupes: false,
      songSort: 'title',
    });
  });

  it('lowercases a hand-typed letter so the bar can highlight it', () => {
    expect(libraryV2SearchSchema.parse({ letter: 'C' }).letter).toBe('c');
  });

  it('maps an old view=albums bookmark onto the albums section', () => {
    expect(libraryV2SearchSchema.parse({ view: 'albums', sort: 'year_desc' })).toMatchObject({
      section: 'albums',
      albumSort: 'year_desc',
      view: 'cards',
      sort: 'name',
    });
  });

  it('maps an old view=songs bookmark, letter and source carried', () => {
    expect(
      libraryV2SearchSchema.parse({
        view: 'songs',
        sort: 'recent',
        letter: 'b',
        source: '!spotify',
      }),
    ).toMatchObject({ section: 'songs', songSort: 'recent', letter: 'b', source: '!spotify' });
  });

  it('maps the old watchlist filter and recent sort for artists', () => {
    expect(
      libraryV2SearchSchema.parse({ view: 'artists', watchlist: 'watched', sort: 'recent' }),
    ).toMatchObject({ section: 'artists', monitored: 'monitored', sort: 'added' });
  });

  it("leaves this page's own view values alone", () => {
    expect(libraryV2SearchSchema.parse({ view: 'table', sort: 'tracks' })).toMatchObject({
      section: 'artists',
      view: 'table',
      sort: 'tracks',
    });
  });
});
