/**
 * The video wishlist's server shapes, traced from api/video/wishlist.py
 * (query_wishlist, _annotate_live_state, _annotate_trouble) and
 * api/video/youtube.py (query_youtube_wishlist). Not guessed: every field here
 * is one those builders write.
 */

/** The three video tabs. Their URL values sit beside music and audiobooks. */
export type VideoMedia = 'movies' | 'tv' | 'youtube';

export const VIDEO_MEDIA: readonly VideoMedia[] = ['movies', 'tv', 'youtube'];

export function isVideoMedia(value: unknown): value is VideoMedia {
  return value === 'movies' || value === 'tv' || value === 'youtube';
}

/** The kind the list endpoint and the clear endpoint take for each tab. */
export const MEDIA_KIND: Record<VideoMedia, 'movie' | 'show' | 'youtube'> = {
  movies: 'movie',
  tv: 'show',
  youtube: 'youtube',
};

/** What one source did on the row's last search (core: source_outcome). */
export interface VideoSourceOutcome {
  ran?: boolean;
  results?: number;
  accepted?: number;
  rejected?: number;
  reason?: string | null;
  samples?: {
    title?: string;
    accepted?: boolean;
    rejected?: string | null;
    quality?: string | null;
    source?: string | null;
    indexer?: string | null;
    seeders?: number | null;
  }[];
}

export interface VideoSearchSnapshot {
  chain?: string[];
  sources?: Record<string, VideoSourceOutcome>;
}

/** core.video.wishlist_evidence.row_trouble. null = the receipt can't say. */
export type VideoRowTrouble = 'unsearchable' | 'notfound' | 'awaiting' | 'refused' | null;

/** Fields every acquirable row carries: a movie, an episode, a YouTube video. */
export interface VideoWishRowBase {
  status?: string | null;
  /** an active download row exists for it (live state). */
  downloading?: boolean;
  /** owned below the profile's cutoff: the upgrade watch. */
  upgrade_from?: string | null;
  muted?: boolean;
  search_attempts?: number;
  last_search_at?: string | null;
  last_refusal?: string | null;
  last_refusal_quality?: string | null;
  search_snapshot?: VideoSearchSnapshot | null;
  trouble?: VideoRowTrouble;
}

export interface VideoMovieItem extends VideoWishRowBase {
  kind?: 'movie';
  tmdb_id: number;
  title: string;
  poster_url?: string | null;
  year?: number | string | null;
  library_id?: number | null;
}

export interface VideoEpisode extends VideoWishRowBase {
  episode_number: number;
  title?: string | null;
  still_url?: string | null;
  overview?: string | null;
  air_date?: string | null;
  /** YouTube only: the video id. */
  source_id?: string;
  /** YouTube only: deleted, private or members-only. */
  unavailable?: boolean;
}

export interface VideoSeason {
  season_number: number;
  /** YouTube only: the upload year (also season_number). */
  year?: number;
  poster_url?: string | null;
  episodes: VideoEpisode[];
}

/** A TV show, or a YouTube channel shaped like one (year = season, video = episode). */
export interface VideoShowItem {
  kind?: 'show' | 'channel';
  source?: 'youtube';
  tmdb_id: number;
  /** YouTube only: the channel id. */
  youtube_id?: string;
  title: string;
  poster_url?: string | null;
  library_id?: number | null;
  year?: number | string | null;
  wanted: number;
  done: number;
  muted?: boolean;
  downloading_count?: number;
  upgrade_count?: number;
  seasons: VideoSeason[];
}

export interface VideoPagination {
  page: number;
  total_pages: number;
  total_count: number;
  has_prev: boolean;
  has_next: boolean;
}

export interface VideoWishlistCounts {
  movie?: number;
  show?: number;
  episode?: number;
  total?: number;
  channel?: number;
  video?: number;
}

export interface VideoWishlistResponse<T> {
  success?: boolean;
  counts?: VideoWishlistCounts;
  items?: T[];
  pagination?: VideoPagination;
  error?: string;
}

export type VideoSort = 'added' | 'oldest' | 'wanted' | 'title';

export const VIDEO_SORTS: { key: VideoSort; label: string }[] = [
  { key: 'added', label: 'Recently added' },
  { key: 'oldest', label: 'Oldest first' },
  { key: 'wanted', label: 'Most wanted' },
  { key: 'title', label: 'A–Z' },
];

/** The diagnostics drawer payload (GET /wishlist/diagnostics). */
export interface VideoDiagnostics {
  success?: boolean;
  row?: VideoWishRowBase & { search_attempts?: number };
  ids?: { tmdb_id?: number | null; tvdb_id?: number | null; imdb_id?: string | null };
  target_dir?: string | null;
  downloads?: { status?: string; progress?: number; release_title?: string }[];
}
