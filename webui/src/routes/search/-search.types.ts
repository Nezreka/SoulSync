/**
 * Enhanced (metadata) search shapes.
 *
 * Field names are the vanilla's — `spotify_artists` / `spotify_albums` /
 * `spotify_tracks` are historical and carry results from EVERY source, not just
 * Spotify. Renaming them would mean renaming the API contract, so they stay.
 */

/** Metadata source ids, in the picker's canonical display order. */
export const SOURCE_ORDER = [
  'spotify',
  'itunes',
  'deezer',
  'discogs',
  'hydrabase',
  'amazon',
  'musicbrainz',
  'jiosaavn',
  'bandcamp',
  'youtube_videos',
  'soulseek',
] as const;

export type SearchSource = (typeof SOURCE_ORDER)[number];

/**
 * Opt-in sources, hidden from the picker unless enabled in
 * Settings → Advanced → Experimental. The backend reports their state in the
 * `_experimental` payload.
 */
export const EXPERIMENTAL_SOURCES: ReadonlySet<string> = new Set(['jiosaavn', 'bandcamp']);

/**
 * Sources config-status does not cover because they need no credentials — they
 * always render as configured.
 *
 * Soulseek is deliberately NOT here: it needs an slskd URL, so the picker dims
 * it when none is set up and sends clicks to Settings instead.
 */
export const ALWAYS_CONFIGURED_SOURCES: ReadonlySet<string> = new Set([
  'amazon',
  'musicbrainz',
  'jiosaavn',
  'bandcamp',
  'youtube_videos',
]);

export interface SourceLabel {
  text: string;
  icon: string;
  logo?: string;
  tabClass: string;
  badgeClass: string;
}

/** Mirrors SOURCE_LABELS in shared-helpers.js; pinned by a differential test. */
export const SOURCE_LABELS: Record<string, SourceLabel> = {
  spotify: {
    text: 'Spotify',
    icon: '🎵',
    logo: '/static/img/brands/spotify.png',
    tabClass: 'enh-tab-spotify',
    badgeClass: 'enh-badge-spotify',
  },
  spotify_free: {
    text: 'Spotify (no auth)',
    icon: '🎵',
    logo: '/static/img/brands/spotify.png',
    tabClass: 'enh-tab-spotify',
    badgeClass: 'enh-badge-spotify',
  },
  itunes: {
    text: 'Apple Music',
    icon: '🍎',
    logo: '/static/img/brands/itunes.png',
    tabClass: 'enh-tab-itunes',
    badgeClass: 'enh-badge-itunes',
  },
  deezer: {
    text: 'Deezer',
    icon: '🎶',
    logo: '/static/img/brands/deezer.png',
    tabClass: 'enh-tab-deezer',
    badgeClass: 'enh-badge-deezer',
  },
  discogs: {
    text: 'Discogs',
    icon: '📀',
    logo: '/static/img/brands/discogs-icon.png',
    tabClass: 'enh-tab-discogs',
    badgeClass: 'enh-badge-discogs',
  },
  hydrabase: {
    text: 'Hydrabase',
    icon: '💎',
    logo: '/static/hydrabase.png',
    tabClass: 'enh-tab-hydrabase',
    badgeClass: 'enh-badge-hydrabase',
  },
  amazon: {
    text: 'Amazon Music',
    icon: '🛒',
    tabClass: 'enh-tab-amazon',
    badgeClass: 'enh-badge-amazon',
  },
  musicbrainz: {
    text: 'MusicBrainz',
    icon: '🧠',
    logo: '/static/img/brands/musicbrainz.png',
    tabClass: 'enh-tab-musicbrainz',
    badgeClass: 'enh-badge-musicbrainz',
  },
  jiosaavn: {
    text: 'JioSaavn',
    icon: '🎵',
    tabClass: 'enh-tab-jiosaavn',
    badgeClass: 'enh-badge-jiosaavn',
  },
  bandcamp: {
    text: 'Bandcamp',
    icon: '🎵',
    logo: '/static/img/brands/bandcamp.svg',
    tabClass: 'enh-tab-bandcamp',
    badgeClass: 'enh-badge-bandcamp',
  },
  youtube_videos: {
    text: 'Music Videos',
    icon: '🎬',
    tabClass: 'enh-tab-youtube',
    badgeClass: 'enh-badge-youtube',
  },
  soulseek: {
    // Routes through /api/search (raw slskd file results) — called "Basic
    // Search" in the UI since before the source picker existed.
    text: 'Basic Search',
    icon: '🎼',
    tabClass: 'enh-tab-soulseek',
    badgeClass: 'enh-badge-soulseek',
  },
};

