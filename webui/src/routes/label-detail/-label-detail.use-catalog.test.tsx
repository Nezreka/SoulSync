import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import { releaseKey } from './-label-detail.helpers';
import { useLabelCatalog } from './-label-detail.use-catalog';

const release = (album: string, artist = 'A') => ({ album, artist, year: '2020' });

function stubCatalog(pages: Record<number, unknown>) {
  const seen: number[] = [];
  server.use(
    http.get('/api/labels/:id/catalog', ({ request }) => {
      const page = Number(new URL(request.url).searchParams.get('page') || 1);
      seen.push(page);
      return HttpResponse.json(pages[page] ?? { releases: [] });
    }),
  );
  return seen;
}

function stubOwnership(flags: boolean[] | (() => Response)) {
  const bodies: { albums: { name: string; artist: string }[] }[] = [];
  server.use(
    http.post('/api/enhanced-search/library-check', async ({ request }) => {
      if (typeof flags === 'function') return flags();
      bodies.push((await request.json()) as { albums: { name: string; artist: string }[] });
      return HttpResponse.json({ albums: flags });
    }),
  );
  return bodies;
}

describe('useLabelCatalog', () => {
  it('takes its identity from the FIRST page only', async () => {
    stubCatalog({
      1: {
        label: { name: 'Warp Records' },
        total: 90,
        artist_count: 12,
        is_watching: true,
        backlog: true,
        has_more: true,
        releases: [release('One')],
      },
      2: { label: { name: 'WRONG' }, total: 0, artist_count: 0, releases: [release('Two')] },
    });
    stubOwnership([]);

    const { result } = renderHook(() => useLabelCatalog('mb-1', ''));
    await waitFor(() => expect(result.current.releases).toHaveLength(1));
    expect(result.current.name).toBe('Warp Records');
    expect(result.current.total).toBe(90);
    expect(result.current.watch).toEqual({ watching: true, backlog: true });

    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.releases).toHaveLength(2));
    // Page 2's label block must not overwrite the heading or the totals.
    expect(result.current.name).toBe('Warp Records');
    expect(result.current.total).toBe(90);
  });

  it('falls back to the passed name, then to Label', async () => {
    stubCatalog({ 1: { releases: [] } });
    stubOwnership([]);

    const { result } = renderHook(() => useLabelCatalog('mb-1', 'From Search'));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.name).toBe('From Search');

    const bare = renderHook(() => useLabelCatalog('mb-2', ''));
    await waitFor(() => expect(bare.result.current.loading).toBe(false));
    expect(bare.result.current.name).toBe('Label');
  });

  it('appends later pages rather than replacing', async () => {
    stubCatalog({
      1: { has_more: true, releases: [release('One')] },
      2: { has_more: false, releases: [release('Two')] },
    });
    stubOwnership([]);

    const { result } = renderHook(() => useLabelCatalog('mb-1', ''));
    await waitFor(() => expect(result.current.hasMore).toBe(true));
    act(() => result.current.loadMore());
    await waitFor(() =>
      expect(result.current.releases.map((r) => r.album)).toEqual(['One', 'Two']),
    );
    expect(result.current.hasMore).toBe(false);
  });

  it('checks ownership only for releases it has not checked before', async () => {
    stubCatalog({
      1: { has_more: true, releases: [release('One')] },
      2: { has_more: false, releases: [release('One'), release('Two')] },
    });
    const bodies = stubOwnership([true]);

    const { result } = renderHook(() => useLabelCatalog('mb-1', ''));
    await waitFor(() => expect(result.current.owned.size).toBe(1));

    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.releases).toHaveLength(3));
    await waitFor(() => expect(bodies).toHaveLength(2));
    // The duplicate 'One' is not re-asked; only the genuinely new 'Two' is.
    expect(bodies[1].albums.map((a) => a.name)).toEqual(['Two']);
  });

  it('marks releases checked only once the answer is in', async () => {
    // Marking them when the request goes OUT would paint "Missing" across the
    // whole grid for as long as the check takes.
    stubCatalog({ 1: { releases: [release('One')] } });
    stubOwnership([false]);

    const { result } = renderHook(() => useLabelCatalog('mb-1', ''));
    await waitFor(() => expect(result.current.releases).toHaveLength(1));
    await waitFor(() => expect(result.current.checked.has(releaseKey(release('One')))).toBe(true));
  });

  it('errors on a failed FIRST page', async () => {
    server.use(http.get('/api/labels/:id/catalog', () => HttpResponse.error()));
    const { result } = renderHook(() => useLabelCatalog('mb-1', ''));
    await waitFor(() => expect(result.current.error).toBe(true));
  });

  it('keeps the loaded grid when a LATER page fails', async () => {
    let calls = 0;
    server.use(
      http.get('/api/labels/:id/catalog', () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ has_more: true, releases: [release('One')] })
          : HttpResponse.error();
      }),
    );
    stubOwnership([]);

    const { result } = renderHook(() => useLabelCatalog('mb-1', ''));
    await waitFor(() => expect(result.current.releases).toHaveLength(1));
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.loading).toBe(false));
    // Still on screen, and NOT flipped into the error state.
    expect(result.current.releases).toHaveLength(1);
    expect(result.current.error).toBe(false);
  });

  it('does not page past the end', async () => {
    const seen = stubCatalog({ 1: { has_more: false, releases: [release('One')] } });
    stubOwnership([]);

    const { result } = renderHook(() => useLabelCatalog('mb-1', ''));
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.loadMore());
    act(() => result.current.loadMore());
    await new Promise((r) => setTimeout(r, 20));
    expect(seen).toEqual([1]);
  });

  it('clears the previous label before the new one loads', async () => {
    stubCatalog({ 1: { releases: [release('One')] } });
    stubOwnership([]);

    const { result, rerender } = renderHook(({ id }) => useLabelCatalog(id, ''), {
      initialProps: { id: 'mb-1' },
    });
    await waitFor(() => expect(result.current.releases).toHaveLength(1));

    rerender({ id: 'mb-2' });
    // Synchronous: the old label's releases must not render under the new
    // label's heading, not even for one frame.
    expect(result.current.releases).toEqual([]);
    expect(result.current.total).toBe(0);
  });
});
