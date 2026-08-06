/**
 * The mirrored card's pure core, pinned against stats-automations.js.
 * timeAgo runs differentially against the REAL vanilla body.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { MirroredPlaylistRow } from './-sync.mirrored';

import { extractFunction } from '../../test/vanilla-extract';
import {
  MIRRORED_DETAIL_SOURCE_ICONS,
  MIRRORED_DETAIL_SOURCE_LABELS,
  MIRRORED_SOURCE_ICONS,
  mirroredDetailSourceIcon,
  mirroredDetailSourceLabel,
  mirroredDiscoveryTracks,
  mirroredHash,
  mirroredHeroArt,
  mirroredRowDuration,
  mirroredTotalRuntime,
  retryFailedMirroredDiscovery,
  mirroredPhaseLine,
  mirroredRatio,
  mirroredSourceIcon,
  pipelinePhaseFor,
  timeAgo,
} from './-sync.mirrored';

const STATS = readFileSync(resolve(process.cwd(), 'static/stats-automations.js'), 'utf8');

describe('mirroredSourceIcon (571-572)', () => {
  it('maps the five known sources and falls back to the clipboard', () => {
    expect(mirroredSourceIcon('spotify')).toBe('🎵');
    expect(mirroredSourceIcon('tidal')).toBe('🌊');
    expect(mirroredSourceIcon('youtube')).toBe('▶');
    expect(mirroredSourceIcon('beatport')).toBe('🎛');
    expect(mirroredSourceIcon('file')).toBe('📄');
    expect(mirroredSourceIcon('listenbrainz')).toBe('📋');
    expect(mirroredSourceIcon(undefined)).toBe('📋');
  });

  it('the icon table matches the vanilla literal', () => {
    expect(STATS).toContain(
      "{ spotify: '🎵', tidal: '🌊', youtube: '▶', beatport: '🎛', file: '📄' }",
    );
    expect(Object.keys(MIRRORED_SOURCE_ICONS)).toEqual([
      'spotify',
      'tidal',
      'youtube',
      'beatport',
      'file',
    ]);
  });
});

describe('pipelinePhaseFor (530-533)', () => {
  const row = (status?: string): MirroredPlaylistRow =>
    ({ id: 1, pipeline_state: status ? { status } : null }) as MirroredPlaylistRow;

  it('running/finished map to their phases; error AND skipped both mean error', () => {
    expect(pipelinePhaseFor(row('running'))).toBe('pipeline_running');
    expect(pipelinePhaseFor(row('finished'))).toBe('pipeline_complete');
    expect(pipelinePhaseFor(row('error'))).toBe('pipeline_error');
    expect(pipelinePhaseFor(row('skipped'))).toBe('pipeline_error');
  });

  it('no pipeline_state, or an unknown status, yields null', () => {
    expect(pipelinePhaseFor(row())).toBeNull();
    expect(pipelinePhaseFor(row('queued'))).toBeNull();
    expect(pipelinePhaseFor({ id: 1 } as MirroredPlaylistRow)).toBeNull();
  });
});

describe('mirroredPhaseLine — the unified renderer', () => {
  const row = { id: 1, track_count: 25 } as MirroredPlaylistRow;

  it('paints every phase with the vanilla text and inline colour', () => {
    expect(mirroredPhaseLine('pipeline_running', { pipeline_progress: 40 }, row)).toEqual({
      text: 'Pipeline running 40%',
      color: '#38bdf8',
    });
    expect(
      mirroredPhaseLine(
        'pipeline_running',
        { pipeline_phase: 'Discovering', pipeline_progress: 5 },
        row,
      ),
    ).toEqual({ text: 'Discovering 5%', color: '#38bdf8' });
    expect(mirroredPhaseLine('pipeline_complete', null, row)).toEqual({
      text: 'Pipeline complete',
      color: '#22c55e',
    });
    expect(mirroredPhaseLine('pipeline_error', null, row)).toEqual({
      text: 'Pipeline error',
      color: '#ef4444',
    });
    expect(mirroredPhaseLine('syncing', null, row)).toEqual({
      text: 'Syncing...',
      color: '#3b82f6',
    });
    expect(mirroredPhaseLine('sync_complete', null, row)).toEqual({
      text: 'Synced',
      color: '#3b82f6',
    });
    expect(mirroredPhaseLine('downloading', null, row)).toEqual({
      text: 'Downloading...',
      color: '#f59e0b',
    });
    expect(mirroredPhaseLine('download_complete', null, row)).toEqual({
      text: 'Downloaded',
      color: '#22c55e',
    });
  });

  it('the DECLARED unification: discovering keeps its percent (the live writer drops it)', () => {
    // renderMirroredCard 557-558 shows the percent; updateMirroredCardPhase
    // 853 shows a bare 'Discovering...'. We take the former.
    expect(mirroredPhaseLine('discovering', { discoveryProgress: 40 }, row)).toEqual({
      text: 'Discovering 40%',
      color: '#a78bfa',
    });
    expect(STATS).toContain('Discovering ${pct}%');
    expect(STATS).toContain('>Discovering...<');
  });

  it('the DECLARED unification: discovered falls back to track_count (the live writer does not)', () => {
    // renderMirroredCard 561: spotify_total || p.track_count.
    // updateMirroredCardPhase 857: spotify_total || 0.
    expect(mirroredPhaseLine('discovered', { spotifyMatches: 3 }, row)).toEqual({
      text: 'Discovered 3/25',
      color: '#22c55e',
    });
    expect(mirroredPhaseLine('discovered', { spotifyMatches: 3, spotifyTotal: 10 }, row)).toEqual({
      text: 'Discovered 3/10',
      color: '#22c55e',
    });
  });

  it('the counters default to 0 when the state lacks them (549/557/560)', () => {
    // hydrateMirroredDiscoveryStates (988-1010) writes no pipeline_* keys, so
    // a pipeline_running phase over a hydrated state hits this default.
    expect(mirroredPhaseLine('pipeline_running', {}, row)).toEqual({
      text: 'Pipeline running 0%',
      color: '#38bdf8',
    });
    expect(mirroredPhaseLine('discovering', {}, row)).toEqual({
      text: 'Discovering 0%',
      color: '#a78bfa',
    });
    expect(mirroredPhaseLine('discovered', {}, row)).toEqual({
      text: 'Discovered 0/25',
      color: '#22c55e',
    });
    expect(mirroredPhaseLine('pipeline_running', null, row)).toEqual({
      text: 'Pipeline running 0%',
      color: '#38bdf8',
    });
  });

  it('a row with no track_count renders 0, not undefined (declared divergence)', () => {
    // The vanilla chain ENDS at p.track_count (561), so it would render
    // 'Discovered 3/undefined'. Unreachable live — mirrored_playlists.
    // track_count is INTEGER DEFAULT 0 (music_database.py 674) — but the
    // extra link is ours, so it is pinned rather than left implicit.
    expect(mirroredPhaseLine('discovered', { spotifyMatches: 3 }, { id: 1 })).toEqual({
      text: 'Discovered 3/0',
      color: '#22c55e',
    });
  });

  it('no phase, or an unknown one, renders nothing', () => {
    expect(mirroredPhaseLine(null, null, row)).toBeNull();
    expect(mirroredPhaseLine('fresh', null, row)).toBeNull();
  });
});

describe('mirroredRatio (575-582)', () => {
  it('hidden until something is discovered', () => {
    expect(mirroredRatio({ id: 1, discovered_count: 0, track_count: 9 }, 'Spotify')).toBeNull();
    expect(mirroredRatio({ id: 1, track_count: 9 }, 'Spotify')).toBeNull();
  });

  it('total prefers total_count, and complete flips at disc >= tot', () => {
    expect(
      mirroredRatio({ id: 1, discovered_count: 4, total_count: 9, track_count: 3 }, 'Spotify'),
    ).toEqual({ text: '4/9 discovered on Spotify', complete: false });
    expect(mirroredRatio({ id: 1, discovered_count: 9, total_count: 9 }, 'Spotify')).toEqual({
      text: '9/9 discovered on Spotify',
      complete: true,
    });
    // track_count is the fallback when total_count is absent.
    expect(mirroredRatio({ id: 1, discovered_count: 2, track_count: 5 }, 'Plex')).toEqual({
      text: '2/5 discovered on Plex',
      complete: false,
    });
  });
});

describe('timeAgo (1045-1061) — differential against the vanilla body', () => {
  const NOW = Date.UTC(2026, 0, 15, 12, 0, 0);
  // The real vanilla body with its one clock call substituted, so both sides
  // measure from the same instant. Everything else runs verbatim.
  const vanillaSource = extractFunction('timeAgo', STATS).replace('Date.now()', 'NOW');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const vanillaTimeAgo = new Function('NOW', `${vanillaSource}; return timeAgo;`)(NOW) as (
    d: string,
  ) => string;

  it('agrees with the vanilla across every threshold', () => {
    {
      const cases = [
        '',
        '2026-01-15T11:59:58Z',
        '2026-01-15T11:59:30Z',
        '2026-01-15T11:30:00Z',
        '2026-01-15T06:00:00Z',
        '2026-01-10T12:00:00Z',
        // the d→mo boundary: 29d, exactly 30d, 31d, 45d
        '2025-12-17T12:00:00Z',
        '2025-12-16T12:00:00Z',
        '2025-12-15T12:00:00Z',
        '2025-12-01T12:00:00Z',
        // inputs that land BETWEEN thresholds, so each bound discriminates
        '2026-01-15T11:59:55Z',
        '2026-01-13T20:00:00Z',
        '2026-01-15T11:59:56Z',
        '2026-01-15T11:05:00Z',
        '2026-01-14T14:00:00Z',
        '2025-11-01T12:00:00Z',
        // the s→m, m→h and h→d edges
        '2026-01-15T11:59:00Z',
        '2026-01-15T11:00:00Z',
        '2026-01-14T12:00:00Z',
        '2025-10-15T12:00:00Z',
        '2026-01-15T11:30:00',
        '2026-01-15T11:30:00+00:00',
      ];
      for (const c of cases) {
        expect(timeAgo(c, NOW), `input ${c || '(empty)'}`).toBe(vanillaTimeAgo(c));
      }
    }
  });

  it('pins the boundary wording', () => {
    expect(timeAgo('2026-01-15T11:59:58Z', NOW)).toBe('just now');
    expect(timeAgo('2026-01-15T11:59:30Z', NOW)).toBe('30s ago');
    expect(timeAgo('2026-01-15T11:30:00Z', NOW)).toBe('30m ago');
    expect(timeAgo('2026-01-15T06:00:00Z', NOW)).toBe('6h ago');
    expect(timeAgo('2026-01-10T12:00:00Z', NOW)).toBe('5d ago');
    expect(timeAgo('2025-10-15T12:00:00Z', NOW)).toBe('3mo ago');
    // 29 days is still days; 30 flips to months (days/30 floors to 1).
    expect(timeAgo('2025-12-17T12:00:00Z', NOW)).toBe('29d ago');
    expect(timeAgo('2025-12-16T12:00:00Z', NOW)).toBe('1mo ago');
    // and the other three edges
    expect(timeAgo('2026-01-15T11:59:00Z', NOW)).toBe('1m ago');
    expect(timeAgo('2026-01-15T11:00:00Z', NOW)).toBe('1h ago');
    expect(timeAgo('2026-01-14T12:00:00Z', NOW)).toBe('1d ago');
    // between-threshold values: each one moves if its bound moves.
    expect(timeAgo('2026-01-15T11:59:56Z', NOW)).toBe('just now'); // 4s, bound is 5
    expect(timeAgo('2026-01-15T11:05:00Z', NOW)).toBe('55m ago'); // 55m, bound is 60
    expect(timeAgo('2026-01-14T14:00:00Z', NOW)).toBe('22h ago'); // 22h, bound is 24
    expect(timeAgo('2025-11-01T12:00:00Z', NOW)).toBe('2mo ago'); // 75d, divisor is 30
    // exactly 5s bounds `secs < 5` from ABOVE (4s alone only bounds it below)
    expect(timeAgo('2026-01-15T11:59:55Z', NOW)).toBe('5s ago');
    // 40h: floor gives 1d, round would give 2d — every other days-case is an
    // exact 24h multiple, where the two agree
    expect(timeAgo('2026-01-13T20:00:00Z', NOW)).toBe('1d ago');
    expect(timeAgo(null, NOW)).toBe('');
  });

  it('a bare ISO string is read as UTC, not local', () => {
    // The 'Z' is appended only when there is no Z, no '+', and no '-' at or
    // after index 10 — the date's own hyphens must not count.
    expect(timeAgo('2026-01-15T11:30:00', NOW)).toBe('30m ago');
    expect(timeAgo('2026-01-15T11:30:00+00:00', NOW)).toBe('30m ago');
  });
});

describe('mirroredHash', () => {
  it('carries the marker INSIDE the id — it is never a prepended prefix', () => {
    expect(mirroredHash(3)).toBe('mirrored_3');
    expect(STATS).toContain('`mirrored_${p.id}`');
  });
});

/* ── The DETAIL modal's helpers (openMirroredPlaylistModal, 1086-1100) ─────── */

