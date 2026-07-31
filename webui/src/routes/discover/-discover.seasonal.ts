/**
 * Seasonal Discovery — the season's albums and its playlist.
 *
 * Transcribed from `loadSeasonalContent` (4263), `_renderSeasonalAlbumCard`
 * (4295), `loadSeasonalAlbums` (4316), `loadSeasonalPlaylist` (4348),
 * `hideSeasonalSections` (4381), `_buildDiscoverArtistContext` (4393) and
 * `openDownloadModalForSeasonalAlbum` (4422) — read end to end.
 *
 * `openDownloadModalForSeasonalPlaylist` (4505) and `syncSeasonalPlaylist`
 * (4522) are NOT ported: both are in the unreachable set (see
 * discover_dead_code_audit.md). The playlist's Download and Sync now come from
 * the shared mix modal instead.
 */

export const SEASONAL_CURRENT_URL = '/api/discover/seasonal/current';

export function seasonalPlaylistUrl(seasonKey: string): string {
  return `/api/discover/seasonal/${seasonKey}/playlist`;
}

export const SEASONAL_ALBUMS_EMPTY = 'No seasonal albums found';
export const SEASONAL_ALBUMS_ERROR = 'Failed to load seasonal albums';
export const SEASONAL_PLAYLIST_LOADING = 'Loading playlist...';
export const SEASONAL_PLAYLIST_EMPTY = 'No tracks available yet';
export const SEASONAL_PLAYLIST_ERROR = 'Failed to load playlist';
export const SEASONAL_ALBUM_PLACEHOLDER = '/static/placeholder-album.png';

export interface SeasonData {
  success?: boolean;
  season?: string;
  name?: string;
  icon?: string;
  description?: string;
  albums?: SeasonalAlbum[];
  playlist_available?: boolean;
}

export interface SeasonalAlbum {
  album_name?: string;
  artist_name?: string;
  album_cover_url?: string;
  spotify_album_id?: string;
  source?: string;
  [key: string]: unknown;
}

/**
 * No active season → BOTH sections hide (4274-4277, 4381-4391).
 *
 * `success` alone is not enough; a response can succeed and carry no season,
 * which is the off-season state.
 */
export function seasonalIsActive(data: SeasonData | null | undefined): boolean {
  return Boolean(data?.success && data.season);
}

/** The playlist half only loads when the season advertises one (4285). */
export function seasonalHasPlaylist(data: SeasonData | null | undefined): boolean {
  return Boolean(data?.playlist_available);
}

/** `${icon} ${name}` / `description` (4329-4330). */
export function seasonalHeader(data: SeasonData): { title: string; subtitle: string } {
  return {
    title: `${data.icon ?? ''} ${data.name ?? ''}`,
    subtitle: data.description ?? '',
  };
}

/** The mix-card title and subtitle (4374-4375) — the subtitle LOWERCASES the name. */
export function seasonalMixTitles(data: SeasonData): { title: string; subtitle: string } {
  const name = data.name ?? '';
  return {
    title: `${data.icon ?? ''} ${name} Mix`,
    subtitle: `Curated playlist for ${name.toLowerCase()}`,
  };
}

export function seasonalAlbumCover(album: SeasonalAlbum): string {
  return album.album_cover_url || SEASONAL_ALBUM_PLACEHOLDER;
}

/**
 * The playlist controller is REBUILT when the season key changes (4354).
 *
 * Its `fetchUrl` bakes in the season, so a cached controller would keep asking
 * the old season's endpoint after a rollover.
 */
export function seasonalControllerIsStale(
  cachedKey: string | null,
  currentKey: string | null,
): boolean {
  return cachedKey !== currentKey;
}

/**
 * The albums section runs in no-fetch `data:` mode (4322).
 *
 * `loadSeasonalContent` already fetched the season payload, so the albums
 * controller is handed `{ success: true, albums }` directly rather than making
 * a second request for data it is holding.
 */
export function seasonalAlbumsData(data: SeasonData | null | undefined): {
  success: true;
  albums: SeasonalAlbum[];
} {
  return { success: true, albums: data?.albums ?? [] };
}

/**
 * Which source to ask for an album's tracks (4434).
 *
 * The heuristic: a `spotify_album_id` that is ALL DIGITS is not a Spotify id
 * (those are base62) — it is an iTunes id stored in the same column. So a
 * numeric value routes to itunes and anything else to spotify. An explicit
 * `source` on the row overrides the guess entirely.
 */
