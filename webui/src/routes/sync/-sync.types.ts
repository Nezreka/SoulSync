/**
 * Sync page — shared types for the port.
 *
 * The whole page runs on ONE state machine: every source vertical (Tidal,
 * Qobuz, Deezer, YouTube, Beatport, SpotifyPublic, iTunesLink, ListenBrainz,
 * mirrored) drives its cards and the shared discovery modal through the same
 * seven phases. The vanilla encodes the machine four times over (button text,
 * phase text, color, progress width) in sync-spotify.js 1394-1433; the port
 * keeps them as pure maps in -sync.core.ts.
 */

export type SyncPhase =
  | 'fresh'
  | 'discovering'
  | 'discovered'
  | 'syncing'
  | 'sync_complete'
  | 'downloading'
  | 'download_complete';

/** What getProgressWidth reads off a playlist card's state. */
export interface PhaseProgressInfo {
  /** Usually a SyncPhase; kept open because backends echo raw strings. */
  phase: string;
  spotify_total?: number;
  spotify_matches?: number;
}

/**
 * One row of discovery results, as every vertical's transforms produce and
 * consume it. Status literals drifted per source (see isFoundRow* in core);
 * `spotify_data` is the verbatim Spotify track object when discovery matched,
 * the flat spotify_* fields are the "automatic discovery" format.
 */
export interface DiscoveryResultRow {
  index?: number;
  status?: string;
  status_class?: string;
  wing_it_fallback?: boolean;
  spotify_data?: unknown;
  spotify_track?: string;
  spotify_artist?: string;
  spotify_album?: unknown;
  spotify_id?: string;
}

/** Backend download-task statuses painted by updateCompletedModalResults. */
export type DownloadTaskStatus =
  | 'pending'
  | 'searching'
  | 'downloading'
  | 'post_processing'
  | 'completed'
  | 'not_found'
  | 'failed'
  | 'cancelled';

export interface DownloadTask {
  task_id?: string;
  track_index?: number;
  /** Usually a DownloadTaskStatus; unknown statuses paint the default arm. */
  status: string;
  progress?: number;
  error_message?: string;
  has_candidates?: boolean;
}

/**
 * The per-source boolean flags stamped onto the fake youtubePlaylistStates
 * entries (the shared discovery modal dispatches on these).
 */
export interface VirtualSourceFlags {
  is_listenbrainz_playlist?: boolean;
  is_beatport_playlist?: boolean;
  is_tidal_playlist?: boolean;
  is_qobuz_playlist?: boolean;
  is_deezer_playlist?: boolean;
  is_spotify_public_playlist?: boolean;
  is_itunes_link_playlist?: boolean;
}

/** What the discovery-table actions cell should render for a row. */
export type DiscoveryRowAction = 'fix' | 'fix-wing-it' | 'rematch' | 'none';
