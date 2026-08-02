/**
 * Discover playlist sync — start, poll, and resume-after-refresh.
 *
 * Transcribed from `checkForActiveDiscoverSyncs` (324),
 * `startDiscoverPlaylistSync` (11251) and `startDiscoverSyncPolling` (11364),
 * read end to end.
 *
 * ── One divergence ──────────────────────────────────────────────────────────
 *
 * The completion toast's display-name map (11396 and again at 11459 — it is
 * duplicated in the socket and polling paths) lists SEVEN playlist types, but
 * `startDiscoverPlaylistSync` accepts EIGHT. `listening_mix` is missing from
 * both copies, so finishing that sync toasts the raw key:
 * "listening_mix sync complete!". Added here, from the shelf's own title for
 * that mix ("Your Listening Mix", 4627), and pinned by a test.
 *
 * The duplication is also removed — one map, both paths.
 */

/** `setInterval(..., 500)` (11483). */
export const SYNC_POLL_MS = 500;
/** The status block hides 3s after completion (11403, 11477). */
export const SYNC_STATUS_HIDE_MS = 3000;

export type PlaylistType =
  | 'release_radar'
  | 'discovery_weekly'
  | 'seasonal_playlist'
  | 'popular_picks'
  | 'hidden_gems'
  | 'discovery_shuffle'
  | 'listening_mix'
  | 'build_playlist';

/**
 * Every type `startDiscoverPlaylistSync` can source tracks for (11256-11272),
 * mapped to the module variable it reads.
 *
 * Kept as data so the eight-vs-seven mismatch below cannot recur silently.
 */
export const SYNC_TRACK_SOURCES: Record<PlaylistType, string> = {
  release_radar: 'discoverReleaseRadarTracks',
  discovery_weekly: 'discoverWeeklyTracks',
  seasonal_playlist: 'discoverSeasonalTracks',
  popular_picks: 'personalizedPopularPicks',
  hidden_gems: 'personalizedHiddenGems',
  discovery_shuffle: 'personalizedDiscoveryShuffle',
  listening_mix: 'personalizedListeningMix',
  build_playlist: 'buildPlaylistTracks',
};

/**
 * Completion-toast names. `listening_mix` is the one the vanilla omits.
 *
 * Note "Seasonal Mix" here is deliberately not the shelf's seasonal title,
 * which is built from the live season (`${icon} ${name} Mix`) — a toast fired
 * minutes later should not claim a season it did not compute.
 */
export const PLAYLIST_DISPLAY_NAMES: Record<PlaylistType, string> = {
  release_radar: 'Fresh Tape',
  discovery_weekly: 'The Archives',
  seasonal_playlist: 'Seasonal Mix',
  popular_picks: 'Popular Picks',
  hidden_gems: 'Hidden Gems',
  discovery_shuffle: 'Discovery Shuffle',
  listening_mix: 'Your Listening Mix', //  DIVERGENCE — absent from the vanilla
  build_playlist: 'Custom Playlist',
};

/** Unknown types fall back to the raw key rather than an empty toast. */
export function playlistDisplayName(playlistType: string): string {
  return PLAYLIST_DISPLAY_NAMES[playlistType as PlaylistType] || playlistType;
}

export function syncCompleteToast(playlistType: string): string {
  return `${playlistDisplayName(playlistType)} sync complete!`;
}

export function noTracksToast(playlistName: string): string {
  return `No tracks available for ${playlistName}`;
}

/**
 * The DOM id convention every sync-status element follows: underscores become
 * hyphens (11327, 11334, 11376).
 *
 * This is the same convention `mixStatusBase` mirrors, which is what lets a
 * running sync's progress land on the mix modal's elements.
 */
export function syncIdPrefix(playlistType: string): string {
  return playlistType.replace(/_/g, '-');
}

export const syncStatusId = (t: string) => `${syncIdPrefix(t)}-sync-status`;
export const syncButtonId = (t: string) => `${syncIdPrefix(t)}-sync-btn`;
export const syncCompletedId = (t: string) => `${syncIdPrefix(t)}-sync-completed`;
export const syncPendingId = (t: string) => `${syncIdPrefix(t)}-sync-pending`;
export const syncFailedId = (t: string) => `${syncIdPrefix(t)}-sync-failed`;
export const syncPercentageId = (t: string) => `${syncIdPrefix(t)}-sync-percentage`;

/** `discover_${playlistType}` (11309). */
export function virtualPlaylistId(playlistType: string): string {
  return `discover_${playlistType}`;
}

// ── Track conversion ────────────────────────────────────────────────────────

export interface SpotifyShapedTrack {
  id?: string;
  name?: string;
  artists?: unknown;
  album?: { name?: string; images?: { url: string }[] };
  duration_ms?: number;
}

/**
 * Convert stored tracks into the shape the sync API wants (11280-11306).
 *
 * `track_data_json` is used WHOLE when present — it is already the Spotify
 * shape. Otherwise a minimal object is built from the flat columns. Either way
 * the artist list is flattened to strings, because the sync matcher compares
 * names and an array of objects silently matches nothing.
 */
