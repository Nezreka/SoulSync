import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Guard the vanilla-side definitions that React code calls through `window`.
 *
 * These seams fail SILENTLY. Every call site is optional-chained
 * (`window.openSyncDetailModal?.(id)`), which is right at runtime — a missing
 * global must not throw — but it means deleting the definition produces no
 * error, no toast and no failing test. The button simply stops doing anything,
 * and the page it lives on is usually not the page whose vanilla region was
 * being deleted.
 *
 * That is a live hazard for the migration's deletion phases, which remove whole
 * regions from these files. This test turns "remember not to delete that" into
 * something that fails loudly the moment it happens.
 *
 * A deliberate failure here is fine — it means the seam MOVED. Re-home it (or
 * publish it from React) and update the row, rather than deleting the row to
 * make the suite green.
 *
 * Scope: this file covers REACT -> vanilla only. The vanilla -> vanilla edges
 * (a surviving script calling into a file the port deletes) are far more
 * numerous — 46 of them — and are covered by vanilla-crossfile.test.ts, which
 * COMPUTES them rather than listing them by hand. An earlier hand-written list
 * here caught 8 of those 46 and asserted nothing about the rest.
 */
const SEAMS: { file: string; symbol: string; pattern: RegExp; usedBy: string }[] = [
  {
    file: 'static/shared-helpers.js',
    symbol: 'playlistQualityProfileSelectHtml',
    pattern: /function playlistQualityProfileSelectHtml\b/,
    usedBy: "the Auto-Sync scheduled card's quality-profile select (routes/sync/-ui/autosync-shared.tsx)",
  },
  {
    file: 'static/shared-helpers.js',
    symbol: 'hydratePlaylistQualityProfileSelects',
    pattern: /function hydratePlaylistQualityProfileSelects\b/,
    usedBy: "the same card — the select renders EMPTY without it, silently",
  },
  {
    file: 'static/pages-extra.js',
    symbol: 'openSyncDetailModal',
    pattern: /async function openSyncDetailModal\b/,
    usedBy: "the dashboard's Recent Syncs card (routes/dashboard/-ui/syncs-card.tsx)",
  },
  {
    file: 'static/pages-extra.js',
    symbol: '_syncDetailFilter',
    pattern: /function _syncDetailFilter\b/,
    usedBy: "openSyncDetailModal's own filter pills, via inline onclick",
  },
  {
    file: 'static/pages-extra.js',
    symbol: '_readdSyncWishlist',
    pattern: /async function _readdSyncWishlist\b/,
    usedBy: "openSyncDetailModal's '→ Wishlist' button, via inline onclick",
  },
  {
    file: 'static/auto-sync.js',
    symbol: 'openAutoSyncScheduleModal',
    pattern: /async function openAutoSyncScheduleModal\b/,
    usedBy: "the dashboard's Quick Actions hero tile",
  },
  {
    file: 'static/sync-spotify.js',
    symbol: 'checkForActiveProcesses',
    pattern: /async function checkForActiveProcesses\b/,
    usedBy: 'the dashboard, to rehydrate the download-bubble registries',
  },
  {
    file: 'static/sync-spotify.js',
    symbol: 'openDownloadMissingModal',
    pattern: /async function openDownloadMissingModal\b/,
    usedBy: "React's account tabs, for the download hand-off",
  },
  {
    file: 'static/downloads.js',
    symbol: 'updateCardToSyncing',
    pattern: /function updateCardToSyncing\b/,
    usedBy: "React's Deezer-ARL tab, when rehydrating a sync in flight",
  },
  {
    file: 'static/downloads.js',
    symbol: 'startSyncPolling',
    pattern: /function startSyncPolling\b/,
    usedBy: "React's Deezer-ARL tab, when rehydrating a sync in flight",
  },
  {
    file: 'static/shared-helpers.js',
    symbol: 'registerBeatportDownload',
    pattern: /function registerBeatportDownload\b/,
    usedBy:
      "React's Beatport tab, for every release and chart download — it owns the " +
      'bubble registry, which is a top-level `let` in core.js',
  },
  {
    file: 'static/core.js',
    symbol: 'startDiscoverVirtualSync',
    pattern: /window\.startDiscoverVirtualSync\s*=/,
    usedBy: 'the React discover page — a bridge added BY this migration',
  },
  {
    file: 'static/core.js',
    symbol: 'registerSyncAccountPlaylist',
    pattern: /window\.registerSyncAccountPlaylist\s*=/,
    usedBy:
      "React's account tabs — a bridge added BY this migration, because " +
      '`spotifyPlaylists` is a top-level let no module can reach',
  },
];

describe('vanilla seams React calls through window still exist', () => {
  for (const { file, symbol, pattern, usedBy } of SEAMS) {
    it(`${symbol} (${file}) — ${usedBy}`, () => {
      const source = readFileSync(resolve(process.cwd(), file), 'utf8');
      expect(
        pattern.test(source),
        `${symbol} is gone from ${file}. It is called through window by ${usedBy}, ` +
          'and every such call is optional-chained — so this breaks SILENTLY rather ' +
          'than throwing. Re-home the seam or publish it from React; do not delete ' +
          'this row to go green.',
      ).toBe(true);
    });
  }
});
