/**
 * Sync page — pure core.
 *
 * Every function here is a 1:1 port of a vanilla body (file:line cited per
 * function). Behaviour is pinned by -sync.core.test.ts: differential tests run
 * the REAL vanilla bodies where they are executable, literal assertions pin the
 * regions that only exist inline in DOM code.
 */

import type {
  DiscoveryResultRow,
  DiscoveryRowAction,
  DownloadTask,
  PhaseProgressInfo,
  VirtualSourceFlags,
} from './-sync.types';

/* ── The 7-phase maps (sync-spotify.js 1394-1433) ─────────────────────────── */

export function actionButtonText(phase: string): string {
  switch (phase) {
    case 'fresh':
      return 'Discover';
    case 'discovering':
      return 'View Progress';
    case 'discovered':
      return 'View Results';
    case 'syncing':
      return 'View Sync';
    case 'sync_complete':
      return 'Download';
    case 'downloading':
      return 'View Downloads';
    case 'download_complete':
      return 'Complete';
    default:
      return 'Open';
  }
}

export function phaseText(phase: string): string {
  switch (phase) {
    case 'fresh':
      return 'Ready to discover';
    case 'discovering':
      return 'Discovering...';
    case 'discovered':
      return 'Discovery Complete';
    case 'syncing':
      return 'Syncing...';
    case 'sync_complete':
      return 'Sync Complete';
    case 'downloading':
      return 'Downloading...';
    case 'download_complete':
      return 'Download Complete';
    default:
      return phase;
  }
}

export function phaseColor(phase: string): string {
  switch (phase) {
    case 'fresh':
      return '#999';
    case 'discovering':
    case 'syncing':
    case 'downloading':
      return '#ffa500';
    case 'discovered':
    case 'sync_complete':
    case 'download_complete':
      return 'rgb(var(--accent-rgb))';
    default:
      return '#999';
  }
}

/**
 * Card progress-bar width in percent. Vanilla returns NaN when spotify_total
 * is undefined (undefined !== 0, then undefined arithmetic) — preserved, the
 * card renders `width: NaN%` which the browser drops, so the bar stays where
 * it was. Callers that need a number must guard.
 */
export function progressWidth(info: PhaseProgressInfo): number {
  if (info.phase === 'fresh') return 0;
  if (info.spotify_total === 0) return 0;
  return Math.round(((info.spotify_matches as number) / (info.spotify_total as number)) * 100);
}

/* ── Virtual-playlist id ladders ──────────────────────────────────────────── */

/**
 * Hero "owner" label from a virtual playlist id — the prefix ladder in
 * openDownloadMissingModalForTidal (sync-services.js 1362-1375). Order is
 * load-bearing: spotify_public_ must beat spotify:, and the final
 * `=== 'build_playlist_custom'` arm is unreachable because the build_playlist_
 * prefix already claims it — preserved verbatim anyway so the port and the
 * vanilla cannot disagree on any input.
 */
export function heroSourceLabel(virtualPlaylistId: string): string {
  return virtualPlaylistId.startsWith('beatport_')
    ? 'Beatport'
    : virtualPlaylistId.startsWith('tidal_')
      ? 'Tidal'
      : virtualPlaylistId.startsWith('qobuz_')
        ? 'Qobuz'
        : virtualPlaylistId.startsWith('listenbrainz_')
          ? 'ListenBrainz'
          : virtualPlaylistId.startsWith('spotify_public_')
            ? 'Spotify'
            : virtualPlaylistId.startsWith('itunes_link_')
              ? 'iTunes'
              : virtualPlaylistId.startsWith('spotify:')
                ? 'Spotify'
                : virtualPlaylistId.startsWith('discover_')
                  ? 'SoulSync'
                  : virtualPlaylistId.startsWith('seasonal_')
                    ? 'SoulSync'
                    : virtualPlaylistId.startsWith('spotify_library_')
                      ? 'SoulSync'
                      : virtualPlaylistId.startsWith('build_playlist_')
                        ? 'SoulSync'
                        : virtualPlaylistId.startsWith('decade_')
                          ? 'SoulSync'
                          : virtualPlaylistId === 'build_playlist_custom'
                            ? 'SoulSync'
                            : 'YouTube';
}

/**
 * Virtual playlist id for a discovery state's download hand-off — the flag
 * ladder in startYouTubeDownloadMissing (sync-services.js 10787). Priority:
 * ListenBrainz > Deezer > Beatport > Tidal > Qobuz > YouTube.
 */
