/**
 * Recent Releases — the last of the gaps the coverage audit found.
 *
 * Transcribed from `_renderRecentReleaseCard` (1284),
 * `loadDiscoverRecentReleases` (1303) and `openDownloadModalForRecentAlbum`
 * (11486) — read end to end.
 */

export const RECENT_RELEASES_URL = '/api/discover/recent-releases';
export const RECENT_RELEASES_LOADING = 'Loading recent releases...';
export const RECENT_RELEASES_EMPTY = 'No recent releases found';
export const RECENT_RELEASES_ERROR = 'Failed to load recent releases';
export const RECENT_ALBUM_PLACEHOLDER = '/static/placeholder-album.png';

export const RECENT_ALBUM_NOT_FOUND = 'Album data not found';
export const RECENT_NO_TRACKS = 'No tracks found in album';

export interface RecentAlbum {
  album_name?: string;
  artist_name?: string;
  album_cover_url?: string;
  source?: string;
  album_spotify_id?: string;
  album_deezer_id?: string;
  album_itunes_id?: string;
  /** Stamped server-side by the library's own fuzzy matcher. */
  in_library?: boolean;
  [key: string]: unknown;
}

export function recentAlbumCover(album: RecentAlbum): string {
  return album.album_cover_url || RECENT_ALBUM_PLACEHOLDER;
}

/**
 * Which source to ask, and which id to ask with (11498-11499).
 *
 * Note this is a DIFFERENT heuristic from the seasonal section's: seasonal
 * inspects whether `spotify_album_id` looks numeric, because it only has that
 * one column. Recent Releases has three separate id columns, so it simply picks
 * the first populated one — spotify, then deezer, then itunes — and an explicit
 * `source` on the row overrides the pick.
 *
 * The two steps are separate in the vanilla and can disagree: `source` decides
 * WHICH id field is read, so an explicit `source: 'deezer'` on a row that only
 * has a spotify id yields no id at all, and the caller throws
 * `No deezer album ID available`. Transcribed as-is.
 */
export function recentAlbumSource(album: RecentAlbum): string {
  if (album.source) return album.source;
  if (album.album_spotify_id) return 'spotify';
  if (album.album_deezer_id) return 'deezer';
  return 'itunes';
}

export function recentAlbumId(album: RecentAlbum, source: string): string | undefined {
  if (source === 'spotify') return album.album_spotify_id;
  if (source === 'deezer') return album.album_deezer_id;
  return album.album_itunes_id;
}

/** `No ${source} album ID available` (11502) — names the source that failed. */
export function recentNoIdMessage(source: string): string {
  return `No ${source} album ID available`;
}

/** `discover_album_${albumId}` (11543) — the prefix the download bar keys on. */
export function recentVirtualAlbumId(albumId: string): string {
  return `discover_album_${albumId}`;
}

export function recentAlbumFetchUrl(source: string, albumId: string, album: RecentAlbum): string {
  const params = new URLSearchParams({
    name: album.album_name || '',
    artist: album.artist_name || '',
  });
  return `/api/discover/album/${source}/${albumId}?${params}`;
}

/**
 * The album envelope is rebuilt from the FRESH response, not the card's row
 * (11530-11535).
 *
 * The vanilla flags this as a critical fix in four separate comments: the cached
 * row carries stale art and no album_type, and the download modal classifies on
 * album_type. Rebuilding from `albumData` is what makes an album download behave
 * like an album rather than a loose track set.
 */
export function recentTrackAlbum(albumData: {
  id?: string;
  name?: string;
  album_type?: string;
  total_tracks?: number;
  release_date?: string;
  images?: unknown[];
}) {
  return {
    id: albumData.id,
    name: albumData.name,
    album_type: albumData.album_type || 'album',
    total_tracks: albumData.total_tracks || 0,
    release_date: albumData.release_date || '',
    images: albumData.images || [],
  };
}

/**
 * Artist names for one track (11520-11523).
 *
 * Track artists, then the album's, then the card row's — and each entry is
 * unwrapped with `a.name || a`, so a plain string array survives.
 */
export function recentTrackArtists(
  track: { artists?: unknown },
  albumData: { artists?: unknown },
  album: RecentAlbum,
): unknown {
  let artists: unknown = track.artists || albumData.artists || [{ name: album.artist_name }];
  if (Array.isArray(artists)) {
    artists = artists.map((a: unknown) => (a as { name?: string })?.name || a);
  }
  return artists;
}