describe('the detail modal tables are NOT the card tables', () => {
  it('carries the seven keys the detail modal has, and not the card key', () => {
    expect(MIRRORED_DETAIL_SOURCE_ICONS).toEqual({
      spotify: '🎵',
      spotify_public: '🎵',
      tidal: '🌊',
      youtube: '▶',
      beatport: '🎛',
      deezer: '🎧',
      qobuz: '♫',
    });
    // The card knows `file`; the detail modal does not (571 vs 1086).
    expect('file' in MIRRORED_DETAIL_SOURCE_ICONS).toBe(false);
    expect('file' in MIRRORED_SOURCE_ICONS).toBe(true);
    // And the reverse: three keys only the detail modal has.
    for (const key of ['spotify_public', 'deezer', 'qobuz']) {
      expect(key in MIRRORED_SOURCE_ICONS).toBe(false);
      expect(key in MIRRORED_DETAIL_SOURCE_ICONS).toBe(true);
    }
  });

  it('labels the same seven keys, with Spotify twice (1087)', () => {
    expect(MIRRORED_DETAIL_SOURCE_LABELS).toEqual({
      spotify: 'Spotify',
      spotify_public: 'Spotify',
      tidal: 'Tidal',
      youtube: 'YouTube',
      beatport: 'Beatport',
      deezer: 'Deezer',
      qobuz: 'Qobuz',
    });
    expect(Object.keys(MIRRORED_DETAIL_SOURCE_LABELS)).toEqual(
      Object.keys(MIRRORED_DETAIL_SOURCE_ICONS),
    );
  });

  it('falls back to the clipboard icon but to the RAW source name (1088-1089)', () => {
    expect(mirroredDetailSourceIcon('navidrome')).toBe('📋');
    expect(mirroredDetailSourceLabel('navidrome')).toBe('navidrome');
    expect(mirroredDetailSourceLabel('spotify_public')).toBe('Spotify');
  });
});

