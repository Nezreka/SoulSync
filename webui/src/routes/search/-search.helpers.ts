/**
 * Enhanced search's pure logic, ported from search.js + shared-helpers.js.
 *
 * The one deliberate behaviour CHANGE lives here: `albumOwnershipByIdentity`.
 * See its comment — the vanilla matched the library-check response to cards by
 * list position, and that is demonstrably wrong.
 */

import type {
  EnhancedSearchResponse,
  SearchAlbum,
  SearchArtist,
  SearchTrack,
  SourceResults,
} from './-search.types';

import { EXPERIMENTAL_SOURCES, SOURCE_LABELS, SOURCE_ORDER } from './-search.types';

/** The shortest query that is allowed to fire. Below this the dropdown hides. */
export const MIN_QUERY_LENGTH = 2;

/**
 * Input debounce, raised from 300ms to 600ms for #751.
 *
 * Enter bypasses it entirely — see the page component.
 */
export const SEARCH_DEBOUNCE_MS = 600;

/**
 * A bare MusicBrainz UUID is treated as an ID LOOKUP, not a fuzzy search.
 *
 * Anchored on purpose: a query that merely CONTAINS a uuid is still a text
 * search.
 */
export const MBID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isIdLookupQuery(query: string): boolean {
  return MBID_RE.test(query.trim());
}

export function shouldSearch(query: string): boolean {
  return query.trim().length >= MIN_QUERY_LENGTH;
}

/** The source label row, minus experimental sources the user has not enabled. */
export function visibleSources(enabledExperimental: ReadonlySet<string>): string[] {
  return SOURCE_ORDER.filter(
    (source) => !EXPERIMENTAL_SOURCES.has(source) || enabledExperimental.has(source),
  );
}

/**
 * `spotify_free` has a label but NO icon in the picker order.
 *
 * /status can report it as the user's primary source, and leaving it unmapped
 * renders a picker with nothing active at all.
 */
export function pickerSource(source: string | undefined | null): string {
  if (!source) return 'spotify';
  return source === 'spotify_free' ? 'spotify' : source;
}

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source]?.text ?? source;
}

/** Empty slice — every consumer reads five arrays, so none of them may be absent. */
export function emptySourceResults(): SourceResults {
  return { db_artists: [], artists: [], albums: [], tracks: [], videos: [] };
}

/** Unpack /api/enhanced-search into the per-source cache shape. */
export function sourceResultsFromResponse(data: EnhancedSearchResponse): SourceResults {
  return {
    db_artists: data.db_artists ?? [],
    artists: data.spotify_artists ?? [],
    albums: data.spotify_albums ?? [],
    tracks: data.spotify_tracks ?? [],
    videos: [],
  };
}

/**
 * Did the server serve something other than what was asked for?
 *
 * `primary_source` is what actually answered. When it differs, the banner names
 * both so the user is not silently reading Deezer results under a Spotify icon.
 */
export function fallbackFor(requested: string, data: EnhancedSearchResponse): string | null {
  const served = data.primary_source || data.metadata_source;
  if (!served) return null;
  return pickerSource(served) === pickerSource(requested) ? null : served;
}

export function fallbackBannerText(requested: string, served: string): string {
  return `${sourceLabel(requested)} unavailable — showing ${sourceLabel(served)}.`;
}

/**
 * Albums vs singles/EPs.
 *
 * "albums" is the catch-all: anything whose album_type is not explicitly single
 * or ep lands there, including an unknown or missing type.
 */
export function splitAlbums(all: SearchAlbum[]): {
  albums: SearchAlbum[];
  singlesAndEps: SearchAlbum[];
} {
  const singlesAndEps = all.filter((a) => a.album_type === 'single' || a.album_type === 'ep');
  const albums = all.filter((a) => a.album_type !== 'single' && a.album_type !== 'ep');
  return { albums, singlesAndEps };
}

/**
 * Stable identity for one album row.
 *
 * Used to carry ownership from the library-check response back to the right
 * card. Falls back to name+artist because not every source returns an id.
 */
export function albumIdentity(album: SearchAlbum): string {
  if (album.id != null && album.id !== '') return `id:${String(album.id)}`;
  return `na:${(album.name ?? '').toLowerCase()}|${(album.artist ?? '').toLowerCase()}`;
}

/**
 * Ownership per album, keyed by IDENTITY rather than list position.
 *
 * **This fixes a real bug rather than porting it.** The vanilla sent the
 * unsplit `spotify_albums` array to /api/enhanced-search/library-check, which
 * answers one boolean per row IN REQUEST ORDER — then applied the answers by
 * indexing `document.querySelectorAll('#enh-albums-list .enh-compact-item,
 * #enh-singles-list .enh-compact-item')`, which returns DOCUMENT order: every
 * album, then every single.
 *
 * Those two orders only agree when the response happens to be pre-grouped, and
 * it is not: core/search/orchestrator.py passes the provider's array straight
 * through with no sort, so albums and singles interleave freely. Given
 * [album, single, album], the DOM is [album, album, single] and the third
 * answer lands on the second album — an "In Library" badge on a release you do
 * not own.
 *
 * Keying by identity makes the split irrelevant.
 */
export function albumOwnershipByIdentity(
  requested: SearchAlbum[],
  flags: boolean[] | undefined,
): Set<string> {
  const owned = new Set<string>();
  if (!flags?.length) return owned;
  requested.forEach((album, index) => {
    if (flags[index]) owned.add(albumIdentity(album));
  });
  return owned;
}

/** `_formatViewCount` — 1.2M / 3.4K / raw. */
export function formatViewCount(count: number | undefined | null): string {
  const n = Number(count);
  if (!Number.isFinite(n) || n <= 0) return '';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

/** `formatDuration` — m:ss from milliseconds. */
export function formatDuration(durationMs: number | undefined | null): string {
  const ms = Number(durationMs);
  if (!Number.isFinite(ms) || ms <= 0) return '';
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

/** An artist's display line: the vanilla showed a library track count or the source. */
export function artistMetaLine(artist: SearchArtist, inLibrary: boolean): string {
  if (inLibrary) {
    const count = artist.track_count ?? 0;
    return `${count} track${count === 1 ? '' : 's'}`;
  }
  return sourceLabel(artist.source ?? '');
}

/** A label's display line — `type • area`, or a plain fallback. */
export function labelMetaLine(label: { type?: string; area?: string }): string {
  const parts = [label.type, label.area].filter(Boolean);
  return parts.length ? parts.join(' • ') : 'Record label';
}

/**
 * Does this source's result set have anything at all in it?
 *
 * Drives the empty state. Videos count: a youtube_videos search with videos and
 * nothing else is NOT empty.
 */
export function hasAnyResults(results: SourceResults): boolean {
  return (
    results.db_artists.length > 0 ||
    results.artists.length > 0 ||
    results.albums.length > 0 ||
    results.tracks.length > 0 ||
    results.videos.length > 0
  );
}

/** Track identity, for carrying library-check answers back to track rows. */
export function trackIdentity(track: SearchTrack): string {
  if (track.id != null && track.id !== '') return `id:${String(track.id)}`;
  return `na:${(track.name ?? '').toLowerCase()}|${(track.artist ?? '').toLowerCase()}`;
}
