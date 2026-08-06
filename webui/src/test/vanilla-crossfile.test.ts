import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Every call that crosses OUT of the files the sync migration will delete.
 *
 * The sync port removes these seven files (or whole regions of them). Anything
 * defined in one of them but CALLED from a file that survives is an edge that
 * snaps at sever time — and in a classic script that is an unqualified global
 * call, so it snaps at the caller's next interaction, on a page nobody is
 * looking at while porting the sync page.
 *
 * The list is COMPUTED rather than hand-written. Hand-written is how the first
 * version of this guard missed 38 of the 46 edges: it listed the eight Settings
 * functions someone happened to notice and asserted the rest did not exist,
 * having checked a `head -40` of the evidence.
 *
 * A failure here is not a bug to paper over — it is the flip asking a question:
 * this edge is about to break, so re-home the definition, publish it from React,
 * or delete the caller too. Then update the snapshot deliberately.
 */
const PORT_FILES = [
  'sync-services.js',
  'sync-spotify.js',
  'sync-listenbrainz.js',
  'sync-lastfm.js',
  'sync-soulsync-discovery.js',
  'auto-sync.js',
  'beatport-ui.js',
];

/** name <- comma-separated surviving callers [file that defines it] */
const EXPECTED_EDGES = [
  'applyProgressiveTrackRendering <- downloads.js,shared-helpers.js [sync-spotify.js]',
  'autoSavePlaylistM3U <- downloads.js [sync-spotify.js]',
  'cleanupBeatportChartsSlider <- core.js [beatport-ui.js]',
  'cleanupBeatportDJSlider <- core.js [beatport-ui.js]',
  'cleanupBeatportHypePicksSlider <- core.js [beatport-ui.js]',
  'cleanupBeatportRebuildSlider <- core.js [beatport-ui.js]',
  'cleanupBeatportReleasesSlider <- core.js [beatport-ui.js]',
  'cleanupDownloadProcess <- downloads.js [sync-spotify.js]',
  'cleanupWishlist <- downloads.js [sync-services.js]',
  'clearWishlist <- downloads.js [sync-services.js]',
  'closeDeezerArlPlaylistDetailsModal <- downloads.js,shared-helpers.js [sync-services.js]',
  'closePlaylistDetailsModal <- downloads.js,shared-helpers.js [sync-spotify.js]',
  'editMirroredCustomName <- stats-automations.js [auto-sync.js]',
  'editMirroredSourceRef <- stats-automations.js [auto-sync.js]',
  'exportPlaylistAsM3U <- downloads.js,shared-helpers.js [sync-spotify.js]',
  'formatDuration <- downloads.js,shared-helpers.js,stats-automations.js,wishlist-tools.js [sync-services.js]',
  'generateDiscoveryActionButton <- wishlist-tools.js [sync-services.js]',
  'generateDownloadModalHeroSection <- downloads.js,shared-helpers.js [sync-spotify.js]',
  'getActionButtonText <- downloads.js [sync-spotify.js]',
  'getMirroredSourceRef <- stats-automations.js [auto-sync.js]',
  'initializeSyncPage <- init.js [sync-services.js]',
  'loadJellyfinMusicLibraries <- settings.js [beatport-ui.js]',
  'loadJellyfinUsers <- settings.js [beatport-ui.js]',
  'loadNavidromeMusicFolders <- settings.js [beatport-ui.js]',
  'loadPlexMusicLibraries <- settings.js [beatport-ui.js]',
  'loadSyncData <- init.js [sync-spotify.js]',
  'openDownloadMissingModal <- shared-helpers.js [sync-spotify.js]',
  'openYouTubeDiscoveryModal <- core.js,stats-automations.js [sync-services.js]',
  'pollMirroredPipelineStatus <- stats-automations.js [auto-sync.js]',
  'rehydrateModal <- api-monitor.js,core.js,downloads.js,init.js,wishlist-tools.js [sync-spotify.js]',
  'runMirroredPlaylistPipeline <- stats-automations.js [auto-sync.js]',
  'startListenBrainzDiscoveryPolling <- core.js [sync-services.js]',
  'startListenBrainzSyncPolling <- downloads.js [sync-services.js]',
  'startYouTubeDiscoveryPolling <- stats-automations.js [sync-services.js]',
  'startYouTubeSyncPolling <- downloads.js [sync-services.js]',
  'updateBeatportCardPhase <- downloads.js [sync-services.js]',
  'updateCompletedModalResults <- stats-automations.js [sync-services.js]',
  'updateDeezerCardPhase <- downloads.js [sync-services.js]',
  'updateDeezerCardProgress <- wishlist-tools.js [sync-services.js]',
  'updateITunesLinkCardProgress <- wishlist-tools.js [sync-services.js]',
  'updatePlaylistCardUI <- downloads.js [sync-spotify.js]',
  'updateSpotifyPublicCardPhase <- downloads.js [sync-services.js]',
  'updateSpotifyPublicCardProgress <- wishlist-tools.js [sync-services.js]',
  'updateTidalCardPhase <- downloads.js [sync-services.js]',
  'updateYouTubeCardPhase <- downloads.js [sync-services.js]',
  'updateYouTubeModalButtons <- downloads.js,stats-automations.js [sync-services.js]',
];

