import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useGapFill } from './-artist-detail.use-gap-fill';

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function sse(frames: string[]) {
  const encoder = new TextEncoder();
  let i = 0;
  return new Response(
    new ReadableStream({
      pull(controller) {
        if (i < frames.length) controller.enqueue(encoder.encode(frames[i++]));
        else controller.close();
      },
    }),
    { status: 200 },
  );
}

let requested: string[] = [];

function stubRoutes(routes: Record<string, () => Response>) {
  requested = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      requested.push(url);
      for (const [fragment, handler] of Object.entries(routes)) {
        if (url.includes(fragment)) return handler();
      }
      return json({ success: false });
    }),
  );
}

const GAPS = {
  success: true,
  gaps: { albums: [{ id: 'g1', title: 'Gap Album', gap_source: 'deezer', year: 2001 }] },
};

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('useGapFill', () => {
  it('fetches nothing while the chip is off', async () => {
    stubRoutes({ 'gap-fill': () => json(GAPS) });
    const { result } = renderHook(() => useGapFill(42, 'Aphex Twin', 'spotify', {}));

    await new Promise((r) => setTimeout(r, 20));
    expect(result.current.enabled).toBe(false);
    expect(result.current.releases).toEqual([]);
    expect(requested).toEqual([]);
  });

  it('loads and exposes gaps once enabled', async () => {
    stubRoutes({ 'gap-fill': () => json(GAPS) });
    const { result } = renderHook(() => useGapFill(42, 'Aphex Twin', 'spotify', {}));

    act(() => result.current.toggle());
    await waitFor(() => expect(result.current.releases).toHaveLength(1));
    expect(result.current.releases[0].title).toBe('Gap Album');
    expect(requested[0]).toContain('base_source=spotify');
  });

  it('persists the toggle so the next page load keeps it on', async () => {
    stubRoutes({ 'gap-fill': () => json({ success: true, gaps: {} }) });
    const { result } = renderHook(() => useGapFill(42, 'A', 'spotify', {}));
    act(() => result.current.toggle());
    expect(localStorage.getItem('discog_gapfill')).toBe('1');

    const second = renderHook(() => useGapFill(42, 'A', 'spotify', {}));
    expect(second.result.current.enabled).toBe(true);
  });

  it('drops gaps the page already renders', async () => {
    stubRoutes({ 'gap-fill': () => json(GAPS) });
    const { result } = renderHook(() =>
      useGapFill(42, 'A', 'spotify', { albums: [{ title: 'Gap Album', year: 2001 }] }),
    );

    act(() => result.current.toggle());
    await new Promise((r) => setTimeout(r, 20));
    expect(result.current.releases).toEqual([]);
    // Nothing survived the dedup, so no ownership stream is opened for it.
    expect(requested.some((u) => u.includes('completion-stream'))).toBe(false);
  });

  it('streams ownership for the gaps on their OWN sources', async () => {
    stubRoutes({
      'gap-fill': () => json(GAPS),
      'completion-stream': () =>
        sse(['data: {"type":"completion","id":"g1","category":"albums","status":"ok"}\n']),
    });
    const { result } = renderHook(() => useGapFill(42, 'Aphex Twin', 'spotify', {}));

    act(() => result.current.toggle());
    // #1071: an album bought on another platform must light up OWNED here too.
    await waitFor(() => expect(result.current.releases[0]?.owned).toBe(true));
  });

  it('clears the gaps when switched back off', async () => {
    stubRoutes({ 'gap-fill': () => json(GAPS) });
    const { result } = renderHook(() => useGapFill(42, 'A', 'spotify', {}));

    act(() => result.current.toggle());
    await waitFor(() => expect(result.current.releases).toHaveLength(1));
    act(() => result.current.toggle());
    expect(result.current.releases).toEqual([]);
  });

  it('leaves the page alone when the request fails', async () => {
    stubRoutes({ 'gap-fill': () => json({ success: false }, 500) });
    const { result } = renderHook(() => useGapFill(42, 'A', 'spotify', {}));

    act(() => result.current.toggle());
    await new Promise((r) => setTimeout(r, 20));
    expect(result.current.releases).toEqual([]);
  });

  it('aborts in flight when the artist changes', async () => {
    let aborted = false;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: unknown, init?: RequestInit) => {
        init?.signal?.addEventListener('abort', () => {
          aborted = true;
        });
        return new Promise<Response>(() => {});
      }),
    );
    localStorage.setItem('discog_gapfill', '1');
    const { rerender } = renderHook(({ id }) => useGapFill(id, 'A', 'spotify', {}), {
      initialProps: { id: 42 as unknown },
    });

    rerender({ id: 99 });
    // The vanilla used a request sequence number to ignore stale responses;
    // aborting stops the request itself.
    await waitFor(() => expect(aborted).toBe(true));
  });
});
