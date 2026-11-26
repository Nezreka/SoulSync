/** shapes the player reads. only the fields it uses are typed. */

// ── the player's own api ───────────────────────────────────────────────

export interface ProgressMeta {
  title?: string;
  subtitle?: string;
  artwork?: string;
  backdrop?: string;
  href?: string;
  parent_id?: string;
  parent_title?: string;
}

export interface ProgressRow {
  item_type: string;
  item_id: string;
  position_seconds: number;
  duration_seconds: number | null;
  finished: boolean;
  meta: ProgressMeta | null;
  updated_at: string | null;
}

export interface CatalogMovie {
  id: number;
  title: string;
  year: number | null;
  runtime_seconds: number | null;
  content_rating: string | null;
  rating: number | null;
  genres: string[];
  added_at: string | null;
  has_poster: boolean;
  has_backdrop: boolean;
  studio: string | null;
  tmdb_id: number | null;
  /** the franchise tmdb files it under (a box set) */
  collection_id?: number | null;
  collection?: string | null;
  /** the library its files sit in (a title in no library folder is in the main one) */
  library_id?: number | null;
}

/** [episode id, season, episode, seconds, title] */
export type CatalogEpisode = [number, number, number, number | null, string];

export interface CatalogShow {
  id: number;
  title: string;
  year: number | null;
  network: string | null;
  content_rating: string | null;
  rating: number | null;
  genres: string[];
  added_at: string | null;
  has_poster: boolean;
  has_backdrop: boolean;
  /** every owned episode */
  episode_count: number;
  /** a sample of the owned episodes, spread across the run, in airing order */
  episodes: CatalogEpisode[];
  tmdb_id: number | null;
  library_id?: number | null;
}

export interface CatalogRecentEpisode {
  id: number;
  show_id: number;
  show_title: string;
  season_number: number;
  episode_number: number;
  title: string | null;
  has_still: boolean;
  added_at: string | null;
  /** its length, for the guide's premiere slot */
  runtime_seconds?: number | null;
}

export interface CatalogYoutube {
  /** the youtube video id */
  id: string;
  youtube_id: string;
  title: string;
  duration_seconds: number | null;
  published_at: string | null;
  thumbnail_url: string | null;
  channel_id: string | null;
  channel_title: string | null;
  added_at: string | null;
}

/**
 * a video library as the player lists it (core/player/libraries.py). one
 * with own_page has its own entry in the sidebar and its own page; the rest
 * are part of Movies / TV. a library hidden from this profile is never sent.
 */
export interface PlayerLibrary {
  id: number;
  /** home: clips from a camera or a phone (core/video/home_videos.py) */
  kind: 'movie' | 'show' | 'home';
  name: string;
  own_page: boolean;
  main: boolean;
  /** whether the guide schedules its titles (home libraries start off) */
  in_guide?: boolean;
}

/** a home video the guide may schedule (only home libraries switched into the guide) */
export interface CatalogHome {
  id: number;
  title: string;
  duration_s: number | null;
  thumb_url: string | null;
  album: string;
  filmed_at: string | null;
}

export interface VideoCatalog {
  /** the libraries this profile can see (none sent by an older server) */
  libraries?: PlayerLibrary[];
  /** home videos in the guide's pool (guide=1 only) */
  home?: CatalogHome[];
  movies: CatalogMovie[];
  shows: CatalogShow[];
  recent_episodes: CatalogRecentEpisode[];
  youtube: CatalogYoutube[];
  kinds: { movies: boolean; shows: boolean; youtube: boolean };
}

// ── music (library v2) ─────────────────────────────────────────────────

export interface Pagination {
  page: number;
  total_pages: number;
  total_count: number;
  has_next: boolean;
}

export interface LibArtist {
  id: number;
  name: string;
  image_url: string | null;
  remote_image_url?: string | null;
  genres?: string[];
  album_count?: number;
  track_count?: number;
  added_at?: string | null;
}

export interface LibAlbum {
  id: number;
  title: string;
  album_type?: string | null;
  year?: number | null;
  image_url: string | null;
  remote_image_url?: string | null;
  added_at?: string | null;
  artist_id?: number | null;
  artist_name?: string | null;
  track_count?: number;
  tracks_present?: number;
}

export interface LibRelease {
  id: number;
  title: string;
  album_type?: string | null;
  year?: number | null;
  image_url: string | null;
  remote_image_url?: string | null;
  /** 'library' or 'discography' (a provider-only release) */
  origin?: string;
  tracks_present?: number;
  track_count?: number;
}