function computeEdges(): string[] {
  const dir = resolve(process.cwd(), 'static');
  const all = readdirSync(dir).filter((f) => f.endsWith('.js'));
  const survivors = all.filter((f) => !PORT_FILES.includes(f)).sort();

  const definedIn = new Map<string, string>();
  for (const file of PORT_FILES) {
    if (!all.includes(file)) continue;
    const source = readFileSync(resolve(dir, file), 'utf8');
    for (const m of source.matchAll(/^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)/gm)) {
      if (!definedIn.has(m[1])) definedIn.set(m[1], file);
    }
  }

  const callers = new Map<string, Set<string>>();
  for (const file of survivors) {
    const source = readFileSync(resolve(dir, file), 'utf8');
    for (const m of source.matchAll(/\b([A-Za-z_$][\w$]*)\s*\(/g)) {
      if (!definedIn.has(m[1])) continue;
      if (!callers.has(m[1])) callers.set(m[1], new Set());
      callers.get(m[1])!.add(file);
    }
  }

  return [...callers.keys()]
    .sort()
    .map(
      (name) => `${name} <- ${[...callers.get(name)!].sort().join(',')} [${definedIn.get(name)}]`,
    );
}

describe('calls that cross out of the files the sync port deletes', () => {
  it('matches the recorded inventory exactly', () => {
    const actual = computeEdges();
    const missing = EXPECTED_EDGES.filter((e) => !actual.includes(e));
    const added = actual.filter((e) => !EXPECTED_EDGES.includes(e));

    expect(
      { missing, added },
      'The cross-file edge inventory moved.\n' +
        'GONE means a definition (or its caller) was removed — if the definition ' +
        'went while the caller survives, that caller is now broken at runtime.\n' +
        'NEW means fresh coupling into a doomed file.\n' +
        'Re-home, publish from React, or remove the caller too — then update ' +
        'EXPECTED_EDGES on purpose.',
    ).toEqual({ missing: [], added: [] });
  });

  it('covers the Settings functions squatting in beatport-ui.js', () => {
    // The specific case that exposed the hand-written list as unsound: these
    // belong to the SETTINGS page, not to the Beatport tab the file is named for.
    const actual = computeEdges();
    for (const name of [
      'loadPlexMusicLibraries',
      'loadJellyfinUsers',
      'loadJellyfinMusicLibraries',
      'loadNavidromeMusicFolders',
    ]) {
      expect(actual).toContain(`${name} <- settings.js [beatport-ui.js]`);
    }
  });

  it('covers the Beatport slider teardown core.js drives on page-leave', () => {
    const actual = computeEdges();
    for (const name of [
      'cleanupBeatportRebuildSlider',
      'cleanupBeatportReleasesSlider',
      'cleanupBeatportHypePicksSlider',
      'cleanupBeatportChartsSlider',
      'cleanupBeatportDJSlider',
    ]) {
      expect(actual).toContain(`${name} <- core.js [beatport-ui.js]`);
    }
  });
});
