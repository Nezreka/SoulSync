import { describe, expect, it } from 'vite-plus/test';

import { HttpResponse, http, server } from '@/test/msw';

import type { IssueRecord } from './-issues.types';

import {
  createIssue,
  deleteIssue,
  fetchIssue,
  fetchIssueCounts,
  fetchIssueList,
  updateIssue,
} from './-issues.api';

const counts = {
  open: 4,
  in_progress: 2,
  resolved: 1,
  dismissed: 3,
  total: 10,
};

const issue: IssueRecord = {
  id: 17,
  profile_id: 2,
  entity_type: 'track',
  entity_id: '987',
  category: 'wrong_metadata',
  title: 'Wrong metadata',
  description: 'Title is incorrect',
  status: 'open',
  priority: 'normal',
  snapshot_data: null,
  created_at: '2026-05-01T10:00:00.000Z',
  updated_at: '2026-05-01T10:30:00.000Z',
};

describe('issue api', () => {
  it('fetches issue counts with the profile header', async () => {
    server.use(
      http.get('/api/issues/counts', ({ request }) => {
        expect(request.headers.get('X-Profile-Id')).toBe('42');
        return HttpResponse.json({
          success: true,
          counts,
        });
      }),
    );

    await expect(fetchIssueCounts(42)).resolves.toEqual(counts);
  });

  it('includes list filters and surfaces backend error messages', async () => {
    server.use(
      http.get('/api/issues', ({ request }) => {
        const url = new URL(request.url);

        expect(request.headers.get('X-Profile-Id')).toBe('7');
        expect(url.searchParams.get('limit')).toBe('100');
        expect(url.searchParams.get('status')).toBe('open');
        expect(url.searchParams.get('category')).toBe('wrong_metadata');

        return HttpResponse.json(
          {
            error: 'Issue list unavailable',
          },
          { status: 500 },
        );
      }),
    );

    await expect(
      fetchIssueList(7, {
        status: 'open',
        category: 'wrong_metadata',
      }),
    ).rejects.toThrow('Issue list unavailable');
  });

  it('falls back when an issue payload is missing the record', async () => {
    server.use(
      http.get('/api/issues/:issueId', ({ params, request }) => {
        expect(request.headers.get('X-Profile-Id')).toBe('8');
        expect(params.issueId).toBe('19');

        return HttpResponse.json({
          success: false,
        });
      }),
    );

    await expect(fetchIssue(8, 19)).rejects.toThrow('Issue not found');
  });

  it('normalizes create issue payloads before posting', async () => {
    server.use(
      http.post('/api/issues', async ({ request }) => {
        expect(request.headers.get('X-Profile-Id')).toBe('13');
        expect(request.headers.get('Content-Type')).toContain('application/json');
        await expect(request.json()).resolves.toEqual({
          entity_type: 'album',
          entity_id: 'album-55',
          category: 'wrong_cover',
          title: 'Missing cover',
          description: '',
          priority: 'normal',
        });

        return HttpResponse.json({
          success: true,
          issue,
        });
      }),
    );

    await expect(
      createIssue(13, {
        entity_type: 'album',
        entity_id: 'album-55',
        category: 'wrong_cover',
        title: 'Missing cover',
      }),
    ).resolves.toEqual(issue);
  });

  it('posts issue updates to the correct endpoint', async () => {
    server.use(
      http.put('/api/issues/:issueId', async ({ params, request }) => {
        expect(request.headers.get('X-Profile-Id')).toBe('21');
        expect(params.issueId).toBe('17');
        await expect(request.json()).resolves.toEqual({
          status: 'resolved',
          admin_response: 'Fixed upstream',
        });

        return HttpResponse.json({
          success: true,
        });
      }),
    );

    await expect(
      updateIssue(21, 17, {
        status: 'resolved',
        admin_response: 'Fixed upstream',
      }),
    ).resolves.toBeUndefined();
  });

  it('surfaces delete errors from the server', async () => {
    server.use(
      http.delete('/api/issues/:issueId', ({ params, request }) => {
        expect(request.headers.get('X-Profile-Id')).toBe('5');
        expect(params.issueId).toBe('91');

        return HttpResponse.json(
          {
            error: 'Cannot delete issue',
          },
          { status: 403 },
        );
      }),
    );

    await expect(deleteIssue(5, 91)).rejects.toThrow('Cannot delete issue');
  });
});
