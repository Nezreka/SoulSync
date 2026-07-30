import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import { activeResults, useSearchController } from './-search.use-controller';

/** A search handler you can settle per source, so races are observable. */
function deferredSearch() {
  const pending: { source: string; settle: (body: Record<string, unknown>) => void }[] = [];
  server.use(
    http.post('/api/enhanced-search', async ({ request }) => {
      const body = (await request.json()) as { source: string };
      return new Promise((resolve) => {
        pending.push({
          source: body.source,
          settle: (payload) => resolve(HttpResponse.json(payload)),
        });
      });
    }),
  );
  return pending;
}

function stubSearch(bySource: Record<string, unknown>) {
  const seen: string[] = [];
  server.use(
    http.post('/api/enhanced-search', async ({ request }) => {
      const body = (await request.json()) as { source: string };
      seen.push(body.source);
      return HttpResponse.json(bySource[body.source] ?? {});
    }),
  );
  return seen;
}

/**
 * Let pending promises and timers land.
 *
 * Wrapped in act() because a settling fetch writes state, and this hook has one
 * request in flight from the moment it mounts (config status) that no individual
 * test asked for.
 */
const flush = () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 10));
  });

describe('useSearchController', () => {
  // Every mount fetches config status. Left unhandled it is not a failure — the
  // catch swallows it — but MSW logs an error for each one and the real noise
  // hides a real miss. Tests that care register their own handler after this.
  beforeEach(() => {
    server.use(http.get('/api/settings/config-status', () => HttpResponse.json({})));
  });

  it('starts with every source usable, so no flash of dimmed icons', () => {
    const { result } = renderHook(() => useSearchController());
    expect(result.current.state.configuredSources.spotify).toBe(true);
    expect(result.current.state.configuredSources.deezer).toBe(true);
  });

  it('replaces the optimistic map with the real config status', async () => {
    server.use(
      http.get('/api/settings/config-status', () =>
        HttpResponse.json({ spotify: { configured: true }, deezer: { configured: false } }),
      ),
    );
    const { result } = renderHook(() => useSearchController());
    await waitFor(() => expect(result.current.state.configuredSources.deezer).toBe(false));
    expect(result.current.state.configuredSources.spotify).toBe(true);
  });

  it('fetches the active source and exposes its results', async () => {
    stubSearch({ spotify: { spotify_albums: [{ id: 'a1', name: 'Drukqs' }] } });
    const { result } = renderHook(() => useSearchController());

    act(() => result.current.submitQuery('aphex'));
    await waitFor(() => expect(activeResults(result.current.state).albums).toHaveLength(1));
  });

  it('serves a repeat query for the same source from cache, with no request', async () => {
    const seen = stubSearch({ spotify: { spotify_albums: [{ id: 'a1' }] } });
    const { result } = renderHook(() => useSearchController());

    act(() => result.current.submitQuery('aphex'));
    await waitFor(() => expect(activeResults(result.current.state).albums).toHaveLength(1));
    act(() => result.current.submitQuery('aphex'));
    await flush();

    expect(seen).toEqual(['spotify']);
  });

  it('empties the cache when the query changes', async () => {
    stubSearch({ spotify: { spotify_albums: [{ id: 'a1' }] } });
    const { result } = renderHook(() => useSearchController());

    act(() => result.current.submitQuery('aphex'));
    await waitFor(() => expect(activeResults(result.current.state).albums).toHaveLength(1));

    act(() => result.current.submitQuery('squarepusher'));
    // Synchronous: the previous query's results must not sit under the new one.
    expect(result.current.state.sources).toEqual({});
    expect(result.current.state.fallbacks).toEqual({});
  });

  it('FETCHES again for a new query, rather than reading the emptied cache', async () => {
    // Caught by mutation testing: `previous.sources` is the pre-change state, so
    // a cache check that ignores whether the query changed returns early and
    // the second search never fires at all.
    const seen = stubSearch({ spotify: { spotify_albums: [{ id: 'a1' }] } });
    const { result } = renderHook(() => useSearchController());

    act(() => result.current.submitQuery('aphex'));
    await waitFor(() => expect(seen).toEqual(['spotify']));

    act(() => result.current.submitQuery('squarepusher'));
    await waitFor(() => expect(seen).toEqual(['spotify', 'spotify']));
    await waitFor(() => expect(activeResults(result.current.state).albums).toHaveLength(1));
  });

  it('does not write a settle that lands after the query changed', async () => {
    // The tokens are DELETED on a query change, not just re-stamped, precisely
    // so this cannot repopulate the cache we just emptied.
    const pending = deferredSearch();
    const { result } = renderHook(() => useSearchController());

    act(() => result.current.submitQuery('aphex'));
    await waitFor(() => expect(pending).toHaveLength(1));

    act(() => result.current.submitQuery('squarepusher'));
    await act(async () => {
      pending[0].settle({ spotify_albums: [{ id: 'stale' }] });
      await flush();
    });

    expect(result.current.state.sources.spotify).toBeUndefined();
  });

  it('clears the spinner of a source that a DIFFERENT source superseded', async () => {
    // The reason the tokens are per-source: switching aborts spotify's fetch,
    // and spotify's own catch still has to clear its loadingSources entry.
    const pending = deferredSearch();
    const { result } = renderHook(() => useSearchController());

    act(() => result.current.submitQuery('aphex'));
    await waitFor(() => expect(result.current.state.loadingSources.has('spotify')).toBe(true));

    act(() => result.current.setActiveSource('deezer'));
    await waitFor(() => expect(result.current.state.loadingSources.has('deezer')).toBe(true));

    await act(async () => {
      pending[0].settle({ spotify_albums: [] });
      await flush();
    });
    // Spotify is no longer spinning, even though Deezer took over.
    expect(result.current.state.loadingSources.has('spotify')).toBe(false);
  });

  it('records a fallback when the server serves a different source', async () => {
    stubSearch({ spotify: { primary_source: 'deezer', spotify_albums: [] } });
    const { result } = renderHook(() => useSearchController());

    act(() => result.current.submitQuery('aphex'));
    await waitFor(() => expect(result.current.state.fallbacks.spotify).toBe('deezer'));
  });

  it('hands a soulseek query off instead of fetching', async () => {
    const onSoulseekSelected = vi.fn();
    const seen = stubSearch({});
    const { result } = renderHook(() => useSearchController({ onSoulseekSelected }));

    act(() => result.current.setActiveSource('soulseek'));
    act(() => result.current.submitQuery('aphex'));
    await flush();

    expect(onSoulseekSelected).toHaveBeenCalledWith('aphex');
    expect(seen).toEqual([]);
  });

  it('re-fires the handoff when soulseek is clicked while ALREADY active', async () => {
    // Deliberately not a no-op — clicking it again is how a basic search is
    // re-run.
    const onSoulseekSelected = vi.fn();
    // submitQuery runs while SPOTIFY is still active, so it really does fetch.
    // Stubbed and drained here rather than left in flight: an unstubbed request
    // settling after the test ends lands inside the NEXT one, as an unhandled
    // MSW request and an act() warning nowhere near its cause.
    stubSearch({ spotify: { spotify_albums: [] } });
    const { result } = renderHook(() => useSearchController({ onSoulseekSelected }));

    act(() => result.current.submitQuery('aphex'));
    act(() => result.current.setActiveSource('soulseek'));
    act(() => result.current.setActiveSource('soulseek'));

    expect(onSoulseekSelected).toHaveBeenCalledTimes(2);
    await act(async () => {
      await flush();
    });
  });

  it('fetches a newly-selected source but not one already cached', async () => {
    const seen = stubSearch({ spotify: { spotify_albums: [] }, deezer: { spotify_albums: [] } });
    const { result } = renderHook(() => useSearchController());

    act(() => result.current.submitQuery('aphex'));
    await waitFor(() => expect(seen).toEqual(['spotify']));

    act(() => result.current.setActiveSource('deezer'));
    await waitFor(() => expect(seen).toEqual(['spotify', 'deezer']));

    // Back to spotify — already cached, so nothing new goes out.
    act(() => result.current.setActiveSource('spotify'));
    await flush();
    expect(seen).toEqual(['spotify', 'deezer']);
  });

  it('does not fetch on source change when there is no query yet', async () => {
    const seen = stubSearch({ deezer: {} });
    const { result } = renderHook(() => useSearchController());
    act(() => result.current.setActiveSource('deezer'));
    await flush();
    expect(seen).toEqual([]);
  });

  it('fills the video grid progressively from the NDJSON stream', async () => {
    const encoder = new TextEncoder();
    const lines = [
      '{"type":"videos","data":[{"video_id":"v1"}]}\n',
      '{"type":"videos","data":[{"video_id":"v2"}]}\n',
    ];
    let i = 0;
    server.use(
      http.post('/api/enhanced-search/source/youtube_videos', () => {
        i = 0;
        return new Response(
          new ReadableStream({
            pull(controller) {
              if (i < lines.length) controller.enqueue(encoder.encode(lines[i++]));
              else controller.close();
            },
          }),
          { status: 200 },
        );
      }),
    );

    const { result } = renderHook(() => useSearchController());
    act(() => result.current.setActiveSource('youtube_videos'));
    act(() => result.current.submitQuery('aphex'));

    await waitFor(() => expect(activeResults(result.current.state).videos).toHaveLength(2));
  });

  it('adopts the resolved source when an id lookup seeds it', () => {
    // Replaces the vanilla's reach-in state mutation with an explicit seam.
    const { result } = renderHook(() => useSearchController());
    act(() =>
      result.current.seedFromIdLookup('770a1e6b-2d17-4bbe-a0c2-a3c4f77e9bce', {
        available: true,
        source: 'musicbrainz',
        albums: [{ id: 'mb1', name: 'Found' }],
      }),
    );

    expect(result.current.state.activeSource).toBe('musicbrainz');
    expect(activeResults(result.current.state).albums).toHaveLength(1);
    expect(result.current.state.loadingSources.size).toBe(0);
  });

  it('maps a spotify_free lookup onto the spotify icon', () => {
    // spotify_free has no icon of its own; leaving it unmapped shows a picker
    // with nothing active.
    const { result } = renderHook(() => useSearchController());
    act(() => result.current.seedFromIdLookup('x', { source: 'spotify_free', albums: [] }));
    expect(result.current.state.activeSource).toBe('spotify');
  });
});
