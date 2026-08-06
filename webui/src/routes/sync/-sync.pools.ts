/**
 * The two pool modals' pure core (stats-automations.js 1206-2022).
 *
 * Discovery Pool and Wing It Pool are the same shell twice — a two-card
 * category grid over a filterable list — but they are NOT one component with a
 * flag, because almost every detail drifts:
 *
 *   - counts: the Discovery Pool reads a `stats` object, Wing It reads ARRAY
 *     LENGTHS (1409-1410 vs 1243-1244)
 *   - the mosaic background is Discovery-only; Wing It keeps the flat gradient
 *   - the matched row is rich on one side (cover, confidence band, use count,
 *     provider, remove) and plain on the other
 *   - "Re-match" means two different things: a Wing It row carries a TRACK id
 *     into /discovery-pool/fix, a Discovery matched row carries a CACHE id
 *     into /discovery-pool/rematch
 *
 * So the drift lives here as data, the way -sync.sources.ts holds the nine
 * verticals' drift, and the components read it.
 */

/* ── Row shapes (duck-typed, as the vanilla reads them) ───────────────────── */

/** A failed/attention row — both pools use this shape. */
export interface PoolTrackRow {
  id: number;
  track_name?: string;
  artist_name?: string;
  playlist_name?: string;
  /** Wing It only: a JSON *string* holding {matched_data:{name}}. */
  extra_data?: string;
}

/** A Discovery Pool cache entry (the matched list). */
export interface PoolCacheEntry {
  id: number;
  original_title?: string;
  original_artist?: string;
  provider?: string;
  confidence?: number;
  use_count?: number;
  /** Already an OBJECT here — unlike Wing It's JSON string. */
  matched_data?: {
    name?: string;
    image_url?: string;
    artists?: (string | { name?: string })[];
    album?: { images?: { url?: string }[] } | string;
  };
}

export interface DiscoveryPoolData {
  stats?: { matched?: number; failed?: number };
  playlists?: { id: number; name: string }[];
  matched?: PoolCacheEntry[];
  failed?: PoolTrackRow[];
}

export interface WingItPoolData {
  playlists?: { id: number; name: string }[];
  tracks?: PoolTrackRow[];
  matched?: PoolTrackRow[];
}

/* ── Search (1493-1506, 1661-1667, 1687-1695) ─────────────────────────────── */

/** The vanilla lowercases AND trims before comparing (1495, 1656). */
export function poolQuery(raw: string): string {
  return raw.toLowerCase().trim();
}

/**
 * Track rows match on name / artist / PLAYLIST — the playlist field is easy to
 * miss and is what makes "filter to one playlist" work without the dropdown.
 */
export function poolTrackMatches(track: PoolTrackRow, query: string): boolean {
  if (!query) return true;
  return (
    (track.track_name || '').toLowerCase().includes(query) ||
    (track.artist_name || '').toLowerCase().includes(query) ||
    (track.playlist_name || '').toLowerCase().includes(query)
  );
}

/** Cache entries match on the ORIGINAL pair and the matched name (1688-1694). */
export function poolCacheMatches(entry: PoolCacheEntry, query: string): boolean {
  if (!query) return true;
  const matchedName = entry.matched_data?.name || '';
  return (
    (entry.original_title || '').toLowerCase().includes(query) ||
    (entry.original_artist || '').toLowerCase().includes(query) ||
    matchedName.toLowerCase().includes(query)
  );
}

/* ── Empty states ─────────────────────────────────────────────────────────── */

export type DiscoveryPoolView = 'categories' | 'failed' | 'matched';
export type WingItPoolView = 'categories' | 'attention' | 'matched';

/**
 * Eight distinct strings: two pools × two lists × filtered-vs-empty. Note the
 * asymmetry — the Discovery Pool words its filtered-empty per list, Wing It
 * shares ONE across both (1509-1513 vs 1670-1672, 1701-1703).
 */
export function discoveryPoolEmptyMessage(view: 'failed' | 'matched', filtered: boolean): string {
  if (view === 'failed') {
    return filtered
      ? 'No failed tracks match your filter.'
      : 'No failed discoveries. All tracks matched successfully.';
  }
  return filtered ? 'No matched tracks match your filter.' : 'No cached discovery matches yet.';
}

