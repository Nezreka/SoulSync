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
    usedBy:
      "the Auto-Sync scheduled card's quality-profile select (routes/sync/-ui/autosync-shared.tsx)",
  },
  {
    file: 'static/shared-helpers.js',
    symbol: 'hydratePlaylistQualityProfileSelects',
    pattern: /function hydratePlaylistQualityProfileSelects\b/,
    usedBy: 'the same card — the select renders EMPTY without it, silently',
  },
  {
    file: 'static/core.js',
    symbol: 'getSyncAccountPlaylists',
    // The `.slice()` is part of the contract, not styling: handing out the
    // live array would let any caller sort or splice the engine's own
    // playlist list, which startPlaylistSync resolves every id against.
    pattern:
      /window\.getSyncAccountPlaylists = function \(\) \{\s*return spotifyPlaylists\.slice\(\);/,
    usedBy:
      "the sync page's queue order and the sidebar's name lookup (routes/sync) — spotifyPlaylists is a top-level `let`, so it is NOT on window",
  },
  {
    file: 'static/core.js',
    symbol: 'isPlaylistSyncing',
    pattern: /window\.isPlaylistSyncing = function isPlaylistSyncing\b/,
    usedBy:
      "the sequential-sync runner's completion watch (routes/sync/-sync.use-sequential.ts) — activeSyncPollers is a top-level `let`, so it is NOT on window and this accessor is the only way to read it",
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
  /*
   * The five below were found by sweeping every `window.x` the sync route reads
   * and classifying it against the vanilla. All five are real, live calls that
   * had NO row here — they were typed in `declare global` blocks inside the
   * component files rather than in globals.d.ts, so nothing pointed the seam
   * list at them. A local declaration type-checks perfectly while asserting
   * nothing about the vanilla, which is the gap this file exists to close.
   */
  {
    file: 'static/stats-automations.js',
    symbol: 'openMirroredPlaylistModal',
    pattern: /async function openMirroredPlaylistModal\b/,
    usedBy:
      'the SoulSync Discovery tab after a mirror (routes/sync/-ui/soulsync-discovery-tab.tsx) ' +
      "and the Auto-Sync monitor's Details button — note S4 deletes _initImportFileTab from " +
      'this same file, so a region-sized deletion could take this with it',
  },
  {
    file: 'static/core.js',
    symbol: 'getActiveMetadataSource',
    pattern: /function getActiveMetadataSource\b/,
    usedBy:
      'metadataSourceLabel() (routes/sync/-sync.modal-core.ts) — the knowing fix for the ' +
      "vanilla's hardcoded 'Spotify' headers; without it the label silently reverts to the " +
      'wrong provider name rather than failing',
  },
  {
    file: 'static/shared-helpers.js',
    symbol: 'getMetadataSourceLabel',
    pattern: /function getMetadataSourceLabel\b/,
    usedBy: 'the other half of metadataSourceLabel() — maps the source key to its display name',
  },
  {
    file: 'static/downloads.js',
    symbol: 'wingItDownload',
    pattern: /async function wingItDownload\b/,
    usedBy: "the discovery modal's Download action (routes/sync/-ui/discovery-modal.tsx)",
  },
  {
    file: 'static/downloads.js',
    symbol: '_wingItSyncFromModal',
    pattern: /async function _wingItSyncFromModal\b/,
    usedBy: "the discovery modal's Sync action — the Wing It twin of the above",
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
