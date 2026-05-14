import { z } from 'zod';

export const STATS_RANGE_VALUES = ['7d', '30d', '12m', 'all'] as const;
export type StatsRange = (typeof STATS_RANGE_VALUES)[number];

export const statsSearchSchema = z.object({
  range: z.enum(STATS_RANGE_VALUES).default('7d').catch('7d'),
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
  lastfm?: number;
  itunes?: number;
  audiodb?: number;
  genius?: number;
  tidal?: number;
  qobuz?: number;
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
