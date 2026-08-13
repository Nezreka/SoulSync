/**
 * The Spotify and Deezer-ARL account tabs' pure core (sync-spotify.js
 * 1598-1721 + 1832-1958, sync-services.js 2437-2699).
 *
 * These two are NOT the tidal/qobuz archetype and share nothing with
 * -sync.sources.ts. They are ENGINE-DRIVEN: their cards carry
 * `.playlist-card[data-playlist-id]` and the vanilla download engine paints
 * into them BY SELECTOR — updateCardToSyncing writes #progress-<id>,
 * updateCardToDefault writes the status span, updatePlaylistCardUI writes the
 * two action buttons. React renders the skeleton with those exact ids and
 * classes and never re-renders over what the engine wrote (the adopted-region
 * pattern; the vanilla itself wipes and rehydrates the same way at 1618-1621).
 */

/** `deezer_arl_<id>` — a CLIENT-side id space; the API path takes the raw id. */
export function deezerArlId(playlistId: number | string): string {
  return `deezer_arl_${playlistId}`;
}

/**
 * Spotify's status class (1640-1642).
 *
 * Three SEQUENTIAL writes, not a ladder — transcribed in order. Their ORDER is
 * unobservable and a mutation survivor here is equivalent, not a gap: no status
 * can satisfy two of the three tests at once ('Synced' and 'Last Sync' are
 * disjoint prefixes, and 'Needs Sync' starts with neither), so whichever runs
 * last cannot differ. Kept in the vanilla's order anyway — transcribing beats
 * reasoning about it.
 *
 * Note the
 * vanilla does NOT guard sync_status here, so an absent one throws; the port
 * cannot reproduce a crash, and treating absent as 'never synced' is the only
 * sane reading. Deezer-ARL, which DOES guard, is the evidence that this is an
 * oversight rather than a contract.
 */
export function spotifyStatusClass(syncStatus: string | null | undefined): string {
  let statusClass = 'status-never-synced';
  const status = syncStatus ?? '';
  if (status.startsWith('Synced')) statusClass = 'status-synced';
  if (status === 'Needs Sync' || status.startsWith('Last Sync')) statusClass = 'status-needs-sync';
  return statusClass;
}

/**
 * Deezer-ARL's status class (2499-2500) — only TWO states. It has no
 * 'Needs Sync' arm at all, and it guards the absent case.
 */
export function deezerArlStatusClass(syncStatus: string | null | undefined): string {
  return syncStatus && syncStatus.startsWith('Synced') ? 'status-synced' : 'status-never-synced';
}

/** Deezer-ARL falls back to a literal where Spotify prints whatever it has (2509). */
export function deezerArlStatusLabel(syncStatus: string | null | undefined): string {
  return syncStatus || 'Never Synced';
}

/** A row of /api/spotify/playlists or /api/deezer/arl-playlists. */
export interface AccountPlaylistRow {
  id: string | number;
  name?: string;
  owner?: string;
  description?: string;
  image_url?: string;
  track_count?: number;
  sync_status?: string;
  tracks?: unknown[];
}

/**
 * The spotifyPlaylists shim (2646-2654 and 2471).
 *
 * openDownloadMissingModal only knows the spotifyPlaylists array, so an ARL
 * playlist has to be pushed into it under the PREFIXED id before the download
 * modal can serve it. The vanilla builds this twice with DIFFERENT track
 * counts: the modal-time shim counts the fetched tracks, the load-time one
 * takes the row's track_count. Both are reproduced rather than unified — the
 * count is what the download modal's hero shows.
 */
export function arlShimRow(
  row: AccountPlaylistRow,
  source: 'modal' | 'load',
  tracks?: unknown[],
): {
  id: string;
  name: string | undefined;
  track_count: number;
  image_url: string;
  owner: string;
} {
  return {
    id: deezerArlId(row.id),
    name: row.name,
    track_count: source === 'modal' ? (tracks ? tracks.length : 0) : (row.track_count ?? 0),
    image_url: row.image_url || '',
    owner: row.owner || '',
  };
}

/**
 * The selection count line (updateSyncActionsUI, 1823-1829).
 *
 * Pluralised at count > 1, so ONE playlist reads "1 playlist selected". The
 * button is disabled at exactly zero.
 */
export function selectionInfoText(count: number): string {
  if (count === 0) return 'Select playlists to sync';
  return `${count} playlist${count > 1 ? 's' : ''} selected`;
}