export interface LibArtistDetail {
  id: number;
  name: string;
  image_url: string | null;
  remote_image_url?: string | null;
  summary?: string | null;
  /** the resolved bio and tags with their sources (core/library2/artist_about) */
  about?: { bio?: string | null; bio_source?: string | null; tags?: string[] } | null;
  style?: string | null;
  mood?: string | null;
  label?: string | null;
  genres?: string[];
  album_count?: number;
  single_count?: number;
  albums?: LibRelease[];
  eps?: LibRelease[];
  singles?: LibRelease[];
  appears_on?: LibRelease[];
}

export interface LibTrackFile {
  file_id?: number;
  format?: string | null;
  bitrate?: number | null;
  sample_rate?: number | null;
  bit_depth?: number | null;
  quality_tier?: string | null;
}

export interface LibTrack {
  id: number | null;
  title: string;
  track_number?: number | null;
  disc_number?: number | null;
  /** milliseconds */
  duration?: number | null;
  explicit?: boolean;
  is_missing?: boolean;
  file_status?: string | null;
  file_count?: number;
  server_track_id?: string | number | null;
  artists?: { id?: number; name: string }[] | string[];
  file?: LibTrackFile | null;
}

export interface LibAlbumDetail {
  id: number;
  title: string;
  album_type?: string | null;
  year?: number | null;
  release_date?: string | null;
  image_url: string | null;
  genres?: string[];
  label?: string | null;
  primary_artist?: { id: number; name: string } | null;
  tracks: LibTrack[];
  track_count?: number;
  tracks_present?: number;
}

export interface LibSong {
  id: number;
  title: string;
  artist_name?: string | null;
  album_title?: string | null;
  album_id?: number | null;
  duration?: number | null;
  album_thumb_url?: string | null;
}

// ── video detail ───────────────────────────────────────────────────────

export interface CastMember {
  name: string;
  character?: string | null;
  photo?: string | null;
  tmdb_id?: number | null;
}

export interface PersonCredit {
  kind: 'movie' | 'show';
  tmdb_id: number;
  title: string;
  year?: number | string | null;
  role?: string | null;
  department?: string | null;
  poster?: string | null;
  /** the library title when you own it */
  library_id?: number | null;
}

export interface Person {
  name: string;
  biography?: string | null;
  birthday?: string | null;
  deathday?: string | null;
  place_of_birth?: string | null;
  known_for?: string | null;
  photo?: string | null;
  /** tmdb profile images, a small and a full copy of each */
  photos?: { thumb: string; full: string }[] | null;
  also_known_as?: string[] | null;
  credits?: PersonCredit[];
}

export interface VideoDetailBase {
  id: number;
  title: string;
  year?: number | null;
  overview?: string | null;
  tagline?: string | null;
  content_rating?: string | null;
  rating?: number | null;
  imdb_rating?: number | null;
  rt_rating?: number | null;
  metacritic?: number | null;
  trakt_rating?: number | null;
  awards?: string | null;
  status?: string | null;
  genres?: string[];
  runtime_minutes?: number | null;
  logo?: string | null;
  has_poster?: boolean;
  has_backdrop?: boolean;
  cast?: CastMember[];
  crew?: { name: string; job: string; tmdb_id?: number | null; photo?: string | null }[];
  studio?: string | null;
  subtitle_langs?: string[] | string | null;
  watched?: boolean | null;
  play_count?: number | null;
  imdb_id?: string | null;
  /** a scene during or after the credits */
  mediastinger?: string | boolean | null;
}

export interface MovieDetail extends VideoDetailBase {
  kind: 'movie';
  release_date?: string | null;
  digital_release_date?: string | null;
}

export interface DetailEpisode {
  id: number;
  episode_number: number;
  title?: string | null;
  overview?: string | null;
  air_date?: string | null;
  runtime_minutes?: number | null;
  has_still?: boolean;
  owned?: boolean;
  rating?: number | null;
  watched?: boolean | null;
  resolution?: string | null;
  versions?: number | null;
}

export interface DetailSeason {
  id: number;
  season_number: number;
  title?: string | null;
  has_poster?: boolean;
  episode_total?: number;
  episode_owned?: number;
  episodes: DetailEpisode[];
}

export interface ShowDetail extends VideoDetailBase {
  kind: 'show';
  network?: string | null;
  seasons?: DetailSeason[];
  season_count?: number;
  first_air_date?: string | null;
  last_air_date?: string | null;
  episode_total?: number | null;
  episode_owned?: number | null;
  watched_episodes?: number | null;
  next_up?: {
    season_number: number;
    episode_number: number;
    title?: string | null;
    resume?: boolean;
    view_offset_ms?: number | null;
    runtime_minutes?: number | null;
  } | null;
}