export function wingItPoolEmptyMessage(view: 'attention' | 'matched', filtered: boolean): string {
  if (filtered) return 'No tracks match your filter.';
  return view === 'matched'
    ? 'No resolved Wing It tracks yet — ones you Fix here will land in this list.'
    : 'No Wing It guesses to review.';
}

/* ── Titles and headers ───────────────────────────────────────────────────── */

export function discoveryPoolListTitle(view: 'failed' | 'matched'): string {
  return view === 'failed' ? 'Failed Tracks' : 'Matched Tracks';
}

export function wingItPoolListTitle(view: 'attention' | 'matched'): string {
  return view === 'matched' ? '✓ Resolved Wing It guesses' : '⚡ Guesses to review';
}

/** The Discovery Pool's counts come from `stats` (1243-1244). */
export function discoveryPoolCounts(data: DiscoveryPoolData | null): {
  matched: number;
  failed: number;
} {
  return {
    matched: data?.stats?.matched || 0,
    failed: data?.stats?.failed || 0,
  };
}

/** Wing It's come from ARRAY LENGTHS (1409-1410) — a real per-modal drift. */
export function wingItPoolCounts(data: WingItPoolData | null): {
  attention: number;
  matched: number;
} {
  return {
    attention: (data?.tracks || []).length,
    matched: (data?.matched || []).length,
  };
}

/* ── The matched row (1704-1730) ──────────────────────────────────────────── */

/** Three bands, both boundaries inclusive-from-below (1708). */
export function poolConfidence(confidence: number | undefined): {
  percent: number;
  band: 'high' | 'mid' | 'low';
} {
  const percent = Math.round((confidence || 0) * 100);
  return { percent, band: percent >= 80 ? 'high' : percent >= 70 ? 'mid' : 'low' };
}

/**
 * The cover: the entry's own image, else the album's first (1710-1711).
 *
 * The `typeof album === 'object'` guard is the vanilla's and is UNOBSERVABLE —
 * a string album has no `.images` either way, so dropping it changes nothing
 * for any value (checked exhaustively, and it survives as an equivalent mutant
 * in the mutation pass). Kept for faithfulness, flagged so nobody hunts for a
 * test that can kill it.
 */
export function poolMatchImage(entry: PoolCacheEntry): string {
  const md = entry.matched_data || {};
  if (md.image_url) return md.image_url;
  const album = md.album;
  const images = typeof album === 'object' && album ? album.images : undefined;
  return images && images.length > 0 ? images[0].url || '' : '';
}

/*
 * NOT PORTED, deliberately: the vanilla computes `matchedArtists` from
 * `md.artists` at 1706 — handling both bare strings and {name} objects — and
 * then never renders it. The matched row shows the ORIGINAL artist, the
 * matched NAME and the provider (1716-1720), so that variable is dead in the
 * vanilla and has no counterpart here.
 */

/**
 * A Wing It matched row's new name. `extra_data` is a JSON STRING here (the
 * Discovery Pool's equivalent is already an object), and any parse failure is
 * swallowed to '' (1485-1490).
 */
export function wingItMatchedName(track: PoolTrackRow): string {
  try {
    const parsed = JSON.parse(track.extra_data || '{}') as {
      matched_data?: { name?: string };
    };
    return parsed.matched_data?.name || '';
  } catch {
    return '';
  }
}

/* ── The Discovery-only mosaic (1315-1350) ────────────────────────────────── */

/** At most 20 DISTINCT cover urls, in first-seen order (1316-1324). */
export function poolMosaicImages(entries: PoolCacheEntry[]): string[] {
  const images: string[] = [];
  for (const entry of entries) {
    const url = entry.matched_data?.image_url;
    if (url && !images.includes(url)) {
      images.push(url);
      if (images.length >= 20) break;
    }
  }
  return images;
}

export const POOL_MOSAIC_ROWS = 4;

