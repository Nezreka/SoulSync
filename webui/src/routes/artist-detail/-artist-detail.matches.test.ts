import { afterEach, describe, expect, it } from 'vitest';

import {
  buildMatchRows,
  matchState,
  matchSummary,
  parseAttempted,
  relativeTime,
  showsFixMatchButton,
} from './-artist-detail.matches';

afterEach(() => {
  delete window.filterJiosaavnServiceEntries;
});

describe('buildMatchRows', () => {
  it('reads one row per chip service, in chip order, with the stored id and its link', () => {
    const rows = buildMatchRows({
      spotify_artist_id: '6kBDZFXuLrZgHnvmPu9NsG',
      spotify_match_status: 'matched',
      spotify_last_attempted: '2026-09-15 10:00:00',
      musicbrainz_id: 'f22942a1',
      deezer_match_status: 'not_found',
      audiodb_match_status: 'error',
      itunes_match_status: 'pending',
      genius_id: 123,
      genius_url: 'https://genius.com/artists/Aphex-twin',
      lastfm_url: 'https://www.last.fm/music/Aphex+Twin',
      amazon_id: 'B000',
    });
    expect(rows.map((r) => r.svc)).toEqual([
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
    const by = Object.fromEntries(rows.map((r) => [r.svc, r]));
    expect(by.spotify).toMatchObject({
      value: '6kBDZFXuLrZgHnvmPu9NsG',
      url: 'https://open.spotify.com/artist/6kBDZFXuLrZgHnvmPu9NsG',
      state: 'matched',
      stateLabel: 'Matched',
      attempted: '2026-09-15 10:00:00',
      canAutoLookup: false,
    });
    expect(by.deezer).toMatchObject({ value: null, state: 'not_found', canAutoLookup: true });
    expect(by.audiodb).toMatchObject({ state: 'error', stateLabel: 'Lookup failed' });
    expect(by.itunes).toMatchObject({ state: 'pending', stateLabel: 'Queued' });
    expect(by.discogs).toMatchObject({ state: 'never', stateLabel: 'Not tried yet' });
    // genius shows its id but links through the stored page url
    expect(by.genius).toMatchObject({
      value: '123',
      url: 'https://genius.com/artists/Aphex-twin',
    });
    expect(by.lastfm.url).toBe('https://www.last.fm/music/Aphex+Twin');
    // amazon has no artist page and no on-demand lookup
    expect(by.amazon).toMatchObject({ value: 'B000', url: null, canAutoLookup: false });
  });

  it('counts a stored id as matched whatever the status column says, and a 0 id as absent', () => {
    expect(matchState('x', 'not_found')).toBe('matched');
    expect(matchState(null, 'matched')).toBe('never');
    const [spotify] = buildMatchRows({ spotify_artist_id: 0, spotify_match_status: 'matched' });
    expect(spotify.value).toBeNull();
    expect(spotify.state).toBe('never');
  });

  it('filters jiosaavn through the shared helper when it is loaded', () => {
    window.filterJiosaavnServiceEntries = ((items: { svc: string }[]) => items) as never;
    expect(buildMatchRows({}).some((r) => r.svc === 'jiosaavn')).toBe(true);
    delete window.filterJiosaavnServiceEntries;
    expect(buildMatchRows({}).some((r) => r.svc === 'jiosaavn')).toBe(false);
  });

  it('summarises matched over total', () => {
    const rows = buildMatchRows({ spotify_artist_id: 'a', deezer_id: 5 });
    expect(matchSummary(rows)).toEqual({ matched: 2, total: 11 });
  });
});

describe('timestamps', () => {
  it('reads sqlite CURRENT_TIMESTAMP as UTC, not local time', () => {
    expect(parseAttempted('2026-09-15 10:00:00')?.toISOString()).toBe('2026-09-15T10:00:00.000Z');
    expect(parseAttempted('2026-09-15T10:00:00Z')?.toISOString()).toBe('2026-09-15T10:00:00.000Z');
    expect(parseAttempted('garbage')).toBeNull();
    expect(parseAttempted(null)).toBeNull();
  });

  it('renders a coarse relative age', () => {
    const now = Date.parse('2026-09-16T12:00:00Z');
    expect(relativeTime('2026-09-16 11:59:40', now)).toBe('just now');
    expect(relativeTime('2026-09-16 11:30:00', now)).toBe('30m ago');
    expect(relativeTime('2026-09-16 09:00:00', now)).toBe('3h ago');
    expect(relativeTime('2026-09-10 12:00:00', now)).toBe('6d ago');
    expect(relativeTime('2026-05-16 12:00:00', now)).toBe('4mo ago');
    expect(relativeTime('2023-09-16 12:00:00', now)).toBe('3y ago');
    expect(relativeTime(null, now)).toBe('');
  });
});

describe('showsFixMatchButton', () => {
  it('needs a library artist and an admin', () => {
    expect(showsFixMatchButton({ id: 42 }, false, true)).toBe(true);
    expect(showsFixMatchButton({ id: 42 }, true, true)).toBe(false);
    expect(showsFixMatchButton({ id: 42 }, false, false)).toBe(false);
    expect(showsFixMatchButton({}, false, true)).toBe(false);
    expect(showsFixMatchButton(undefined, false, true)).toBe(false);
  });
});
