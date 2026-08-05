/**
 * Sync pure core — differential + literal pinning.
 *
 * Where the vanilla body is executable outside the DOM it is lifted with
 * extractFunction and run BESIDE the port over a matrix including the awkward
 * inputs — the discover-port method, so the port matches the code it replaces
 * rather than what the porter believed it did. The sync family is still live
 * in webui/static, so the source is read from there; when the flip PR deletes
 * it, these switch to frozen fixtures the way the downloads port did.
 *
 * Regions that only exist inline in DOM functions (the hero-source ladder, the
 * isFound expressions, the M3U prefix lists) are pinned with LITERAL
 * assertions transcribed at read time (file:line in each describe), plus a raw
 * source anchor where a silent vanilla edit would otherwise go unnoticed.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import type { DiscoveryResultRow } from './-sync.types';

import { extractFunction } from '../../test/vanilla-extract';
import {
  M3U_AUTOSAVE_SKIP_PREFIXES,
  M3U_EXPORT_ALBUM_PREFIXES,
  actionButtonText,
  discoveryRowAction,
  downloadTaskStatusText,
  finalDownloadProgress,
  formatDuration,
  heroSourceLabel,
  isFoundRowLenient,
  isFoundRowQobuz,
  isWingItRow,
  m3uAutoSaveSkipped,
  m3uExportContextType,
  phaseColor,
  phaseText,
  progressWidth,
  virtualPlaylistIdFor,
} from './-sync.core';

const SYNC_SPOTIFY = readFileSync(resolve(process.cwd(), 'static/sync-spotify.js'), 'utf8');
const SYNC_SERVICES = readFileSync(resolve(process.cwd(), 'static/sync-services.js'), 'utf8');

/** Lift named vanilla functions from a given source file and evaluate them. */
function lift<T>(names: string[], source: string): T {
  const body = names.map((n) => extractFunction(n, source)).join('\n');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(`${body}\nreturn { ${names.join(', ')} };`)() as T;
}

/* ── The 7-phase maps (sync-spotify.js 1394-1433) ─────────────────────────── */

type PhaseMapVanilla = {
  getActionButtonText: (p: string) => string;
  getPhaseText: (p: string) => string;
  getPhaseColor: (p: string) => string;
  getProgressWidth: (info: Record<string, unknown>) => number;
};

const phaseVanilla = lift<PhaseMapVanilla>(
  ['getActionButtonText', 'getPhaseText', 'getPhaseColor', 'getProgressWidth'],
  SYNC_SPOTIFY,
);

const ALL_PHASES = [
  'fresh',
  'discovering',
  'discovered',
  'syncing',
  'sync_complete',
  'downloading',
  'download_complete',
  'some_future_phase',
  '',
];

describe('phase maps (differential vs sync-spotify.js)', () => {
  it('actionButtonText matches the vanilla over every phase', () => {
    for (const phase of ALL_PHASES) {
      expect(actionButtonText(phase)).toBe(phaseVanilla.getActionButtonText(phase));
    }
  });

  it('phaseText matches the vanilla over every phase', () => {
    for (const phase of ALL_PHASES) {
      expect(phaseText(phase)).toBe(phaseVanilla.getPhaseText(phase));
    }
  });

  it('phaseColor matches the vanilla over every phase', () => {
    for (const phase of ALL_PHASES) {
      expect(phaseColor(phase)).toBe(phaseVanilla.getPhaseColor(phase));
    }
  });

  it('pins the user-facing words as literals', () => {
    expect(actionButtonText('fresh')).toBe('Discover');
    expect(actionButtonText('sync_complete')).toBe('Download');
    expect(actionButtonText('download_complete')).toBe('Complete');
    expect(actionButtonText('anything else')).toBe('Open');
    expect(phaseText('fresh')).toBe('Ready to discover');
    expect(phaseText('discovered')).toBe('Discovery Complete');
    // Unknown phases echo straight through — getPhaseText's default arm.
    expect(phaseText('some_future_phase')).toBe('some_future_phase');
    expect(phaseColor('fresh')).toBe('#999');
    expect(phaseColor('syncing')).toBe('#ffa500');
    expect(phaseColor('sync_complete')).toBe('rgb(var(--accent-rgb))');
  });
});

