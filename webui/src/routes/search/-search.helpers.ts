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
const RECENT_KEY = 'soulsyncRecentSearches';
const RECENT_MAX = 8;

/** The recent-searches list, newest first. Storage failures read as empty. */
export function loadRecentSearches(): string[] {
  try {
    const raw: unknown = JSON.parse(window.localStorage.getItem(RECENT_KEY) || '[]');
    return Array.isArray(raw)
      ? raw
          .filter((q): q is string => typeof q === 'string' && q.trim() !== '')
          .slice(0, RECENT_MAX)
      : [];
  } catch {
    return [];
  }
}

/** Record a submitted query — case-insensitively deduped, capped, newest first. */
export function saveRecentSearch(query: string): string[] {
  const q = query.trim();
  if (!q) return loadRecentSearches();
  const next = [
    q,
    ...loadRecentSearches().filter((x) => x.toLowerCase() !== q.toLowerCase()),
  ].slice(0, RECENT_MAX);
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* storage full/denied — the list just doesn't persist */
  }
  return next;
}

/** Forget one entry (the chip's ✕). */
export function removeRecentSearch(query: string): string[] {
  const next = loadRecentSearches().filter((x) => x.toLowerCase() !== query.trim().toLowerCase());
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* ditto */
  }
  return next;
}

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

/**
 * Which source the picker should start on.
 *
 * Four rules, in the order shared-helpers.js:361-395 applies them, and each one
 * exists because of a way the picker can otherwise open unusable:
 *
 *   1. take /status's primary source, mapped through pickerSource;
 *   2. anything unrecognised falls back to spotify;
 *   3. an experimental source that is not enabled is not in the row at all, so
 *      starting on it would leave nothing selected;
 *   4. a source with no credentials would show an empty result set and blame the
 *      provider — fall forward to the first source that IS configured.
 */
export function resolveInitialSource({
  statusSource,
  enabledExperimental,
  configured,
}: {
  statusSource: string;
  enabledExperimental: ReadonlySet<string>;
  configured: Record<string, boolean>;
}): string {
  let source = pickerSource(statusSource);
  if (!SOURCE_LABELS[source]) source = 'spotify';
  if (EXPERIMENTAL_SOURCES.has(source) && !enabledExperimental.has(source)) source = 'spotify';
  if (configured[source] === false) {
    const usable = visibleSources(enabledExperimental).find((s) => configured[s] !== false);
    if (usable) source = usable;
  }
  return source;
}

/** May the picker switch to this source at all? */
export function canSelectSource(source: string, enabledExperimental: ReadonlySet<string>): boolean {
  if (!SOURCE_LABELS[source]) return false;
  // A hidden experimental source has no icon; selecting it programmatically
  // would strand the row with nothing active.
  return !EXPERIMENTAL_SOURCES.has(source) || enabledExperimental.has(source);
}

/** Empty slice — every consumer reads five arrays, so none of them may be absent. */
export function emptySourceResults(): SourceResults {
  return { db_artists: [], artists: [], albums: [], tracks: [], videos: [] };
}

/**
 * The library artists any source of this query already resolved (iss29-B04a).
 *
 * "In Your Library" is a local catalogue result: it does not depend on which
 * provider tab is active, so a source that cannot produce it (the video
 * search) should show what a sibling already found rather than an empty
 * section.
 */
