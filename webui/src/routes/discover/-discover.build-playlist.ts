/**
 * Build a Playlist — seed-artist picker and generator.
 *
 * Transcribed from `searchBuildPlaylistArtists` (10891),
 * `addBuildPlaylistArtist` (10956), `removeBuildPlaylistArtist` (10980),
 * `renderBuildPlaylistSelectedArtists` (10985), `generateBuildPlaylist` (11021)
 * and `openDownloadModalForBuildPlaylist` (11103) — read end to end.
 */

/** `setTimeout(..., 400)` (10953) — matches the Your Albums grid, not the modal's 300. */
export const BP_SEARCH_DEBOUNCE_MS = 400;

/** `buildPlaylistSelectedArtists.length >= 5` (10961). */
export const BP_MAX_SEEDS = 5;

/** `playlist_size: 50` (11048) — fixed, not user-configurable. */
export const BP_PLAYLIST_SIZE = 50;

export const BP_ARTIST_PLACEHOLDER = '/static/placeholder-album.png';

export const BP_SEARCH_FAILED = 'Search failed';
export const BP_ALREADY_SELECTED = 'Artist already selected';
export const BP_MAX_REACHED = 'Maximum 5 seed artists';
export const BP_NEED_ONE = 'Please select at least 1 artist';
export const BP_ALL_SELECTED = 'All results already selected';
export const BP_NO_SELECTION_HINT = 'Search above to add seed artists';
export const BP_GENERATE_FAILED = 'Failed to generate playlist';
export const BP_NO_TRACKS = 'No tracks found. Try different seed artists.';
export const BP_NO_PLAYLIST_TRACKS = 'No playlist tracks available';

export interface SeedArtist {
  id: string;
  name: string;
  image_url?: string;
}

export function bpSearchUrl(query: string): string {
  return `/api/discover/build-playlist/search-artists?query=${encodeURIComponent(query)}`;
}

/**
 * An EMPTY query short-circuits before the debounce (10897-10902).
 *
 * Clearing the box hides the results immediately rather than 400ms later, and
 * fires no request — so backspacing to empty does not leave a stale list up.
 */
export function bpQueryIsEmpty(raw: string): boolean {
  return raw.trim() === '';
}

export type BpSearchOutcome =
  | { kind: 'results'; artists: SeedArtist[] }
  | { kind: 'none'; message: string }
  | { kind: 'all-selected'; message: string };

/**
 * What the search does with its response (10915-10929).
 *
 * Already-selected artists are filtered OUT of the results, and a page where
 * everything was filtered gets its own message — "All results already selected"
 * rather than "No artists found", which would read as a failed search.
 */
export function bpSearchOutcome(
  data: { success?: boolean; artists?: SeedArtist[] } | null | undefined,
  query: string,
  selected: SeedArtist[],
): BpSearchOutcome {
  if (!data?.success || !data.artists || data.artists.length === 0) {
    return { kind: 'none', message: bpNoResultsMessage(query) };
  }
  const selectedIds = new Set(selected.map((a) => a.id));
  const filtered = data.artists.filter((a) => !selectedIds.has(a.id));
  if (filtered.length === 0) return { kind: 'all-selected', message: BP_ALL_SELECTED };
  return { kind: 'results', artists: filtered };
}

/** `No artists found for "<query>"` (10916). React escapes the query at render. */
export function bpNoResultsMessage(query: string): string {
  return `No artists found for "${query}"`;
}

export type BpAddResult =
  | { added: true; selected: SeedArtist[] }
  | { added: false; warning: string };

/**
 * Adding a seed (10956-10978).
 *
 * Two refusals with DIFFERENT messages, and the duplicate check comes first —
 * so re-adding an artist while already at five says "already selected", not
 * "maximum reached", which would be misleading.
 */
