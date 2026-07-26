import { describe, expect, it } from 'vitest';

import type { WatchlistArtist } from './-watchlist.types';

import {
  artistPills,
  artistSourceKeys,
  filterArtists,
  formatArtistCount,
  formatCountdown,
  formatRelativeScanTime,
  formatTimeAgo,
  primaryArtistId,
  sortArtists,
  timestampValue,
} from './-watchlist.helpers';

function artist(overrides: Partial<WatchlistArtist> = {}): WatchlistArtist {
  return {
    id: 1,
    artist_name: 'Boards of Canada',
    date_added: null,
    last_scan_timestamp: null,
    created_at: null,
    updated_at: null,
    image_url: null,
    spotify_artist_id: null,
    itunes_artist_id: null,
    deezer_artist_id: null,
    discogs_artist_id: null,
    musicbrainz_artist_id: null,
    amazon_artist_id: null,
    include_albums: false,
    include_eps: false,
    include_singles: false,
    include_live: false,
    include_remixes: false,
    include_acoustic: false,
    include_compilations: false,
    ...overrides,
  };
}

describe('primaryArtistId', () => {
  it('prefers Spotify, then iTunes, then the rest in provider order', () => {
    // The order is load-bearing: it decides which config the gear opens for an
    // artist matched on more than one provider.
    expect(primaryArtistId(artist({ spotify_artist_id: 'sp1', deezer_artist_id: 'dz1' }))).toBe(
      'sp1',
    );
    expect(primaryArtistId(artist({ itunes_artist_id: 'it1', deezer_artist_id: 'dz1' }))).toBe(
      'it1',
    );
    expect(primaryArtistId(artist({ musicbrainz_artist_id: 'mb1', amazon_artist_id: 'am1' }))).toBe(
      'mb1',
    );
  });

  it('is null when the artist has no provider id at all', () => {
    // The server permits this — a library-only artist backfilled without a
    // match — and the card must not render `undefined` into a data attribute.
    expect(primaryArtistId(artist())).toBeNull();
  });

  it('ignores empty and whitespace-only ids', () => {
    expect(primaryArtistId(artist({ spotify_artist_id: '', deezer_artist_id: 'dz1' }))).toBe('dz1');
    expect(primaryArtistId(artist({ spotify_artist_id: '   ', deezer_artist_id: 'dz1' }))).toBe(
      'dz1',
    );
  });
});

describe('artistSourceKeys', () => {
  it('lists only populated providers, in a stable order', () => {
    expect(
      artistSourceKeys(
        artist({ amazon_artist_id: 'am1', spotify_artist_id: 'sp1', deezer_artist_id: 'dz1' }),
      ),
    ).toEqual(['spotify_artist_id', 'deezer_artist_id', 'amazon_artist_id']);
  });

  it('is empty for an unmatched artist', () => {
    expect(artistSourceKeys(artist())).toEqual([]);
  });
});

describe('timestampValue', () => {
  it('is 0 for absent or unparseable timestamps', () => {
    expect(timestampValue(null)).toBe(0);
    expect(timestampValue(undefined)).toBe(0);
    expect(timestampValue('')).toBe(0);
    expect(timestampValue('not a date')).toBe(0);
  });

  it('parses an ISO timestamp', () => {
    expect(timestampValue('2026-01-02T03:04:05Z')).toBe(Date.parse('2026-01-02T03:04:05Z'));
  });
});

describe('sortArtists', () => {
  const a = artist({
    id: 1,
    artist_name: 'Aphex Twin',
    last_scan_timestamp: '2026-01-03T00:00:00Z',
    date_added: '2026-01-01T00:00:00Z',
  });
  const b = artist({
    id: 2,
    artist_name: 'boards of canada',
    last_scan_timestamp: '2026-01-01T00:00:00Z',
    date_added: '2026-01-03T00:00:00Z',
  });
  const c = artist({
    id: 3,
    artist_name: 'Clark',
    last_scan_timestamp: null,
    date_added: '2026-01-02T00:00:00Z',
  });

  it('sorts by name case-insensitively', () => {
    // 'boards' lowercase must still land between Aphex and Clark.
    expect(sortArtists([c, b, a], 'name-asc').map((x) => x.id)).toEqual([1, 2, 3]);
    expect(sortArtists([a, b, c], 'name-desc').map((x) => x.id)).toEqual([3, 2, 1]);
  });

  it('floats never-scanned artists to the top of Oldest Scanned', () => {
    // They are the most overdue for a scan, so this is the useful ordering.
    expect(sortArtists([a, b, c], 'scan-oldest').map((x) => x.id)).toEqual([3, 2, 1]);
  });

  it('sorts by most recent scan and most recent add', () => {
    expect(sortArtists([b, c, a], 'scan-newest').map((x) => x.id)).toEqual([1, 2, 3]);
    expect(sortArtists([a, c, b], 'added-newest').map((x) => x.id)).toEqual([2, 3, 1]);
  });

  it('does not mutate the array it was given', () => {
    // The input is the react-query cache's array; sorting it in place would
    // mutate cached state and desync other readers.
    const input = [c, a, b];
    const snapshot = [...input];
    sortArtists(input, 'name-asc');
    expect(input).toEqual(snapshot);
  });
});

