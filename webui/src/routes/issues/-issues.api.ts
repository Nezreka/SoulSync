import { infiniteQueryOptions, queryOptions, type QueryClient } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type {
  CreateIssuePayload,
  CreateIssueResult,
  IssueCounts,
  IssueCountsResponse,
  IssueDetailResponse,
  IssueListResponse,
  IssueRecord,
  IssuesSearch,
  IssueUpdatePayload,
} from './-issues.types';

// identity is the session now. the old X-Profile-Id header is gone: the
// server ignores it, and sending it only suggested it still mattered.

export const ISSUE_PAGE_SIZE = 50;
export const ISSUES_QUERY_KEY = ['issues'] as const;

export type IssueListFilters = Pick<IssuesSearch, 'status' | 'category' | 'entity'>;

export async function fetchIssueCounts(): Promise<IssueCounts> {
  const payload = await readJson<IssueCountsResponse>(apiClient.get('issues/counts'));
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to load issue counts');
  }
  return payload.counts;
}

export async function fetchIssueList(
  search: IssueListFilters,
  page: { offset?: number; limit?: number } = {},
): Promise<IssueListResponse> {
  const params = new URLSearchParams();
  params.set('limit', String(page.limit ?? ISSUE_PAGE_SIZE));
  if (page.offset) {
    params.set('offset', String(page.offset));
  }
  if (search.status !== 'all') {
    params.set('status', search.status);
  }
  if (search.category !== 'all') {
    params.set('category', search.category);
  }
  if (search.entity) {
    params.set('entity_type', search.entity);
  }

  const payload = await readJson<IssueListResponse>(
    apiClient.get('issues', { searchParams: params }),
  );
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to load issues');
  }
  return payload;
}

export async function fetchIssue(issueId: number): Promise<IssueRecord> {
  const payload = await readJson<IssueDetailResponse>(apiClient.get(`issues/${issueId}`));
  if (!payload.success || !payload.issue) {
    throw new Error(payload.error || 'Issue not found');
  }
  return payload.issue;
}

export async function updateIssue(issueId: number, updates: IssueUpdatePayload): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.put(`issues/${issueId}`, { json: updates }),
  );
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to update issue');
  }
}

export async function addIssueComment(issueId: number, body: string): Promise<number | null> {
  const payload = await readJson<{ success: boolean; id?: number; error?: string }>(
    apiClient.post(`issues/${issueId}/comments`, { json: { body } }),
  );
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to send the reply');
  }
  return payload.id ?? null;
}

/**
 * files a report. the server answers one of three ways: a new issue (201),
 * merged into someone else's open report for the same thing (the caller now
 * follows it), or already reported by this same person.
 */
export async function createIssue(payload: CreateIssuePayload): Promise<CreateIssueResult> {
  const json: Record<string, string> = {
    entity_type: payload.entity_type,
    entity_id: String(payload.entity_id),
    category: payload.category,
    title: payload.title,
    description: payload.description || '',
  };
  // the server ignores priority from members, so only send it when set
  if (payload.priority) {
    json.priority = payload.priority;
  }
  const response = await readJson<{
    success: boolean;
    id?: number;
    merged?: boolean;
    already?: boolean;
    error?: string;
  }>(apiClient.post('issues', { json }));
  if (!response.success) {
    throw new Error(response.error || 'Failed to submit issue');
  }
  return {
    id: response.id ?? null,
    merged: Boolean(response.merged),
    already: Boolean(response.already),
  };
}

export function createIssueToast(result: CreateIssueResult): {
  message: string;
  type: 'success' | 'info';
} {
  if (result.already) {
    return { message: "You already reported this. It's still open.", type: 'info' };
  }
  if (result.merged) {
    return {
      message: "Someone already reported this. You'll hear when it's fixed.",
      type: 'info',
    };
  }
  return { message: 'Issue reported', type: 'success' };
}

export async function deleteIssue(issueId: number): Promise<void> {
  const payload = await readJson<{ success: boolean; error?: string }>(
    apiClient.delete(`issues/${issueId}`),
  );
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to delete issue');
  }
}

// profileId stays in the keys so a profile switch never shows the last
// profile's cached rows, even though the request no longer carries it.

export function issueCountsQueryOptions(profileId: number) {
  return queryOptions({
    queryKey: [...ISSUES_QUERY_KEY, 'counts', profileId],
    queryFn: () => fetchIssueCounts(),
  });
}

export function issueListQueryOptions(profileId: number, search: IssueListFilters) {
  return infiniteQueryOptions({
    queryKey: [
      ...ISSUES_QUERY_KEY,
      'list',
      profileId,
      search.status,
      search.category,
      search.entity ?? 'all',
    ],
    queryFn: ({ pageParam }) => fetchIssueList(search, { offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, pages) => nextIssueOffset(lastPage, pages),
  });
}

/** where the next page starts, or undefined when everything is loaded */
export function nextIssueOffset(
  lastPage: IssueListResponse,
  pages: IssueListResponse[],
): number | undefined {
  const loaded = pages.reduce((sum, page) => sum + (page.issues?.length ?? 0), 0);
  // a short page means the end, whatever total says (video's total ignores
  // the category filter)
  if ((lastPage.issues?.length ?? 0) < ISSUE_PAGE_SIZE) return undefined;
  if (typeof lastPage.total === 'number' && loaded >= lastPage.total) return undefined;
  return loaded;
}

export function issueDetailQueryOptions(profileId: number, issueId: number) {
  return queryOptions({
    queryKey: [...ISSUES_QUERY_KEY, 'detail', profileId, issueId],
    queryFn: () => fetchIssue(issueId),
    enabled: issueId > 0,
  });
}

export function invalidateIssuesQueries(queryClient: QueryClient) {
  return queryClient.invalidateQueries({ queryKey: ISSUES_QUERY_KEY });
}