export function knownDbArtists(
  sources: Partial<Record<string, SourceResults>>,
): SourceResults['db_artists'] {
  for (const results of Object.values(sources)) {
    if (results?.db_artists?.length) return results.db_artists;
  }
  return [];
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
  // A RAW comparison, as shared-helpers.js:504 has it — deliberately not
  // normalised through pickerSource. On a no-credentials instance the server
  // answers a 'spotify' request with 'spotify_free', and that IS worth saying:
  // normalising would collapse the two and silently drop the banner telling the
  // user which Spotify actually served their results.
  return served === requested ? null : served;
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

/**
 * `_formatViewCount` — 1.2B / 1.2M / 3.4K / raw.
 *
 * The billions branch is not hypothetical on a music-video grid: plenty of the
 * things people search for here have passed a billion plays, and without it they
 * render as "1200.0M".
 */
export function formatViewCount(count: number | undefined | null): string {
  const n = Number(count);
  if (!Number.isFinite(n) || n <= 0) return '';
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
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

/**
 * Video durations are SECONDS, unlike track durations which are milliseconds.
 *
 * Two fields both called `duration`, in different units, on shapes that sit
 * side by side in the same results — kept as separate functions so no caller
 * has to remember which is which.
 */
export function formatVideoDuration(seconds: number | undefined | null): string {
  const total = Number(seconds);
  if (!Number.isFinite(total) || total <= 0) return '';
  const minutes = Math.floor(total / 60);
  return `${minutes}:${String(Math.floor(total % 60)).padStart(2, '0')}`;
}

/**
 * An artist's display line — two fixed strings, as search.js:480/494 has them.
 *
 * Deliberately NOT a track count: `_build_db_artists`
 * (core/search/orchestrator.py:121-134) sends only id, name and image_url, so a
 * count would read "0 tracks" on every library artist. The badge beside it
 * already says Library or names the source.
 */
export function artistMetaLine(inLibrary: boolean): string {
  return inLibrary ? 'In Your Library' : 'Artist';
}

/** A track's display line — `artist • album`, skipping whichever is missing. */
export function trackMetaLine(track: SearchTrack): string {
  return [track.artist, track.album].filter(Boolean).join(' • ');
}

/** An album's display line — `artist • year`, with the vanilla's N/A year. */
export function albumMetaLine(album: SearchAlbum): string {
  const year = album.release_date ? album.release_date.slice(0, 4) : 'N/A';
  return `${album.artist ?? ''} • ${year}`;
}

/**
 * Where an artist card points, mirroring buildArtistDetailPath (init.js:2964).
 *
 * The SOURCE SEGMENT is not optional. `/artist-detail/<source>/<id>` is what
 * parseArtistDetailPath splits on, and it rejects anything with fewer than three
 * segments outright — so a path without it resolves to nothing at all. An artist
 * from the library has no metadata source, and the literal 'library' is what
 * fills the slot (_normalizeArtistDetailSource, init.js:2959).
 *
 * `name` rides along because some sources have no id lookup at all (Bandcamp):
 * on a cold page load the name is the only thing left to resolve against.
 */
export function artistDetailPath(
  artistId: string | number,
  source?: string | null,
  name?: string | null,
): string {
  const normalized =
    String(source ?? '')
      .trim()
      .toLowerCase() || 'library';
  const path = `/artist-detail/${encodeURIComponent(normalized)}/${encodeURIComponent(String(artistId))}`;
  return name ? `${path}?name=${encodeURIComponent(name)}` : path;
}

/**
 * Where an "In Your Library" artist card points.
 *
 * The library is Library v2 now, and so is the bucket: `_build_db_artists`
 * reads the v2 catalogue, so every card here has a v2 id and opens the page
 * that can actually manage the artist (monitoring, wanted, quality profile).
 * The artist-detail fallback stays for callers that pass an artist from
 * somewhere else — inventing a v2 id for one would 404 the page.
 */
export function inLibraryArtistPath(artist: {
  id?: string | number;
  library_v2_id?: number | null;
}): string {
  return artist.library_v2_id
    ? // ldp-05 (iss29-B05): arriving from a search result means landing on what
      // the legacy artist page showed — full discography, cards, rich header.
      // Without these the direct deep link fell back to the in-library defaults
      // (My Library / table / compact), so the same artist looked different
      // depending on whether v2 had mapped it yet.
      `/library?artist=${encodeURIComponent(String(artist.library_v2_id))}` +
        `&releases=all&releaseView=cards&header=rich`
    : artistDetailPath(artist.id ?? '');
}

/** Where a label card points — buildLabelDetailPath (init.js:3003). */
export function labelDetailPath(labelId: string | number, name?: string | null): string {
  const path = `/label-detail/${encodeURIComponent(String(labelId))}`;
  return name ? `${path}?name=${encodeURIComponent(name)}` : path;
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