describe('filterArtists', () => {
  const list = [
    artist({ id: 1, artist_name: 'Aphex Twin' }),
    artist({ id: 2, artist_name: 'Boards of Canada' }),
  ];

  it('matches case-insensitively on a substring', () => {
    expect(filterArtists(list, 'aphex').map((x) => x.id)).toEqual([1]);
    expect(filterArtists(list, 'CANADA').map((x) => x.id)).toEqual([2]);
    expect(filterArtists(list, 'of').map((x) => x.id)).toEqual([2]);
  });

  it('returns everything for an empty or whitespace query', () => {
    expect(filterArtists(list, '')).toHaveLength(2);
    expect(filterArtists(list, '   ')).toHaveLength(2);
  });

  it('returns nothing when nothing matches', () => {
    expect(filterArtists(list, 'zzz')).toEqual([]);
  });
});

describe('formatRelativeScanTime', () => {
  const now = Date.parse('2026-07-25T12:00:00Z');

  it('reports never for a missing timestamp', () => {
    expect(formatRelativeScanTime(null, now)).toBe('Never scanned');
    expect(formatRelativeScanTime('nonsense', now)).toBe('Never scanned');
  });

  it('steps through minutes, hours, days and months', () => {
    expect(formatRelativeScanTime('2026-07-25T11:59:30Z', now)).toBe('Scanned just now');
    expect(formatRelativeScanTime('2026-07-25T11:30:00Z', now)).toBe('Scanned 30m ago');
    expect(formatRelativeScanTime('2026-07-25T09:00:00Z', now)).toBe('Scanned 3h ago');
    expect(formatRelativeScanTime('2026-07-20T12:00:00Z', now)).toBe('Scanned 5d ago');
    expect(formatRelativeScanTime('2026-05-25T12:00:00Z', now)).toBe('Scanned 2mo ago');
  });
});

describe('formatTimeAgo', () => {
  const now = Date.parse('2026-07-25T12:00:00Z');

  it('names yesterday rather than counting a day', () => {
    expect(formatTimeAgo('2026-07-24T11:00:00Z', now)).toBe('yesterday');
  });

  it('steps through minutes, hours and days', () => {
    expect(formatTimeAgo('2026-07-25T11:59:30Z', now)).toBe('just now');
    expect(formatTimeAgo('2026-07-25T11:01:00Z', now)).toBe('59m ago');
    expect(formatTimeAgo('2026-07-25T06:00:00Z', now)).toBe('6h ago');
    expect(formatTimeAgo('2026-07-22T12:00:00Z', now)).toBe('3d ago');
  });

  it('rolls over to hours at exactly 60 minutes', () => {
    // The boundary is `< 60`, so 60m reads as 1h rather than "60m ago".
    expect(formatTimeAgo('2026-07-25T11:00:00Z', now)).toBe('1h ago');
  });

  it('is empty for a missing timestamp', () => {
    expect(formatTimeAgo(null, now)).toBe('');
  });
});

describe('formatArtistCount', () => {
  it('pluralises', () => {
    expect(formatArtistCount(0)).toBe('0 artists');
    expect(formatArtistCount(1)).toBe('1 artist');
    expect(formatArtistCount(2)).toBe('2 artists');
  });
});

describe('formatCountdown', () => {
  it('shows a placeholder when nothing is scheduled', () => {
    // 0 means the scan_watchlist automation is not scheduled at all, which is
    // different from "due now" — it must not render as 0s.
    expect(formatCountdown(0)).toBe('Next Auto: --');
    expect(formatCountdown(-5)).toBe('Next Auto: --');
    expect(formatCountdown(Number.NaN)).toBe('Next Auto: --');
  });

  it('formats hours, minutes and seconds', () => {
    expect(formatCountdown(7320)).toBe('Next Auto: 2h 02m');
    expect(formatCountdown(605)).toBe('Next Auto: 10m 05s');
    expect(formatCountdown(42)).toBe('Next Auto: 42s');
  });
});

describe('artistPills', () => {
  it('separates release types from filter types, in the vanilla order', () => {
    expect(
      artistPills(
        artist({
          include_albums: true,
          include_singles: true,
          include_remixes: true,
          include_compilations: true,
        }),
      ),
    ).toEqual([
      { label: 'Albums', kind: 'active' },
      { label: 'Singles', kind: 'active' },
      { label: 'Remixes', kind: 'filter' },
      { label: 'Compilations', kind: 'filter' },
    ]);
  });

  it('is empty when nothing is enabled', () => {
    expect(artistPills(artist())).toEqual([]);
  });
});
