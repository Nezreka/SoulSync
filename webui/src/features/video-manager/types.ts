/** the video library manager's rows (core/video/library_manager.py). */

export type Side = 'movies' | 'shows';
export type TitleKind = 'movie' | 'show';
export type ViewMode = 'posters' | 'table' | 'files';

export interface FileFacts {
  file_count: number;
  size_bytes: number;
  /** the best file's resolution: 4K | 1080p | 720p | SD | '' */
  resolution: string;
  resolution_rank: number;
  /** how many files sit at each resolution */
  resolution_mix: Record<string, number>;
  video_codecs: string[];
  audio_codecs: string[];
  dynamic_range: string[];
  release_sources: string[];
  audio_channels: number | null;
  atmos: boolean;
  audio_languages: string[];
  subtitle_languages: string[];
  extra_copies: number;
  unprobed_files: number;
  files_without_subtitles: number;
  path: string | null;
  drive: string | null;
}

export interface TitleRow extends FileFacts {
  id: number;
  kind: TitleKind;
  title: string;
  year: number | null;
  tmdb_id: number | null;
  matched: boolean;
  monitored: boolean;
  owned: boolean;
  watched: boolean;
  added_at: string | null;
  content_rating: string | null;
  runtime_minutes: number | null;
  rating: number | null;
  has_poster: boolean;
  quality_profile_id: number | null;
  video_library_id: number | null;
  locked_fields: string[];
  /** the padlock: reviewed, and automation leaves it and its files alone */
  locked: boolean;
  status: string | null;
  genres: string[];
  below_cutoff: boolean;
  cutoff: string;
  // shows
  seasons?: number;
  episodes_total?: number;
  episodes_owned?: number;
  episodes_missing?: number;
  network?: string | null;
  series_type?: string | null;
  // movies
  studio?: string | null;
}

export interface FileRow {
  id: number;
  kind: TitleKind;
  title_id: number;
  title: string;
  year: number | null;
  episode_id: number | null;
  season_number: number | null;
  episode_number: number | null;
  episode_title: string | null;
  path: string;
  filename: string;
  drive: string | null;
  size_bytes: number;
  resolution: string;
  resolution_rank: number;
  video_codec: string | null;
  audio_codec: string | null;
  release_source: string | null;
  quality: string | null;
  runtime_seconds: number | null;
  audio_channels: number | null;
  dynamic_range: string | null;
  atmos: boolean;
  audio_languages: string[];
  subtitle_languages: string[];
  probed: boolean;
  added_at: string | null;
}

export interface Pagination {
  page: number;
  limit: number;
  total_pages: number;
  total_count: number;
  has_prev: boolean;
  has_next: boolean;
}

export interface TitlesPage {
  items: TitleRow[];
  kind: Side;
  totals: { titles: number; size_bytes: number; files: number };
  pagination: Pagination;
}

export interface FilesPage {
  items: FileRow[];
  kind: Side;
  totals: { files: number; size_bytes: number };
  pagination: Pagination;
}

export interface Counted {
  value: string;
  count: number;
}

export type Need =
  | 'missing'
  | 'below_cutoff'
  | 'copies'
  | 'unmatched'
  | 'no_subtitles'
  | 'unprobed'
  | 'unmonitored'
  | 'unwatched'
  | 'not_owned';

export interface Facets {
  kind: Side;
  titles: number;
  size_bytes: number;
  files: number;
  owned: number;
  resolutions: Counted[];
  codecs: Counted[];
  audio_codecs: Counted[];
  release_sources: Counted[];
  hdr: Counted[];
  audio_languages: Counted[];
  subtitle_languages: Counted[];
  genres: Counted[];
  drives: (Counted & { size_bytes: number })[];
  needs: Record<Need, number>;
  quality_profiles: { id: number; name: string }[];
  libraries: { id: number; name: string }[];
}

/** the page's filters, as the query string carries them */
export interface Filters {
  search: string;
  needs: Need[];
  status: '' | 'owned' | 'not_owned';
  monitored: '' | '1' | '0';
  /** the padlock is on */
  locked: '' | '1' | '0';
  resolution: string;
  codec: string;
  hdr: '' | '1' | '0';
  audio_language: string;
  subtitle_language: string;
  drive: string;
  genre: string;
  video_library: string;
  quality_profile: string;
  year_from: string;
  year_to: string;
}

export const EMPTY_FILTERS: Filters = {
  search: '',
  needs: [],
  status: '',
  monitored: '',
  locked: '',
  resolution: '',
  codec: '',
  hdr: '',
  audio_language: '',
  subtitle_language: '',
  drive: '',
  genre: '',
  video_library: '',
  quality_profile: '',
  year_from: '',
  year_to: '',
};

export interface Sort {
  by: string;
  dir: 'asc' | 'desc';
}

export interface BulkStatus {
  running: boolean;
  phase?: string | null;
  done: number;
  total: number;
  ok: number;
  failed: number;
  pushed?: number;
  changed?: number;
  /** padlocked titles the run left alone */
  held?: number;
  /** the file changes of this run, for Undo */
  batch?: string | null;
  action?: string | null;
  label?: string | null;
  error?: string | null;
}

export type BulkAction =
  | 'content_rating'
  | 'genre_add'
  | 'genre_remove'
  | 'monitored'
  | 'watched'
  | 'refresh_art'
  | 'quality_profile'
  | 'rename'
  | 'wish'
  | 'subtitles'
  | 'rematch'
  | 'series_type'
  | 'delete'
  | 'lock'
  | 'move'
  | 'collection_add';

/** one change made to a file by hand (core/video/file_history.py) */
export interface FileChange {
  id: number;
  at: string;
  action: 'rename' | 'move' | 'delete' | 'reassign';
  kind: TitleKind | null;
  item_id: number | null;
  title: string | null;
  label: string | null;
  from_path: string | null;
  to_path: string | null;
  from_name: string;
  to_name: string;
  batch: string | null;
  undone: boolean;
}

/** what a finished run offers to put back */
export interface Undoable {
  batch: string;
  text: string;
}

/** a named set of filters, sort, view and columns */
export interface SavedView {
  name: string;
  state: {
    tab: Side;
    view: ViewMode;
    filters: Filters;
    sort: Sort;
    columns?: string[];
  };
}

export interface EpisodeFile {
  file_id: number;
  filename: string;
  size_bytes: number;
  resolution: string | null;
  quality: string | null;
}

export interface SeasonEpisodes {
  season: number;
  episodes: {
    id: number;
    episode: number;
    title: string | null;
    air_date: string | null;
    files: EpisodeFile[];
  }[];
}
