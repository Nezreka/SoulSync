import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { bulkResultToast, bulkUpdateIssues } from './-issues.api';

describe('bulk issue api', () => {
  it('posts the ids and the one change to /bulk', async () => {
    let body: unknown = null;
    server.use(
      http.post('/api/issues/bulk', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ success: true, done: 2, failed: 0 });
      }),
    );
    await expect(bulkUpdateIssues([3, 4], { status: 'resolved' })).resolves.toEqual({
      done: 2,
      failed: 0,
    });
    expect(body).toEqual({ ids: [3, 4], status: 'resolved' });
  });

  it('sends delete as its own flag', async () => {
    let body: unknown = null;
    server.use(
      http.post('/api/issues/bulk', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ success: true, done: 1, failed: 0 });
      }),
    );
    await bulkUpdateIssues([9], { delete: true });
    expect(body).toEqual({ ids: [9], delete: true });
  });

  it('throws the server error', async () => {
    server.use(
      http.post('/api/issues/bulk', () =>
        HttpResponse.json({ success: false, error: 'Admin only' }, { status: 403 }),
      ),
    );
    await expect(bulkUpdateIssues([1], { status: 'dismissed' })).rejects.toThrow();
  });

  it('words the toast after the change', () => {
    expect(bulkResultToast({ status: 'resolved' }, { done: 3, failed: 0 })).toEqual({
      message: 'Resolved 3',
      type: 'success',
    });
    expect(bulkResultToast({ status: 'dismissed' }, { done: 2, failed: 0 }).message).toBe(
      'Closed 2',
    );
    expect(bulkResultToast({ priority: 'high' }, { done: 2, failed: 0 }).message).toBe(
      'Set high priority on 2',
    );
    expect(bulkResultToast({ delete: true }, { done: 1, failed: 1 })).toEqual({
      message: "Deleted 1, 1 didn't change",
      type: 'warning',
    });
  });
});