export interface SearchArtist {
  id?: string | number;
  name?: string;
  image_url?: string;
  images?: { url?: string }[];
  source?: string;
  followers?: number;
  /**
   * `lib2_artists.id`. The "In Your Library" bucket is Library v2's catalogue
   * (`_build_db_artists`, core/search/orchestrator.py), so every entry of that
   * bucket carries one and its `id` is the same value. Absent on provider
   * results, which have no library row.
   */
  library_v2_id?: number | null;
  /**
   * iss29-B04c: this row's `id` is a LIB2 id and its artwork is already served
   * by Library V2. The generic
   * `/api/artist/<id>/image` resolver forwards whatever id it gets to the
   * providers, so asking it about a lib2 id returned whichever Deezer/iTunes
   * artist owned that number. Cards must not lazily resolve these.
   */
  image_is_native?: boolean;
}

export interface SearchAlbum {
  id?: string | number;
  name?: string;
  artist?: string;
  artists?: { id?: string | number; name?: string }[];
  album_type?: string;
  image_url?: string;
  images?: { url?: string }[];
  release_date?: string;
  total_tracks?: number;
  source?: string;
  release_group_id?: string;
  /**
   * Per-source escape hatches, not just links:
   *   - `hydrabase_plugin` names the plugin an album came from, and the server
   *     needs it to route the detail fetch to the right client.
   *   - `bandcamp` is the release URL; Bandcamp has no id-lookup API, so it is
   *     the only way to fetch that exact release instead of re-searching.
   */
  external_urls?: Record<string, string | undefined>;
}

export interface SearchTrack {
  id?: string | number;
  name?: string;
  /** The joined display string ("A, B"); `artists` carries the real list. */
  artist?: string;
  artists?: string[];
  /**
   * The album NAME, not an album object.
   *
   * core/search/sources.py:105 copies `track.album` straight from the Track
   * dataclass, where it is a plain string (core/spotify_client.py:438). Typing
   * it as an object silently loses the album from every track's meta line.
   */
  album?: string;
  duration_ms?: number;
  image_url?: string;
  release_date?: string;
  source?: string;
  popularity?: number;
  preview_url?: string | null;
  external_urls?: Record<string, string | undefined> | null;
}

export interface SearchLabel {
  id?: string;
  name?: string;
  type?: string;
  area?: string;
}

export interface SearchVideo {
  video_id?: string;
  title?: string;
  channel?: string;
  thumbnail?: string;
  /** SECONDS, not milliseconds — see formatVideoDuration. */
  duration?: number;
  view_count?: number;
  /**
   * The watch URL. Not decoration: the download POST sends
   * {video_id, url, title, channel} (downloads.js:5463), so dropping this field
   * from the type means the request goes out with `url: undefined`.
   */
  url?: string;
  upload_date?: string;
}

/** One source's slice of results, as cached per (query, source). */
export interface SourceResults {
  db_artists: SearchArtist[];
  artists: SearchArtist[];
  albums: SearchAlbum[];
  tracks: SearchTrack[];
  videos: SearchVideo[];
}

export interface EnhancedSearchResponse {
  db_artists?: SearchArtist[];
  spotify_artists?: SearchArtist[];
  spotify_albums?: SearchAlbum[];
  spotify_tracks?: SearchTrack[];
  /** What the server ACTUALLY served — differs from the request on fallback. */
  primary_source?: string;
  metadata_source?: string;
  source_available?: boolean;
}

/** The per-row answer from /api/enhanced-search/library-check. */
export interface LibraryCheckTrack {
  in_library?: boolean;
  in_wishlist?: boolean;
  track_id?: string | number;
  title?: string;
  file_path?: string;
  album_title?: string;
  artist_name?: string;
  album_thumb_url?: string;
}

export interface LibraryCheckResponse {
  albums?: boolean[];
  tracks?: LibraryCheckTrack[];
}
