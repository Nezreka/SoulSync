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
  // The four `load*` Settings edges that used to sit here are GONE, and
  // deliberately: their definitions were re-homed from beatport-ui.js into
  // settings.js, which survives, so they stopped being edges. See the rehome
  // guard below.
  // `initializeSyncPage <- init.js` and `loadSyncData <- init.js` were here.
  // The flip deleted both call sites: loadPageData's `case 'sync'` (React pages
  // never reach it) and the unconditional bootstrap call, whose every branch
  // looked up sync markup that no longer exists.
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

/**
 * Comments are NOT calls.
 *
 * Without this, a comment saying "initializeSyncPage() was here" counts as a
 * call and keeps a resolved edge in the inventory forever — which is exactly
 * what happened when the flip removed that call and left a note in its place.
 * An edge that cannot be retired is worse than no entry: it trains you to
 * ignore the list.
 */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/^[ \t]*\/\/.*$/gm, ' ');
}

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
    const source = stripComments(readFileSync(resolve(dir, file), 'utf8'));
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

  it('keeps the Settings pickers OUT of beatport-ui.js', () => {
    // This used to assert the opposite — that these functions sat in
    // beatport-ui.js — because that was the reality and the point was to make
    // the flip trip over it. They have now been re-homed into settings.js,
    // where they always belonged: they drive the Plex / Jellyfin / Navidrome
    // selects and have nothing to do with the Beatport tab.
    //
    // Inverted rather than deleted, because the failure mode is a REGRESSION:
    // somebody appends a Settings helper to a sync file again and it quietly
    // becomes deletable-by-association.
    const dir = resolve(process.cwd(), 'static');
    const settings = readFileSync(resolve(dir, 'settings.js'), 'utf8');
    const beatport = readFileSync(resolve(dir, 'beatport-ui.js'), 'utf8');
    for (const name of [
      'loadPlexMusicLibraries',
      'selectPlexLibrary',
      'loadJellyfinUsers',
      'selectJellyfinUser',
      'loadJellyfinMusicLibraries',
      'selectJellyfinLibrary',
      'loadNavidromeMusicFolders',
      'selectNavidromeMusicFolder',
    ]) {
      expect(settings, `${name} should be defined in settings.js`).toContain(
        `async function ${name}(`,
      );
      expect(beatport, `${name} must not be back in beatport-ui.js`).not.toContain(
        `function ${name}(`,
      );
    }
  });

  it('no inline handler in index.html calls into a doomed file', () => {
    // THE BLIND SPOT THIS GUARD HAD. computeEdges only reads `static/*.js`, so
    // a function reached ONLY from an `onclick`/`onchange` attribute was
    // invisible to it — which is exactly how `selectPlexLibrary` and its three
    // siblings escaped: index.html 4392/4456/4465/4492 were their only callers.
    //
    // This once carved out the sync page's own markup, since handlers inside it
    // would die with it. The flip has now deleted that markup, so the carve-out
    // is gone and the rule is simply: no handler anywhere may call into a file
    // the port deletes.
    const dir = resolve(process.cwd(), 'static');
    const all = readdirSync(dir).filter((f) => f.endsWith('.js'));
    const doomed = new Map<string, string>();
    for (const file of PORT_FILES) {
      if (!all.includes(file)) continue;
      const source = readFileSync(resolve(dir, file), 'utf8');
      for (const m of source.matchAll(/^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)/gm)) {
        if (!doomed.has(m[1])) doomed.set(m[1], file);
      }
    }

    const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');
    const offenders: string[] = [];
    html.split('\n').forEach((line, i) => {
      for (const m of line.matchAll(/\bon[a-z]+="\s*([A-Za-z_$][\w$]*)\s*\(/g)) {
        const home = doomed.get(m[1]);
        if (home) offenders.push(`${m[1]}() at index.html:${i + 1} [${home}]`);
      }
    });

    expect(
      offenders,
      'An inline handler calls a function the flip deletes.\n' +
        'Re-home the definition before deleting its file, or the attribute throws\n' +
        'ReferenceError the first time a user touches that control.',
    ).toEqual([]);
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
