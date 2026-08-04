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
  amazonPill,
  audiodbPill,
  bandcampPill,
  discogsPill,
  geniusPill,
  hydrabasePill,
  itunesPill,
  repairFindingsBadge,
  repairPill,
  similarArtistsPill,
  soulidPill,
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

// ═══ Wave 2: the nine oddballs ═══

describe('itunesPill', () => {
  it('keeps MusicBrainz gates but Form B tiers', () => {
    expect(itunesPill({ paused: true, yield_reason: 'downloads' }).status).toBe(
      'Yielding for downloads',
    );
    // includes() matching, artists incomplete — equality would pick artists
    expect(
      itunesPill({
        ...running,
        current_item: { name: 'x', type: 'album_group' },
        progress: progressArtists,
      }).progress,
    ).toBe('Albums: 0 / 5 (0%)');
  });

  it('has no current fallback', () => {
    expect(itunesPill({ ...running }).current).toBeNull();
    expect(itunesPill({ ...running, current_item: { name: 'Y' } }).current).toBe('Now: Y');
  });
});

describe('geniusPill', () => {
  it('has NO albums pass — an album current type lands on Tracks', () => {
    // `artist || (!artistsComplete && !currentType)` is the only artists arm;
    // 'album' fails it and there is no albums arm at all.
    expect(
      geniusPill({
        authenticated: true,
        ...running,
        current_item: { name: 'x', type: 'album' },
        progress: progressArtists,
      }).progress,
    ).toBe('Tracks: 0 / 20 (0%)');
    expect(
      geniusPill({ authenticated: true, ...running, progress: progressArtists }).progress,
    ).toBe('Artists: 3 / 10 (30%)');
  });

  it('carries the token copy', () => {
    expect(geniusPill({ authenticated: false }).current).toBe(
      'Add Genius access token in Settings to enrich',
    );
    expect(geniusPill({ paused: true, authenticated: false }).current).toBe(
      'Add Genius access token in Settings to enrich',
    );
  });
});

describe('bandcampPill', () => {
  it('gates on enabled === false STRICTLY — an absent enabled is not disabled', () => {
    expect(bandcampPill({ enabled: false }).stateClass).toBe('no-auth');
    expect(bandcampPill({ enabled: false }).status).toBe('Disabled');
    // undefined enabled: NOT disabled (unlike JioSaavn's !enabled)
    expect(bandcampPill({ ...running }).stateClass).toBe('active');
    expect(bandcampPill({ ...running }).status).toBe('Running');
  });

  it('maps disabled to the no-auth CLASS with the experimental copy', () => {
    const view = bandcampPill({ enabled: false, progress: {}, stats: { pending: 9 } });
    expect(view.current).toBe('Enable in Settings → Advanced → Experimental');
    expect(view.progress).toBe('Pending: 9 items');
  });

  it('paused copy has NO auth ternary', () => {
    expect(bandcampPill({ paused: true }).current).toBe('Click to resume');
  });

  it('is albums-first two-tier (no artist pass)', () => {
    expect(
      bandcampPill({
        ...running,
        progress: { albums: { matched: 1, total: 4, percent: 25 }, tracks: {} },
      }).progress,
    ).toBe('Albums: 1 / 4 (25%)');
    expect(
      bandcampPill({
        ...running,
        current_item: { name: 'x', type: 'artist' },
        progress: { albums: { matched: 1, total: 4, percent: 25 }, tracks: {} },
      }).progress,
    ).toBe('Tracks: 0 / 0 (0%)');
  });
});

describe('amazonPill', () => {
  it('checks paused FIRST and keeps yield_reason', () => {
    const view = amazonPill({ paused: true, idle: true, yield_reason: 'downloads' });
    expect(view.stateClass).toBe('paused');
    expect(view.status).toBe('Yielding for downloads');
  });

  it('keeps its current else', () => {
    expect(amazonPill({ ...running }).current).toBe('No active matches');
  });

  it('uses Form C: unknown type falls to Tracks', () => {
    expect(
      amazonPill({
        ...running,
        current_item: { name: 'x', type: 'weird' },
        progress: progressArtists,
      }).progress,
    ).toBe('Tracks: 0 / 20 (0%)');
  });
});

describe('discogsPill', () => {
  it('renders the raw STRING item quoted', () => {
    expect(discogsPill({ ...running, current_item: 'Aphex Twin - SAW' }).current).toBe(
      'Processing: "Aphex Twin - SAW"',
    );
    expect(discogsPill({ ...running }).current).toBe('No active matches');
  });

  it('reads stats with pipe separators, not progress tiers', () => {
    expect(
      discogsPill({ ...running, stats: { matched: 5, not_found: 2, pending: 9 } }).progress,
    ).toBe('Matched: 5 | Not found: 2 | Pending: 9');
    // progress payload is IGNORED; only stats drives the line
    expect(discogsPill({ ...running, progress: progressArtists }).progress).toBeNull();
  });
});

