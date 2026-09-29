import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { createElement, type ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { usePromoVideo } from './-discover.use-promo';

let fire: (ratio: number) => void = () => {};
class FakeObserver {
  constructor(cb: (entries: { intersectionRatio: number }[]) => void) {
    fire = (ratio) => cb([{ intersectionRatio: ratio }]);
  }
  observe() {}
  disconnect() {}
}

afterEach(() => vi.unstubAllGlobals());

function mount(artist: string | null) {
  const client = createTestQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children);
  return renderHook(() => usePromoVideo(`promo-test-${artist}`, artist, null, null, true), {
    wrapper,
  });
}

describe('usePromoVideo', () => {
  it('looks its video up once seen, then plays it, and drops one youtube refuses', async () => {
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    let calls = 0;
    server.use(
      http.get('*/api/discover/backdrop-video', () => {
        calls += 1;
        return HttpResponse.json({ success: true, video_id: 'v1' });
      }),
    );
    const { result } = mount('Tool');
    expect(calls).toBe(0);
    act(() => result.current.ref(document.createElement('section')));
    act(() => fire(0.9));
    await waitFor(() => expect(result.current.videoId).toBe('v1'));
    expect(result.current.playing).toBe(true);
    // pointing at it is fine while it already holds the stage
    act(() => result.current.setHover(true));
    expect(result.current.playing).toBe(true);
    act(() => result.current.onUnplayable('v1'));
    expect(result.current.videoId).toBeNull();
    expect(result.current.playing).toBe(false);
    expect(calls).toBe(1);
  });
});
