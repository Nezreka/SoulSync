import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import type { IssueListResponse } from './-issues.types';

import {
  addIssueComment,
  createIssue,
  createIssueToast,
  deleteIssue,
  fetchIssue,
  fetchIssueCounts,
  fetchIssueList,
  ISSUE_PAGE_SIZE,
  nextIssueOffset,
  updateIssue,
} from './-issues.api';
import { formatFollowers, formatIssueAgo, parseIssueDate } from './-issues.helpers';

const counts = {
  open: 4,
  in_progress: 2,
  resolved: 1,
  dismissed: 3,
  total: 10,
  updates: 1,
};

describe('issue api', () => {
  // identity is the session; the header let anyone claim to be anyone
  it('never sends the old X-Profile-Id header', async () => {
    const seen: Array<string | null> = [];
    server.use(
      http.get('/api/issues/counts', ({ request }) => {
        seen.push(request.headers.get('X-Profile-Id'));
        return HttpResponse.json({ success: true, counts });
      }),
      http.get('/api/issues', ({ request }) => {
        seen.push(request.headers.get('X-Profile-Id'));
        return HttpResponse.json({ success: true, issues: [], total: 0 });
      }),
      http.post('/api/issues', ({ request }) => {
        seen.push(request.headers.get('X-Profile-Id'));
        return HttpResponse.json({ success: true, id: 3 }, { status: 201 });
      }),
    );

    await expect(fetchIssueCounts()).resolves.toEqual(counts);
    await fetchIssueList({ status: 'open', category: 'all' });
    await createIssue({ entity_type: 'album', entity_id: '1', category: 'other', title: 't' });
    expect(seen).toEqual([null, null, null]);
  });

  it('includes list filters, paging and surfaces backend error messages', async () => {
    server.use(
      http.get('/api/issues', ({ request }) => {
        const url = new URL(request.url);

        expect(url.searchParams.get('limit')).toBe(String(ISSUE_PAGE_SIZE));
        expect(url.searchParams.get('offset')).toBe('50');
        expect(url.searchParams.get('status')).toBe('open');
        expect(url.searchParams.get('category')).toBe('wrong_metadata');
        expect(url.searchParams.get('entity_type')).toBe('track');

        return HttpResponse.json({ error: 'Issue list unavailable' }, { status: 500 });
      }),
    );

    await expect(
      fetchIssueList(
        { status: 'open', category: 'wrong_metadata', entity: 'track' },
        { offset: 50 },
      ),
    ).rejects.toThrow('Issue list unavailable');
  });

  it('leaves out filters that are set to all', async () => {
    server.use(
      http.get('/api/issues', ({ request }) => {
        const url = new URL(request.url);
        expect(url.searchParams.has('status')).toBe(false);
        expect(url.searchParams.has('category')).toBe(false);
        expect(url.searchParams.has('entity_type')).toBe(false);
        expect(url.searchParams.has('offset')).toBe(false);
        return HttpResponse.json({ success: true, issues: [], total: 0 });
      }),
    );
    await expect(fetchIssueList({ status: 'all', category: 'all' })).resolves.toMatchObject({
      total: 0,
    });
  });

  it('falls back when an issue payload is missing the record', async () => {
    server.use(
      http.get('/api/issues/:issueId', ({ params }) => {
        expect(params.issueId).toBe('19');
        return HttpResponse.json({ success: false });
      }),
    );

    await expect(fetchIssue(19)).rejects.toThrow('Issue not found');
  });

  it('posts a new report and reads the id back, not a record that never existed', async () => {
    server.use(
      http.post('/api/issues', async ({ request }) => {
        expect(request.headers.get('Content-Type')).toContain('application/json');
        await expect(request.json()).resolves.toEqual({
          entity_type: 'album',
          entity_id: 'album-55',
          category: 'wrong_cover',
          title: 'Missing cover',
          description: '',
        });
        return HttpResponse.json({ success: true, id: 44 }, { status: 201 });
      }),
    );

    await expect(
      createIssue({
        entity_type: 'album',
        entity_id: 'album-55',
        category: 'wrong_cover',
        title: 'Missing cover',
      }),
    ).resolves.toEqual({ id: 44, merged: false, already: false });
  });

  it('sends priority only when one is given', async () => {
    server.use(
      http.post('/api/issues', async ({ request }) => {
        await expect(request.json()).resolves.toMatchObject({ priority: 'high' });
        return HttpResponse.json({ success: true, id: 2 }, { status: 201 });
      }),
    );
    await createIssue({
      entity_type: 'track',
      entity_id: '9',
      category: 'other',
      title: 't',
      priority: 'high',
    });
  });

  it('reads a merged report as following someone else’s issue', async () => {
    server.use(
      http.post('/api/issues', () =>
        HttpResponse.json({ success: true, id: 12, merged: true }, { status: 200 }),
      ),
    );
    const result = await createIssue({
      entity_type: 'track',
      entity_id: '9',
      category: 'wrong_track',
      title: 'Wrong track',
    });
    expect(result).toEqual({ id: 12, merged: true, already: false });
    expect(createIssueToast(result)).toEqual({
      message: "Someone already reported this. You'll hear when it's fixed.",
      type: 'info',
    });
  });

  it('picks the toast for each create outcome', () => {
    expect(createIssueToast({ id: 1, merged: false, already: false })).toEqual({
      message: 'Issue reported',
      type: 'success',
    });
    expect(createIssueToast({ id: 1, merged: false, already: true }).message).toMatch(
      /already reported/i,
    );
  });

  it('puts triage updates to the issue', async () => {
    server.use(
      http.put('/api/issues/:issueId', async ({ params, request }) => {
        expect(params.issueId).toBe('17');
        await expect(request.json()).resolves.toEqual({ status: 'resolved', priority: 'high' });
        return HttpResponse.json({ success: true });
      }),
    );

    await expect(updateIssue(17, { status: 'resolved', priority: 'high' })).resolves.toBe(
      undefined,
    );
  });

  it('posts a reply to the thread without touching the status', async () => {
    server.use(
      http.post('/api/issues/:issueId/comments', async ({ params, request }) => {
        expect(params.issueId).toBe('17');
        await expect(request.json()).resolves.toEqual({ body: 'on it' });
        return HttpResponse.json({ success: true, id: 5 }, { status: 201 });
      }),
    );
    await expect(addIssueComment(17, 'on it')).resolves.toBe(5);
  });

  it('surfaces delete errors from the server', async () => {
    server.use(
      http.delete('/api/issues/:issueId', ({ params }) => {
        expect(params.issueId).toBe('91');
        return HttpResponse.json({ error: 'Cannot delete issue' }, { status: 403 });
      }),
    );

    await expect(deleteIssue(91)).rejects.toThrow('Cannot delete issue');
  });
});

