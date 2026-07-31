/**
 * Your Albums — the interactions, as opposed to the grid rendering that lives
 * in `-discover.your-albums.ts`.
 *
 * Transcribed from `debouncedYourAlbumsSearch` (1339), `openYourAlbumDownload`
 * (1506), `refreshYourAlbums` (1579), `openYourAlbumsSourcesModal` (1605) and
 * its four helpers, `downloadMissingYourAlbums` (1725), `_yourAlbumsPickSource`
 * (1757) and the batch modal (1767-2034) — each read end to end first.
 */

/** `setTimeout(..., 400)` (1341). The search ALSO resets to page 1 (1342). */
export const YOUR_ALBUMS_SEARCH_DEBOUNCE_MS = 400;

/** `refreshYourAlbums` polls every 4s and gives up after 60s (1587, 1598). */
export const YOUR_ALBUMS_REFRESH_POLL_MS = 4000;
export const YOUR_ALBUMS_REFRESH_TIMEOUT_MS = 60000;

export interface YourAlbum {
  album_name?: string;
  artist_name?: string;
  release_date?: string;
  total_tracks?: number;
  image_url?: string;
  in_library?: boolean;
  spotify_album_id?: string | number;
  deezer_album_id?: string | number;
  tidal_album_id?: string | number;
  discogs_release_id?: string | number;
  discogs_id?: string | number;
  [key: string]: unknown;
}

export interface AlbumSource {
  id: string;
  source: string;
}

/**
 * The single best source-id for the download-discography endpoint (1757).
 *
 * Argument-pure, so this one IS differentially tested against the real vanilla.
 * A row usually carries exactly one populated id — the service it was saved on
 * — so this is a priority pick, not a merge. `String()` matters: the ids come
 * back as numbers from Deezer and Discogs, and the endpoint compares strings.
 */
export function yourAlbumsPickSource(album: YourAlbum): AlbumSource | null {
  if (album.spotify_album_id) return { id: String(album.spotify_album_id), source: 'spotify' };
  if (album.deezer_album_id) return { id: String(album.deezer_album_id), source: 'deezer' };
  if (album.tidal_album_id) return { id: String(album.tidal_album_id), source: 'tidal' };
  const discogsId = album.discogs_release_id || album.discogs_id;
  if (discogsId) return { id: String(discogsId), source: 'discogs' };
  return null;
}

export type MissingOutcome =
  | { kind: 'none'; message: string; toast: 'info' }
  | { kind: 'all-owned'; message: string; toast: 'success' }
  | { kind: 'open'; missing: YourAlbum[] };

export const MISSING_NONE = 'No missing albums to download';
export const MISSING_ALL_OWNED = 'All albums are already in your library!';

/**
 * What `downloadMissingYourAlbums` does with the response (1736-1745).
 *
 * The second filter is not redundant with `status=missing`: the query selects
 * rows the cache believes are missing, and `in_library` is stamped fresh by the
 * handler, so an album imported since the last cache write is caught here.
 */
export function missingAlbumsOutcome(
  data: { success?: boolean; albums?: YourAlbum[] } | null | undefined,
): MissingOutcome {
  if (!data?.success || !data.albums || data.albums.length === 0) {
    return { kind: 'none', message: MISSING_NONE, toast: 'info' };
  }
  const missing = data.albums.filter((a) => !a.in_library);
  if (missing.length === 0) {
    return { kind: 'all-owned', message: MISSING_ALL_OWNED, toast: 'success' };
  }
  return { kind: 'open', missing };
}

export interface BatchRow extends YourAlbum {
  _src: AlbumSource;
  /**
   * Index in the PRE-filter array (1779). Rows without a usable source-id are
   * dropped afterwards, so these are non-contiguous — and that is the point:
   * `_index` is the join key between a checkbox and its row, not a position.
   */
  _index: number;
}

export const BATCH_NO_SOURCES = 'No missing albums have a usable source ID to resolve';

/** `.map(...).filter(a => a._src)` (1778-1780) — the order is load-bearing. */
export function prepareBatchRows(missingAlbums: YourAlbum[]): BatchRow[] {
  return missingAlbums
    .map((a, i) => ({ ...a, _src: yourAlbumsPickSource(a), _index: i }))
    .filter((a): a is BatchRow => a._src !== null);
}

/** `${rows.length} albums missing from your library` (1796). */
export function batchModalSubtitle(rowCount: number): string {
  return `${rowCount} albums missing from your library`;
}

/**
 * The card's meta line (1856).
 *
 * Each segment is conditional and each carries its own ' · ' prefix, so an
 * album with no year, no track count and no source renders the artist alone
 * with no dangling separator.
 */
