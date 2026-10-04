import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';

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

/** profileAsksFirst() reads window.canDownload at click time, never cached. */
function stubCanDownload(value: boolean) {
  (window as unknown as { canDownload?: () => boolean }).canDownload = () => value;
}

afterEach(() => {
  delete (window as unknown as { canDownload?: () => boolean }).canDownload;
});

describe('useVideoDownloads music video requests', () => {
  it('files a request instead of downloading when the profile asks first', async () => {
    stubCanDownload(false); // profileAsksFirst() === true
    let body: Record<string, unknown> | null = null;
    let downloads = 0;
    server.use(
      http.post('/api/requests/music/videos', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, id: 7 });
      }),
      http.post('/api/music-video/download', () => {
        downloads += 1;
        return HttpResponse.json({ ok: true });
      }),
    );

    const { result } = renderHook(() => useVideoDownloads());
    await act(async () => {
      await result.current.request(video());
    });

    await waitFor(() =>
      expect(body).toEqual({
        video_id: 'v1',
        url: 'https://youtu.be/v1',
        title: 'Windowlicker',
        channel: 'Warp',
      }),
    );
    // the download endpoint must never be touched for an asks-first profile
    expect(downloads).toBe(0);
  });

  it('marks the video requested after a successful file', async () => {
    stubCanDownload(false);
    server.use(
      http.post('/api/requests/music/videos', () => HttpResponse.json({ success: true, id: 7 })),
    );

    const { result } = renderHook(() => useVideoDownloads());
    expect(result.current.requested.has('v1')).toBe(false);

    await act(async () => {
      await result.current.request(video());
    });

    await waitFor(() => expect(result.current.requested.has('v1')).toBe(true));
  });

  it('still downloads directly when the profile has download rights', async () => {
    stubCanDownload(true); // profileAsksFirst() === false
    let body: Record<string, unknown> | null = null;
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
    // a direct download files no request
    expect(result.current.requested.has('v1')).toBe(false);
  });
});