describe('progressWidth (differential vs sync-spotify.js)', () => {
  const CASES = [
    { phase: 'fresh', spotify_total: 100, spotify_matches: 50 },
    { phase: 'discovered', spotify_total: 0, spotify_matches: 0 },
    { phase: 'discovered', spotify_total: 3, spotify_matches: 1 },
    { phase: 'discovered', spotify_total: 3, spotify_matches: 2 },
    { phase: 'syncing', spotify_total: 7, spotify_matches: 7 },
    { phase: 'discovered', spotify_total: 10, spotify_matches: 15 }, // matches > total
    { phase: 'discovered', spotify_total: 4, spotify_matches: -1 },
    { phase: 'discovered' }, // undefined totals — the NaN case
    { phase: 'downloading', spotify_matches: 5 },
  ];

  it('matches the vanilla over the matrix, NaN included', () => {
    for (const info of CASES) {
      const ours = progressWidth(info as never);
      const theirs = phaseVanilla.getProgressWidth(info as never);
      expect(Object.is(ours, theirs), JSON.stringify(info)).toBe(true);
    }
  });

  it('pins the arithmetic as literals', () => {
    expect(progressWidth({ phase: 'fresh', spotify_total: 100, spotify_matches: 50 })).toBe(0);
    expect(progressWidth({ phase: 'discovered', spotify_total: 0, spotify_matches: 0 })).toBe(0);
    expect(progressWidth({ phase: 'discovered', spotify_total: 3, spotify_matches: 1 })).toBe(33);
    expect(progressWidth({ phase: 'discovered', spotify_total: 3, spotify_matches: 2 })).toBe(67);
    expect(Number.isNaN(progressWidth({ phase: 'discovered' }))).toBe(true);
  });
});

/* ── Hero source ladder (sync-services.js 1362-1375, inline — literals) ───── */

describe('heroSourceLabel', () => {
  it('maps every prefix branch', () => {
    expect(heroSourceLabel('beatport_abc')).toBe('Beatport');
    expect(heroSourceLabel('tidal_123')).toBe('Tidal');
    expect(heroSourceLabel('qobuz_123')).toBe('Qobuz');
    expect(heroSourceLabel('listenbrainz_mbid')).toBe('ListenBrainz');
    expect(heroSourceLabel('spotify_public_h4sh')).toBe('Spotify');
    expect(heroSourceLabel('itunes_link_h4sh')).toBe('iTunes');
    expect(heroSourceLabel('spotify:playlist:xyz')).toBe('Spotify');
    expect(heroSourceLabel('discover_release_radar')).toBe('SoulSync');
    expect(heroSourceLabel('seasonal_album_1')).toBe('SoulSync');
    expect(heroSourceLabel('spotify_library_1')).toBe('SoulSync');
    expect(heroSourceLabel('build_playlist_custom')).toBe('SoulSync');
    expect(heroSourceLabel('decade_1990')).toBe('SoulSync');
    expect(heroSourceLabel('youtube_h4sh')).toBe('YouTube');
    expect(heroSourceLabel('anything-else')).toBe('YouTube');
  });

  it('spotify_public_ wins over the spotify: arm (ladder order is load-bearing)', () => {
    // A spotify_public_ id never reaches the spotify: branch, and a bare
    // spotify_library_ id must NOT read as public Spotify.
    expect(heroSourceLabel('spotify_library_x')).toBe('SoulSync');
  });

  it('anchors the vanilla ladder so a silent edit there fails here', () => {
    expect(SYNC_SERVICES).toContain("virtualPlaylistId.startsWith('spotify_public_') ? 'Spotify'");
    expect(SYNC_SERVICES).toContain("virtualPlaylistId === 'build_playlist_custom' ? 'SoulSync'");
  });
});

/* ── Virtual id ladder (sync-services.js 10761, inline — literals) ────────── */