export function virtualPlaylistIdFor(flags: VirtualSourceFlags, urlHash: string): string {
  return flags.is_listenbrainz_playlist
    ? `listenbrainz_${urlHash}`
    : flags.is_deezer_playlist
      ? `deezer_${urlHash}`
      : flags.is_beatport_playlist
        ? `beatport_${urlHash}`
        : flags.is_tidal_playlist
          ? `tidal_${urlHash}`
          : flags.is_qobuz_playlist
            ? `qobuz_${urlHash}`
            : `youtube_${urlHash}`;
}

/* ── Discovery-row status predicates (the per-source drift, made explicit) ── */

/** wing_it_fallback flag or the 'wing-it' status class (sync-services.js 630 et al). */
export function isWingItRow(r: DiscoveryResultRow): boolean {
  return Boolean(r.wing_it_fallback || r.status_class === 'wing-it');
}

/**
 * The LENIENT match test used by the Tidal/Deezer/YouTube transforms
 * (sync-services.js 491, 635, 2951...): any of the two status spellings, the
 * status class, or the mere presence of matched data counts.
 */
export function isFoundRowLenient(r: DiscoveryResultRow): boolean {
  return Boolean(
    r.status === 'found' ||
    r.status === '✅ Found' ||
    r.status_class === 'found' ||
    r.spotify_data ||
    r.spotify_track,
  );
}

/**
 * Qobuz's variant (sync-services.js 1832, 1920...): its backend also emits the
 * bare capitalised 'Found', so the union grows by one literal. The unified
 * controller must use this superset for Qobuz rows.
 */
export function isFoundRowQobuz(r: DiscoveryResultRow): boolean {
  return isFoundRowLenient(r) || r.status === 'Found';
}

/**
 * Actions-cell classification — generateDiscoveryActionButton
 * (sync-services.js 10072-10121). NOTE this uses a STRICTER found-test than
 * the transforms (no spotify_data/spotify_track fallback), and not-found/error
 * outranks wing-it, which outranks found.
 */
export function discoveryRowAction(r: DiscoveryResultRow): DiscoveryRowAction {
  const isNotFound =
    r.status === 'not_found' ||
    r.status_class === 'not-found' ||
    r.status === '❌ Not Found' ||
    r.status === 'Not Found';
  const isError = r.status === 'error' || r.status_class === 'error' || r.status === '❌ Error';
  const isWingIt = Boolean(r.wing_it_fallback || r.status_class === 'wing-it');
  const isFound = r.status === 'found' || r.status_class === 'found' || r.status === '✅ Found';

  if (isNotFound || isError) return 'fix';
  if (isWingIt) return 'fix-wing-it';
  if (isFound) return 'rematch';
  return 'none';
}

/* ── Download-task status painting (updateCompletedModalResults, sync-services.js 380-390) ── */

export function downloadTaskStatusText(status: string, progress?: number): string {
  switch (status) {
    case 'pending':
      return '⏸️ Pending';
    case 'searching':
      return '🔍 Searching...';
    case 'downloading':
      return `⏬ Downloading... ${Math.round(progress || 0)}%`;
    case 'post_processing':
      return '⌛ Processing...';
    case 'completed':
      return '✅ Completed';
    case 'not_found':
      return '🔇 Not Found';
    case 'failed':
      return '❌ Failed';
    case 'cancelled':
      return '🚫 Cancelled';
    default:
      return `⚪ ${status}`;
  }
}

export interface FinalDownloadProgress {
  completed: number;
  notFound: number;
  failedOrCancelled: number;
  percent: number;
  text: string;
}

/**
 * Final download-bar state from a completed batch's tasks — the tally +
 * formula at sync-services.js 367-418. Missing count comes from the analysis
 * results (tracks not found in the library), not from the task list.
 */
export function finalDownloadProgress(
  tasks: DownloadTask[],
  missingCount: number,
): FinalDownloadProgress {
  let completed = 0;
  let failedOrCancelled = 0;
  let notFound = 0;
  for (const task of tasks) {
    if (task.status === 'completed') completed++;
    else if (task.status === 'failed' || task.status === 'cancelled') failedOrCancelled++;
    else if (task.status === 'not_found') notFound++;
  }
  const totalFinished = completed + failedOrCancelled + notFound;
  const percent = missingCount > 0 ? (totalFinished / missingCount) * 100 : 100;
  return {
    completed,
    notFound,
    failedOrCancelled,
    percent,
    text: `${completed}/${missingCount} completed (${percent.toFixed(0)}%)`,
  };
}

/* ── M3U context prefixes (sync-spotify.js 2427-2431 and 2559) ────────────── */

/**
 * Downloads whose playlistId starts with one of these never auto-save an M3U
 * (autoSavePlaylistM3U). This list is DELIBERATELY LONGER than the export
 * list below — the vanilla lists drifted (autoSave gained the redownload
 * prefixes; export never did) and the port preserves both verbatim.
 */
