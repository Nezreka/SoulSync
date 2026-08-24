import { z } from 'zod';

export const STATS_RANGE_VALUES = ['7d', '30d', '12m', 'all'] as const;
export type StatsRange = (typeof STATS_RANGE_VALUES)[number];

/**
 * The page carries two different kinds of fact that were sharing one surface.
 *
 * 'listening' is personal — your plays, your artists, your genres. 'library'
 * is operational — disk usage, database size, format spread. They are read by
 * different people for different reasons at different frequencies, and mixing
 * them meant the personal numbers were buried under storage tables.
 *
 * In the URL so a tab is linkable and survives a reload, same as `range`.
 */
export const STATS_TAB_VALUES = ['listening', 'library'] as const;
export type StatsTab = (typeof STATS_TAB_VALUES)[number];

/**
 * The Year in Listening takeover, opened as `?story=year`.
 *
 * In the URL rather than component state for the same reason `tab` is: the
 * story is worth linking to, and closing it should be a back-button away
 * rather than a state reset that leaves the page behind it looking untouched.
 */
export const STATS_STORY_VALUES = ['year'] as const;
export type StatsStory = (typeof STATS_STORY_VALUES)[number];

export const statsSearchSchema = z.object({
  range: z.enum(STATS_RANGE_VALUES).default('7d').catch('7d'),
  tab: z.enum(STATS_TAB_VALUES).default('listening').catch('listening'),
  story: z.enum(STATS_STORY_VALUES).optional().catch(undefined),
});

export type StatsSearch = z.infer<typeof statsSearchSchema>;

export interface StatsOverview {
  total_plays: number;
  total_time_ms: number;
  unique_artists: number;
  unique_albums: number;
  unique_tracks: number;
}

export interface StatsArtistRow {
  id?: string | number | null;
  name: string;
  image_url?: string | null;
  play_count: number;
  global_listeners?: number | null;
  soul_id?: string | null;
}

export interface StatsAlbumRow {
  name: string;
  artist?: string | null;
  artist_id?: string | number | null;
  image_url?: string | null;
  play_count: number;
}

export interface StatsTrackRow {
  name: string;
  artist?: string | null;
  artist_id?: string | number | null;
  album?: string | null;
  image_url?: string | null;
  play_count: number;
}

export interface StatsTimelineRow {
  date: string;
  plays: number;
}

export interface StatsGenreRow {
  genre: string;
  play_count: number;
  percentage: number;
}

export interface StatsEnrichmentCoverage {
  spotify?: number;
  musicbrainz?: number;
  deezer?: number;
  jiosaavn?: number;
  lastfm?: number;
  itunes?: number;
  audiodb?: number;
  genius?: number;
  tidal?: number;
  qobuz?: number;
  bandcamp?: number;
}

export interface StatsHealth {
  total_tracks?: number;
  unplayed_count?: number;
  unplayed_percentage?: number;
  total_duration_ms?: number;
  format_breakdown?: Record<string, number>;
  enrichment_coverage?: StatsEnrichmentCoverage;
}

export interface StatsRecentTrack {
  title: string;
  artist?: string | null;
  album?: string | null;
  played_at?: string | null;
}

export interface StatsCachedPayload {
  success: boolean;
  overview?: Partial<StatsOverview>;
  /** The same aggregate over the period BEFORE this range, powering the tile
   *  deltas. null for 'all' — there is no period before everything, and the UI
   *  omits the comparison rather than comparing against nothing. */
  previous?: Partial<StatsOverview> | null;
  clock?: StatsClock;
  rhythm?: StatsRhythm;
  own_vs_play?: StatsOwnVsPlay[];
  neglected?: StatsNeglectedAlbum[];
  top_artists?: StatsArtistRow[];
  top_albums?: StatsAlbumRow[];
  top_tracks?: StatsTrackRow[];
  timeline?: StatsTimelineRow[];
  genres?: StatsGenreRow[];
  recent?: StatsRecentTrack[];
  health?: StatsHealth;
  error?: string;
}

export interface ListeningStatsStatus {
  stats?: {
    last_poll?: string | null;
  };
  error?: string;
}

export interface LastfmListeningImportStatus {
  success: boolean;
  enabled?: boolean;
  api_key_configured?: boolean;
  authenticated_user_available?: boolean;
  username?: string | null;
  running?: boolean;
  status?: 'idle' | 'running' | 'complete' | 'error' | 'cancelled' | 'skipped' | string;
  phase?: string | null;
  progress?: number | null;
  imported?: number;
  inserted?: number;
  duplicates?: number;
  total_scrobbles?: number | null;
  page?: number;
  total_pages?: number | null;
  last_success_at?: string | null;
  last_imported_at?: string | null;
  next_run_in_seconds?: number;
  error?: string;
}

export interface StatsDbStorageTable {
  name: string;
  size: number;
}

export interface StatsDbStoragePayload {
  success: boolean;
  tables?: StatsDbStorageTable[];
  total_file_size?: number;
  method?: string;
  error?: string;
}

export interface StatsLibraryDiskUsagePayload {
  success: boolean;
  has_data?: boolean;
  total_bytes?: number;
  tracks_with_size?: number;
  tracks_without_size?: number;
  by_format?: Record<string, number>;
  error?: string;
}

export type StatsListeningEventsFilter =
  | { type: 'date'; date: string }
  | { type: 'weekday_hour'; weekday: number; hour: number }
  | { type: 'hour'; hour: number };

export interface StatsListeningEventTrack {
  title: string;
  artist?: string | null;
  album?: string | null;
  played_at?: string | null;
  duration_ms?: number | null;
  server_source?: string | null;
  image_url?: string | null;
  artist_db_id?: string | number | null;
  db_track_id?: string | number | null;
}

export interface StatsListeningEventsPayload {
  success: boolean;
  title?: string;
  total?: number;
  limit?: number;
  has_more?: boolean;
  items?: StatsListeningEventTrack[];
  error?: string;
}

export interface StatsResolveTrackPayload {
  success: boolean;
  error?: string;
  track?: {
    id: string | number;
    title: string;
    file_path: string;
    bitrate?: string | number | null;
    artist_id?: string | number | null;
    album_id?: string | number | null;
    image_url?: string | null;
    album_title?: string | null;
    artist_name?: string | null;
  };
}

export interface StatsStreamTrackPayload {
  success: boolean;
  error?: string;
  result?: Record<string, unknown>;
}

/** Plays by weekday (0=Sunday) x hour. Dense 7x24 — a heatmap needs a value
 *  in every cell, so gaps are zeros from the backend, never undefined. */
export interface StatsClock {
  grid: number[][];
  peak: { weekday: number | null; hour: number | null; plays: number };
  total: number;
}

/** Listening as a habit rather than a total. */
export interface StatsRhythm {
  current_streak: number;
  longest_streak: number;
  busiest_day: { date: string | null; plays: number };
  active_days: number;
}

/** A genre's share of the library against its share of plays. Both are
 *  percentages of the genre-known population, so they compare honestly. */
export interface StatsOwnVsPlay {
  genre: string;
  owned_pct: number;
  played_pct: number;
  gap: number;
  owned_tracks: number;
  plays: number;
}

/** An album you own where nothing has ever been played. */
export interface StatsNeglectedAlbum {
  id: number | string;
  /** The album's display name. The backend selects albums.title AS the second
   *  column and emits it under `name` — keep the two in step. */
  name: string;
  artist: string;
  tracks: number;
}