export type PlayVerdict = 'yes' | 'maybe' | 'no';

export interface VideoFile {
  id: number;
  resolution?: string | null;
  resolution_label?: string | null;
  quality?: string | null;
  video_codec?: string | null;
  audio_codec?: string | null;
  audio_channels?: number | null;
  audio_languages?: string | null;
  subtitle_languages?: string | null;
  dynamic_range?: string | null;
  atmos?: boolean | number | null;
  size_bytes?: number | null;
  runtime_seconds?: number | null;
  release_source?: string | null;
  filename?: string | null;
  playable?: { verdict: PlayVerdict; reasons?: string[] } | null;
}

export interface MovieLibraryPayload {
  files?: VideoFile[];
}

export interface ShowLibraryPayload {
  seasons?: {
    season_number: number;
    episodes: { id: number; episode_number: number; files?: VideoFile[] }[];
  }[];
}

// ── podcasts + audiobooks ──────────────────────────────────────────────

export interface PodcastSub {
  id: number;
  feed_url: string;
  title: string;
  author?: string | null;
  artwork_url?: string | null;
  episode_count?: number | null;
  description?: string | null;
}

export interface PodcastEpisode {
  guid?: string | null;
  title: string;
  enclosure_url?: string | null;
  pub_date?: string | null;
  duration_seconds?: number | null;
  description?: string | null;
  artwork_url?: string | null;
  season?: number | null;
  episode_number?: number | null;
  downloaded?: boolean;
}

export interface PodcastShow {
  title: string;
  author?: string | null;
  description?: string | null;
  artwork_url?: string | null;
  feed_url?: string | null;
  categories?: string[];
  episodes: PodcastEpisode[];
}

export interface OwnedBook {
  asin: string;
  title: string;
  author?: string | null;
  narrator?: string | null;
  series_title?: string | null;
  series_sequence?: string | number | null;
  runtime_minutes?: number | null;
  imported_at?: string | null;
  catalog_asin?: string | null;
  cover_url?: string | null;
}

export interface CatalogBook {
  asin: string;
  title: string;
  subtitle?: string | null;
  summary?: string | null;
  runtime_minutes?: number | null;
  cover_url?: string | null;
  cover_url_large?: string | null;
  authors?: ({ name: string } | string)[];
  narrators?: ({ name: string } | string)[];
  genres?: ({ name: string } | string)[];
  release_date?: string | null;
}

// ── profiles ───────────────────────────────────────────────────────────

export interface PlayerProfile {
  id: number;
  name: string;
  avatar_color?: string | null;
  avatar_url?: string | null;
  is_admin?: boolean;
  has_pin?: boolean;
  has_password?: boolean;
  disabled?: boolean;
}

// ── youtube ────────────────────────────────────────────────────────────

export interface YtChannel {
  id: string;
  title: string;
  handle?: string | null;
  avatar?: string | null;
  banner?: string | null;
  subscribers?: number | null;
  followed: boolean;
  video_count?: number;
  downloaded_count: number;
  latest_at?: string | null;
  description?: string | null;
}

export interface YtVideo {
  id: string;
  title: string;
  channel_id: string | null;
  channel_title: string | null;
  thumbnail: string;
  duration_seconds: number | null;
  published_at: string | null;
  views: number | null;
  downloaded: boolean;
  downloaded_at?: string | null;
}

// ── tmdb extras (the video side's own: trailers, gallery, collection…) ─

export interface ExtraTitle {
  title: string;
  tmdb_id: number;
  kind: 'movie' | 'show';
  poster?: string | null;
  year?: string | number | null;
  /** the library title, when you own it */
  library_id?: number | null;
}

export interface Extras {
  trailer?: { key: string; name?: string | null } | null;
  videos?: { key: string; name?: string | null; type?: string | null }[];
  gallery?: {
    backdrops?: { thumb: string; full: string }[];
    posters?: { thumb: string; full: string }[];
  };
  keywords?: string[];
  recommendations?: ExtraTitle[];
  similar?: ExtraTitle[];
  collection?: {
    id: number;
    name?: string | null;
    poster?: string | null;
    items?: ExtraTitle[];
  } | null;
  facts?: { budget?: number; revenue?: number; original_language?: string; countries?: string[] };
  next_episode?: {
    season_number?: number | null;
    episode_number?: number | null;
    name?: string | null;
    air_date?: string | null;
  } | null;
}