export function toSyncTracks(tracks: Record<string, unknown>[]): SpotifyShapedTrack[] {
  return tracks.map((track) => {
    let spotifyTrack: SpotifyShapedTrack;
    if (track.track_data_json) {
      spotifyTrack = { ...(track.track_data_json as SpotifyShapedTrack) };
    } else {
      spotifyTrack = {
        id: track.spotify_track_id as string,
        name: track.track_name as string,
        artists: [{ name: track.artist_name }],
        album: {
          name: track.album_name as string,
          images: track.album_cover_url ? [{ url: track.album_cover_url as string }] : [],
        },
        duration_ms: (track.duration_ms as number) || 0,
      };
    }
    if (Array.isArray(spotifyTrack.artists)) {
      spotifyTrack.artists = spotifyTrack.artists.map(
        (a: unknown) => (a as { name?: string })?.name || a,
      );
    }
    return spotifyTrack;
  });
}

/** The download-bar bubble's art, taken from the FIRST track only (11347). */
export function syncBubbleImage(tracks: SpotifyShapedTrack[]): string | null {
  const first = tracks[0];
  return first?.album?.images?.[0]?.url ?? null;
}

// ── Progress ────────────────────────────────────────────────────────────────

export interface SyncProgress {
  total: number;
  matched: number;
  failed: number;
  processed: number;
  pending: number;
  percentage: number;
}

/**
 * Progress arithmetic, identical in both paths (11378-11383, 11431-11436).
 *
 * "Processed" counts matched AND failed — a failed track is finished, not
 * pending — so a sync where everything fails still reaches 100% instead of
 * appearing to hang at 0.
 */
export function syncProgress(
  progress: { total_tracks?: number; matched_tracks?: number; failed_tracks?: number } | undefined,
): SyncProgress {
  const total = progress?.total_tracks || 0;
  const matched = progress?.matched_tracks || 0;
  const failed = progress?.failed_tracks || 0;
  const processed = matched + failed;
  return {
    total,
    matched,
    failed,
    processed,
    pending: total - processed,
    percentage: total > 0 ? Math.round((processed / total) * 100) : 0,
  };
}

/** Only 'finished' ends a sync (11389, 11444). */
export function syncIsFinished(status: string | undefined): boolean {
  return status === 'finished';
}

/**
 * Whether the REST poll runs.
 *
 * ALWAYS — unlike the download bar's monitor, which returns early when the
 * socket is connected. The vanilla says why at 11410: there are no dedicated
 * websocket events for discovery progress, so the socket handler is an
 * accelerator and the poll is the source of truth. Making this conditional
 * would freeze the progress numbers for socket-connected users.
 */
export function syncPollAlwaysRuns(): true {
  return true;
}

// ── Resume after refresh ────────────────────────────────────────────────────

/**
 * The three syncs `checkForActiveDiscoverSyncs` probes on page load (327, 357,
 * 386), each `/api/sync/status/discover_<type>`.
 *
 * Only these three. Without the resume, reloading mid-sync leaves a running
 * sync looking dead: the status block is hidden and the button is enabled, so
 * the user starts a second one.
 */
export const RESUMABLE_SYNCS: PlaylistType[] = [
  'release_radar',
  'discovery_weekly',
  'seasonal_playlist',
];

/** `'syncing' || 'starting'` (330) — 'starting' matters, it is the first state. */
export function syncIsActive(status: string | undefined): boolean {
  return status === 'syncing' || status === 'starting';
}

export function resumeStatusUrl(playlistType: string): string {
  return `/api/sync/status/${virtualPlaylistId(playlistType)}`;
}

/** A failed probe is swallowed (351) — "not active" and "unreachable" look the same. */
export const RESUME_PROBE_FAILURE_IS_SILENT = true;

export interface SyncButtonState {
  disabled: boolean;
  opacity: string;
  cursor: string;
}

/** The button's three inline styles move together (341-345, 11452-11456). */
export const SYNC_BUTTON_RUNNING: SyncButtonState = {
  disabled: true,
  opacity: '0.5',
  cursor: 'not-allowed',
};
export const SYNC_BUTTON_IDLE: SyncButtonState = {
  disabled: false,
  opacity: '1',
  cursor: 'pointer',
};

export function syncButtonState(running: boolean): SyncButtonState {
  return running ? SYNC_BUTTON_RUNNING : SYNC_BUTTON_IDLE;
}

// ── Opening the whole-playlist download modal (11129) ───────────────────────

/**
 * `openDownloadModalForDiscoverPlaylist` (11129), found missing by the coverage
 * audit. It is the mix modal's Download action.
 *
 * It reads the SAME eight track sources as startDiscoverPlaylistSync and does
 * the SAME conversion — track_data_json whole when present, otherwise built
 * from the flat columns, artists flattened to strings either way. Two callers,
 * one shape, which is why `toSyncTracks` is shared rather than duplicated.
 *
 * The virtual id is also the same `discover_${type}`. That matters: the
 * download modal and the sync both address one playlist, and the download bar
 * keys its bubble on it.
 */
export const PLAYLIST_DOWNLOAD_NO_TRACKS = noTracksToast;

export function playlistDownloadFailed(message: string): string {
  return `Failed to open download modal: ${message}`;
}

/**
 * Whether the Download action can proceed (11153).
 *
 * Same guard and same warning as the sync path — an empty mix is not an error,
 * just nothing to do.
 */
export function canOpenPlaylistDownload(tracks: unknown[] | null | undefined): boolean {
  return Array.isArray(tracks) && tracks.length > 0;
}
