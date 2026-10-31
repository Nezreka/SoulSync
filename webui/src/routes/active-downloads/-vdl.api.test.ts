import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import {
  blockVideoRelease,
  cancelVideoDownload,
  stopVideoWishlistSearches,
  clearFailedVideoDownloads,
  clearVideoBlocklist,
  clearVideoDownloads,
  clearVideoHistory,
  deleteVideoHistory,
  emptyVideoRecycle,
  fetchVideoBlocklist,
  fetchVideoDownloads,
  fetchVideoHistory,
  fetchVideoHistoryCount,
  fetchVideoMeta,
  fetchVideoRecycle,
  fetchVideoYtMeta,
  purgeVideoRecycleEntry,
  restoreAllVideoRecycle,
  restoreVideoRecycleEntry,
  removeVideoDownload,
  retryVideoDownload,
  unblockVideoRelease,
} from './-vdl.api';

/** Capture the URL and body of whatever request lands on `path`. */
function capture(
  method: 'get' | 'post' | 'delete',
  path: string,
  payload: Record<string, unknown> = { success: true },
) {
  const seen: { url: URL; body: string }[] = [];
  server.use(
    http[method](path, async ({ request }) => {
      let body = '';
      try {
        body = await request.text();
      } catch {
        body = '';
      }
      seen.push({ url: new URL(request.url), body });
      return HttpResponse.json(payload);
    }),
  );
  return seen;
}

describe('the reads never throw', () => {
  // They run on a 2s poll or a page-load path. One bad response must not tear
  // the page down, so each swallows and returns an honest empty value.

  it('fetchVideoDownloads returns {} on a network failure', async () => {
    server.use(http.get('/api/video/downloads/active', () => HttpResponse.error()));
    await expect(fetchVideoDownloads()).resolves.toEqual({});
  });

  it('fetchVideoDownloads parses the downloads array', async () => {
    server.use(
      http.get('/api/video/downloads/active', () =>
        HttpResponse.json({ downloads: [{ id: 7, status: 'downloading' }] }),
      ),
    );
    await expect(fetchVideoDownloads()).resolves.toEqual({
      downloads: [{ id: 7, status: 'downloading' }],
    });
  });

  it('fetchVideoMeta returns null on failure', async () => {
    server.use(
      http.get('/api/video/downloads/meta/movie/1', () => new HttpResponse(null, { status: 500 })),
    );
    await expect(fetchVideoMeta('/api/video/downloads/meta/movie/1')).resolves.toBeNull();
  });

  it('fetchVideoYtMeta returns null on failure', async () => {
    server.use(http.get('/api/video/downloads/yt-meta/x', () => HttpResponse.error()));
    await expect(fetchVideoYtMeta('x')).resolves.toBeNull();
  });

  it('fetchVideoRecycle returns null on failure, the bin on success', async () => {
    server.use(http.get('/api/video/downloads/recycle', () => HttpResponse.error()));
    await expect(fetchVideoRecycle()).resolves.toBeNull();

    const bin = { items: [{ name: 'a.mkv' }], keep_days: 7 };
    server.use(http.get('/api/video/downloads/recycle', () => HttpResponse.json(bin)));
    await expect(fetchVideoRecycle()).resolves.toEqual({ ...bin, recycle_enabled: undefined });
  });

  it('fetchVideoBlocklist returns [] on failure', async () => {
    server.use(http.get('/api/video/downloads/blocklist', () => HttpResponse.error()));
    await expect(fetchVideoBlocklist()).resolves.toEqual([]);
  });

  it('fetchVideoHistory returns an empty page on failure', async () => {
    server.use(http.get('/api/video/downloads/history', () => HttpResponse.error()));
    await expect(fetchVideoHistory('all', '', 1)).resolves.toEqual({
      items: [],
      counts: {},
      totalPages: 1,
    });
  });

  it('fetchVideoHistory parses items, counts and pagination', async () => {
    server.use(
      http.get('/api/video/downloads/history', () =>
        HttpResponse.json({
          success: true,
          items: [{ id: 3 }],
          counts: { total: 1, movie: 1, show: 0, youtube: 0 },
          pagination: { total_pages: 4 },
        }),
      ),
    );
    await expect(fetchVideoHistory('movie', 'dune', 2)).resolves.toEqual({
      items: [{ id: 3 }],
      counts: { total: 1, movie: 1, show: 0, youtube: 0 },
      totalPages: 4,
    });
  });

  it('fetchVideoHistory passes kind/search/page through', async () => {
    const seen = capture('get', '/api/video/downloads/history', {
      success: true,
      items: [],
      counts: {},
      pagination: { total_pages: 1 },
    });
    await fetchVideoHistory('show', 'bear', 3);
    expect(seen).toHaveLength(1);
    expect(seen[0].url.searchParams.get('kind')).toBe('show');
    expect(seen[0].url.searchParams.get('search')).toBe('bear');
    expect(seen[0].url.searchParams.get('page')).toBe('3');
  });

  it('fetchVideoHistoryCount returns null on failure', async () => {
    server.use(http.get('/api/video/downloads/history', () => HttpResponse.error()));
    await expect(fetchVideoHistoryCount()).resolves.toBeNull();
  });
});

