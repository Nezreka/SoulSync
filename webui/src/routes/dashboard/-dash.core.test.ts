/**
 * Dashboard pure core — wave 1: the eight object-item providers.
 *
 * Assertions are LITERALS, never interpolated from the module under test, so a
 * drifted label or reordered gate has to be re-typed here deliberately. Each
 * provider's quirks get their own tests — the quirks are the reason there is
 * one reducer per provider at all.
 */

import { describe, expect, it } from 'vitest';

import {
  audiodbPill,
  deezerPill,
  jiosaavnPill,
  lastfmPill,
  musicbrainzPill,
  qobuzPill,
  spotifyPill,
  tidalPill,
} from './-dash.core';

const running = { running: true, paused: false };
const progressArtists = {
  artists: { matched: 3, total: 10, percent: 30 },
  albums: { matched: 0, total: 5, percent: 0 },
  tracks: { matched: 0, total: 20, percent: 0 },
};

// ── MusicBrainz ──────────────────────────────────────────────────────────────

describe('musicbrainzPill', () => {
  it('maps the class chain: idle > running > paused', () => {
    expect(musicbrainzPill({ idle: true }).stateClass).toBe('complete');
    expect(musicbrainzPill(running).stateClass).toBe('active');
    expect(musicbrainzPill({ paused: true }).stateClass).toBe('paused');
    expect(musicbrainzPill({}).stateClass).toBeNull();
  });

  it('shows the yield reason only for downloads', () => {
    expect(musicbrainzPill({ paused: true, yield_reason: 'downloads' }).status).toBe(
      'Yielding for downloads',
    );
    expect(musicbrainzPill({ paused: true, yield_reason: 'other' }).status).toBe('Paused');
    expect(musicbrainzPill({}).status).toBe('Idle');
  });

  it('capitalises the item type and quotes the name', () => {
    expect(
      musicbrainzPill({ ...running, current_item: { name: 'Tool', type: 'artist' } }).current,
    ).toBe('Artist: "Tool"');
    // type falls back to 'item'
    expect(musicbrainzPill({ ...running, current_item: { name: 'X' } }).current).toBe('Item: "X"');
  });

  it('falls back to "No active matches" — it HAS a final else', () => {
    expect(musicbrainzPill({ ...running }).current).toBe('No active matches');
  });

  it('a STRING current_item behaves like property access — no name, fallback', () => {
    // The vanilla reads `.name` off whatever arrives; a string yields undefined
    // and the fallback branch runs. The reducer must not "helpfully" treat the
    // string as a name — that would be behavior the vanilla does not have.
    expect(musicbrainzPill({ ...running, current_item: 'raw string' }).current).toBe(
      'No active matches',
    );
    expect(deezerPill({ ...running, current_item: 'raw string' }).current).toBeNull();
  });

  it('renders the BARE total — a missing total prints "undefined"', () => {
    // Verbatim vanilla quirk: MusicBrainz is the only provider without `|| 0`
    // on the total slot. Fixing it here would be silent drift.
    const view = musicbrainzPill({
      ...running,
      progress: { artists: { matched: 2, percent: 10 } },
    });
    expect(view.progress).toBe('Artists: 2 / undefined (10%)');
  });

  it('Form A: falls through artists → albums → tracks by completion', () => {
    expect(musicbrainzPill({ ...running, progress: progressArtists }).progress).toBe(
      'Artists: 3 / 10 (30%)',
    );
    expect(
      musicbrainzPill({
        ...running,
        progress: {
          artists: { matched: 10, total: 10, percent: 100 },
          albums: { matched: 1, total: 5, percent: 20 },
          tracks: {},
        },
      }).progress,
    ).toBe('Albums: 1 / 5 (20%)');
    expect(
      musicbrainzPill({
        ...running,
        progress: {
          artists: { matched: 10, total: 10 },
          albums: { matched: 5, total: 5 },
          tracks: { matched: 7, total: 20, percent: 35 },
        },
      }).progress,
    ).toBe('Tracks: 7 / 20 (35%)');
  });

  it('an unknown current type falls to the Artists else-arm', () => {
    expect(
      musicbrainzPill({
        ...running,
        current_item: { name: 'x', type: 'weird' },
        progress: progressArtists,
      }).progress,
    ).toBe('Artists: 3 / 10 (30%)');
  });

  it('keeps the previous progress when the payload has none', () => {
    expect(musicbrainzPill({ ...running }).progress).toBeNull();
  });
});