describe('mirroredHeroArt (1092)', () => {
  it('prefers the playlist cover', () => {
    expect(mirroredHeroArt('http://cover', [{ image_url: 'http://track' }])).toBe('http://cover');
  });

  it('falls to the FIRST track carrying art, skipping those without', () => {
    // Two arted tracks, so first-vs-last is actually distinguishable.
    expect(
      mirroredHeroArt('', [{}, { image_url: 'http://second' }, { image_url: 'http://fourth' }]),
    ).toBe('http://second');
  });

  it('is empty when nothing has art — the gradient-fallback signal', () => {
    expect(mirroredHeroArt(null, [{}, {}])).toBe('');
    expect(mirroredHeroArt(undefined, [])).toBe('');
  });
});

describe('mirroredTotalRuntime (1095-1097)', () => {
  it('reads minutes below the hour', () => {
    expect(mirroredTotalRuntime([{ duration_ms: 219000 }, { duration_ms: 326000 }]).label).toBe(
      '9 min',
    );
  });

  it('splits hours and minutes at and above 60', () => {
    expect(mirroredTotalRuntime([{ duration_ms: 3600000 }]).label).toBe('1 hr 0 min');
    expect(mirroredTotalRuntime([{ duration_ms: 5400000 }]).label).toBe('1 hr 30 min');
  });

  it('rounds to whole minutes BEFORE splitting, not after', () => {
    // 89.6 min → rounds to 90 → "1 hr 30 min", never "1 hr 29 min".
    expect(mirroredTotalRuntime([{ duration_ms: 5376000 }]).label).toBe('1 hr 30 min');
  });

  it('treats missing durations as zero and reports totalMs for the gate (1133)', () => {
    const none = mirroredTotalRuntime([{}, {}]);
    expect(none.totalMs).toBe(0);
    expect(none.label).toBe('0 min');
    expect(mirroredTotalRuntime([{ duration_ms: 219000 }]).totalMs).toBe(219000);
  });
});