describe('issue paging', () => {
  const page = (n: number, total: number): IssueListResponse => ({
    success: true,
    total,
    issues: Array.from({ length: n }, (_, i) => ({
      id: i,
      profile_id: 1,
      entity_type: 'track' as const,
      entity_id: String(i),
      category: 'other',
      title: 't',
      status: 'open',
      priority: 'normal',
      snapshot_data: null,
    })),
  });

  it('asks for the next offset while there is more', () => {
    const first = page(ISSUE_PAGE_SIZE, 120);
    expect(nextIssueOffset(first, [first])).toBe(ISSUE_PAGE_SIZE);
  });

  it('stops at the total or on a short page', () => {
    const full = page(ISSUE_PAGE_SIZE, ISSUE_PAGE_SIZE);
    expect(nextIssueOffset(full, [full])).toBeUndefined();
    const short = page(3, 999);
    expect(nextIssueOffset(short, [page(ISSUE_PAGE_SIZE, 999), short])).toBeUndefined();
  });
});

describe('issue dates', () => {
  it('reads sqlite stamps as utc, not local time', () => {
    expect(parseIssueDate('2026-04-03 10:30:00')?.toISOString()).toBe('2026-04-03T10:30:00.000Z');
  });

  it('leaves stamps that already carry a zone alone', () => {
    expect(parseIssueDate('2026-05-01T10:00:00.000Z')?.toISOString()).toBe(
      '2026-05-01T10:00:00.000Z',
    );
    expect(parseIssueDate('2026-05-01T12:00:00+02:00')?.toISOString()).toBe(
      '2026-05-01T10:00:00.000Z',
    );
  });

  it('returns null for junk', () => {
    expect(parseIssueDate('')).toBeNull();
    expect(parseIssueDate('not a date')).toBeNull();
  });

  it('says how long ago against utc', () => {
    const now = Date.parse('2026-04-03T12:30:00Z');
    expect(formatIssueAgo('2026-04-03 10:30:00', now)).toBe('2h ago');
    expect(formatIssueAgo('2026-04-03 12:29:30', now)).toBe('just now');
  });
});

describe('followers line', () => {
  it('names the first and counts the rest', () => {
    expect(formatFollowers([])).toBe('');
    expect(formatFollowers(['Kim'])).toBe('Kim hit this too');
    expect(formatFollowers(['Kim', 'Ada'])).toBe('Kim and 1 other hit this too');
    expect(formatFollowers(['Kim', 'Ada', 'Lee'])).toBe('Kim and 2 others hit this too');
  });
});
