export type IssueStatus = 'open' | 'in_progress' | 'resolved' | 'dismissed';
export type IssueEntityType = 'track' | 'album' | 'artist';
export type IssuePriority = 'low' | 'normal' | 'high';

export interface IssueTrackRow extends Record<string, unknown> {
  bitrate?: string | number;
  disc_number?: string | number;
  duration?: string | number;
  format?: string;
  id?: string | number;
  title?: string;
  track_number?: string | number;
}

export interface IssueSnapshot {
  [key: string]: unknown;
  album_track_count?: string | number;
  bitrate?: string | number;
  bpm?: string | number;
  disc_number?: string | number;
  duration?: string | number;
  file_path?: string;
  format?: string;
  genres?: string[];
  label?: string;
  name?: string;
  record_type?: string;
  title?: string;
  track_count?: string | number;
  tracks?: IssueTrackRow[];
  track_number?: string | number;
  year?: string | number;
  artist_name?: string;
  album_title?: string;
  thumb_url?: string;
  artist_thumb?: string;
  album_thumb?: string;
  spotify_album_id?: string;
  spotify_artist_id?: string;
  spotify_track_id?: string;
  artist_id?: string | number;
  album_id?: string | number;
  quality?: string;
  artist_musicbrainz_id?: string;
  musicbrainz_release_id?: string;
  musicbrainz_recording_id?: string;
  artist_deezer_id?: string;
  album_deezer_id?: string;
  track_deezer_id?: string;
  artist_tidal_id?: string;
  album_tidal_id?: string;
  artist_qobuz_id?: string | number;
  album_qobuz_id?: string | number;
}

export interface IssueRecord {
  id: number;
  profile_id: number;
  entity_type: IssueEntityType;
  entity_id: string;
  category: string;
  title: string;
  description?: string | null;
  status: string;
  priority: string;
  snapshot_data: IssueSnapshot | string | null;
  created_at?: string;
  updated_at?: string;
  resolved_at?: string | null;
  resolved_by?: number | null;
  admin_response?: string | null;
  reporter_name?: string | null;
  reporter_color?: string | null;
  reporter_avatar?: string | null;
}

export interface IssueCounts {
  open: number;
  in_progress: number;
  resolved: number;
  dismissed: number;
  total: number;
}

export interface IssueListResponse {
  success: boolean;
  issues: IssueRecord[];
  total: number;
  error?: string;
}

export interface IssueDetailResponse {
  success: boolean;
  issue?: IssueRecord;
  error?: string;
}

export interface IssueCountsResponse {
  success: boolean;
  counts: IssueCounts;
  error?: string;
}

export interface IssuesSearch {
  category?: string;
  issueId?: string | number;
  status?: IssueStatus | 'all';
}

export interface CreateIssuePayload {
  entity_type: IssueEntityType;
  entity_id: string;
  category: string;
  title: string;
  description?: string;
  priority?: IssuePriority;
}

export interface IssueReportPayload {
  entityType: IssueEntityType;
  entityId: string | number;
  entityName: string;
  artistName?: string;
  albumTitle?: string;
}

export interface IssueDomainBridge {
  openReportIssue: (payload: IssueReportPayload) => void;
  refresh: () => void;
  closeReportIssue?: () => void;
}
