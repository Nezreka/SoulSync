/**
 * Playlist Explorer — the shapes the four endpoints actually return
 * (pages-extra.js:1-1141).
 *
 * Every numeric field is optional because the vanilla read them all through
 * `|| 0` / `|| ''` fallbacks: the mirrored-playlist rows come from several
 * source tables and not one of them fills the whole set.
 */

/** GET /api/mirrored-playlists — one picker card. */
export interface MirroredPlaylist {
  id: number;
  name?: string | null;
  source?: string | null;
  image_url?: string | null;
  /** The vanilla preferred total_count and fell back to track_count (:104). */
  total_count?: number | null;
  track_count?: number | null;
  discovered_count?: number | null;
  wishlisted_count?: number | null;
  in_library_count?: number | null;
  /** Set once the tree has been built; either flag counts as explored (:110). */
  explored_at?: string | null;
  explored?: boolean | null;
}

/** POST /api/playlist-explorer/build-tree — 'albums' | 'discographies' (:241). */
export type ExplorerMode = 'albums' | 'discographies';

/** The NDJSON `meta` line: the root node. */
export interface ExplorerMeta {
  type: 'meta';
  playlist_name?: string | null;
  playlist_image?: string | null;
  total_tracks?: number | null;
  total_artists?: number | null;
}

export interface ExplorerAlbum {
  spotify_id?: string | null;
  title?: string | null;
  year?: string | number | null;
  album_type?: string | null;
  track_count?: number | null;
  image_url?: string | null;
  owned?: boolean | null;
  in_playlist?: boolean | null;
}

/** The NDJSON `artist` line: one branch. `error` renders a dead node (:428). */
export interface ExplorerArtist {
  type?: 'artist';
  name?: string | null;
  image_url?: string | null;
  artist_id?: string | null;
  spotify_id?: string | null;
  error?: string | boolean | null;
  albums?: ExplorerAlbum[] | null;
}

/** GET /api/playlist-explorer/album-tracks/<id> */
export interface ExplorerTrack {
  track_number?: number | null;
  name?: string | null;
  duration_ms?: number | null;
}

/** One artist's selected albums, as the wishlist modal groups them (:625). */
export interface ExplorerArtistSection {
  artistId: string | null;
  name: string;
  image: string | null;
  albums: ExplorerAlbum[];
}
