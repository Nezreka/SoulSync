import {
  WATCHLIST_SOURCE_KEYS,
  type WatchlistArtist,
  type WatchlistSort,
  type WatchlistSourceKey,
} from './-watchlist.types';

/**
 * The id the card acts on — gear, detail view, batch remove all use this.
 *
 * Provider precedence is load-bearing and matches the vanilla page: change the
 * order and an artist matched on two providers starts opening a different
 * config. Returns null for an artist with no provider id at all, which the
 * server permits (a library-only artist backfilled without a match).
 */
export function primaryArtistId(artist: WatchlistArtist): string | null {
  for (const key of WATCHLIST_SOURCE_KEYS) {
    const value = artist[key];
    if (value != null && String(value).trim() !== '') return String(value);
  }
  return null;
}

/** Which provider badges a card shows, in a stable order. */
export function artistSourceKeys(artist: WatchlistArtist): WatchlistSourceKey[] {
  return WATCHLIST_SOURCE_KEYS.filter((key) => {
    const value = artist[key];
    return value != null && String(value).trim() !== '';
  });
}

/**
 * Parse a server timestamp to epoch millis, or 0 when absent/unparseable.
 *
 * 0 is deliberate rather than -Infinity: the vanilla sorts treated a
 * never-scanned artist as time 0, which floats them to the top of
 * "Oldest Scanned" — the useful place for them, since they are the ones most
 * overdue for a scan.
 */
export function timestampValue(iso: string | null | undefined): number {
  if (!iso) return 0;
  const ms = new Date(iso).getTime();
  return Number.isFinite(ms) ? ms : 0;
}

/** Sort a copy of the artists. Never mutates the query cache's array. */
export function sortArtists(artists: WatchlistArtist[], sort: WatchlistSort): WatchlistArtist[] {
  const out = [...artists];
  switch (sort) {
    case 'name-asc':
      return out.sort((a, b) => compareNames(a, b));
    case 'name-desc':
      return out.sort((a, b) => compareNames(b, a));
    case 'scan-oldest':
      return out.sort(
        (a, b) => timestampValue(a.last_scan_timestamp) - timestampValue(b.last_scan_timestamp),
      );
    case 'scan-newest':
      return out.sort(
        (a, b) => timestampValue(b.last_scan_timestamp) - timestampValue(a.last_scan_timestamp),
      );
    case 'added-newest':
      return out.sort((a, b) => timestampValue(b.date_added) - timestampValue(a.date_added));
    default:
      return out;
  }
}

function compareNames(a: WatchlistArtist, b: WatchlistArtist): number {
  // The vanilla page compared a lowercased data attribute; localeCompare on the
  // raw name with sensitivity:'base' is the same ordering without the
  // pre-lowercasing step, and handles accents the way a user expects.
  return (a.artist_name || '').localeCompare(b.artist_name || '', undefined, {
    sensitivity: 'base',
  });
}

/** Substring filter on artist name, case-insensitive. Empty query = no filter. */
export function filterArtists(artists: WatchlistArtist[], query: string): WatchlistArtist[] {
  const needle = query.toLowerCase().trim();
  if (!needle) return artists;
  return artists.filter((artist) => (artist.artist_name || '').toLowerCase().includes(needle));
}

/**
 * "Scanned 3h ago" / "Never scanned" — the card's meta line.
 * Ported verbatim from `formatRelativeScanTime` so cards read identically.
 */
export function formatRelativeScanTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return 'Never scanned';
  const parsed = new Date(iso).getTime();
  if (!Number.isFinite(parsed)) return 'Never scanned';

  const diff = now - parsed;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'Scanned just now';
  if (mins < 60) return `Scanned ${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `Scanned ${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `Scanned ${days}d ago`;
  const months = Math.floor(days / 30);
  return `Scanned ${months}mo ago`;
}

/**
 * "just now" / "yesterday" / a date — used by the last-scan strip.
 * Ported verbatim from `_formatTimeAgo`.
 */
export function formatTimeAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '';
  const parsed = new Date(iso);
  const ms = parsed.getTime();
  if (!Number.isFinite(ms)) return '';

  const diffMins = Math.floor((now - ms) / 60000);
  if (diffMins < 1) return 'just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays === 1) return 'yesterday';
  if (diffDays < 7) return `${diffDays}d ago`;
  return parsed.toLocaleDateString();
}

/** "3 artists" / "1 artist" — the header count chip. */
export function formatArtistCount(count: number): string {
  return `${count} artist${count !== 1 ? 's' : ''}`;
}

/**
 * The "Next Auto" chip. The server sends seconds until the `scan_watchlist`
 * system automation next fires; 0 means it is not scheduled at all.
 */
export function formatCountdown(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return 'Next Auto: --';
  const total = Math.floor(seconds);
  const hrs = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  if (hrs > 0) return `Next Auto: ${hrs}h ${pad(mins)}m`;
  if (mins > 0) return `Next Auto: ${mins}m ${pad(secs)}s`;
  return `Next Auto: ${secs}s`;
}

/** The release-type / filter pills a card shows, in the vanilla order. */
export interface WatchlistPill {
  label: string;
  kind: 'active' | 'filter';
}

/** Accepts anything carrying the include flags — a grid row or a fetched
 *  per-artist config, which are separate shapes with the same fields. */
export type PillSource = Pick<
  WatchlistArtist,
  | 'include_albums'
  | 'include_eps'
  | 'include_singles'
  | 'include_live'
  | 'include_remixes'
  | 'include_acoustic'
  | 'include_compilations'
>;