describe('the mutations hit the right endpoints with the right bodies', () => {
  it('cancelVideoDownload posts {id}', async () => {
    const seen = capture('post', '/api/video/downloads/cancel', { ok: true });
    await cancelVideoDownload(11);
    expect(seen).toHaveLength(1);
    expect(JSON.parse(seen[0].body)).toEqual({ id: 11 });
  });

  it('stopVideoWishlistSearches posts to search-stop', async () => {
    const seen = capture('post', '/api/video/wishlist/search-stop', { success: true });
    await stopVideoWishlistSearches();
    expect(seen).toHaveLength(1);
  });

  it('retryVideoDownload posts {id} — and {id, next:true} for another release', async () => {
    const seen = capture('post', '/api/video/downloads/retry', { ok: true });
    await retryVideoDownload(11);
    await retryVideoDownload(12, true);
    expect(seen).toHaveLength(2);
    expect(JSON.parse(seen[0].body)).toEqual({ id: 11 });
    expect(JSON.parse(seen[1].body)).toEqual({ id: 12, next: true });
  });

  it('clearVideoDownloads posts to /clear', async () => {
    const seen = capture('post', '/api/video/downloads/clear', { cleared: 2 });
    const r = await clearVideoDownloads();
    expect(seen).toHaveLength(1);
    expect(r?.cleared).toBe(2);
  });

  it('clearFailedVideoDownloads posts {scope: failed} to /clear', async () => {
    const seen = capture('post', '/api/video/downloads/clear', { cleared: 3 });
    const r = await clearFailedVideoDownloads();
    expect(seen).toHaveLength(1);
    expect(JSON.parse(seen[0].body)).toEqual({ scope: 'failed' });
    expect(r?.cleared).toBe(3);
  });

  it('removeVideoDownload posts {id} to /remove', async () => {
    const seen = capture('post', '/api/video/downloads/remove', { ok: true });
    const r = await removeVideoDownload(9);
    expect(seen).toHaveLength(1);
    expect(JSON.parse(seen[0].body)).toEqual({ id: 9 });
    expect(r?.ok).toBe(true);
  });

  it('blockVideoRelease posts {download_id}', async () => {
    const seen = capture('post', '/api/video/downloads/blocklist', { success: true, id: 5 });
    await blockVideoRelease(11);
    expect(JSON.parse(seen[0].body)).toEqual({ download_id: 11 });
  });

  it('unblockVideoRelease deletes /blocklist/:id', async () => {
    const seen = capture('delete', '/api/video/downloads/blocklist/5', { success: true });
    await unblockVideoRelease(5);
    expect(seen).toHaveLength(1);
  });

  it('clearVideoBlocklist posts to /blocklist/clear', async () => {
    const seen = capture('post', '/api/video/downloads/blocklist/clear', { success: true });
    await clearVideoBlocklist();
    expect(seen).toHaveLength(1);
  });

  it('recycle restore/purge carry trash_dir + name, or all:true', async () => {
    const seen = capture('post', '/api/video/downloads/recycle/restore', { success: true });
    await restoreVideoRecycleEntry('trash/a', 'f.mkv');
    await restoreAllVideoRecycle();
    expect(seen).toHaveLength(2);
    expect(JSON.parse(seen[0].body)).toEqual({ trash_dir: 'trash/a', name: 'f.mkv' });
    expect(JSON.parse(seen[1].body)).toEqual({ all: true });

    const seenPurge = capture('post', '/api/video/downloads/recycle/purge', { success: true });
    await purgeVideoRecycleEntry('trash/a', 'f.mkv');
    await emptyVideoRecycle();
    expect(JSON.parse(seenPurge[0].body)).toEqual({ trash_dir: 'trash/a', name: 'f.mkv' });
    expect(JSON.parse(seenPurge[1].body)).toEqual({ all: true });
  });

  it('deleteVideoHistory deletes /history/:id', async () => {
    const seen = capture('delete', '/api/video/downloads/history/9', { success: true });
    await deleteVideoHistory(9);
    expect(seen).toHaveLength(1);
  });

  it('clearVideoHistory posts to /history/clear', async () => {
    const seen = capture('post', '/api/video/downloads/history/clear', {
      success: true,
      removed: 3,
    });
    const r = await clearVideoHistory();
    expect(seen).toHaveLength(1);
    expect(r?.removed).toBe(3);
  });

  it('mutations resolve null on transport failure', async () => {
    server.use(http.post('/api/video/downloads/cancel', () => HttpResponse.error()));
    await expect(cancelVideoDownload(1)).resolves.toBeNull();
  });
});
