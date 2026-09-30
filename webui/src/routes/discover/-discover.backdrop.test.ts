import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import { backdropEmbedUrl, fetchBackdropVideo, vividAverage } from './-discover.backdrop';

describe('backdropEmbedUrl', () => {
  it('is muted, looping, chrome-less and cookie-less', () => {
    const url = new URL(backdropEmbedUrl('abc123'));
    expect(url.origin).toBe('https://www.youtube-nocookie.com');
    expect(url.pathname).toBe('/embed/abc123');
    const p = url.searchParams;
    expect([p.get('mute'), p.get('autoplay'), p.get('loop'), p.get('controls')]).toEqual([
      '1',
      '1',
      '1',
      '0',
    ]);
    // looping a single video needs itself as the playlist
    expect(p.get('playlist')).toBe('abc123');
  });
});

describe('vividAverage', () => {
  it('averages the vivid pixels and skips greys and near-blacks', () => {
    const px = [200, 40, 40, 255, 128, 128, 128, 255, 10, 10, 10, 255, 220, 60, 60, 255];
    expect(vividAverage(px)).toBe('210, 50, 50');
  });

  it('is null for a picture with no colour to take', () => {
    expect(vividAverage([128, 128, 128, 255, 5, 5, 5, 255])).toBeNull();
  });
});

describe('fetchBackdropVideo', () => {
  it('asks for the artist, and the release when there is one', async () => {
    let seen: URLSearchParams | null = null;
    server.use(
      http.get('*/api/discover/backdrop-video', ({ request }) => {
        seen = new URL(request.url).searchParams;
        return HttpResponse.json({ success: true, video_id: 'v1' });
      }),
    );
    expect((await fetchBackdropVideo('M83', 'Oceans Niagara')).video_id).toBe('v1');
    expect(seen!.get('artist')).toBe('M83');
    expect(seen!.get('title')).toBe('Oceans Niagara');
  });
});

describe('the hooks', () => {
  it('useBackdropVideoId waits until the banner has been seen', async () => {
    const { renderHook, waitFor } = await import('@testing-library/react');
    const { QueryClientProvider } = await import('@tanstack/react-query');
    const { createTestQueryClient } = await import('@/test/query-client');
    const { createElement } = await import('react');
    const { useBackdropVideoId } = await import('./-discover.backdrop');
    let calls = 0;
    server.use(
      http.get('*/api/discover/backdrop-video', () => {
        calls += 1;
        return HttpResponse.json({ success: true, video_id: 'v9' });
      }),
    );
    const client = createTestQueryClient();
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      createElement(QueryClientProvider, { client }, children);
    const { result, rerender } = renderHook(
      ({ wanted }) => useBackdropVideoId('Tool', null, wanted),
      { wrapper, initialProps: { wanted: false } },
    );
    expect(result.current).toBeNull();
    expect(calls).toBe(0);
    rerender({ wanted: true });
    await waitFor(() => expect(result.current).toBe('v9'));
    expect(calls).toBe(1);
  });

  it('useDominantColor has nothing to say without a picture', async () => {
    const { renderHook } = await import('@testing-library/react');
    const { useDominantColor } = await import('./-discover.backdrop');
    expect(renderHook(() => useDominantColor(null)).result.current).toBeNull();
  });
});
