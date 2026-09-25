import { z } from 'zod';

export type MusicRequestKind = 'album' | 'track';
export type MusicRequestHistoryStatus = 'approved' | 'available' | 'declined' | 'removed';
export type MusicRequestStatus = 'pending' | MusicRequestHistoryStatus;

export interface MusicRequestTrack {
  id: string;
  title: string;
  artist?: string;
}

/** a waiting ask: wishlist rows of one profile grouped by album (or a lone track). */
export interface MusicRequestGroup {
  key: string;
  profile_id: number;
  requester_name: string;
  kind: MusicRequestKind;
  title: string;
  artist?: string;
  album?: string;
  image_url?: string | null;
  track_ids: string[];
  tracks: MusicRequestTrack[];
  track_count: number;
  created_at?: string | null;
  status: 'pending';
}

/** a decided ask, kept as history. */
export interface MusicRequestRow {
  id: number;
  profile_id: number;
  requester_name?: string | null;
  group_key: string;
  kind: MusicRequestKind;
  title: string;
  artist?: string | null;
  image_url?: string | null;
  tracks: MusicRequestTrack[];
  track_count: number;
  status: MusicRequestHistoryStatus;
  admin_response?: string | null;
  resolved_at?: string | null;
  available_at?: string | null;
  seen_at?: string | null;
}

export interface MusicRequestCounts {
  pending: number;
  approved: number;
  available: number;
  declined: number;
  removed: number;
}

export interface MusicRequestListResponse {
  success: boolean;
  error?: string;
  pending: MusicRequestGroup[];
  history: MusicRequestRow[];
  counts: Partial<MusicRequestCounts>;
  asks_first: boolean;
}

export interface MusicRequestList {
  pending: MusicRequestGroup[];
  history: MusicRequestRow[];
  counts: MusicRequestCounts;
  asksFirst: boolean;
}

export interface MusicRequestBadgeCounts {
  pending: number;
  updates: number;
}

export const REQUEST_TAB_VALUES = ['waiting', 'on-the-way', 'available', 'declined', 'all'] as const;
export type RequestTab = (typeof REQUEST_TAB_VALUES)[number];

export const requestSearchSchema = z.object({
  tab: z.enum(REQUEST_TAB_VALUES).default('waiting').catch('waiting'),
});

export type RequestsSearch = z.infer<typeof requestSearchSchema>;

/** one row on the page, either a waiting group or a history row. */
export type RequestItem =
  | { source: 'pending'; status: 'pending'; group: MusicRequestGroup }
  | { source: 'history'; status: MusicRequestHistoryStatus; row: MusicRequestRow };