export function seasonalAlbumSource(album: SeasonalAlbum): string {
  if (album.source) return album.source;
  return album.spotify_album_id && !/^\d+$/.test(String(album.spotify_album_id))
    ? 'spotify'
    : 'itunes';
}

/** `seasonal_album_${albumId}` (4478). */
export function seasonalVirtualAlbumId(albumId: string): string {
  return `seasonal_album_${albumId}`;
}

export const SEASONAL_NO_ALBUM_ID = 'No album ID available';
export const SEASONAL_NO_TRACKS = 'No tracks found in album';
export const SEASONAL_ALBUM_NOT_FOUND = 'Album data not found';

// ── The shared artist context ───────────────────────────────────────────────

export interface DiscoverArtistContext {
  id: string;
  name: string;
  source: string;
  spotify_artist_id: string;
  itunes_artist_id: string;
  deezer_artist_id: string;
  discogs_artist_id: string;
  amazon_artist_id: string;
  soul_id: string;
  [key: string]: unknown;
}

/**
 * Build the artist context a download modal needs (4393-4420).
 *
 * Argument-pure, so this one IS differentially tested against the real vanilla.
 * Shared with the cache sections (10536, 10596), which is why it lives here
 * rather than inside either caller.
 *
 * Three things that look redundant and are not:
 *
 *   `normalizedSource || activeSource` appears repeatedly because the explicit
 *   `source` argument wins, but falls back to whatever the row itself claims
 *   (`active_source`, then `source`).
 *
 *   Each per-provider id has THREE fallbacks: the canonical field, an
 *   `artist_`-prefixed alias, and — only when that provider is the active one —
 *   the album's own artist id. Different endpoints spell these differently and
 *   the context has to satisfy all of them.
 *
 *   `context.id` is computed twice: once as a generic best-guess, then
 *   OVERWRITTEN by the active provider's own id when there is one (4418). The
 *   second pass is what makes the modal open on the right provider.
 */
export function buildDiscoverArtistContext(
  source: string,
  artistName: string,
  sourceData: Record<string, unknown> = {},
  albumData: Record<string, unknown> = {},
): DiscoverArtistContext {
  const normalizedSource = String(source || '').toLowerCase();
  const artists = albumData.artists;
  const albumArtist = (Array.isArray(artists) ? artists[0] : null) as {
    id?: string;
    name?: string;
  } | null;
  // The cast is a no-op at runtime and keeps `String(...)` on exactly the value
  // the vanilla stringifies (4396). It only tells the linter that these fields
  // are expected to be strings — narrowing the expression instead would change
  // which value wins.
  const activeSource = String(
    (source || sourceData.active_source || sourceData.source || '') as string,
  ).toLowerCase();
  const active = normalizedSource || activeSource;

  const context: DiscoverArtistContext = {
    ...sourceData,
    id: (sourceData.active_source_id || sourceData.artist_id || albumArtist?.id || '') as string,
    name: (artistName ||
      sourceData.artist_name ||
      sourceData.name ||
      albumArtist?.name ||
      '') as string,
    source: normalizedSource || activeSource || '',
    spotify_artist_id: (sourceData.spotify_artist_id ||
      sourceData.artist_spotify_id ||
      (active === 'spotify' ? albumArtist?.id : '') ||
      '') as string,
    itunes_artist_id: (sourceData.itunes_artist_id ||
      sourceData.artist_itunes_id ||
      (active === 'itunes' ? albumArtist?.id : '') ||
      '') as string,
    deezer_artist_id: (sourceData.deezer_artist_id ||
      sourceData.artist_deezer_id ||
      sourceData.deezer_id ||
      (active === 'deezer' ? albumArtist?.id : '') ||
      '') as string,
    discogs_artist_id: (sourceData.discogs_artist_id ||
      sourceData.artist_discogs_id ||
      sourceData.discogs_id ||
      '') as string,
    amazon_artist_id: (sourceData.amazon_artist_id ||
      sourceData.artist_amazon_id ||
      sourceData.amazon_id ||
      '') as string,
    soul_id: (sourceData.soul_id || sourceData.hydrabase_artist_id || '') as string,
  };

  const sourceIdBySource: Record<string, string> = {
    spotify: context.spotify_artist_id,
    itunes: context.itunes_artist_id,
    deezer: context.deezer_artist_id,
    discogs: context.discogs_artist_id,
    amazon: context.amazon_artist_id,
    hydrabase: context.soul_id,
  };
  context.id = sourceIdBySource[active] || context.id;
  return context;
}
