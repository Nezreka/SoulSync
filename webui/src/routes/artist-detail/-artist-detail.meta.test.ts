import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  artistDisplayName,
  buildIdBadges,
  ID_BADGE_SOURCES,
  visibleIdBadgeSources,
} from './-artist-detail.meta';

afterEach(() => {
  delete window.filterJiosaavnServiceEntries;
});

describe('visibleIdBadgeSources', () => {
  it('filters on svc, not key — the wrong id field silently disables it', () => {
    const filter = vi.fn((items, idKey) => items);
    window.filterJiosaavnServiceEntries = filter as never;
    visibleIdBadgeSources();
    expect(filter).toHaveBeenCalledWith(expect.anything(), 'svc');
  });

  it('drops JioSaavn when the shared helper is unavailable', () => {
    expect(visibleIdBadgeSources().map((s) => s.svc)).not.toContain('jiosaavn');
  });

  it('keeps the rest in declaration order', () => {
    expect(visibleIdBadgeSources().map((s) => s.svc)).toEqual([
      'spotify',
      'musicbrainz',
      'deezer',
      'audiodb',
      'discogs',
      'itunes',
      'lastfm',
      'genius',
      'tidal',
      'qobuz',
      'amazon',
    ]);
  });
});

describe('buildIdBadges', () => {
  it('is a THIRD provider list — no Bandcamp, no SoulID, but it has JioSaavn', () => {
    // Deliberately different from buildHeroBadges; merging them would change
    // what one of the two panels shows.
    const keys = ID_BADGE_SOURCES.map((s) => s.svc);
    expect(keys).toContain('jiosaavn');
    expect(keys).not.toContain('bandcamp');
    expect(keys).not.toContain('soulsync');
  });

  it('only badges ids the artist actually has', () => {
    const badges = buildIdBadges({ spotify_artist_id: 'sp', tidal_id: 9 });
    expect(badges.map((b) => b.svc)).toEqual(['spotify', 'tidal']);
    expect(badges.map((b) => b.value)).toEqual(['sp', '9']);
  });

  it('treats a zero id as absent', () => {
    expect(buildIdBadges({ deezer_id: 0, qobuz_id: 0 })).toEqual([]);
  });

  it('reads url fields as ids for Last.fm and Genius', () => {
    const badges = buildIdBadges({ lastfm_url: 'https://last.fm/x' });
    expect(badges[0]).toMatchObject({ svc: 'lastfm', value: 'https://last.fm/x' });
  });

  it('returns nothing for an unenriched artist', () => {
    expect(buildIdBadges({ name: 'Nobody' })).toEqual([]);
  });
});

describe('artistDisplayName', () => {
  it('falls back rather than rendering an empty heading', () => {
    expect(artistDisplayName({ name: 'Aphex Twin' })).toBe('Aphex Twin');
    expect(artistDisplayName({})).toBe('Unknown Artist');
    expect(artistDisplayName({ name: '' })).toBe('Unknown Artist');
  });
});
