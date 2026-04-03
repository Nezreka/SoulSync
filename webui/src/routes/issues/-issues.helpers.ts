import { queryOptions } from '@tanstack/react-query';

import { apiClient, parseJsonResponse } from '@/app/api-client';

import type {
  IssueCounts,
  IssueCountsResponse,
  IssueDetailResponse,
  IssueListResponse,
  IssueRecord,
  IssuesSearch,
  IssueSnapshot,
} from './-issues.types';

const DEFAULT_LIMIT = 100;

export const REFRESH_EVENT = 'ss:issues-refresh';
export const CLOSE_EVENT = 'ss:issues-close-detail';

export const DEFAULT_ISSUES_SEARCH: Required<IssuesSearch> = {
  status: 'open',
  category: 'all',
};

export const ISSUE_CATEGORY_META: Record<
  string,
  { label: string; icon: string; description: string; applies: Array<'track' | 'album' | 'artist'> }
> = {
  wrong_track: {
    label: 'Wrong Track',
    icon: 'XT',
    description: 'This file plays a different song than expected',
    applies: ['track'],
  },
  wrong_metadata: {
    label: 'Wrong Metadata',
    icon: 'MD',
    description: 'Title, artist, year, or other tags are incorrect',
    applies: ['track', 'album'],
  },
  wrong_cover: {
    label: 'Wrong Cover Art',
    icon: 'CA',
    description: 'The artwork is wrong or missing',
    applies: ['album'],
  },
  wrong_artist: {
    label: 'Wrong Artist',
    icon: 'AR',
    description: 'This track is filed under the wrong artist',
    applies: ['track'],
  },
  duplicate_tracks: {
    label: 'Duplicate Tracks',
    icon: 'DT',
    description: 'The same track appears more than once in this album',
    applies: ['album'],
  },
  missing_tracks: {
    label: 'Missing Tracks',
    icon: 'MT',
    description: 'Tracks that should be here are missing',
    applies: ['album'],
  },
  audio_quality: {
    label: 'Audio Quality',
    icon: 'AQ',
    description: 'Audio has quality issues like clipping or low bitrate',
    applies: ['track'],
  },
  wrong_album: {
    label: 'Wrong Album',
    icon: 'AL',
    description: 'This track belongs to a different album',
    applies: ['track'],
  },
  incomplete_album: {
    label: 'Incomplete Album',
    icon: 'IA',
    description: 'Album is partially downloaded',
    applies: ['album'],
  },
  other: {
    label: 'Other',
    icon: 'OT',
    description: 'Any other issue not listed above',
    applies: ['track', 'album', 'artist'],
  },
};

export const ISSUE_STATUS_META: Record<string, { label: string; className: string }> = {
  open: { label: 'Open', className: 'is-open' },
  in_progress: { label: 'In Progress', className: 'is-progress' },
  resolved: { label: 'Resolved', className: 'is-resolved' },
  dismissed: { label: 'Dismissed', className: 'is-dismissed' },
};

function createIssueHeaders(profileId: number, extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  headers.set('X-Profile-Id', String(profileId || 1));
  return headers;
}

export function normalizeIssuesSearch(search: IssuesSearch | undefined): Required<IssuesSearch> {
  const status = search?.status;
  const category = search?.category;

  return {
    status:
      status === 'all' ||
      status === 'open' ||
      status === 'in_progress' ||
      status === 'resolved' ||
      status === 'dismissed'
        ? status
        : DEFAULT_ISSUES_SEARCH.status,
    category: typeof category === 'string' && category.length > 0 ? category : 'all',
  };
}

export async function fetchIssueCounts(profileId: number): Promise<IssueCounts> {
  const payload = await parseJsonResponse<IssueCountsResponse>(
    apiClient.get('issues/counts', {
      headers: createIssueHeaders(profileId),
    }),
  );
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to load issue counts');
  }
  return payload.counts;
}

export async function fetchIssueList(
  profileId: number,
  search: Required<IssuesSearch>,
): Promise<IssueListResponse> {
  const params = new URLSearchParams();
  params.set('limit', String(DEFAULT_LIMIT));
  if (search.status !== 'all') {
    params.set('status', search.status);
  }
  if (search.category !== 'all') {
    params.set('category', search.category);
  }

  const payload = await parseJsonResponse<IssueListResponse>(
    apiClient.get('issues', {
      headers: createIssueHeaders(profileId),
      searchParams: params,
    }),
  );
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to load issues');
  }
  return payload;
}

