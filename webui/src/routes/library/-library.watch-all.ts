/**
 * Watch All Unwatched (openWatchAllUnwatchedModal, library.js:15-220): add
 * every unwatched artist with a provider id to the watchlist. The server
 * takes the active source's id first, then any other it knows, so an artist
 * with any of those ids is added.
 */

export interface WatchAllArtist {
  id?: unknown;
  name: string;
  image_url?: string;
  track_count?: number;
  [key: string]: unknown;
}

/**
 * the ids the watchlist add accepts, in the server's order
 * (core/watchlist/source_picker.SOURCE_ID_COLUMNS). review 5 W2-WL-2: the
 * modal only counted the active source's id, so it said "no id" for artists
 * the add then took, and could disable the button outright.
 */
export const WATCHLIST_ID_FIELDS = [
  'spotify_artist_id',
  'itunes_artist_id',
  'deezer_id',
  'discogs_id',
  'musicbrainz_id',
] as const;

export function hasWatchlistId(artist: WatchAllArtist): boolean {
  return WATCHLIST_ID_FIELDS.some((field) => Boolean(artist[field]));
}

export function watchAllSourceField(sourceName: string | undefined): string {
  return sourceName === 'iTunes'
    ? 'itunes_artist_id'
    : sourceName === 'Deezer'
      ? 'deezer_id'
      : 'spotify_artist_id';
}

/**
 * Fetch every unwatched artist, paginated at 400 (SQLite variable-limit safe,
 * 54-78), splitting eligible (any id the add accepts) from ineligible.
 * otherSource counts the eligible ones without the active source's id.
 * onProgress reports the running total; a false return from shouldContinue
 * aborts (the vanilla stopped when the modal was closed mid-load).
 */
export async function loadUnwatchedArtists(
  sourceField: string,
  onProgress: (loaded: number) => void,
  shouldContinue: () => boolean = () => true,
): Promise<{ eligible: WatchAllArtist[]; ineligible: WatchAllArtist[]; otherSource: number }> {
  const eligible: WatchAllArtist[] = [];
  const ineligible: WatchAllArtist[] = [];
  let otherSource = 0;
  let page = 1;
  const pageSize = 400;

  for (;;) {
    if (!shouldContinue()) break;
    onProgress(eligible.length + ineligible.length);
    const params = new URLSearchParams({
      search: '',
      letter: 'all',
      page: String(page),
      limit: String(pageSize),
      watchlist: 'unwatched',
    });
    const response = await fetch(`/api/library/artists?${params}`);
    const data = await response.json();
    if (!data.success) throw new Error(data.error || 'Failed to load artists');
    for (const artist of data.artists || []) {
      if (!hasWatchlistId(artist)) ineligible.push(artist);
      else {
        eligible.push(artist);
        if (!artist[sourceField]) otherSource += 1;
      }
    }
    if (!data.pagination.has_next) break;
    page++;
  }
  return { eligible, ineligible, otherSource };
}

export interface WatchAllResult {
  added: number;
  skipped_already: number;
  skipped_no_id: number;
}

export async function watchAllUnwatchedRequest(): Promise<WatchAllResult> {
  const response = await fetch('/api/library/watchlist-all-unwatched', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  const data = await response.json();
  if (!data.success) throw new Error(data.error || 'Failed to add artists');
  return {
    added: data.added || 0,
    skipped_already: data.skipped_already || 0,
    skipped_no_id: data.skipped_no_id || 0,
  };
}