describe('virtualPlaylistIdFor', () => {
  it('maps each single flag', () => {
    expect(virtualPlaylistIdFor({ is_listenbrainz_playlist: true }, 'h')).toBe('listenbrainz_h');
    expect(virtualPlaylistIdFor({ is_deezer_playlist: true }, 'h')).toBe('deezer_h');
    expect(virtualPlaylistIdFor({ is_beatport_playlist: true }, 'h')).toBe('beatport_h');
    expect(virtualPlaylistIdFor({ is_tidal_playlist: true }, 'h')).toBe('tidal_h');
    expect(virtualPlaylistIdFor({ is_qobuz_playlist: true }, 'h')).toBe('qobuz_h');
    expect(virtualPlaylistIdFor({}, 'h')).toBe('youtube_h');
  });

  it('resolves flag collisions in the vanilla priority order (LB > Deezer > Beatport > Tidal > Qobuz)', () => {
    expect(
      virtualPlaylistIdFor(
        {
          is_listenbrainz_playlist: true,
          is_deezer_playlist: true,
          is_beatport_playlist: true,
          is_tidal_playlist: true,
          is_qobuz_playlist: true,
        },
        'h',
      ),
    ).toBe('listenbrainz_h');
    expect(virtualPlaylistIdFor({ is_deezer_playlist: true, is_tidal_playlist: true }, 'h')).toBe(
      'deezer_h',
    );
    expect(virtualPlaylistIdFor({ is_qobuz_playlist: true, is_beatport_playlist: true }, 'h')).toBe(
      'beatport_h',
    );
  });
});

/* ── Found/wing-it predicates (inline expressions — literals + source anchor) ── */

describe('discovery row predicates', () => {
  it('isWingItRow accepts the flag or the status class', () => {
    expect(isWingItRow({ wing_it_fallback: true })).toBe(true);
    expect(isWingItRow({ status_class: 'wing-it' })).toBe(true);
    expect(isWingItRow({ status_class: 'found' })).toBe(false);
    expect(isWingItRow({})).toBe(false);
  });

  it('isFoundRowLenient accepts both status spellings, the class, and bare matched data', () => {
    expect(isFoundRowLenient({ status: 'found' })).toBe(true);
    expect(isFoundRowLenient({ status: '✅ Found' })).toBe(true);
    expect(isFoundRowLenient({ status_class: 'found' })).toBe(true);
    expect(isFoundRowLenient({ spotify_data: { id: 'x' } })).toBe(true);
    expect(isFoundRowLenient({ spotify_track: 'HUMBLE.' })).toBe(true);
    expect(isFoundRowLenient({ status: 'Found' })).toBe(false); // Qobuz-only literal
    expect(isFoundRowLenient({ status: 'not_found' })).toBe(false);
    expect(isFoundRowLenient({})).toBe(false);
  });

  it("isFoundRowQobuz additionally accepts the bare 'Found' its backend emits", () => {
    expect(isFoundRowQobuz({ status: 'Found' })).toBe(true);
    expect(isFoundRowQobuz({ status: 'found' })).toBe(true);
    expect(isFoundRowQobuz({ status: 'nope' })).toBe(false);
  });

  it("anchors the vanilla's Qobuz-only 'Found' literal", () => {
    expect(SYNC_SERVICES).toContain("result.status === 'Found' ||");
  });
});

/* ── Actions cell (differential vs generateDiscoveryActionButton) ─────────── */

type ActionVanilla = {
  generateDiscoveryActionButton: (
    result: DiscoveryResultRow,
    identifier: string,
    platform: string,
  ) => string;
};

const actionVanilla = lift<ActionVanilla>(['generateDiscoveryActionButton'], SYNC_SERVICES);

/** Collapse the vanilla's HTML output to the classification the port returns. */
function classifyVanillaAction(html: string): string {
  if (html.includes('Search for a proper metadata match')) return 'fix-wing-it';
  if (html.includes('fix-match-btn')) return 'fix';
  if (html.includes('rematch-btn')) return 'rematch';
  return 'none';
}

