import { z } from 'zod';

export const playlistsSearchSchema = z.object({});

export type PlaylistsSearch = z.infer<typeof playlistsSearchSchema>;

export interface PlaylistKind {
  kind: string;
  name_template: string;
  description: string;
  requires_variant: boolean;
  tags: string[];
  variants: string[];
  default_config: PlaylistConfig;
}

export interface PlaylistConfig {
  limit: number;
  max_per_album: number;
  max_per_artist: number;
  popularity_min: number | null;
  popularity_max: number | null;
  exclude_recent_days: number;
  recency_days: number | null;
  seed: number | null;
  extra: Record<string, unknown>;
}

export interface PlaylistTrack {
  position: number;
  spotify_track_id: string | null;
  itunes_track_id: string | null;
  deezer_track_id: string | null;
  track_name: string;
  artist_name: string;
  album_name: string;
  album_cover_url: string | null;
  duration_ms: number;
  popularity: number;
  source: string | null;
  track_data_json: unknown;
}

export interface PersonalizedPlaylist {
  id: number;
  profile_id: number;
  kind: string;
  variant: string;
  name: string;
  config: PlaylistConfig;
  track_count: number;
  last_generated_at: string | null;
  last_synced_at: string | null;
  last_generation_source: string | null;
  last_generation_error: string | null;
  is_stale: boolean;
  auto_refresh: boolean;
  refresh_interval_hours: number;
}

export interface KindsResponse {
  success: boolean;
  kinds: PlaylistKind[];
}

export interface PlaylistsResponse {
  success: boolean;
  playlists: PersonalizedPlaylist[];
}

export interface PlaylistDetailResponse {
  success: boolean;
  playlist: PersonalizedPlaylist;
  tracks: PlaylistTrack[];
}

export interface ConfigUpdateResponse {
  success: boolean;
  playlist: PersonalizedPlaylist;
}

export interface RefreshResponse {
  success: boolean;
  playlist: PersonalizedPlaylist;
  tracks?: PlaylistTrack[];
  error?: string;
}
