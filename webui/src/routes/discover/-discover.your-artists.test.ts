import { describe, expect, it } from 'vitest';

import type { YourArtist } from './-discover.your-artists';

import { YOUR_ALBUMS_POLL_MAX_ATTEMPTS } from './-discover.section-state';
import {
  SOURCE_COLOR_FALLBACK,
  YOUR_ARTISTS_NO_SOURCES,
  YOUR_ARTISTS_POLL_MAX_ATTEMPTS,
  YOUR_ARTISTS_POLL_MS,
  YOUR_ARTISTS_STALE_BODY,
  YOUR_ARTISTS_STALE_SUBTITLE,
  artistCardIsClickable,
  artistSourceBadges,
  sourceColor,
  watchlistButtonTitle,
  yourArtistsIsEmpty,
  yourArtistsIsStaleEmpty,
  yourArtistsSubtitle,
} from './-discover.your-artists';

const a = (over: Partial<YourArtist> = {}): YourArtist => ({ artist_name: 'Aphex Twin', ...over });

describe('empty vs still-discovering', () => {
  it('is empty only when there is nothing AND no rebuild running', () => {
    expect(yourArtistsIsEmpty([], { stale: false })).toBe(true);
    expect(yourArtistsIsEmpty([], null)).toBe(true);
  });

  it('is NOT empty while upstream is still discovering', () => {
    // Hiding the section here would replace "we're still looking" with silence.
    expect(yourArtistsIsEmpty([], { stale: true })).toBe(false);
    expect(yourArtistsIsStaleEmpty([], { stale: true })).toBe(true);
  });

  it('is neither once artists arrive', () => {
    expect(yourArtistsIsEmpty([a()], { stale: true })).toBe(false);
    expect(yourArtistsIsStaleEmpty([a()], { stale: true })).toBe(false);
  });
});

describe('the poller budgets differ per section, deliberately', () => {
  it('gives your-artists five minutes', () => {
    expect(YOUR_ARTISTS_POLL_MS).toBe(5000);
    expect(YOUR_ARTISTS_POLL_MAX_ATTEMPTS).toBe(60);
  });

  it('is FIVE TIMES your-albums’ budget', () => {
    // Matching artists across connected services takes far longer than
    // reading an album cache. Unifying these would cut artist discovery off
    // after a minute.
    expect(YOUR_ARTISTS_POLL_MAX_ATTEMPTS).toBe(YOUR_ALBUMS_POLL_MAX_ATTEMPTS * 5);
  });
});

describe('the subtitle', () => {
  it('lists the distinct services, joined with "and"', () => {
    const artists = [
      a({ source_services: ['spotify'] }),
      a({ source_services: ['lastfm', 'spotify'] }),
    ];
    expect(yourArtistsSubtitle(artists, null)).toBe('Artists you follow on Spotify and Last.fm');
  });

  it('maps service keys to their display names', () => {
    const artists = [a({ source_services: ['tidal', 'deezer'] })];
    expect(yourArtistsSubtitle(artists, null)).toBe('Artists you follow on Tidal and Deezer');
  });

  it('passes an unknown service through rather than dropping it', () => {
    const artists = [a({ source_services: ['bandcamp'] })];
    expect(yourArtistsSubtitle(artists, null)).toBe('Artists you follow on bandcamp');
  });

  it('falls back rather than leaving a dangling "on "', () => {
    expect(yourArtistsSubtitle([], null)).toBe(`Artists you follow on ${YOUR_ARTISTS_NO_SOURCES}`);
    expect(yourArtistsSubtitle([a()], null)).toBe('Artists you follow on your music services');
  });

  it('appends the updating suffix while stale', () => {
    const artists = [a({ source_services: ['spotify'] })];
    expect(yourArtistsSubtitle(artists, { stale: true })).toBe(
      'Artists you follow on Spotify (updating...)',
    );
  });

  it('keeps the stale copy verbatim', () => {
    expect(YOUR_ARTISTS_STALE_SUBTITLE).toBe(
      'Discovering your artists across connected services...',
    );
    expect(YOUR_ARTISTS_STALE_BODY).toBe('Fetching and matching artists from your services...');
  });
});

describe('source badges', () => {
  it('emits one per populated id, in the vanilla’s fixed order', () => {
    const badges = artistSourceBadges(
      a({
        discogs_artist_id: 'dc',
        deezer_artist_id: 'dz',
        itunes_artist_id: 'it',
        spotify_artist_id: 'sp',
      }),
    );
    expect(badges.map((b) => b.key)).toEqual(['spotify', 'itunes', 'deezer', 'discogs']);
  });

  it('says "Apple Music" for itunes, not "iTunes"', () => {
    expect(artistSourceBadges(a({ itunes_artist_id: 'it' }))[0].title).toBe('Apple Music');
  });

  it('emits none when the artist has no ids', () => {
    expect(artistSourceBadges(a())).toEqual([]);
    expect(artistSourceBadges(null)).toEqual([]);
  });
});

describe('origin dot colours', () => {
  it('uses the vanilla palette', () => {
    expect(sourceColor('spotify')).toBe('#1DB954');
    expect(sourceColor('lastfm')).toBe('#D51007');
    expect(sourceColor('tidal')).toBe('#00FFFF');
    expect(sourceColor('deezer')).toBe('#A238FF');
  });

  it('greys out anything unrecognised rather than rendering no colour', () => {
    expect(sourceColor('bandcamp')).toBe(SOURCE_COLOR_FALLBACK);
    expect(sourceColor('')).toBe('#666');
  });
});

describe('card interactivity', () => {
  it('is clickable only with a resolvable id', () => {
    expect(artistCardIsClickable(a({ spotify_artist_id: 'sp' }))).toBe(true);
    expect(artistCardIsClickable(a())).toBe(false);
    expect(artistCardIsClickable(null)).toBe(false);
  });

  it('labels the watchlist button per state', () => {
    expect(watchlistButtonTitle(true)).toBe('On watchlist');
    expect(watchlistButtonTitle(false)).toBe('Add to watchlist');
    expect(watchlistButtonTitle(undefined)).toBe('Add to watchlist');
  });
});
