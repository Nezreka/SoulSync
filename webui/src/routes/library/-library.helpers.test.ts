import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  BRAND_LOGOS,
  buildArtistBadges,
  canWatchArtist,
  cardAnimationDelay,
  MAX_BADGES_PER_COLUMN,
  readArtistsResponse,
  splitBadgeColumns,
  trackCountLabel,
} from './-library.helpers';
import { type LibraryArtist } from './-library.types';

function artist(over: Partial<LibraryArtist> = {}): LibraryArtist {
  return { id: 1, name: 'Aphex Twin', ...over };
}

describe('brand logos match the vanilla source', () => {
  // Most are top-level `const`s in core.js, which a module cannot read, so the
  // helper restates them. SoulID's was never a constant at all: it was an
  // inline literal in library.js's artist-detail badge builder, which the
  // cleanup deleted along with the rest of the vanilla page — so the same
  // brand's declaration in service-switch.js stands in for it. All three files
  // are vanilla source; between them every logo still has a second declaration
  // to disagree with.
  const vanilla = ['static/core.js', 'static/service-switch.js']
    .map((file) => readFileSync(resolve(process.cwd(), file), 'utf8'))
    .join('\n');

  it.each(Object.entries(BRAND_LOGOS))('%s -> %s', (_key, path) => {
    expect(vanilla).toContain(`'${path}'`);
  });

  // The point of the pin is "a moved or renamed asset must not ship a silently
  // broken image", and text parity only ever caught that second-hand. This
  // catches it directly, and keeps catching it as more vanilla goes away.
  it.each(Object.entries(BRAND_LOGOS))('%s exists on disk', (_key, path) => {
    expect(existsSync(resolve(process.cwd(), path.replace(/^\//, '')))).toBe(true);
  });
});

describe('buildArtistBadges', () => {
  it('returns nothing for an unenriched artist', () => {
    expect(buildArtistBadges(artist())).toEqual([]);
  });

  it('keeps the vanilla declaration order, which is visible on the card', () => {
    const badges = buildArtistBadges(
      artist({
        // deliberately supplied out of order
        tidal_id: 't',
        spotify_artist_id: 's',
        genius_url: 'https://genius.com/x',
        musicbrainz_id: 'mb',
      }),
    );
    expect(badges.map((b) => b.key)).toEqual(['spotify', 'musicbrainz', 'genius', 'tidal']);
  });

  it('deep-links each provider', () => {
    const [spotify] = buildArtistBadges(artist({ spotify_artist_id: 'abc' }));
    expect(spotify.url).toBe('https://open.spotify.com/artist/abc');
    const [mb] = buildArtistBadges(artist({ musicbrainz_id: 'xyz' }));
    expect(mb.url).toBe('https://musicbrainz.org/artist/xyz');
  });

  it('passes through the raw url for Last.fm and Genius', () => {
    // These arrive as full URLs, not ids — building a URL from them would
    // produce a double-prefixed link.
    const [lfm] = buildArtistBadges(artist({ lastfm_url: 'https://last.fm/music/X' }));
    expect(lfm.url).toBe('https://last.fm/music/X');
  });

  it('gives Amazon and SoulID no link', () => {
    expect(buildArtistBadges(artist({ amazon_id: 'a' }))[0].url).toBeNull();
    expect(buildArtistBadges(artist({ soul_id: 'real-id' }))[0].url).toBeNull();
  });

  it('ignores a placeholder soul_id', () => {
    // soul_unnamed_* is a generated stand-in, not an identity — badging it
    // would claim an enrichment that never happened.
    expect(buildArtistBadges(artist({ soul_id: 'soul_unnamed_123' }))).toEqual([]);
    expect(buildArtistBadges(artist({ soul_id: 'soul_real' })).map((b) => b.key)).toEqual([
      'soulsync',
    ]);
  });

  it('slugifies the artist name into the AudioDB url', () => {
    // Spaces become hyphens FIRST, then non-alphanumerics are stripped — so
    // the hyphen survives and the accent does not. Verified against the exact
    // vanilla expression rather than reasoned about: "Sigur Rós!" -> Sigur-Rs.
    const [adb] = buildArtistBadges(artist({ audiodb_id: 5, name: 'Sigur Rós!' }));
    expect(adb.url).toBe('https://www.theaudiodb.com/artist/5-Sigur-Rs');
  });

  it('treats a zero id as absent', () => {
    // Ids arrive as numbers for some providers; 0 is not a real id.
    expect(buildArtistBadges(artist({ deezer_id: 0 }))).toEqual([]);
  });
});

describe('splitBadgeColumns', () => {
  const many = (n: number) =>
    Array.from({ length: n }, (_, i) => ({
      key: `k${i}`,
      logo: '',
      fallback: '',
      title: '',
      url: null,
    }));

  it('keeps everything in one column at or below the cap', () => {
    const out = splitBadgeColumns(many(MAX_BADGES_PER_COLUMN));
    expect(out.needsOverflow).toBe(false);
    expect(out.primary).toHaveLength(MAX_BADGES_PER_COLUMN);
    expect(out.overflow).toEqual([]);
  });

  it('spills the remainder over the cap', () => {
    const out = splitBadgeColumns(many(MAX_BADGES_PER_COLUMN + 3));
    expect(out.needsOverflow).toBe(true);
    expect(out.primary).toHaveLength(MAX_BADGES_PER_COLUMN);
    expect(out.overflow).toHaveLength(3);
  });
});

describe('cardAnimationDelay', () => {
  it('staggers by 20ms and caps at 600', () => {
    expect(cardAnimationDelay(0)).toBe(0);
    expect(cardAnimationDelay(5)).toBe(100);
    // Without the cap a 75-card page would stagger the last card by 1.5s.
    expect(cardAnimationDelay(30)).toBe(600);
    expect(cardAnimationDelay(74)).toBe(600);
  });
});

describe('canWatchArtist', () => {
  it('accepts either id, whichever source is active', () => {
    expect(canWatchArtist(artist({ spotify_artist_id: 's' }), 'Spotify')).toBe(true);
    expect(canWatchArtist(artist({ itunes_artist_id: 1 }), 'iTunes')).toBe(true);
    // The source only expresses preference — the other id still qualifies.
    expect(canWatchArtist(artist({ spotify_artist_id: 's' }), 'iTunes')).toBe(true);
    expect(canWatchArtist(artist({ itunes_artist_id: 1 }), 'Spotify')).toBe(true);
  });

  it('refuses an artist with neither', () => {
    expect(canWatchArtist(artist({ musicbrainz_id: 'mb' }), 'Spotify')).toBe(false);
  });
});

describe('trackCountLabel', () => {
  it('pluralises and hides zero', () => {
    expect(trackCountLabel(1)).toBe('1 track');
    expect(trackCountLabel(12)).toBe('12 tracks');
    expect(trackCountLabel(0)).toBe('');
    expect(trackCountLabel(undefined)).toBe('');
  });
});

describe('readArtistsResponse', () => {
  it('unwraps a successful payload', () => {
    const out = readArtistsResponse({
      success: true,
      artists: [artist()],
      pagination: {
        page: 2,
        limit: 75,
        total_count: 90,
        total_pages: 2,
        has_prev: true,
        has_next: false,
      },
    });
    expect(out.artists).toHaveLength(1);
    expect(out.pagination).toEqual({
      page: 2,
      totalPages: 2,
      totalCount: 90,
      hasPrev: true,
      hasNext: false,
    });
  });

  it('throws the server reason on success:false', () => {
    // The endpoint answers 500 WITH a body, but also has paths that report
    // success:false — swallowing it would show an empty library as if the
    // user simply had no artists.
    expect(() => readArtistsResponse({ success: false, error: 'db locked' })).toThrow('db locked');
  });

  it('falls back to a generic message when the reason is missing', () => {
    expect(() => readArtistsResponse({ success: false })).toThrow('Failed to load artists');
  });

  it('survives a payload with no pagination', () => {
    const out = readArtistsResponse({ artists: [] });
    expect(out.pagination.page).toBe(1);
    expect(out.pagination.hasNext).toBe(false);
  });
});