describe('similarArtistsPill', () => {
  it('progress is UNCONDITIONAL — an empty payload renders the zero line', () => {
    expect(similarArtistsPill({}).progress).toBe('Artists: 0 / 0 (0%)');
    expect(
      similarArtistsPill({ progress: { artists: { matched: 4, total: 9, percent: 44 } } }).progress,
    ).toBe('Artists: 4 / 9 (44%)');
  });

  it('uses the library-artists idle copy and the raw string item', () => {
    expect(similarArtistsPill({ idle: true }).current).toBe('All library artists processed');
    expect(similarArtistsPill({ ...running, current_item: 'Radiohead' }).current).toBe(
      'Now: Radiohead',
    );
    expect(similarArtistsPill({ ...running }).current).toBe('No active matches');
  });
});

describe('hydrabasePill', () => {
  it('speaks its own vocabulary with inline colors', () => {
    expect(hydrabasePill({ paused: true })).toEqual({
      stateClass: 'paused',
      status: 'Paused',
      current: null,
      progress: null,
      statusColor: '#ffc107',
    });
    expect(hydrabasePill({ ...running }).status).toBe('Active');
    expect(hydrabasePill({ ...running }).statusColor).toBe('#ffffff');
    expect(hydrabasePill({}).status).toBe('Stopped');
    expect(hydrabasePill({}).statusColor).toBe('#ff5252');
  });

  it('never uses complete — idle is just Stopped', () => {
    expect(hydrabasePill({ idle: true }).stateClass).toBeNull();
    expect(hydrabasePill({ idle: true }).status).toBe('Stopped');
  });
});

describe('soulidPill', () => {
  it('never carries the paused class even when the status says Paused', () => {
    const view = soulidPill({ paused: true });
    expect(view.status).toBe('Paused');
    expect(view.stateClass).toBeNull();
  });

  it('the raw item OUTRANKS idle and has no prefix', () => {
    expect(soulidPill({ idle: true, current_item: 'artist: Boards of Canada' }).current).toBe(
      'artist: Boards of Canada',
    );
    expect(soulidPill({ idle: true }).current).toBe('All entities have soul IDs');
    expect(soulidPill({}).current).toBe('No items processing');
  });

  it('joins the stats parts with middots and falls back when all are zero', () => {
    expect(
      soulidPill({ stats: { artists_processed: 2, tracks_processed: 5, pending: 1 } }).progress,
    ).toBe('Artists: 2 · Tracks: 5 · Pending: 1');
    expect(soulidPill({ stats: {} }).progress).toBe('No items processed yet');
    expect(soulidPill({}).progress).toBeNull();
  });
});

describe('repairPill', () => {
  it('a payload without `enabled` is forced to paused — even complete', () => {
    expect(repairPill({ idle: true }).stateClass).toBe('paused');
    expect(repairPill({ idle: true, enabled: true }).stateClass).toBe('complete');
    // ...but the STATUS chain is untouched by the override
    expect(repairPill({ idle: true }).status).toBe('Complete');
  });

  it('renders the job line with BARE scanned/percent, gated on total > 0', () => {
    expect(
      repairPill({
        enabled: true,
        ...running,
        current_job: { display_name: 'Orphan Detector' },
        progress: { current_job: { scanned: 4, total: 9, percent: 44 } },
      }).current,
    ).toBe('Orphan Detector: 4 / 9 (44%)');
    expect(
      repairPill({
        enabled: true,
        ...running,
        current_job: { display_name: 'Orphan Detector' },
        progress: { current_job: { total: 0 } },
      }).current,
    ).toBe('Running: Orphan Detector');
    expect(
      repairPill({ enabled: true, ...running, current_job: { display_name: 'X' } }).current,
    ).toBe('Running: X');
  });

  it('falls back to the item name, then to No active repairs', () => {
    expect(
      repairPill({ enabled: true, ...running, current_item: { name: 'track.flac' } }).current,
    ).toBe('Running: track.flac');
    expect(repairPill({ enabled: true, ...running }).current).toBe('No active repairs');
    expect(repairPill({ enabled: true, idle: true }).current).toBe(
      'All jobs complete — waiting for next schedule',
    );
  });

  it('builds the tracks-only progress parts', () => {
    expect(
      repairPill({
        enabled: true,
        progress: { tracks: { checked: 3, total: 10, repaired: 2 } },
        findings_pending: 4,
      }).progress,
    ).toBe('Checked: 3 / 10 · Repaired: 2 · Findings: 4');
    expect(repairPill({ enabled: true, progress: {} }).progress).toBe('No items processed yet');
    expect(repairPill({ enabled: true }).progress).toBeNull();
  });
});

describe('repairFindingsBadge', () => {
  it('shows only when non-zero', () => {
    expect(repairFindingsBadge({ findings_pending: 3 })).toEqual({ count: 3, visible: true });
    expect(repairFindingsBadge({})).toEqual({ count: 0, visible: false });
  });
});
