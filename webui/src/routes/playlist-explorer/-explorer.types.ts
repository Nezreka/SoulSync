/**
 * Playlist Explorer — the shapes the four endpoints actually return
 * (pages-extra.js:1-1134).
 *
 * Fields are optional because the vanilla read them all through `|| 0` / `|| ''`
 * fallbacks. In practice the endpoint always sets the four status counters
 * (total/discovered/wishlisted/in_library, defaulting to 0), while the rest
 * come straight off the mirrored_playlists row — `explored_at` is a nullable
 * migration-added column, and a bare `explored` flag does not exist in the
 * backend at all, so that half of the vanilla's check is dead.
 */

/** GET /api/mirrored-playlists — one picker card. */
export interface MirroredPlaylist {
  id: number;
  name?: string | null;
  source?: string | null;
  image_url?: string | null;
  /** The vanilla preferred total_count and fell back to track_count (explorerRenderPickerCards :109). */
  total_count?: number | null;
  track_count?: number | null;
  discovered_count?: number | null;
  wishlisted_count?: number | null;
  in_library_count?: number | null;
  /** Set once the tree has been built (explorerRenderPickerCards :115). `explored`
   *  has no backend field; the vanilla checked it anyway, so the type keeps it. */
  explored_at?: string | null;
  explored?: boolean | null;
}

/** POST /api/playlist-explorer/build-tree — 'albums' | 'discographies' (explorerSetMode :240). */
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

/** The NDJSON `artist` line: one branch. `error` renders a dead node (_explorerRenderArtistNode :429). */
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

/** One artist's selected albums, as the wishlist modal groups them (explorerAddToWishlist :649). */
export interface ExplorerArtistSection {
  artistId: string | null;
  name: string;
  image: string | null;
  albums: ExplorerAlbum[];
}
