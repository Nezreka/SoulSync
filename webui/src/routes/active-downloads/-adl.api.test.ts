import { HttpResponse, http } from 'msw';
import { beforeEach, describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import {
  cancelBatch,
  cancelTask,
  clearCompleted,
  fetchBatchHistory,
  fetchDownloads,
  fetchQuarantine,
  fetchVerificationConfig,
  quarantineApprove,
  quarantineClear,
  quarantineDelete,
  quarantineEntry,
  quarantinePlay,
  verificationApprove,
  verificationCleanOrphans,
  verificationDelete,
  verificationEntry,
} from './-adl.api';

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
  // All four run on a 2s poll or a page-load path. One bad response must not
  // tear the page down, so each swallows and returns an honest empty value.

  it('fetchDownloads returns empty on a network failure', async () => {
    server.use(http.get('/api/downloads/all', () => HttpResponse.error()));
    await expect(fetchDownloads()).resolves.toEqual({});
  });

  it('fetchDownloads returns empty on a 500', async () => {
    server.use(http.get('/api/downloads/all', () => new HttpResponse(null, { status: 500 })));
    await expect(fetchDownloads()).resolves.toEqual({});
  });

  it('fetchDownloads discards a success:false payload', async () => {
    // Half-rendering a failed response is worse than rendering nothing.
    server.use(
      http.get('/api/downloads/all', () =>
        HttpResponse.json({ success: false, downloads: [{ task_id: 'x' }] }),
      ),
    );
    await expect(fetchDownloads()).resolves.toEqual({});
  });

  it('fetchDownloads passes the payload through on success', async () => {
    server.use(
      http.get('/api/downloads/all', () =>
        HttpResponse.json({ success: true, downloads: [{ task_id: 'x' }], batches: [] }),
      ),
    );
    const data = await fetchDownloads();
    expect(data.downloads).toHaveLength(1);
  });

  it('fetchDownloads asks for the 300 cap', async () => {
    // The cap is exactly why the page must not compute the nav badge from
    // this array — a bigger queue would be silently under-counted.
    const seen = capture('get', '/api/downloads/all', { success: true, downloads: [] });
    await fetchDownloads();
    expect(seen[0].url.searchParams.get('limit')).toBe('300');
  });

  it('fetchBatchHistory returns [] on failure and asks for 7 days / 50 max', async () => {
    const seen = capture('get', '/api/downloads/batch-history', { success: true, history: [{}] });
    await fetchBatchHistory();
    expect(seen[0].url.searchParams.get('days')).toBe('7');
    expect(seen[0].url.searchParams.get('limit')).toBe('50');

    server.use(http.get('/api/downloads/batch-history', () => HttpResponse.error()));
    await expect(fetchBatchHistory()).resolves.toEqual([]);
  });

  it('fetchBatchHistory tolerates a success payload with no history key', async () => {
    server.use(
      http.get('/api/downloads/batch-history', () => HttpResponse.json({ success: true })),
    );
    await expect(fetchBatchHistory()).resolves.toEqual([]);
  });

  it('fetchQuarantine carries the failure instead of faking empty', async () => {
    // "hundreds in the badge, list empty" (aug 25): a failed list fetch must
    // be distinguishable from a genuinely empty quarantine
    server.use(http.get('/api/quarantine/list', () => HttpResponse.error()));
    const result = await fetchQuarantine();
    expect('error' in result).toBe(true);
  });

  it('fetchQuarantine rejects a non-array entries field', async () => {
    server.use(
      http.get('/api/quarantine/list', () =>
        HttpResponse.json({ success: true, entries: 'nope' }),
      ),
    );
    const result = await fetchQuarantine();
    expect('error' in result).toBe(true);
  });
});

describe('the verification config gate', () => {
  it('assumes ENABLED when the read fails', async () => {
    // Hiding the review queue because a config read blipped would silently
    // strand files that need attention. An empty queue is the safe error.
    server.use(http.get('/api/verification/config', () => HttpResponse.error()));
    await expect(fetchVerificationConfig()).resolves.toEqual({ acoustid_enabled: true });
  });

  it('reports what the server actually said otherwise', async () => {
    server.use(
      http.get('/api/verification/config', () =>
        HttpResponse.json({ success: true, acoustid_enabled: false, require_verified: true }),
      ),
    );
    const config = await fetchVerificationConfig();
    expect(config.acoustid_enabled).toBe(false);
    expect(config.require_verified).toBe(true);
  });
});

