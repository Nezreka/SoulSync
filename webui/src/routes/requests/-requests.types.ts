import { z } from 'zod';

export type MusicRequestKind = 'album' | 'track';
/** awaiting_selection: approved, held until an admin picks the release (#2199). */
export type MusicRequestHistoryStatus =
  | 'awaiting_selection'
  | 'approved'
  | 'available'
  | 'declined'
  | 'removed';
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

export type MusicVideoRequestKind = 'video';
export type MusicVideoRequestStatus = 'pending' | 'approved' | 'available' | 'declined';

/**
 * A music-video ask. Unlike tracks, videos have no wishlist to derive a
 * pending queue from, so pending rows are stored (same table as history).
 */
export interface MusicVideoRequest {
  id: number;
  profile_id: number;
  requester_name?: string | null;
  video_id: string;
  url: string;
  title: string;
  channel?: string | null;
  artist?: string | null;
  thumbnail_url?: string | null;
  status: MusicVideoRequestStatus;
  admin_response?: string | null;
  resolved_at?: string | null;
  available_at?: string | null;
  seen_at?: string | null;
  created_at?: string | null;
}

export type MediaRequestKind = 'audiobook' | 'podcast';

/**
 * An audiobook or podcast episode ask (#2188), from a profile that asks
 * first for books or episodes. Stored like a video ask: the row is the queue
 * and the history.
 */
export interface MediaRequest {
  id: number;
  profile_id: number;
  requester_name?: string | null;
  kind: MediaRequestKind;
  item_key: string;
  title: string;
  /** the author, or the show an episode is from */
  subtitle?: string | null;
  image_url?: string | null;
  status: MusicVideoRequestStatus | 'awaiting_selection';
  admin_response?: string | null;
  resolved_at?: string | null;
  created_at?: string | null;
  seen_at?: string | null;
}

/** how an approved request gets its release, per kind (#2199). */
export type AcquisitionMode = 'auto' | 'manual';
export type AcquisitionKind = 'music' | 'movie' | 'tv' | 'audiobook';
export type AcquisitionModes = Record<AcquisitionKind, AcquisitionMode>;

/** a music ask approved on hold: its wishlist rows wait for a pick. */
export interface AwaitingMusicGroup extends Omit<MusicRequestGroup, 'status'> {
  status: 'awaiting_selection';
  approved_at?: string | null;
}

/** a book ask approved on hold. */
export interface AwaitingMedia extends MediaRequest {
  asin?: string;
}

/** a movie, show or episode approved on hold (the video side's queue). */
export interface AwaitingVideoTitle {
  id: number;
  kind: 'movie' | 'show' | 'episode';
  tmdb_id: number;
  title: string;
  year?: number | null;
  poster_url?: string | null;
  season_number?: number | null;
  episode_number?: number | null;
  approved_at?: string | null;
  requesters: string[];
  request_ids: number[];
  seasons: Array<{ season_number: number; episodes: number[] }>;
  downloading: boolean;
}

export interface AwaitingQueue {
  music: AwaitingMusicGroup[];
  media: AwaitingMedia[];
  video: AwaitingVideoTitle[];
}

export interface MusicRequestCounts {
  pending: number;
  approved: number;
  available: number;
  declined: number;
  removed: number;
}

/** a member's request limit: asks per window, and how many are left. */
export interface RequestQuota {
  limit: number;
  days: number;
  used: number;
  remaining: number;
}

export interface MusicRequestListResponse {
  success: boolean;
  error?: string;
  pending: MusicRequestGroup[];
  history: MusicRequestRow[];
  pending_videos: MusicVideoRequest[];
  video_history: MusicVideoRequest[];
  pending_media?: MediaRequest[];
  media_history?: MediaRequest[];
  counts: Partial<MusicRequestCounts>;
  asks_first: boolean;
  quota?: Partial<RequestQuota> | null;
  acquisition?: Partial<AcquisitionModes>;
  /** asks waiting for this viewer to pick a release */
  awaiting?: number;
}

export interface MusicRequestList {
  pending: MusicRequestGroup[];
  history: MusicRequestRow[];
  pendingVideos: MusicVideoRequest[];
  videoHistory: MusicVideoRequest[];
  pendingMedia?: MediaRequest[];
  mediaHistory?: MediaRequest[];
  counts: MusicRequestCounts;
  asksFirst: boolean;
  quota: RequestQuota | null;
  /** how approved requests download, per kind (#2199); every kind automatic when absent */
  acquisition?: AcquisitionModes;
  awaiting?: number;
}

export interface MusicRequestBadgeCounts {
  pending: number;
  updates: number;
}

export const REQUEST_TAB_VALUES = [
  'pick',
  'waiting',
  'on-the-way',
  'available',
  'declined',
  'all',
] as const;
export type RequestTab = (typeof REQUEST_TAB_VALUES)[number];

export const requestSearchSchema = z.object({
  tab: z.enum(REQUEST_TAB_VALUES).default('waiting').catch('waiting'),
});

export type RequestsSearch = z.infer<typeof requestSearchSchema>;

/** one row on the page: a waiting group, a history row, or a video ask. */
export type RequestItem =
  | { source: 'pending'; status: 'pending'; group: MusicRequestGroup }
  | { source: 'history'; status: MusicRequestHistoryStatus; row: MusicRequestRow }
  | { source: 'video-pending'; status: 'pending'; video: MusicVideoRequest }
  | {
      source: 'video-history';
      status: MusicRequestHistoryStatus;
      video: MusicVideoRequest;
    }
  | { source: 'media-pending'; status: 'pending'; media: MediaRequest }
  | { source: 'media-history'; status: MusicRequestHistoryStatus; media: MediaRequest };