export function batchCardMeta(row: BatchRow): string {
  const artistName = row.artist_name || '';
  const year = row.release_date ? row.release_date.substring(0, 4) : '';
  const tracks = row.total_tracks || 0;
  const src = row._src?.source || '';
  return (
    artistName +
    (year ? ' · ' + year : '') +
    (tracks ? ' · ' + tracks + ' tracks' : '') +
    (src ? ' · ' + src : '')
  );
}

/** `animation-delay:${index * 0.03}s` (1847) — the FILTERED index, not `_index`. */
export function batchCardAnimationDelay(index: number): string {
  return `${index * 0.03}s`;
}

/** Every checkbox renders `checked` (1849). */
export const BATCH_DEFAULT_CHECKED = true;

export interface BatchFooter {
  releases: number;
  tracks: number;
  info: string;
  submitText: string;
  submitDisabled: boolean;
}

export const BATCH_SUBMIT_IDLE = 'Select albums';

/**
 * Footer tally (1872-1887).
 *
 * The vanilla counts only cards that are not `display:none`. This modal's
 * `.discog-filters` div is rendered EMPTY (1801) — the library's discography
 * modal populates it, this one does not — so nothing ever hides a card here.
 * The visibility argument is kept because the semantics are the vanilla's, not
 * because a caller currently exercises it.
 */
export function batchFooter(selected: { total_tracks?: number }[]): BatchFooter {
  const releases = selected.length;
  const tracks = selected.reduce((sum, r) => sum + (Number(r.total_tracks) || 0), 0);
  return {
    releases,
    tracks,
    info: `${releases} album${releases !== 1 ? 's' : ''}${tracks ? ' · ' + tracks + ' tracks' : ''}`,
    submitText: releases > 0 ? `Add ${releases} to Wishlist` : BATCH_SUBMIT_IDLE,
    submitDisabled: releases === 0,
  };
}

/** Selected rows, joined on `_index` (1906-1911). */
export function selectedBatchRows(rows: BatchRow[], selectedIndices: number[]): BatchRow[] {
  return rows.filter((r) => selectedIndices.includes(r._index));
}

export interface BatchAlbumPayload {
  id: string;
  name: string;
  artist_name: string;
  source: string;
}

/**
 * The request body (1954-1969).
 *
 * `artist_name: 'Your Albums'` and `source: null` are placeholders — the
 * backend resolves each album through its own per-album `source` and
 * `artist_name`, so the envelope's values are never read. The URL's
 * `your-albums` segment is the same idea: it makes the route match without
 * naming an arbitrary library artist.
 */
export const BATCH_ENDPOINT = '/api/artist/your-albums/download-discography';
export const BATCH_ENVELOPE_ARTIST = 'Your Albums';

export function batchAlbumsPayload(selected: BatchRow[]): BatchAlbumPayload[] {
  return selected.map((r) => ({
    id: r._src.id,
    name: r.album_name || '',
    artist_name: r.artist_name || '',
    source: r._src.source,
  }));
}

export function batchRequestBody(selected: BatchRow[]): {
  albums: BatchAlbumPayload[];
  artist_name: string;
  source: null;
} {
  return {
    albums: batchAlbumsPayload(selected),
    artist_name: BATCH_ENVELOPE_ARTIST,
    source: null,
  };
}

/** Progress-row key (1930) — composite, since one album_id can span sources. */
export function batchProgressKey(src: AlbumSource): string {
  return `${src.source}-${src.id}`;
}

export type BatchItemStatus = 'waiting' | 'done' | 'error';

export interface BatchProgressState {
  totalAdded: number;
  totalSkipped: number;
  items: Record<string, { status: BatchItemStatus; text: string }>;
}

export const BATCH_WAITING_TEXT = 'Waiting...';
export const BATCH_PROCESSING_INFO = 'Processing... this may take a moment';
export const BATCH_DONE_TEXT = 'Done';

export function initialBatchProgress(selected: BatchRow[]): BatchProgressState {
  const items: BatchProgressState['items'] = {};
  for (const row of selected) {
    items[batchProgressKey(row._src)] = { status: 'waiting', text: BATCH_WAITING_TEXT };
  }
  return { totalAdded: 0, totalSkipped: 0, items };
}

export interface BatchEvent {
  status?: string;
  album_id?: string | number;
  tracks_added?: number;
  tracks_skipped?: number;
  total_added?: number;
  total_skipped?: number;
  message?: string;
}

