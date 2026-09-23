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

/** a pasted link, one token, no spaces. the lookup endpoint says if it knows the site */
export const LINK_RE = /^https?:\/\/\S+$/i;

export function isIdLookupQuery(query: string): boolean {
  const trimmed = query.trim();
  return MBID_RE.test(trimmed) || LINK_RE.test(trimmed);
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

/** Empty slice — every consumer reads six arrays, so none of them may be absent. */
export function emptySourceResults(): SourceResults {
  return { db_artists: [], artists: [], albums: [], tracks: [], playlists: [], videos: [] };
}

/** Unpack /api/enhanced-search into the per-source cache shape. */
export function sourceResultsFromResponse(data: EnhancedSearchResponse): SourceResults {
  return {
    db_artists: data.db_artists ?? [],
    artists: data.spotify_artists ?? [],
    albums: data.spotify_albums ?? [],
    tracks: data.spotify_tracks ?? [],
    playlists: data.spotify_playlists ?? [],
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

/**
 * An album's display line: artist, then the year.
 *
 * deezer's album search carries no date, so the vanilla printed "N/A" on every
 * card. no year falls back to the track count, and nothing falls back to
 * nothing. `withKind` names a single or EP folded in with the albums.
 */
export function albumMetaLine(album: SearchAlbum, { withKind = false } = {}): string {
  const year = album.release_date ? album.release_date.slice(0, 4) : '';
  const tracks = album.total_tracks ? `${album.total_tracks} tracks` : '';
  const kind = withKind ? singleKind(album) : '';
  return [album.artist, kind || year || tracks, kind ? year : ''].filter(Boolean).join(' • ');
}

function singleKind(album: SearchAlbum): string {
  if (album.album_type === 'single') return 'Single';
  if (album.album_type === 'ep') return 'EP';
  return '';
}

/** below this many, singles and EPs ride at the end of the albums row */
export const SINGLES_SHELF_MIN = 3;

/**
 * The albums and singles as the page lays them out.
 *
 * one or two singles made a whole shelf for a card or two. they fold into the
 * albums row instead, marked with their kind. counts and render both read this
 * so a pill never counts something the page does not show under it.
 */
export function shelfAlbums(all: SearchAlbum[]): {
  albums: SearchAlbum[];
  singlesAndEps: SearchAlbum[];
} {
  const { albums, singlesAndEps } = splitAlbums(all);
  if (albums.length > 0 && singlesAndEps.length > 0 && singlesAndEps.length < SINGLES_SHELF_MIN) {
    return { albums: [...albums, ...singlesAndEps], singlesAndEps: [] };
  }
  return { albums, singlesAndEps };
}

function nameKey(name: string | undefined): string {
  return (name ?? '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
}

export interface ArtistFaceEntry {
  artist: SearchArtist;
  inLibrary: boolean;
}

/**
 * Library artists first, then the found ones, without the twin.
 *
 * the source's best match for a library artist is the same artist, so showing
 * both put two identical U2 faces side by side. the first found artist sharing
 * a library artist's name is dropped; the library one wins because it links to
 * what you own. later same-named artists are real strangers and stay.
 */
export function mergeArtistFaces(
  dbArtists: SearchArtist[],
  artists: SearchArtist[],
): ArtistFaceEntry[] {
  const unmatched = new Set(dbArtists.map((artist) => nameKey(artist.name)));
  const found = artists.filter((artist) => {
    const key = nameKey(artist.name);
    if (!key || !unmatched.has(key)) return true;
    unmatched.delete(key);
    return false;
  });
  return [
    ...dbArtists.map((artist) => ({ artist, inLibrary: true })),
    ...found.map((artist) => ({ artist, inLibrary: false })),
  ];
}

/** 9800000 → "9.8M", 12400 → "12K" */
export function compactCount(value: number): string {
  if (value >= 1_000_000) return `${trimZero(value / 1_000_000)}M`;
  if (value >= 1_000) return `${trimZero(value / 1_000)}K`;
  return String(value);
}

function trimZero(value: number): string {
  return (value >= 100 ? Math.round(value) : Math.round(value * 10) / 10).toString();
}

/** the quiet line under a found artist. deezer calls them fans */
export function foundArtistLine(artist: SearchArtist): string {
  if (!artist.followers) return 'Artist';
  const noun = artist.source === 'deezer' ? 'fans' : 'followers';
  return `${compactCount(artist.followers)} ${noun}`;
}

const EDITORIAL_RE = /\b(deezer|spotify|tidal|apple music|qobuz)\b/i;

/**
 * Playlists with the likely click first.
 *
 * the source ranks by its own idea of popular, which put a stranger's
 * 2171-track "Joe Joe Vault II" beside "100% U2". editorial playlists that name
 * the query lead, then any that name it, then editorial, then the rest, each
 * group in the source's order.
 */
export function rankPlaylists<T extends { name?: string; creator?: string }>(
  playlists: T[],
  query: string,
): T[] {
  const needle = nameKey(query);
  const score = (playlist: T) => {
    const named = needle ? nameKey(playlist.name).includes(needle) : false;
    const editorial = EDITORIAL_RE.test(playlist.creator ?? '');
    return (named ? 0 : 2) + (editorial ? 0 : 1);
  };
  return playlists
    .map((playlist, index) => ({ playlist, index, rank: score(playlist) }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((entry) => entry.playlist);
}

const QUALIFIER_RE =
  /remaster|live|version|edit\b|mix\b|mono|stereo|deluxe|edition|bonus|acoustic|demo|instrumental|anniversary/i;
const TRAILING_GROUP_RE = /\s*([([][^()[\]]*[)\]])\s*$/;
const TRAILING_DASH_RE = /\s+-\s+([^-]+)$/;

/**
 * "Pride (In The Name Of Love) (Remastered 2009)" → the title, and the
 * trailing qualifier to set in a quieter tone. only a last group or dash tail
 * that reads like a release note dims; "(In The Name Of Love)" is the name.
 */
export function splitTitleExtra(title: string): { main: string; extra: string } {
  const trimmed = title.trim();
  for (const re of [TRAILING_GROUP_RE, TRAILING_DASH_RE]) {
    const match = re.exec(trimmed);
    if (!match || !QUALIFIER_RE.test(match[1])) continue;
    const main = trimmed.slice(0, match.index).trim();
    if (main) return { main, extra: match[1].trim() };
  }
  return { main: title, extra: '' };
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
    results.playlists.length > 0 ||
    results.videos.length > 0
  );
}

/** Track identity, for carrying library-check answers back to track rows. */
export function trackIdentity(track: SearchTrack): string {
  if (track.id != null && track.id !== '') return `id:${String(track.id)}`;
  return `na:${(track.name ?? '').toLowerCase()}|${(track.artist ?? '').toLowerCase()}`;
}