export interface PoolMosaicRow {
  /** Odd rows scroll the other way (1338). */
  scrollRight: boolean;
  /** The `--speed` custom property, in seconds (1339). */
  speedSeconds: number;
  /** animationDelay, in seconds (1340). */
  delaySeconds: number;
  tiles: string[];
}

/**
 * The four scrolling rows, or NULL when there is nothing worth animating —
 * under four covers the card keeps its flat gradient (1327).
 *
 * Each row holds twice as many tiles as it needs so the loop is seamless, and
 * starts three images further along than the row above it, so no two rows
 * march in lockstep.
 */
export function poolMosaicRows(images: string[]): PoolMosaicRow[] | null {
  if (images.length < 4) return null;
  const perRow = Math.ceil(images.length / POOL_MOSAIC_ROWS) * 2;
  const rows: PoolMosaicRow[] = [];
  for (let r = 0; r < POOL_MOSAIC_ROWS; r++) {
    const tiles: string[] = [];
    for (let i = 0; i < perRow; i++) tiles.push(images[(i + r * 3) % images.length]);
    rows.push({
      scrollRight: r % 2 === 1,
      speedSeconds: 25 + r * 5,
      delaySeconds: r * 0.15,
      tiles,
    });
  }
  return rows;
}

/* ── The fix / rematch sub-modal (1739-2022) ──────────────────────────────── */

/**
 * ONE modal, two modes. The vanilla smuggles the mode and its ids through
 * `fixOverlay.dataset` and branches on `dataset.mode === 'rematch'` at submit
 * time (1795-1799, 1976); here it is a discriminated union and the dataset
 * goes away.
 */
export type PoolFixTarget =
  | { mode: 'fix'; trackId: number; trackName: string; artistName: string }
  | {
      mode: 'rematch';
      cacheId: number;
      originalTitle: string;
      originalArtist: string;
      trackName: string;
      artistName: string;
    };

export function poolFixHeading(target: PoolFixTarget): string {
  return target.mode === 'rematch' ? 'Rematch Track' : 'Fix Track Match';
}

export function poolFixSourceLabel(target: PoolFixTarget): string {
  return target.mode === 'rematch' ? 'Current Match' : 'Original Track';
}

/** The auto-search the vanilla fires once the inputs are on screen (1809, 1902). */
export const POOL_FIX_AUTOSEARCH_MS = 500;

/** The Spotify search the picker runs (1931). */
export const POOL_FIX_SEARCH_LIMIT = 20;

export interface PoolSearchTrack {
  name?: string;
  artists?: string[];
  album?: string;
  duration_ms?: number;
}

/**
 * The search box's four outcomes (1918-1963). The error arm exists on purpose:
 * the vanilla's comment says an auth failure, a 500 or an upstream abort must
 * not be masked as a bland "No results found".
 */
export type PoolFixSearchState =
  | { kind: 'idle'; message: string }
  | { kind: 'searching' }
  | { kind: 'error'; message: string }
  | { kind: 'empty'; message: string }
  | { kind: 'results'; tracks: PoolSearchTrack[] };

export const POOL_FIX_NEEDS_QUERY = 'Enter a search term';
export const POOL_FIX_NO_RESULTS = 'No results found';

/** A response is a failure if it is not ok OR carries an error key (1936). */
export function poolFixSearchFailed(ok: boolean, error: string | undefined): boolean {
  return !ok || Boolean(error);
}

/** `Search error: <backend error | statusText | request failed (<status>)>` (1937). */
export function poolFixSearchError(
  status: number,
  statusText: string,
  error: string | undefined,
): string {
  return `Search error: ${error || statusText || `request failed (${status})`}`;
}

export function poolFixThrewMessage(message: string): string {
  return `Search failed: ${message}`;
}

/** The confirm before a match is committed (1969). */
export function poolFixConfirmMessage(track: PoolSearchTrack): string {
  return `Match to "${track.name}" by ${(track.artists || []).join(', ')}?`;
}

export function poolFixMatchedToast(track: PoolSearchTrack): string {
  return `Matched: ${track.name}`;
}

/** removePoolCacheEntry's confirmation (1813). */
export const POOL_REMOVE_CACHE_MESSAGE =
  'Remove this cached match? The track will be re-discovered fresh next time.';
