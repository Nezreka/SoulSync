import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import type { SearchVideo } from './-search.types';

import { useVideoDownloads } from './-search.use-video-downloads';

const video = (over: Partial<SearchVideo> = {}): SearchVideo => ({
  video_id: 'v1',
  title: 'Windowlicker',
  channel: 'Warp',
  url: 'https://youtu.be/v1',
  ...over,
});

/** Queue of statuses the poller will see, one per call. */
function stubStatus(sequence: { status?: string; progress?: number }[]) {
  let index = 0;
  const asked: string[] = [];
  server.use(
    http.post('/api/music-video/download', () => HttpResponse.json({ ok: true })),
    http.get('/api/music-video/status/:id', ({ params }) => {
      asked.push(String(params.id));
      const next = sequence[Math.min(index, sequence.length - 1)];
      index += 1;
      return HttpResponse.json(next);
    }),
  );
  return asked;
}

describe('useVideoDownloads', () => {
  it('sends what the download endpoint needs, url included', async () => {
    // The vanilla posts {video_id, url, title, channel} (downloads.js:5463).
    // `url` is the one that is easy to lose: it is not shown anywhere on the
    // card, so a missing field is invisible until the download fails.
    let body: Record<string, unknown> = {};
    server.use(
      http.post('/api/music-video/download', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ok: true });
      }),
      http.get('/api/music-video/status/:id', () => HttpResponse.json({ progress: 10 })),
    );

    const { result } = renderHook(() => useVideoDownloads());
    act(() => result.current.download(video()));

    await waitFor(() =>
      expect(body).toEqual({
        video_id: 'v1',
        url: 'https://youtu.be/v1',
        title: 'Windowlicker',
        channel: 'Warp',
      }),
    );
  });

  it('marks the card downloading straight away, before any answer', () => {
    server.use(
      http.post('/api/music-video/download', () => new Promise(() => {})),
    );
    const { result } = renderHook(() => useVideoDownloads());
    act(() => result.current.download(video()));

    // Not after the round trip: the ring has to appear on the click.
    expect(result.current.progress.v1).toEqual({ state: 'downloading', percent: 0 });
  });

  it('follows progress and settles on completed', async () => {
    stubStatus([{ progress: 40 }, { progress: 80 }, { status: 'completed', progress: 100 }]);

    const { result } = renderHook(() => useVideoDownloads());
    act(() => result.current.download(video()));

    await waitFor(() => expect(result.current.progress.v1?.percent).toBe(40), { timeout: 3000 });
    await waitFor(() => expect(result.current.progress.v1?.state).toBe('completed'), {
      timeout: 5000,
    });
    expect(result.current.progress.v1?.percent).toBe(100);
  });

  it('ignores a progress field that is missing or zero', async () => {
    // The vanilla guarded on `progress > 0` so an absent field could not snap
    // the ring back to empty mid-download.
    stubStatus([{ progress: 55 }, {}, { progress: 0 }]);

    const { result } = renderHook(() => useVideoDownloads());
    act(() => result.current.download(video()));

    await waitFor(() => expect(result.current.progress.v1?.percent).toBe(55), { timeout: 3000 });
    await new Promise((r) => setTimeout(r, 2500));
    expect(result.current.progress.v1?.percent).toBe(55);
  });

  it('marks an errored download, and lets it be retried', async () => {
    // The vanilla re-armed cardEl.onclick on failure. Here that is simply the
    // absence of the downloading/completed guard.
    stubStatus([{ status: 'error' }]);

    const { result } = renderHook(() => useVideoDownloads());
    act(() => result.current.download(video()));
    await waitFor(() => expect(result.current.progress.v1?.state).toBe('errored'), {
      timeout: 3000,
    });

    stubStatus([{ status: 'completed', progress: 100 }]);
    act(() => result.current.download(video()));
    await waitFor(() => expect(result.current.progress.v1?.state).toBe('completed'), {
      timeout: 3000,
    });
  });

  it('refuses a second click while one is in flight, and after it finished', async () => {
    let posts = 0;
    server.use(
      http.post('/api/music-video/download', () => {
        posts += 1;
        return HttpResponse.json({ ok: true });
      }),
      http.get('/api/music-video/status/:id', () => HttpResponse.json({ progress: 50 })),
    );

    const { result } = renderHook(() => useVideoDownloads());
    act(() => result.current.download(video()));
    await waitFor(() => expect(posts).toBe(1));

    act(() => result.current.download(video()));
    expect(posts).toBe(1);

    // And once it has completed, clicking must not download it a second time.
    server.use(
      http.get('/api/music-video/status/:id', () =>
        HttpResponse.json({ status: 'completed', progress: 100 }),
      ),
    );
    await waitFor(() => expect(result.current.progress.v1?.state).toBe('completed'), {
      timeout: 3000,
    });
    act(() => result.current.download(video()));
    expect(posts).toBe(1);
  });

  it('errors the card when the download request itself is refused', async () => {
    server.use(
      http.post('/api/music-video/download', () => new HttpResponse(null, { status: 500 })),
    );

    const { result } = renderHook(() => useVideoDownloads());
    act(() => result.current.download(video()));
    await waitFor(() => expect(result.current.progress.v1?.state).toBe('errored'));
  });

  it('keeps polling through a single failed status check', async () => {
    // One dropped request is not a failed download; the next tick may answer.
    let calls = 0;
    server.use(
      http.post('/api/music-video/download', () => HttpResponse.json({ ok: true })),
      http.get('/api/music-video/status/:id', () => {
        calls += 1;
        if (calls === 1) return new HttpResponse(null, { status: 503 });
        return HttpResponse.json({ status: 'completed', progress: 100 });
      }),
    );

    const { result } = renderHook(() => useVideoDownloads());
    act(() => result.current.download(video()));
    await waitFor(() => expect(result.current.progress.v1?.state).toBe('completed'), {
      timeout: 5000,
    });
  });

  it('tracks each video separately', async () => {
    stubStatus([{ progress: 30 }]);
    const { result } = renderHook(() => useVideoDownloads());

    act(() => result.current.download(video({ video_id: 'v1' })));
    act(() => result.current.download(video({ video_id: 'v2' })));

    await waitFor(() => expect(result.current.progress.v1?.percent).toBe(30), { timeout: 3000 });
    await waitFor(() => expect(result.current.progress.v2?.percent).toBe(30), { timeout: 3000 });
  });

  it('ignores a video with no id, which nothing could be polled by', async () => {
    let posts = 0;
    server.use(
      http.post('/api/music-video/download', () => {
        posts += 1;
        return HttpResponse.json({ ok: true });
      }),
    );
    const { result } = renderHook(() => useVideoDownloads());
    act(() => result.current.download(video({ video_id: undefined })));

    // Awaited, not asserted on the spot: the request would be in flight, and a
    // synchronous check passes whether or not it was ever sent.
    await new Promise((r) => setTimeout(r, 50));
    expect(posts).toBe(0);
    expect(result.current.progress).toEqual({});
  });

  it('stops polling when the page goes away', async () => {
    let calls = 0;
    server.use(
      http.post('/api/music-video/download', () => HttpResponse.json({ ok: true })),
      http.get('/api/music-video/status/:id', () => {
        calls += 1;
        return HttpResponse.json({ progress: 10 });
      }),
    );

    const { result, unmount } = renderHook(() => useVideoDownloads());
    act(() => result.current.download(video()));
    await waitFor(() => expect(calls).toBeGreaterThan(0), { timeout: 3000 });

    unmount();
    const afterUnmount = calls;
    await new Promise((r) => setTimeout(r, 2500));
    // A live interval would have added several more requests for a page that
    // no longer exists.
    expect(calls).toBe(afterUnmount);
  });
});