describe('discoveryRowAction (differential vs sync-services.js)', () => {
  const ROWS: DiscoveryResultRow[] = [
    { status: 'not_found', index: 0 },
    { status_class: 'not-found', index: 1 },
    { status: '❌ Not Found', index: 2 },
    { status: 'Not Found', index: 3 },
    { status: 'error', index: 4 },
    { status_class: 'error', index: 5 },
    { status: '❌ Error', index: 6 },
    { wing_it_fallback: true, index: 7 },
    { status_class: 'wing-it', index: 8 },
    { status: 'found', index: 9 },
    { status_class: 'found', index: 10 },
    { status: '✅ Found', index: 11 },
    // Precedence: not-found + wing-it flag → fix outranks wing-it.
    { status: 'not_found', wing_it_fallback: true, index: 12 },
    // Wing-it + found class → wing-it outranks found.
    { wing_it_fallback: true, status_class: 'found', index: 13 },
    // Matched data alone does NOT make the actions cell 'found' (strict test).
    { spotify_data: { id: 'x' }, index: 14 },
    { spotify_track: 'HUMBLE.', index: 15 },
    { status: 'Found', index: 16 }, // Qobuz literal is NOT in this function's union
    { index: 17 },
  ];

  it('classifies every row exactly as the vanilla renders it', () => {
    for (const row of ROWS) {
      const theirs = classifyVanillaAction(
        actionVanilla.generateDiscoveryActionButton(row, 'hash', 'youtube'),
      );
      expect(discoveryRowAction(row), JSON.stringify(row)).toBe(theirs);
    }
  });

  it('pins the surprising arms as literals', () => {
    expect(discoveryRowAction({ status: 'not_found', wing_it_fallback: true })).toBe('fix');
    expect(discoveryRowAction({ wing_it_fallback: true, status_class: 'found' })).toBe(
      'fix-wing-it',
    );
    expect(discoveryRowAction({ spotify_data: { id: 'x' } })).toBe('none');
    expect(discoveryRowAction({ status: 'Found' })).toBe('none');
  });
});

/* ── Download task status text (updateCompletedModalResults 380-390 — literals) ── */

describe('downloadTaskStatusText', () => {
  it('maps every backend status', () => {
    expect(downloadTaskStatusText('pending')).toBe('⏸️ Pending');
    expect(downloadTaskStatusText('searching')).toBe('🔍 Searching...');
    expect(downloadTaskStatusText('downloading', 42.4)).toBe('⏬ Downloading... 42%');
    expect(downloadTaskStatusText('downloading')).toBe('⏬ Downloading... 0%');
    expect(downloadTaskStatusText('post_processing')).toBe('⌛ Processing...');
    expect(downloadTaskStatusText('completed')).toBe('✅ Completed');
    expect(downloadTaskStatusText('not_found')).toBe('🔇 Not Found');
    expect(downloadTaskStatusText('failed')).toBe('❌ Failed');
    expect(downloadTaskStatusText('cancelled')).toBe('🚫 Cancelled');
    expect(downloadTaskStatusText('imported')).toBe('⚪ imported');
  });
});

describe('finalDownloadProgress', () => {
  it('tallies terminal statuses and formats the bar text', () => {
    const out = finalDownloadProgress(
      [
        { status: 'completed' },
        { status: 'completed' },
        { status: 'failed' },
        { status: 'cancelled' },
        { status: 'not_found' },
        { status: 'downloading' }, // in-flight tasks are not tallied
        { status: 'searching' },
      ],
      6,
    );
    expect(out.completed).toBe(2);
    expect(out.failedOrCancelled).toBe(2);
    expect(out.notFound).toBe(1);
    expect(out.percent).toBeCloseTo(83.333, 3);
    expect(out.text).toBe('2/6 completed (83%)');
  });

  it('an empty missing set reads as 100% done', () => {
    const out = finalDownloadProgress([], 0);
    expect(out.percent).toBe(100);
    expect(out.text).toBe('0/0 completed (100%)');
  });
});

/* ── M3U prefix lists (sync-spotify.js 2427-2431 vs 2559 — the drift is real) ── */

