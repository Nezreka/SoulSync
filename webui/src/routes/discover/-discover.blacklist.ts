/**
 * The Blocked Artists modal.
 *
 * Transcribed from `openDiscoveryBlacklistModal` (5058), `_dblSearch` (5100),
 * `_dblBlockFromSearch` (5132), `_dblLoadList` (5152) and
 * `unblockDiscoveryArtist` (5174) — read end to end.
 *
 * ── One of its two entry points is dead ─────────────────────────────────────
 *
 * `blockDiscoveryArtist` (5034) — the "block this artist" action that would
 * live on a card — is unreachable (discover_dead_code_audit.md). The modal
 * itself is reachable and its own search-and-block flow works, so blocking is
 * possible; it just cannot be started from a discovery card. Not ported, and
 * not resurrected: wiring it up is a feature decision, not a port.
 *
 * `loadDiscoveryBlacklist` (5827) is an EMPTY STUB — `function ... () { }` —
 * despite being in the below-fold loader list. Nothing to port.
 */

export const BLACKLIST_URL = '/api/discover/artist-blacklist';
export const BLACKLIST_SEARCH_URL = '/api/enhanced-search';

export function blacklistDeleteUrl(id: number | string): string {
  return `${BLACKLIST_URL}/${id}`;
}

/** `setTimeout(() => _dblSearch(q), 300)` (5094). */
export const BLACKLIST_SEARCH_DEBOUNCE_MS = 300;

/**
 * `if (q.length < 2)` (5093) — checked BEFORE the debounce, unlike Last.fm.
 *
 * So a one-character query hides the results immediately rather than scheduling
 * a timer that does nothing.
 */
export const BLACKLIST_MIN_QUERY_LENGTH = 2;

export const BLACKLIST_TITLE = 'Blocked Artists';
export const BLACKLIST_SUBTITLE =
  "These artists won't appear in any discovery playlist across all sources";
export const BLACKLIST_SEARCH_PLACEHOLDER = 'Search for an artist to block...';
export const BLACKLIST_NO_RESULTS = 'No artists found';
export const BLACKLIST_EMPTY = 'No blocked artists yet — search above to block one';
export const BLACKLIST_LOAD_FAILED = 'Failed to load';
export const BLACKLIST_BLOCK_ERROR = 'Error blocking artist';

/** `{ query, limit: 8 }` (5108) — a small list, since it is a picker not a browser. */
export const BLACKLIST_SEARCH_LIMIT = 8;

export function blacklistSearchBody(query: string): { query: string; limit: number } {
  return { query, limit: BLACKLIST_SEARCH_LIMIT };
}

export function blacklistQueryTooShort(raw: string): boolean {
  return raw.trim().length < BLACKLIST_MIN_QUERY_LENGTH;
}

export interface BlacklistSearchArtist {
  name?: string;
  image_url?: string;
}

/**
 * `data.spotify_artists || data.artists || []` (5111).
 *
 * The shared enhanced-search endpoint answers with a source-specific key when it
 * has one and a generic key otherwise; this reads both rather than assuming
 * Spotify is configured.
 */
export function blacklistSearchResults(
  data:
    | { spotify_artists?: BlacklistSearchArtist[]; artists?: BlacklistSearchArtist[] }
    | null
    | undefined,
): BlacklistSearchArtist[] {
  const list = data?.spotify_artists || data?.artists;
  return Array.isArray(list) ? list : [];
}

/** `{ artist_name }` (5137) — blocking is BY NAME, not by id. */
export function blacklistBlockBody(artistName: string): { artist_name: string } {
  return { artist_name: artistName };
}

export function blacklistBlockedToast(artistName: string): string {
  return `Blocked ${artistName} from discovery`;
}

export function blacklistUnblockedToast(artistName: string): string {
  return `Unblocked ${artistName}`;
}

/**
 * A successful block clears the search AND reloads the list (5141-5145).
 *
 * Clearing matters: the blocked artist would otherwise still sit in the results
 * offering a "Block" button that now does nothing visible.
 */
export interface BlacklistBlockEffects {
  toast: string;
  clearSearch: true;
  reloadList: true;
}

export function blacklistBlockEffects(
  artistName: string,
  data: { success?: boolean } | null | undefined,
): BlacklistBlockEffects | null {
  if (!data?.success) return null;
  return { toast: blacklistBlockedToast(artistName), clearSearch: true, reloadList: true };
}

export interface BlacklistEntry {
  id?: number;
  artist_name?: string;
  created_at?: string;
}

/** `!data.success || !data.entries || !data.entries.length` → the empty copy (5158). */
export function blacklistEntries(
  data: { success?: boolean; entries?: BlacklistEntry[] } | null | undefined,
): BlacklistEntry[] {
  if (!data?.success || !Array.isArray(data.entries)) return [];
  return data.entries;
}

/**
 * The blocked-at date is LOCALE-formatted, and empty when absent (5165).
 *
 * `new Date(undefined)` would render "Invalid Date", so the guard is what keeps
 * a row without a timestamp blank instead of wrong.
 */
export function blacklistEntryDate(createdAt: string | undefined): string {
  return createdAt ? new Date(createdAt).toLocaleDateString() : '';
}
