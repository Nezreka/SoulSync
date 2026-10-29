/**
 * The /api/video/calendar payload, as api/video/calendar.py writes it.
 */

/** core/video/calendar_state.py's vocabulary. */
export type AcqState =
  | 'owned'
  | 'downloading'
  | 'queued'
  | 'failed'
  | 'wanted'
  | 'ignored'
  | 'unaired'
  | 'missing'
  /** the show has no TMDB id: nothing can wish for or search it until it's matched */
  | 'unmatched';

export interface CalendarEpisode {
  /** library episode id, or 'tmdb-<show>-<s>-<e>' for a followed show you don't own */
  id: number | string;
  show_id: number | null;
  show_tmdb_id: number | null;
  show_title: string;
  season_number: number;
  episode_number: number;
  title: string | null;
  overview: string | null;
  air_date: string;
  runtime_minutes?: number | null;
  rating?: number | null;
  has_file: number | boolean;
  monitored: number | boolean;
  has_still?: number | boolean;
  still_url?: string | null;
  poster_url?: string | null;
  wishlist_status?: string | null;
  network?: string | null;
  airs_time?: string | null;
  show_year?: number | null;
  show_status?: string | null;
  show_has_poster?: number | boolean;
  show_has_backdrop?: number | boolean;
  /** a followed show that isn't in the library: its art is TMDB's */
  not_in_library?: boolean;
  acq: AcqState;
  needs_action: boolean;
  progress?: number | null;
  eta_seconds?: number | null;
}

export interface CalendarMovie {
  date: string;
  type: 'cinema' | 'available';
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_url: string | null;
  owned: boolean;
  library_id: number | null;
  status: string | null;
  acq: AcqState;
  progress?: number | null;
}

export interface AutomationStatus {
  enabled: boolean;
  trigger_type?: string | null;
  interval_hours?: number | null;
  /** utc 'YYYY-MM-DD HH:MM:SS' */
  last_run: string | null;
  next_run?: string | null;
  last_error?: string | null;
  last_status?: string | null;
}

export interface CalendarPayload {
  today: string;
  start: string;
  end: string;
  days: number;
  scope: 'watchlist' | 'all';
  specials: boolean;
  total: number;
  owned: number;
  acq_counts: { state: AcqState; count: number }[];
  needs_action: number;
  schedule: { refreshed_at: string | null; stale: boolean; age_days: number | null };
  automations: { airing: AutomationStatus | null; refresh: AutomationStatus | null };
  episodes: CalendarEpisode[];
  movies: CalendarMovie[];
}

export type CalendarView = 'week' | 'agenda' | 'month';
export type CalendarScope = 'watchlist' | 'all';