export const M3U_AUTOSAVE_SKIP_PREFIXES: readonly string[] = [
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
];

/** Prefixes that make exportPlaylistAsM3U send context_type 'album'. */
export const M3U_EXPORT_ALBUM_PREFIXES: readonly string[] = [
  'artist_album_',
  'discover_album_',
  'enhanced_search_album_',
  'seasonal_album_',
  'spotify_library_',
  'beatport_release_',
  'discover_cache_',
];

export function m3uAutoSaveSkipped(playlistId: string): boolean {
  return M3U_AUTOSAVE_SKIP_PREFIXES.some((p) => playlistId.startsWith(p));
}

export function m3uExportContextType(playlistId: string): 'album' | 'playlist' {
  return M3U_EXPORT_ALBUM_PREFIXES.some((p) => playlistId.startsWith(p)) ? 'album' : 'playlist';
}

/* ── The discovery-complete toast (_discoveryCompleteToast, 9192-9205) ─────── */

/** The #815 retry baseline retryFailedMirroredDiscovery stamps (2183-2186). */
export interface RetryDiscoveryBaseline {
  matchesBefore: number;
  retryCount: number;
}

export interface ToastSpec {
  message: string;
  type: 'success' | 'info';
}

/**
 * Which toast a finished discovery raises — null for the six verticals that
 * raise none.
 *
 * The vanilla is NOT uniform here and the port must not make it so: only
 * youtube and mirrored call _discoveryCompleteToast (9233 socket, 9281 poll,
 * and mirrored rides that same poller — stats-automations.js 637/1037/2054/
 * 2188), and listenbrainz raises its own differently-worded toast inline
 * (11076, 11171). Tidal, Qobuz, Deezer, Spotify-public, iTunes-link and
 * Beatport complete with a console.log and no toast at all (759, 3180, 7106,
 * 8132 and twins). `defaultMessage` therefore comes from the config table.
 *
 * `retry` is the #815 override, mirrored-only in practice: it reports how many
 * of the retried tracks were newly found against the baseline instead of the
 * generic line. The caller must drop the baseline afterwards — the vanilla
 * deletes it at 9198 so the NEXT plain discovery reads as plain.
 */
export function discoveryCompleteToast(
  defaultMessage: string | null,
  spotifyMatches: number,
  retry?: RetryDiscoveryBaseline | null,
): ToastSpec | null {
  if (retry) {
    const found = Math.max(0, (spotifyMatches || 0) - (retry.matchesBefore || 0));
    // `found`'s clamp IS observable — it prints. `stillFailed`'s is not: the
    // value is only ever tested `> 0` below, so every negative behaves exactly
    // as 0 does. Transcribed from 9197 because the vanilla clamps, but no test
    // can distinguish it; a mutation survivor here is equivalent, not a gap.
    const stillFailed = Math.max(0, (retry.retryCount || 0) - found);
    let message = `Retry complete: ${found} of ${retry.retryCount} newly found`;
    if (stillFailed > 0) message += `, ${stillFailed} still not found`;
    return { message, type: found > 0 ? 'success' : 'info' };
  }
  if (defaultMessage === null) return null;
  return { message: defaultMessage, type: 'success' };
}

/* ── formatDuration (sync-services.js 10062; sync-spotify.js 1967 is a twin) ── */

export function formatDuration(durationMs: number | null | undefined): string {
  if (!durationMs) return '0:00';
  const minutes = Math.floor(durationMs / 60000);
  const seconds = Math.floor((durationMs % 60000) / 1000);
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

/**
 * Run `fn` over `items`, at most `limit` in flight at once.
 *
 * Exists because the account tabs' per-playlist track crawl was sequential —
 * one `await` per playlist, which is what made Tidal's refresh take 3-5 minutes
 * for a large account (Specialmed, Discord Aug 11). Unbounded parallelism is
 * not the answer either: it would fire one request per playlist at Tidal at
 * once and earn a rate limit.
 *
 * Rejections are swallowed per item, deliberately: this drives a best-effort
 * background crawl, and one dead playlist must not strand the rest. `limit` is
 * clamped to at least 1 so a bad caller cannot deadlock the walk.
 */
export async function mapWithConcurrency<T>(
  items: readonly T[],
  limit: number,
  fn: (item: T, index: number) => Promise<void>,
): Promise<void> {
  const safeLimit = Math.max(1, Math.floor(limit) || 1);
  let cursor = 0;
  const workers = Array.from({ length: Math.min(safeLimit, items.length) }, async () => {
    for (;;) {
      const index = cursor++;
      if (index >= items.length) return;
      try {
        await fn(items[index], index);
      } catch {
        // Per-item failure is not the walk's problem.
      }
    }
  });
  await Promise.all(workers);
}