export async function fetchIssue(profileId: number, issueId: number): Promise<IssueRecord> {
  const payload = await parseJsonResponse<IssueDetailResponse>(
    apiClient.get(`issues/${issueId}`, {
      headers: createIssueHeaders(profileId),
    }),
  );
  if (!payload.success || !payload.issue) {
    throw new Error(payload.error || 'Issue not found');
  }
  return payload.issue;
}

export async function updateIssue(
  profileId: number,
  issueId: number,
  updates: { status?: string; admin_response?: string },
): Promise<void> {
  const payload = await parseJsonResponse<{ success: boolean; error?: string }>(
    apiClient.put(`issues/${issueId}`, {
      headers: createIssueHeaders(profileId),
      json: updates,
    }),
  );
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to update issue');
  }
}

export async function deleteIssue(profileId: number, issueId: number): Promise<void> {
  const payload = await parseJsonResponse<{ success: boolean; error?: string }>(
    apiClient.delete(`issues/${issueId}`, {
      headers: createIssueHeaders(profileId),
    }),
  );
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to delete issue');
  }
}

export function issueCountsQueryOptions(profileId: number) {
  return queryOptions({
    queryKey: ['issues', 'counts', profileId],
    queryFn: () => fetchIssueCounts(profileId),
  });
}

export function issueListQueryOptions(profileId: number, search: Required<IssuesSearch>) {
  return queryOptions({
    queryKey: ['issues', 'list', profileId, search.status, search.category],
    queryFn: () => fetchIssueList(profileId, search),
  });
}

export function issueDetailQueryOptions(profileId: number, issueId: number) {
  return queryOptions({
    queryKey: ['issues', 'detail', profileId, issueId],
    queryFn: () => fetchIssue(profileId, issueId),
    enabled: issueId > 0,
  });
}

export function dispatchIssuesRefreshEvent() {
  window.dispatchEvent(new CustomEvent(REFRESH_EVENT));
}

export function dispatchIssuesCloseEvent() {
  window.dispatchEvent(new CustomEvent(CLOSE_EVENT));
}

export function parseSnapshot(snapshot: IssueRecord['snapshot_data']): IssueSnapshot {
  if (!snapshot) {
    return {};
  }
  if (typeof snapshot === 'string') {
    try {
      return JSON.parse(snapshot) as IssueSnapshot;
    } catch {
      return {};
    }
  }
  return snapshot;
}

export function getEntityLabel(entityType: IssueRecord['entity_type']): string {
  return entityType === 'track' ? 'Track' : entityType === 'album' ? 'Album' : 'Artist';
}

export function getEntityName(issue: IssueRecord, snapshot: IssueSnapshot): string {
  const entityLabel = getEntityLabel(issue.entity_type);
  return String(snapshot.title || snapshot.name || `${entityLabel} #${issue.entity_id}`);
}

export function getEntityDetails(issue: IssueRecord, snapshot: IssueSnapshot): string[] {
  const details: string[] = [];
  if (issue.entity_type === 'track') {
    if (snapshot.artist_name) details.push(String(snapshot.artist_name));
    if (snapshot.album_title) details.push(String(snapshot.album_title));
  } else if (issue.entity_type === 'album') {
    if (snapshot.artist_name) details.push(String(snapshot.artist_name));
  } else if (issue.entity_type === 'artist' && snapshot.name) {
    details.push(String(snapshot.name));
  }
  return details;
}

export function getIssueArtwork(snapshot: IssueSnapshot): string {
  return String(snapshot.thumb_url || snapshot.album_thumb || snapshot.artist_thumb || '');
}

export function formatIssueDate(value?: string): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatStatusLabel(status: string): string {
  return ISSUE_STATUS_META[status]?.label || status.replace(/_/g, ' ');
}

export function getPriorityClassName(priority: string): 'high' | 'low' | 'normal' {
  if (priority === 'high') return 'high';
  if (priority === 'low') return 'low';
  return 'normal';
}
