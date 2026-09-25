import { z } from 'zod';

export const ISSUE_ENTITY_TYPE_VALUES = ['track', 'album', 'artist'] as const;
export type IssueEntityType = (typeof ISSUE_ENTITY_TYPE_VALUES)[number];

export const ISSUE_CATEGORY_VALUES = [
  'wrong_track',
  'wrong_metadata',
  'wrong_cover',
  'wrong_artist',
  'duplicate_tracks',
  'missing_tracks',
  'audio_quality',
  'wrong_album',
  'incomplete_album',
  'other',
] as const;

export type IssueCategory = (typeof ISSUE_CATEGORY_VALUES)[number];

export const ISSUE_STATUS_VALUES = ['open', 'in_progress', 'resolved', 'dismissed'] as const;
export type IssueStatus = (typeof ISSUE_STATUS_VALUES)[number];

export const ISSUE_PRIORITY_VALUES = ['low', 'normal', 'high'] as const;
export type IssuePriority = (typeof ISSUE_PRIORITY_VALUES)[number];

export const ISSUE_SEARCH_STATUS_VALUES = [
  'open',
  'all',
  'in_progress',
  'resolved',
  'dismissed',
] as const;
export const ISSUE_SEARCH_CATEGORY_VALUES = ['all', ...ISSUE_CATEGORY_VALUES] as const;

export const issueSearchSchema = z.object({
  status: z.enum(ISSUE_SEARCH_STATUS_VALUES).default('open').catch('open'),
  category: z.enum(ISSUE_SEARCH_CATEGORY_VALUES).default('all').catch('all'),
  // optional, not defaulted: an absent entity keeps the url as it was
  entity: z.enum(ISSUE_ENTITY_TYPE_VALUES).optional().catch(undefined),
  issueId: z.coerce.number().int().positive().optional().catch(undefined),
});

export type IssuesSearch = z.infer<typeof issueSearchSchema>;

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
  /** 1 when the reporter has news they haven't opened yet */
  reporter_unread?: number | boolean | null;
  fix_action?: IssueFixAction | null;
  /** detail only */
  comments?: IssueComment[];
  /** detail only: people who reported the same thing */
  followers?: IssueFollower[];
}

export type IssueFixActionId =
  | 'reidentify'
  | 'edit_metadata'
  | 'pick_art'
  | 'redownload'
  | 'wishlist_missing'
  | 'find_duplicates';

export interface IssueFixAction {
  // a string, not the union: an id this build doesn't know falls back to the item page
  id: string;
  label: string;
}

export interface IssueComment {
  id: number;
  author_id?: number | null;
  author_name?: string | null;
  kind: 'comment' | 'event';
  body: string;
  created_at?: string;
}

export interface IssueFollower {
  follower_id: number;
  follower_name?: string | null;
  created_at?: string;
}

export interface CreateIssueResult {
  id: number | null;
  /** an open report for the same thing already existed, the caller now follows it */
  merged: boolean;
  /** the caller had already reported it */
  already: boolean;
}

export interface IssueUpdatePayload {
  status?: IssueStatus;
  priority?: IssuePriority;
  category?: string;
  admin_response?: string;
  title?: string;
  description?: string;
}

export interface IssueCounts {
  open: number;
  in_progress: number;
  resolved: number;
  dismissed: number;
  total: number;
  /** a member's own reports with news they haven't opened */
  updates?: number;
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