describe('M3U context prefixes', () => {
  it('auto-save skips every non-playlist prefix, the redownload family included', () => {
    for (const p of [
      'artist_album_',
      'discover_album_',
      'enhanced_search_album_',
      'enhanced_search_track_',
      'seasonal_album_',
      'spotify_library_',
      'beatport_release_',
      'discover_cache_',
      'issue_download_',
      'library_redownload_',
      'redownload_',
    ]) {
      expect(m3uAutoSaveSkipped(`${p}xyz`), p).toBe(true);
    }
    expect(m3uAutoSaveSkipped('spotify:playlist:abc')).toBe(false);
    expect(m3uAutoSaveSkipped('tidal_123')).toBe(false);
  });

  it('export album-context list is the SHORTER one — the redownload family exports as playlist', () => {
    expect(m3uExportContextType('artist_album_x')).toBe('album');
    expect(m3uExportContextType('discover_cache_x')).toBe('album');
    expect(m3uExportContextType('enhanced_search_track_x')).toBe('playlist');
    expect(m3uExportContextType('issue_download_x')).toBe('playlist');
    expect(m3uExportContextType('library_redownload_x')).toBe('playlist');
    expect(m3uExportContextType('redownload_x')).toBe('playlist');
    expect(m3uExportContextType('spotify:playlist:abc')).toBe('playlist');
  });

  it('pins both list lengths so an added prefix must be added here too', () => {
    expect(M3U_AUTOSAVE_SKIP_PREFIXES).toHaveLength(11);
    expect(M3U_EXPORT_ALBUM_PREFIXES).toHaveLength(7);
  });

  it('anchors both vanilla lists so a silent edit there fails here', () => {
    expect(SYNC_SPOTIFY).toContain("'issue_download_', 'library_redownload_', 'redownload_',");
    expect(SYNC_SPOTIFY).toContain(
      "const albumPrefixes = ['artist_album_', 'discover_album_', 'enhanced_search_album_', 'seasonal_album_', 'spotify_library_', 'beatport_release_', 'discover_cache_'];",
    );
  });
});

/* ── formatDuration (differential vs BOTH vanilla twins) ──────────────────── */

describe('formatDuration (differential vs the load-order WINNER)', () => {
  /**
   * THREE vanilla declarations exist and they disagree: sync-spotify.js 1967
   * has no falsy guard (undefined → 'NaN:NaN'), wishlist-tools.js 1575 guards
   * to '--:--', sync-services.js 10036 guards to '0:00'. All are plain global
   * function declarations, so index.html load order (sync-spotify →
   * wishlist-tools → sync-services) makes the sync-services copy the ONE every
   * runtime caller actually gets. The port matches the winner.
   */
  const servicesVanilla = lift<{ formatDuration: (ms: unknown) => string }>(
    ['formatDuration'],
    SYNC_SERVICES,
  );
  const spotifyVanilla = lift<{ formatDuration: (ms: unknown) => string }>(
    ['formatDuration'],
    SYNC_SPOTIFY,
  );

  const MATRIX = [
    0,
    null,
    undefined,
    NaN,
    500,
    999,
    1000,
    59999,
    60000,
    61500,
    3599999,
    3600000,
    -1000,
  ];

  it('matches the surviving sync-services copy over the matrix', () => {
    for (const ms of MATRIX) {
      expect(formatDuration(ms as never), String(ms)).toBe(servicesVanilla.formatDuration(ms));
    }
  });

  it('documents the shadowed sync-spotify twin drift the differential caught', () => {
    // The twins only disagree on the falsy guard; the port deliberately does
    // NOT match the shadowed copy. If this starts failing, the twins changed.
    expect(spotifyVanilla.formatDuration(undefined)).toBe('NaN:NaN');
    expect(servicesVanilla.formatDuration(undefined)).toBe('0:00');
  });

  it('pins the shape as literals', () => {
    expect(formatDuration(0)).toBe('0:00');
    expect(formatDuration(null)).toBe('0:00');
    expect(formatDuration(61500)).toBe('1:01');
    expect(formatDuration(3600000)).toBe('60:00'); // no hour rollover, by design
  });
});
