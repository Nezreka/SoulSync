export type IssueStatus = 'open' | 'in_progress' | 'resolved' | 'dismissed';

export interface IssueSnapshot {
  [key: string]: unknown;
  title?: string;
  name?: string;
  artist_name?: string;
  album_title?: string;
  thumb_url?: string;
  artist_thumb?: string;
  album_thumb?: string;
  spotify_album_id?: string;
  spotify_artist_id?: string;
  spotify_track_id?: string;
}

export interface IssueRecord {
  id: number;
  profile_id: number;
  entity_type: 'track' | 'album' | 'artist';
  entity_id: string;
  category: string;
  title: string;
  description?: string | null;
  status: IssueStatus | string;
  priority: 'low' | 'normal' | 'high' | string;
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
  status?: IssueStatus | 'all';
}