/**
 * Fold one ndjson event into the progress state (1987-2012).
 *
 * A `complete` event carries the totals; anything with an `album_id` updates
 * one row. The row is found by comparing `String(album_id)` against the
 * source-id we sent — an unmatched id is ignored rather than treated as an
 * error, because the stream can mention albums this modal did not submit.
 */
export function reduceBatchEvent(
  state: BatchProgressState,
  event: BatchEvent,
  selected: BatchRow[],
): BatchProgressState {
  if (event.status === 'complete') {
    return {
      ...state,
      totalAdded: event.total_added || 0,
      totalSkipped: event.total_skipped || 0,
    };
  }
  if (event.album_id === undefined || event.album_id === null || event.album_id === '') {
    return state;
  }
  const matching = selected.find((s) => s._src.id === String(event.album_id));
  if (!matching) return state;
  const key = batchProgressKey(matching._src);
  if (event.status === 'done') {
    const text = `${event.tracks_added || 0} added · ${event.tracks_skipped || 0} skipped`;
    return { ...state, items: { ...state.items, [key]: { status: 'done', text } } };
  }
  if (event.status === 'error') {
    const text = `Error: ${event.message || 'unknown'}`;
    return { ...state, items: { ...state.items, [key]: { status: 'error', text } } };
  }
  return state;
}

/**
 * Split an ndjson buffer into complete lines, carrying the remainder (1981-82).
 *
 * `lines.pop()` is what keeps a chunk that splits mid-JSON from being parsed as
 * two broken halves. Blank lines are skipped (1984); a line that fails to parse
 * is logged and skipped, never aborting the stream (2013-2015).
 */
export function splitNdjson(buffer: string): { lines: string[]; rest: string } {
  const parts = buffer.split('\n');
  const rest = parts.pop() ?? '';
  return { lines: parts.filter((l) => l.trim()), rest };
}

/** Final footer + toast (2019-2029). */
export function batchSummary(state: BatchProgressState): {
  info: string;
  toast: string;
  toastLevel: 'success' | 'info';
} {
  return {
    info: `${state.totalAdded} tracks added to wishlist · ${state.totalSkipped} skipped`,
    toast: `${state.totalAdded} tracks added to wishlist`,
    toastLevel: state.totalAdded > 0 ? 'success' : 'info',
  };
}

/**
 * The per-source attempt order for opening one album's download modal (1524-8).
 *
 * Streaming sources come first because they return tracklists with ids the
 * downloader can act on; Discogs is last because a pure-collection item has a
 * tracklist but no streaming ids. A source that responds with an EMPTY payload
 * does not end the loop (1535) — it falls through to the next.
 */
export function albumDownloadSources(album: YourAlbum): AlbumSource[] {
  const sources: AlbumSource[] = [];
  if (album.spotify_album_id) {
    sources.push({ id: String(album.spotify_album_id), source: 'spotify' });
  }
  if (album.deezer_album_id) sources.push({ id: String(album.deezer_album_id), source: 'deezer' });
  if (album.tidal_album_id) sources.push({ id: String(album.tidal_album_id), source: 'tidal' });
  const discogsId = album.discogs_release_id || album.discogs_id;
  if (discogsId) sources.push({ id: String(discogsId), source: 'discogs' });
  return sources;
}

export const ALBUM_NOT_FOUND = 'Album data not found';
export const ALBUM_NO_TRACKS = 'No tracks found for this album';

/** `discover_album_${...}` (1563) — falls back to the grid index, not the name. */
export function albumVirtualId(album: YourAlbum, index: number): string {
  return `discover_album_${album.spotify_album_id || album.deezer_album_id || album.tidal_album_id || index}`;
}

export interface AlbumDetail {
  id?: string;
  name?: string;
  album_type?: string;
  total_tracks?: number;
  release_date?: string;
  images?: unknown[];
  artists?: unknown;
  tracks?: {
    id?: string;
    name?: string;
    artists?: unknown;
    duration_ms?: number;
    track_number?: number;
  }[];
}

/**
 * Flatten the album-detail payload into the shape the download modal wants
 * (1547-1562).
 *
 * Artist names come from the track, then the album, then the grid row — and
 * each entry is unwrapped with `a.name || a` so a plain string array survives
 * as-is rather than becoming a list of undefineds.
 */
export function mapAlbumTracks(albumData: AlbumDetail, album: YourAlbum) {
  return (albumData.tracks || []).map((track) => {
    let artists: unknown = track.artists || albumData.artists || [{ name: album.artist_name }];
    if (Array.isArray(artists)) {
      artists = artists.map((a) => (a as { name?: string })?.name || a);
    }
    return {
      id: track.id,
      name: track.name,
      artists,
      album: {
        id: albumData.id,
        name: albumData.name,
        album_type: albumData.album_type || 'album',
        total_tracks: albumData.total_tracks || 0,
        release_date: albumData.release_date || '',
        images: albumData.images || [],
      },
      duration_ms: track.duration_ms || 0,
      track_number: track.track_number || 0,
    };
  });
}