export function bpAddArtist(selected: SeedArtist[], artist: SeedArtist): BpAddResult {
  if (selected.some((a) => a.id === artist.id)) {
    return { added: false, warning: BP_ALREADY_SELECTED };
  }
  if (selected.length >= BP_MAX_SEEDS) {
    return { added: false, warning: BP_MAX_REACHED };
  }
  return {
    added: true,
    selected: [...selected, { id: artist.id, name: artist.name, image_url: artist.image_url }],
  };
}

export function bpRemoveArtist(selected: SeedArtist[], artistId: string): SeedArtist[] {
  return selected.filter((a) => a.id !== artistId);
}

export interface BpSelectionState {
  count: number;
  counterLabel: string;
  /** The generate button is disabled with nothing selected (10999). */
  generateDisabled: boolean;
  showEmptyHint: boolean;
}

/** `${count} / 5` (10991). */
export function bpSelectionState(selected: SeedArtist[]): BpSelectionState {
  const count = selected.length;
  return {
    count,
    counterLabel: `${count} / ${BP_MAX_SEEDS}`,
    generateDisabled: count === 0,
    showEmptyHint: count === 0,
  };
}

export function bpArtistImage(artist: SeedArtist): string {
  return artist.image_url || BP_ARTIST_PLACEHOLDER;
}

// ── Generating ──────────────────────────────────────────────────────────────

export const BP_GENERATE_URL = '/api/discover/build-playlist/generate';

export function bpGenerateBody(selected: SeedArtist[]): {
  seed_artist_ids: string[];
  playlist_size: number;
} {
  return {
    seed_artist_ids: selected.map((a) => a.id),
    playlist_size: BP_PLAYLIST_SIZE,
  };
}

export interface BpPlaylistMeta {
  total_tracks?: number;
  similar_artists_count?: number;
  albums_count?: number;
}

/**
 * The error a failed generate reports (11053-11058).
 *
 * Two distinct failures with two distinct fallbacks: the request failing reads
 * `data.error`, while a successful request that produced no tracks reads
 * `data.playlist.error` — the generator explains WHY it found nothing, and
 * flattening these would lose that.
 */
export function bpGenerateError(
  responseOk: boolean,
  data: { success?: boolean; error?: string; playlist?: { tracks?: unknown[]; error?: string } },
): string | null {
  if (!responseOk || !data.success) return data.error || BP_GENERATE_FAILED;
  if (!data.playlist?.tracks || data.playlist.tracks.length === 0) {
    return data.playlist?.error || BP_NO_TRACKS;
  }
  return null;
}

export const BP_RESULT_TITLE = 'Custom Playlist';

/** `Based on: ${names}` (11066) — COMMA-joined, unlike the ' and ' elsewhere. */
export function bpResultSubtitle(selected: SeedArtist[]): string {
  return `Based on: ${selected.map((a) => a.name).join(', ')}`;
}

export interface BpMetaStat {
  value: number;
  label: string;
}

/** The three stat tiles, in order (11070-11084). */
export function bpMetaStats(metadata: BpPlaylistMeta | undefined): BpMetaStat[] {
  return [
    { value: metadata?.total_tracks ?? 0, label: 'Tracks' },
    { value: metadata?.similar_artists_count ?? 0, label: 'Similar Artists' },
    { value: metadata?.albums_count ?? 0, label: 'Albums Sampled' },
  ];
}

// ── Downloading the result ──────────────────────────────────────────────────

/**
 * `build_playlist_custom` (11111).
 *
 * NOT `discover_build_playlist`. The sync path builds its virtual id as
 * `discover_${type}` and this download path does not follow that convention —
 * the two ids refer to the same playlist through different systems. Unifying
 * them would break whichever caller was not updated.
 */
export const BP_DOWNLOAD_PLAYLIST_ID = 'build_playlist_custom';

/** `Custom Playlist - ${names}` (11110) — a HYPHEN, where the subtitle uses a colon. */
export function bpDownloadName(selected: SeedArtist[]): string {
  return `Custom Playlist - ${selected.map((a) => a.name).join(', ')}`;
}