describe('cancelling', () => {
  it('cancels a task by (playlist_id, track_index), not task_id', async () => {
    // The atomic v2 endpoint is what frees the worker slot; cancelling by
    // task id alone leaves the slot held.
    const seen = capture('post', '/api/downloads/cancel_task_v2');
    await cancelTask('pl-1', 4);
    expect(seen[0].url.pathname).toBe('/api/downloads/cancel_task_v2');
    expect(JSON.parse(seen[0].body)).toEqual({ playlist_id: 'pl-1', track_index: 4 });
  });

  it('sends track_index 0 rather than dropping it', async () => {
    // A falsy-check on the first track would cancel the wrong row.
    const seen = capture('post', '/api/downloads/cancel_task_v2');
    await cancelTask('pl-1', 0);
    expect(JSON.parse(seen[0].body)).toEqual({ playlist_id: 'pl-1', track_index: 0 });
  });

  it('cancels a batch through /api/playlists/, not /api/downloads/', async () => {
    const seen = capture('post', '/api/playlists/:id/cancel_batch');
    await cancelBatch('batch-9');
    expect(seen[0].url.pathname).toBe('/api/playlists/batch-9/cancel_batch');
  });

  it('escapes an id with a slash in it', async () => {
    const seen = capture('post', '/api/playlists/:id/cancel_batch');
    await cancelBatch('a/b');
    expect(seen[0].url.pathname).toBe('/api/playlists/a%2Fb/cancel_batch');
  });

  it('clears completed with no body', async () => {
    const seen = capture('post', '/api/downloads/clear-completed', {
      success: true,
      total_cleared: 3,
    });
    const data = await clearCompleted();
    expect(seen[0].url.pathname).toBe('/api/downloads/clear-completed');
    expect(data.total_cleared).toBe(3);
  });
});

describe('the verification endpoints', () => {
  it('reads an entry with GET and mutates with POST', async () => {
    // A mutating GET would be cached and could re-fire on a back-navigation.
    const read = capture('get', '/api/verification/:id/entry', { success: true, entry: { id: 7 } });
    await verificationEntry('7');
    expect(read[0].url.pathname).toBe('/api/verification/7/entry');

    const approve = capture('post', '/api/verification/:id/approve');
    await verificationApprove('7');
    expect(approve[0].url.pathname).toBe('/api/verification/7/approve');

    const del = capture('post', '/api/verification/:id/delete');
    await verificationDelete('7');
    expect(del[0].url.pathname).toBe('/api/verification/7/delete');
  });

  it('cleans orphans and reports the tally', async () => {
    capture('post', '/api/verification/clean-orphans', {
      success: true,
      removed: 2,
      checked: 40,
    });
    const data = await verificationCleanOrphans();
    expect(data.removed).toBe(2);
    expect(data.checked).toBe(40);
  });

  it('lets a mutation error surface rather than swallowing it', async () => {
    // Unlike the polled reads: a failed delete must not look like a success.
    server.use(http.post('/api/verification/:id/delete', () => HttpResponse.error()));
    await expect(verificationDelete('7')).rejects.toThrow();
  });
});

describe('the quarantine endpoints', () => {
  it('always asks the approve to remove sibling candidates', async () => {
    // A quarantined track usually has several rejected candidates alongside
    // it; approving one without clearing the rest leaves duplicates behind.
    const seen = capture('post', '/api/quarantine/:id/approve');
    await quarantineApprove('q1');
    expect(seen[0].url.pathname).toBe('/api/quarantine/q1/approve');
    expect(JSON.parse(seen[0].body)).toEqual({ remove_siblings: true });
  });

  it('deletes with DELETE on the bare entry path', async () => {
    const seen = capture('delete', '/api/quarantine/:id');
    await quarantineDelete('q1');
    expect(seen[0].url.pathname).toBe('/api/quarantine/q1');
  });

  it('clears the whole quarantine with POST', async () => {
    const seen = capture('post', '/api/quarantine/clear');
    await quarantineClear();
    expect(seen[0].url.pathname).toBe('/api/quarantine/clear');
  });

  it('reads a synthesised entry with GET and plays with POST', async () => {
    const read = capture('get', '/api/quarantine/:id/entry', { success: true, entry: {} });
    await quarantineEntry('q1');
    expect(read[0].url.pathname).toBe('/api/quarantine/q1/entry');

    const play = capture('post', '/api/quarantine/:id/play');
    await quarantinePlay('q1');
    expect(play[0].url.pathname).toBe('/api/quarantine/q1/play');
  });

  it('escapes quarantine ids too', async () => {
    const seen = capture('delete', '/api/quarantine/:id');
    await quarantineDelete('a b/c');
    expect(seen[0].url.pathname).toBe('/api/quarantine/a%20b%2Fc');
  });
});
