import { queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  CreateIssuePayload,
  IssueCounts,
  IssueCountsResponse,
  IssueDetailResponse,
  IssueListResponse,
  IssueRecord,
  IssuesSearch,
} from './-issues.types';

const DEFAULT_LIMIT = 100;

function createIssueHeaders(profileId: number, extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  headers.set('X-Profile-Id', String(profileId || 1));
  return headers;
}

export async function fetchIssueCounts(profileId: number): Promise<IssueCounts> {
  const payload = await readJson<IssueCountsResponse>(
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
  search: Pick<IssuesSearch, 'status' | 'category'>,
): Promise<IssueListResponse> {
  const params = new URLSearchParams();
  params.set('limit', String(DEFAULT_LIMIT));
  if (search.status !== 'all') {
    params.set('status', search.status);
  }
  if (search.category !== 'all') {
    params.set('category', search.category);
  }

  const payload = await readJson<IssueListResponse>(
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
  const payload = await readJson<IssueDetailResponse>(
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
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.put(`issues/${issueId}`, {
      headers: createIssueHeaders(profileId),
      json: updates,
    }),
  );
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to update issue');
  }
}

export async function createIssue(
  profileId: number,
  payload: CreateIssuePayload,
): Promise<IssueRecord | null> {
  const response = await readJson<{
    success: boolean;
    issue?: IssueRecord;
    error?: string;
  }>(
    apiClient.post('issues', {
      headers: createIssueHeaders(profileId, { 'Content-Type': 'application/json' }),
      json: {
        entity_type: payload.entity_type,
        entity_id: String(payload.entity_id),
        category: payload.category,
        title: payload.title,
        description: payload.description || '',
        priority: payload.priority || 'normal',
      },
    }),
  );
  if (!response.success) {
    throw new Error(response.error || 'Failed to submit issue');
  }
  return response.issue ?? null;
}

export async function deleteIssue(profileId: number, issueId: number): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
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

export function issueListQueryOptions(
  profileId: number,
  search: Pick<IssuesSearch, 'status' | 'category'>,
) {
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