// ── AudioDB ──────────────────────────────────────────────────────────────────

describe('audiodbPill', () => {
  it('is MusicBrainz-shaped but GUARDS the total', () => {
    const view = audiodbPill({ ...running, progress: { artists: { matched: 2, percent: 10 } } });
    expect(view.progress).toBe('Artists: 2 / 0 (10%)');
    // ...and the percent slot: a missing percent is 0, never "undefined".
    expect(audiodbPill({ ...running, progress: { artists: { matched: 2 } } }).progress).toBe(
      'Artists: 2 / 0 (0%)',
    );
  });

  it('keeps the capitalised-type current and its else', () => {
    expect(
      audiodbPill({ ...running, current_item: { name: 'AC/DC', type: 'artist' } }).current,
    ).toBe('Artist: "AC/DC"');
    expect(audiodbPill({ ...running }).current).toBe('No active matches');
  });
});

// ── Deezer ───────────────────────────────────────────────────────────────────

describe('deezerPill', () => {
  it('uses the Now: form for the current item', () => {
    expect(deezerPill({ ...running, current_item: { name: 'Kraftwerk' } }).current).toBe(
      'Now: Kraftwerk',
    );
  });

  it('has NO current fallback — the tooltip goes stale (null = keep previous)', () => {
    expect(deezerPill({ ...running }).current).toBeNull();
  });

  it('still yields for downloads', () => {
    expect(deezerPill({ paused: true, yield_reason: 'downloads' }).status).toBe(
      'Yielding for downloads',
    );
  });
});

// ── JioSaavn ─────────────────────────────────────────────────────────────────

describe('jiosaavnPill', () => {
  it('disabled OUTRANKS everything — even idle', () => {
    const view = jiosaavnPill({ enabled: false, idle: true, running: true });
    expect(view.stateClass).toBe('paused');
    expect(view.status).toBe('Disabled');
    expect(view.current).toBe('Enable in Settings → Advanced → Experimental');
  });

  it('behaves like Deezer once enabled', () => {
    const view = jiosaavnPill({ enabled: true, ...running, current_item: { name: 'X' } });
    expect(view.stateClass).toBe('active');
    expect(view.status).toBe('Running');
    expect(view.current).toBe('Now: X');
    expect(jiosaavnPill({ enabled: true, ...running }).current).toBeNull();
  });
});

// ── The authenticated family: LastFM / Tidal / Qobuz ─────────────────────────

describe('the authed family', () => {
  it('paused outranks not-authenticated for the CLASS, but the copy still explains auth', () => {
    const view = lastfmPill({ paused: true, authenticated: false });
    expect(view.stateClass).toBe('paused');
    expect(view.status).toBe('Paused');
    expect(view.current).toBe('Add Last.fm API key in Settings to enrich');
  });

  it('paused while authenticated invites a resume', () => {
    expect(lastfmPill({ paused: true, authenticated: true }).current).toBe('Click to resume');
  });

  it('not-authenticated shows the per-provider connect copy', () => {
    expect(lastfmPill({ authenticated: false }).current).toBe(
      'Add Last.fm API key in Settings to enrich',
    );
    expect(tidalPill({ authenticated: false }).current).toBe('Connect Tidal in Settings to enrich');
    expect(qobuzPill({ authenticated: false }).current).toBe('Connect Qobuz in Settings to enrich');
  });

  it('has NO yield_reason handling — paused is just Paused', () => {
    expect(tidalPill({ paused: true, yield_reason: 'downloads', authenticated: true }).status).toBe(
      'Paused',
    );
  });

  it('unauthenticated progress becomes a Pending line — but ONLY when a progress payload exists', () => {
    expect(tidalPill({ authenticated: false, progress: {}, stats: { pending: 42 } }).progress).toBe(
      'Pending: 42 items',
    );
    // The vanilla's outer guard is `data.progress && tooltipProgress`; with no
    // progress payload nothing is written, even unauthenticated.
    expect(tidalPill({ authenticated: false, stats: { pending: 42 } }).progress).toBeNull();
  });

  it('LastFM picks tiers with Form A, Tidal/Qobuz with Form C', () => {
    // The forms only diverge on an UNKNOWN current type while artists are
    // incomplete: Form A's else-arm falls back to Artists, Form C's to Tracks.
    // (With everything complete they agree — Form A's third arm catches
    // `artistsComplete && albumsComplete` before its else.)
    const weird = { name: 'x', type: 'weird' };
    expect(
      lastfmPill({
        authenticated: true,
        ...running,
        current_item: weird,
        progress: progressArtists,
      }).progress,
    ).toBe('Artists: 3 / 10 (30%)');
    expect(
      tidalPill({
        authenticated: true,
        ...running,
        current_item: weird,
        progress: progressArtists,
      }).progress,
    ).toBe('Tracks: 0 / 20 (0%)');
  });

  it('has NO current fallback while running (stale-text quirk)', () => {
    expect(tidalPill({ authenticated: true, ...running }).current).toBeNull();
  });
});