describe('mirroredRowDuration (1100) — the inline duplicate, not formatDuration', () => {
  it('renders m:ss with a padded seconds field', () => {
    expect(mirroredRowDuration(219000)).toBe('3:39');
    expect(mirroredRowDuration(61500)).toBe('1:01');
  });

  it('renders EMPTY for a missing or zero duration, where formatDuration says 0:00', () => {
    expect(mirroredRowDuration(0)).toBe('');
    expect(mirroredRowDuration(null)).toBe('');
    expect(mirroredRowDuration(undefined)).toBe('');
  });

  it('does not roll over into hours', () => {
    expect(mirroredRowDuration(3600000)).toBe('60:00');
  });
});

describe('mirroredDiscoveryTracks (2073-2079)', () => {
  it('projects the mirror rows into the discovery modal shape', () => {
    expect(
      mirroredDiscoveryTracks([
        {
          id: 9,
          source_track_id: 'sp1',
          track_name: 'Alright',
          artist_name: 'Kendrick Lamar',
          album_name: 'TPAB',
          duration_ms: 219000,
        },
      ]),
    ).toEqual([
      {
        id: 'sp1',
        name: 'Alright',
        artists: ['Kendrick Lamar'],
        album: 'TPAB',
        duration_ms: 219000,
      },
    ]);
  });

  it('falls back to mirrored_<row id> when no provider id was ever stored', () => {
    const [track] = mirroredDiscoveryTracks([{ id: 42, track_name: 'x', artist_name: 'y' }]);
    expect(track.id).toBe('mirrored_42');
    // The empty-string defaults, not undefined (2077-2078).
    expect(track.album).toBe('');
    expect(track.duration_ms).toBe(0);
  });

  it('wraps the flat artist in a ONE-element array, never a split list', () => {
    const [track] = mirroredDiscoveryTracks([
      { id: 1, track_name: 't', artist_name: 'Simon & Garfunkel' },
    ]);
    expect(track.artists).toEqual(['Simon & Garfunkel']);
  });
});

