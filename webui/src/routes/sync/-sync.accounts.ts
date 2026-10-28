/**
 * An account playlist row, as the Spotify and Deezer account lists send it.
 *
 * The helpers that lived here (the deezer_arl_ id space, the sync-status
 * classes, the engine shim) served the old card flow that drove the download
 * engine from the account tabs. Those tabs import into SoulSync now
 * (account-tabs.tsx), so only the row's shape is left.
 */
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
