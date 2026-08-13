/**
 * Watch All Unwatched (openWatchAllUnwatchedModal, library.js:15-220): add
 * every unwatched artist with a provider id to the watchlist. Eligibility is
 * keyed on the ACTIVE music source's id field — an iTunes-primary install
 * checks itunes_artist_id, not Spotify's.
 */

export interface WatchAllArtist {
  id?: unknown;
  name: string;
  image_url?: string;
  track_count?: number;
  [key: string]: unknown;
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
 * 54-78), splitting eligible from ineligible by the source id field.
 * onProgress reports the running total; a false return from shouldContinue
 * aborts (the vanilla stopped when the modal was closed mid-load).
 */
export async function loadUnwatchedArtists(
  sourceField: string,
  onProgress: (loaded: number) => void,
  shouldContinue: () => boolean = () => true,
): Promise<{ eligible: WatchAllArtist[]; ineligible: WatchAllArtist[] }> {
  const eligible: WatchAllArtist[] = [];
  const ineligible: WatchAllArtist[] = [];
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
      if (artist[sourceField]) eligible.push(artist);
      else ineligible.push(artist);
    }
    if (!data.pagination.has_next) break;
    page++;
  }
  return { eligible, ineligible };
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