export function artistPills(artist: PillSource): WatchlistPill[] {
  const pills: WatchlistPill[] = [];
  if (artist.include_albums) pills.push({ label: 'Albums', kind: 'active' });
  if (artist.include_eps) pills.push({ label: 'EPs', kind: 'active' });
  if (artist.include_singles) pills.push({ label: 'Singles', kind: 'active' });
  if (artist.include_live) pills.push({ label: 'Live', kind: 'filter' });
  if (artist.include_remixes) pills.push({ label: 'Remixes', kind: 'filter' });
  if (artist.include_acoustic) pills.push({ label: 'Acoustic', kind: 'filter' });
  if (artist.include_compilations) pills.push({ label: 'Compilations', kind: 'filter' });
  return pills;
}

/**
 * State of the "Select All" checkbox for the currently VISIBLE cards.
 *
 * Visible-only is the load-bearing part, ported from the vanilla page: it
 * skipped any card whose `style.display` was 'none', i.e. one hidden by the
 * filter. So filtering to "Aphex", ticking Select All and hitting Remove takes
 * out the filtered artist, not the whole watchlist.
 */
export interface BatchSelectionState {
  selectedCount: number;
  allSelected: boolean;
  indeterminate: boolean;
}

export function batchSelectionState(
  visibleArtists: WatchlistArtist[],
  selectedIds: ReadonlySet<string>,
): BatchSelectionState {
  const visibleIds = visibleArtists
    .map((artist) => primaryArtistId(artist))
    .filter((id): id is string => id !== null);
  const selectedCount = visibleIds.filter((id) => selectedIds.has(id)).length;

  return {
    selectedCount,
    allSelected: visibleIds.length > 0 && selectedCount === visibleIds.length,
    indeterminate: selectedCount > 0 && selectedCount < visibleIds.length,
  };
}

/** The ids Remove Selected acts on — selected AND currently visible. */
export function selectedVisibleIds(
  visibleArtists: WatchlistArtist[],
  selectedIds: ReadonlySet<string>,
): string[] {
  return visibleArtists
    .map((artist) => primaryArtistId(artist))
    .filter((id): id is string => id !== null && selectedIds.has(id));
}

/**
 * Provider badge label and its style.css class.
 *
 * The class is spelled out rather than derived from the label: `iTunes` and
 * `MusicBrainz` would only lowercase to the right class by luck, and a future
 * provider whose label does not match its class would break silently.
 */
export const WATCHLIST_SOURCE_BADGES: Record<
  WatchlistSourceKey,
  { label: string; className: string }
> = {
  spotify_artist_id: { label: 'Spotify', className: 'watchlist-source-spotify' },
  itunes_artist_id: { label: 'iTunes', className: 'watchlist-source-itunes' },
  deezer_artist_id: { label: 'Deezer', className: 'watchlist-source-deezer' },
  discogs_artist_id: { label: 'Discogs', className: 'watchlist-source-discogs' },
  musicbrainz_artist_id: { label: 'MusicBrainz', className: 'watchlist-source-musicbrainz' },
  amazon_artist_id: { label: 'Amazon', className: 'watchlist-source-amazon' },
};

// ---------------------------------------------------------------------------
// Auto-download: a global default an artist can override
// ---------------------------------------------------------------------------

/**
 * The three states an artist's auto-download preference can hold. `null` — the
 * artist has no opinion and follows the global — is the one the old boolean
 * column could not express, and the reason a global switch was powerless
 * against a watchlist of artists nobody had ever touched (swiftpawpaw, 225).
 *
 * Mirrors `core/watchlist_auto_download.py`; the server is the authority and
 * resolves this again at scan time. These exist so the modal can SHOW what will
 * happen without a round trip.
 */
export const AUTO_DOWNLOAD_INHERIT = null;
export const AUTO_DOWNLOAD_ALWAYS = 1;
export const AUTO_DOWNLOAD_NEVER = 0;

export type AutoDownloadChoice = 'inherit' | 'always' | 'never';

/** Which `<select>` option a stored preference corresponds to. */
export function autoDownloadSelectValue(pref: number | null | undefined): AutoDownloadChoice {
  if (pref === AUTO_DOWNLOAD_ALWAYS) return 'always';
  if (pref === AUTO_DOWNLOAD_NEVER) return 'never';
  return 'inherit';
}

/** …and back. Anything unrecognised inherits rather than guessing a side. */
export function autoDownloadPrefFromSelect(value: string): number | null {
  if (value === 'always') return AUTO_DOWNLOAD_ALWAYS;
  if (value === 'never') return AUTO_DOWNLOAD_NEVER;
  return AUTO_DOWNLOAD_INHERIT;
}

/** Whether this artist auto-downloads right now. The artist beats the global. */
export function effectiveAutoDownload(
  pref: number | null | undefined,
  globalDefault: boolean,
): boolean {
  if (pref === AUTO_DOWNLOAD_ALWAYS) return true;
  if (pref === AUTO_DOWNLOAD_NEVER) return false;
  return Boolean(globalDefault);
}

/**
 * What the modal says under the control.
 *
 * "Off" is ambiguous between "I set this" and "the global is off", and a user
 * who cannot tell which will go and toggle the wrong one.
 */
export function describeAutoDownload(
  pref: number | null | undefined,
  globalDefault: boolean,
): string {
  if (pref === AUTO_DOWNLOAD_ALWAYS) {
    return 'Set on this artist — downloads even when the global default is off.';
  }
  if (pref === AUTO_DOWNLOAD_NEVER) {
    return 'Set on this artist — follow only, even when the global default is on.';
  }
  return globalDefault
    ? 'Following the global default, which is currently ON — new releases download.'
    : 'Following the global default, which is currently OFF — nothing downloads automatically.';
}
