import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import type { MusicRequestGroup, MusicRequestRow } from './-requests.types';

import {
  approveMusicRequest,
  declineMusicRequest,
  deleteMusicRequest,
  fetchMusicRequestCounts,
  fetchMusicRequests,
  markMusicRequestsSeen,
  normalizeCounts,
  withdrawMusicRequest,
} from './-requests.api';

const group: MusicRequestGroup = {
  key: 'album:abc',
  profile_id: 3,
  requester_name: 'Kim',
  kind: 'album',
  title: 'Blue',
  artist: 'Joni Mitchell',
  album: 'Blue',
  image_url: null,
  track_ids: ['t1', 't2'],
  tracks: [
    { id: 't1', title: 'All I Want' },
    { id: 't2', title: 'My Old Man' },
  ],
  track_count: 2,
  created_at: '2026-09-24 10:00:00',
  status: 'pending',
};

const row: MusicRequestRow = {
  id: 9,
  profile_id: 3,
  requester_name: 'Kim',
  group_key: 'album:def',
  kind: 'album',
  title: 'Court and Spark',
  artist: 'Joni Mitchell',
  tracks: [],
  track_count: 0,
  status: 'approved',
};

describe('music requests api', () => {
  it('loads every status and maps asks_first', async () => {
    server.use(
      http.get('/api/requests/music', ({ request }) => {
        const url = new URL(request.url);
        expect(url.searchParams.get('status')).toBe('all');
        expect(request.headers.get('X-Profile-Id')).toBe('3');
        return HttpResponse.json({
          success: true,
          pending: [group],
          history: [row],
          counts: { pending: 1, approved: 1 },
          asks_first: true,
        });
      }),
    );

    await expect(fetchMusicRequests(3)).resolves.toEqual({
      pending: [group],
      history: [row],
      counts: { pending: 1, approved: 1, available: 0, declined: 0, removed: 0 },
      asksFirst: true,
    });
  });

  it('surfaces the backend error', async () => {
    server.use(
      http.get('/api/requests/music', () =>
        HttpResponse.json({ success: false, error: 'profile_required' }, { status: 401 }),
      ),
    );

    await expect(fetchMusicRequests(1)).rejects.toThrow('profile_required');
  });

  it('reads badge counts', async () => {
    server.use(
      http.get('/api/requests/music/counts', () =>
        HttpResponse.json({ success: true, pending: 4, updates: 2 }),
      ),
    );

    await expect(fetchMusicRequestCounts(1)).resolves.toEqual({ pending: 4, updates: 2 });
  });

  it('approves and declines by owner and key', async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post('/api/requests/music/approve', async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ success: true, approved: 2 });
      }),
      http.post('/api/requests/music/decline', async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ success: true });
      }),
    );

    await approveMusicRequest(1, { profile_id: 3, key: 'album:abc' });
    await declineMusicRequest(1, { profile_id: 3, key: 'album:abc', response: 'not today' });

    expect(bodies).toEqual([
      { profile_id: 3, key: 'album:abc' },
      { profile_id: 3, key: 'album:abc', response: 'not today' },
    ]);
  });

  it('reports a request that stopped waiting', async () => {
    server.use(
      http.post('/api/requests/music/approve', () =>
        HttpResponse.json(
          { success: false, error: "That request isn't waiting any more" },
          { status: 409 },
        ),
      ),
    );

    await expect(approveMusicRequest(1, { profile_id: 3, key: 'gone' })).rejects.toThrow(
      "That request isn't waiting any more",
    );
  });

  it('withdraws, removes history and marks seen', async () => {
    const hits: string[] = [];
    server.use(
      http.post('/api/requests/music/withdraw', async ({ request }) => {
        hits.push(`withdraw:${(await request.json() as { key: string }).key}`);
        return HttpResponse.json({ success: true });
      }),
      http.delete('/api/requests/music/:id', ({ params }) => {
        hits.push(`delete:${String(params.id)}`);
        return HttpResponse.json({ success: true });
      }),
      http.post('/api/requests/music/seen', () => {
        hits.push('seen');
        return HttpResponse.json({ success: true, marked: 1 });
      }),
    );

    await withdrawMusicRequest(3, 'album:abc');
    await deleteMusicRequest(3, 9);
    await markMusicRequestsSeen(3);

    expect(hits).toEqual(['withdraw:album:abc', 'delete:9', 'seen']);
  });

  it('fills missing counts with zero', () => {
    expect(normalizeCounts(undefined)).toEqual({
      pending: 0,
      approved: 0,
      available: 0,
      declined: 0,
      removed: 0,
    });
  });
});