/** The album object handed to the modal (1564-1568). */
export function mapAlbumObject(albumData: AlbumDetail, album: YourAlbum) {
  return {
    id: albumData.id,
    name: albumData.name,
    album_type: albumData.album_type || 'album',
    total_tracks: albumData.total_tracks || 0,
    release_date: albumData.release_date || '',
    images: albumData.images || [],
    artists: [{ name: album.artist_name }],
  };
}

// ── Sources modal (1605) ────────────────────────────────────────────────────

/** The fallback when `/your-albums/sources` cannot be read (1609). */
export const YOUR_ALBUMS_DEFAULT_SOURCES = ['spotify', 'tidal', 'deezer'];

export interface SourceInfo {
  id: string;
  label: string;
  icon: string;
}

/** 1620-1625. Discogs is offered but is NOT on by default. */
export const YOUR_ALBUMS_SOURCE_INFO: SourceInfo[] = [
  { id: 'spotify', label: 'Spotify', icon: '🎵' },
  { id: 'tidal', label: 'Tidal', icon: '🌊' },
  { id: 'deezer', label: 'Deezer', icon: '🎶' },
  { id: 'discogs', label: 'Discogs', icon: '💿' },
];

export const SOURCE_CONNECTED = 'Connected';
export const SOURCE_NOT_CONNECTED = 'Not connected';

/** `state[s.id] = enabled.includes(s.id)` (1627) — every source gets a key. */
export function initialSourcesState(enabled: string[]): Record<string, boolean> {
  const state: Record<string, boolean> = {};
  for (const s of YOUR_ALBUMS_SOURCE_INFO) state[s.id] = enabled.includes(s.id);
  return state;
}

/**
 * Per-source setup hints (1668-1673).
 *
 * These exist because the toggle used to bail silently on a disconnected
 * source, which read as a broken switch. The generic fallback covers a source
 * added to the list without a hint.
 */
export const DISCONNECTED_HINTS: Record<string, string> = {
  spotify: 'Spotify not connected — log in at Settings → Connections first',
  tidal: 'Tidal not connected — set up Tidal in Settings → Connections first',
  deezer: 'Deezer not connected — log in or set ARL token at Settings → Connections first',
  discogs:
    'Discogs not connected — paste your personal access token at Settings → Connections first',
};

export function disconnectedHint(id: string): string {
  return (
    DISCONNECTED_HINTS[id] || `${id} not connected — set it up in Settings → Connections first`
  );
}

/** A disconnected source refuses the toggle and explains why (1689-1698). */
export function toggleSource(
  state: Record<string, boolean>,
  id: string,
  connected: string[],
): { state: Record<string, boolean>; hint: string | null } {
  if (!connected.includes(id)) return { state, hint: disconnectedHint(id) };
  return { state: { ...state, [id]: !state[id] }, hint: null };
}

export const SOURCES_NONE_SELECTED = 'Select at least one source';
export const SOURCES_SAVED = 'Sources saved — refresh to apply';
export const SOURCES_SAVE_FAILED = 'Failed to save sources';

/** Enabled ids, in `YOUR_ALBUMS_SOURCE_INFO` order (1700). */
export function enabledSources(state: Record<string, boolean>): string[] {
  return Object.entries(state)
    .filter(([, v]) => v)
    .map(([k]) => k);
}

/** The settings write (1703-1707) — a COMMA-joined string, not an array. */
export function sourcesSavePayload(enabled: string[]): {
  discover: { your_albums_sources: string };
} {
  return { discover: { your_albums_sources: enabled.join(',') } };
}

/**
 * The subtitle rewritten on save (1711-1716).
 *
 * Joined with ' and ', and the apostrophe is a RIGHT SINGLE QUOTATION MARK
 * (U+2019), not an ASCII one — this string is compared in the parity tests.
 */
export const SOURCE_DISPLAY_NAMES: Record<string, string> = {
  spotify: 'Spotify',
  tidal: 'Tidal',
  deezer: 'Deezer',
  discogs: 'Discogs',
};

export function savedSourcesSubtitle(enabled: string[]): string {
  const names = enabled.map((s) => SOURCE_DISPLAY_NAMES[s] || s).join(' and ');
  return `Albums you’ve saved on ${names}`;
}
