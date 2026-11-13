/**
 * the "your library" tab's payload: GET /api/video/detail/<kind>/<id>/library
 * (core/video/library_manage.py). paths are null for a profile that isn't an
 * admin; the facts are not.
 */

export type TitleKind = 'movie' | 'show';

export interface PlayVerdict {
  verdict: 'yes' | 'maybe' | 'no';
  reasons: string[];
}

export interface SubtitleTrack {
  language: string;
  hi: boolean;
  forced: boolean;
}

export interface LibraryFile {
  id: number;
  path: string | null;
  filename: string;
  root: string | null;
  size_bytes: number;
  resolution: string | null;
  resolution_label: string;
  quality: string | null;
  video_codec: string | null;
  audio_codec: string | null;
  audio_channels: number | null;
  dynamic_range: string | null;
  atmos: boolean;
  /** the file's audio languages ('ja', 'en'); null until soulsync has read them */
  audio_languages?: string[] | null;
  /** its embedded subtitle tracks' languages, same shape */
  subtitle_languages?: string[] | null;
  release_source: string | null;
  runtime_seconds: number | null;
  added_at: string | null;
  aspect: string | null;
  playable: PlayVerdict;
  /** only present once the disk was asked (a movie, or the open season). */
  reachable?: boolean;
  local_path?: string | null;
  /** null when soulsync can't see the folder; [] when it can and there are none. */
  subtitles?: SubtitleTrack[] | null;
  naming?: { expected?: string; expected_name?: string; conforms: boolean } | null;
  best?: boolean;
  below_cutoff?: boolean;
  /** the download that landed this file; absent for one soulsync didn't fetch */
  source?: FileSource;
}

export interface FileSource {
  release: string | null;
  via: string | null;
  indexer: string | null;
  at: string | null;
}

/** copies beyond the best one of each movie or episode */
export interface ExtraCopies {
  titles: number;
  files: number;
  size_bytes: number;
}

export interface ShowFolder {
  path: string | null;
  files: number;
  size_bytes?: number;
  /** the drive or share the folder is on */
  root?: string | null;
  name?: string;
  /** the fullest folder: where the rest would be brought */
  main?: boolean;
}

export interface Identity {
  tmdb_id: number | null;
  imdb_id: string | null;
  tvdb_id: number | null;
  match_status: string | null;
  /** the user fixed the match: ids are locked against scans. */
  fixed: boolean;
  manual_link: number | null;
  server: string | null;
}

export interface QualityBlock {
  profile_id: number | null;
  profile_name: string;
  cutoff_label: string;
  meets?: boolean;
  reasons?: { ok: boolean; text: string }[];
  below_cutoff?: number;
}

export interface MovieLibrary {
  kind: 'movie';
  id: number;
  title: string;
  year: number | null;
  identity: Identity;
  summary: {
    files: number;
    size_bytes: number;
    watched: boolean;
    play_count: number | null;
    last_viewed_at: string | null;
    monitored: boolean;
    added_at: string | null;
  };
  quality: QualityBlock;
  copies: { count: number; roots: string[]; spans_drives: boolean; same_resolution: boolean };
  extra_copies?: ExtraCopies;
  files: LibraryFile[];
  recycle: boolean;
  can_manage: boolean;
}

export interface LibraryEpisode {
  id: number;
  episode_number: number;
  title: string | null;
  air_date: string | null;
  has_file: boolean;
  monitored: boolean;
  watched: boolean;
  aired: boolean;
  has_still?: boolean;
  runtime_minutes?: number | null;
  files: LibraryFile[];
}

export interface LibrarySeason {
  /** the season's own row, for its poster; absent on a show with no season rows */
  id?: number | null;
  has_poster?: boolean;
  extra_copies?: ExtraCopies;
  season_number: number;
  title: string;
  owned: number;
  total: number;
  missing: number;
  upcoming: number;
  size_bytes: number;
  monitored: boolean;
  below_cutoff: number;
  res_mix: { label: string; count: number }[];
  episodes: LibraryEpisode[];
}

export interface ShowLibrary {
  kind: 'show';
  id: number;
  title: string;
  year: number | null;
  identity: Identity;
  summary: {
    owned: number;
    total: number;
    missing: number;
    upcoming: number;
    specials_owned: number;
    files: number;
    size_bytes: number;
    res_mix: { label: string; count: number }[];
    roots: string[];
    folders: ShowFolder[];
    seasons?: number;
    watched_episodes: number;
    series_type: string | null;
    monitored: boolean;
  };
  quality: QualityBlock;
  extra_copies?: ExtraCopies;
  seasons: LibrarySeason[];
  detail_season: number | null;
  recycle: boolean;
  can_manage: boolean;
}

export type TitleLibrary = MovieLibrary | ShowLibrary;

export interface MatchCandidate {
  id: number;
  title: string;
  year: number | null;
  overview: string;
  poster_url: string | null;
}