describe('retryFailedMirroredDiscovery (2155-2194)', () => {
  function stub(response: unknown): { calls: string[]; toasts: [string, string][] } {
    const calls: string[] = [];
    const toasts: [string, string][] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push(`${init?.method ?? 'GET'} ${url}`);
        return new Response(JSON.stringify(response));
      }),
    );
    window.showToast = ((message: string, type: string) => {
      toasts.push([message, type]);
    }) as typeof window.showToast;
    return { calls, toasts };
  }

  function fakeVertical(state: Record<string, unknown>) {
    const resumed: string[] = [];
    let current = state;
    return {
      resumed,
      get state() {
        return current;
      },
      patchState: (_id: string, fn: (s: never) => never) => {
        current = fn(current as never);
      },
      resumeDiscovery: (id: string) => resumed.push(id),
    };
  }

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('strips the mirrored_ prefix to reach the playlist endpoint (2157)', async () => {
    const { calls } = stub({ retry_count: 3 });
    const vertical = fakeVertical({ spotifyMatches: 7 });
    await retryFailedMirroredDiscovery('mirrored_3', vertical as never);
    expect(calls).toEqual(['POST /api/mirrored-playlists/3/retry-failed-discovery']);
  });

  it('stamps the #815 baseline and resumes polling (2171-2188)', async () => {
    const { toasts } = stub({ retry_count: 5 });
    const vertical = fakeVertical({ spotifyMatches: 7 });
    await retryFailedMirroredDiscovery('mirrored_3', vertical as never);
    expect(vertical.state).toMatchObject({
      phase: 'discovering',
      discoveryProgress: 0,
      retryDiscovery: { matchesBefore: 7, retryCount: 5 },
    });
    expect(vertical.resumed).toEqual(['mirrored_3']);
    expect(toasts).toEqual([['Retrying 5 failed tracks...', 'info']]);
  });

  it('says so and changes NOTHING when nothing failed (2165-2168)', async () => {
    const { toasts } = stub({ retry_count: 0 });
    const vertical = fakeVertical({ spotifyMatches: 7 });
    await retryFailedMirroredDiscovery('mirrored_3', vertical as never);
    expect(toasts).toEqual([['All tracks already found!', 'success']]);
    // No baseline, no phase change, no poller — the early return (2167).
    expect(vertical.state).toEqual({ spotifyMatches: 7 });
    expect(vertical.resumed).toEqual([]);
  });

  it('reports a backend error and leaves the state alone (2161-2164)', async () => {
    const { toasts } = stub({ error: 'no such playlist' });
    const vertical = fakeVertical({ spotifyMatches: 7 });
    await retryFailedMirroredDiscovery('mirrored_3', vertical as never);
    expect(toasts).toEqual([['Error: no such playlist', 'error']]);
    expect(vertical.state).toEqual({ spotifyMatches: 7 });
  });

  it('uses its OWN wording when the request itself throws (2192)', async () => {
    const toasts: [string, string][] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('network down');
      }),
    );
    window.showToast = ((message: string, type: string) => {
      toasts.push([message, type]);
    }) as typeof window.showToast;
    const vertical = fakeVertical({ spotifyMatches: 7 });
    await retryFailedMirroredDiscovery('mirrored_3', vertical as never);
    expect(toasts).toEqual([['Error retrying discovery: network down', 'error']]);
  });
});