// ── Spotify ──────────────────────────────────────────────────────────────────

describe('spotifyPill', () => {
  it('the Free bridge neutralises not-authenticated (#887)', () => {
    const view = spotifyPill({
      authenticated: false,
      using_free: true,
      ...running,
      current_item: { name: 'Boards of Canada' },
    });
    expect(view.stateClass).toBe('active');
    expect(view.status).toBe('Running (Spotify Free)');
    expect(view.current).toBe('Now: Boards of Canada (via Spotify Free)');
  });

  it('the Free bridge neutralises rate-limited and the daily budget (#798)', () => {
    expect(spotifyPill({ rate_limited: true, using_free: true, ...running }).status).toBe(
      'Running (Spotify Free)',
    );
    expect(
      spotifyPill({ daily_budget: { exhausted: true }, using_free: true, ...running }).status,
    ).toBe('Running (Spotify Free)');
  });

  it('rate-limited without the bridge is stuck, with a minutes countdown', () => {
    const view = spotifyPill({
      authenticated: true,
      rate_limited: true,
      rate_limit: { remaining_seconds: 150 },
    });
    expect(view.stateClass).toBe('paused');
    expect(view.status).toBe('Rate Limited');
    expect(view.current).toBe('Waiting 3m for rate limit to clear');
    expect(spotifyPill({ authenticated: true, rate_limited: true }).current).toBe(
      'Waiting for rate limit to clear',
    );
  });

  it('an exhausted budget shows the reset countdown in h/m', () => {
    const view = spotifyPill({
      authenticated: true,
      daily_budget: { exhausted: true, resets_in_seconds: 5400 },
    });
    expect(view.status).toBe('Daily Limit Reached');
    expect(view.current).toBe('Resets in 1h 30m');
  });

  it('has its OWN no-item fallback: Waiting for next item...', () => {
    expect(spotifyPill({ authenticated: true, ...running }).current).toBe(
      'Waiting for next item...',
    );
  });

  it('Form B matches compound types via includes()', () => {
    // Artists INCOMPLETE on purpose: an equality match would ignore
    // 'album_group' and fall through to the artists tier — includes() must win.
    expect(
      spotifyPill({
        authenticated: true,
        ...running,
        current_item: { name: 'x', type: 'album_group' },
        progress: progressArtists,
      }).progress,
    ).toBe('Albums: 0 / 5 (0%)');
  });

  it('an exhausted budget still PAUSES the class even while the status chain would say Free', () => {
    // The status chain checks bridgingFree before budgetStuck, so status alone
    // cannot distinguish them — the class can: bridged means active.
    expect(
      spotifyPill({ daily_budget: { exhausted: true }, using_free: true, ...running }).stateClass,
    ).toBe('active');
    expect(
      spotifyPill({ authenticated: true, daily_budget: { exhausted: true }, ...running })
        .stateClass,
    ).toBe('paused');
  });

  it('not-authenticated outranks rate-limited in the status chain', () => {
    expect(spotifyPill({ authenticated: false, rate_limited: true }).status).toBe(
      'Not Authenticated',
    );
  });

  it('not-authenticated (no bridge) pends like the authed family', () => {
    const view = spotifyPill({ authenticated: false, progress: {}, stats: { pending: 7 } });
    expect(view.stateClass).toBe('no-auth');
    expect(view.status).toBe('Not Authenticated');
    expect(view.current).toBe('Connect Spotify in Settings to enrich');
    expect(view.progress).toBe('Pending: 7 items');
  });

  it('paused outranks every other gate', () => {
    expect(spotifyPill({ paused: true, rate_limited: true, authenticated: false }).status).toBe(
      'Paused',
    );
  });
});
