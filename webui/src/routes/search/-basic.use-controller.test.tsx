import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import { IDLE_STATUS, useBasicSearchController } from './-basic.use-controller';

let toasts: { message: string; type?: string }[] = [];

beforeEach(() => {
  toasts = [];
  window.showToast = vi.fn((message: string, type?: string) => {
    toasts.push({ message, type });
  });
  // Default: one source, so the picker is inert unless a test says otherwise.
  server.use(
    http.get('/api/search/sources', () =>
      HttpResponse.json({
        mode: 'soulseek',
        sources: [{ name: 'soulseek', display_name: 'Soulseek' }],
      }),
    ),
  );
});

afterEach(() => {
  delete window.showToast;
  delete window.currentSearchResults;
});

function hybridSources() {
  server.use(
    http.get('/api/search/sources', () =>
      HttpResponse.json({
        mode: 'hybrid',
        sources: [
          { name: 'soulseek', display_name: 'Soulseek' },
          { name: 'tidal', display_name: 'Tidal' },
        ],
      }),
    ),
  );
}

const trackRow = (over: Record<string, unknown> = {}) => ({
  result_type: 'track',
  username: 'peer',
  filename: 'a.flac',
  size: 1000,
  bitrate: 320,
  duration: 100,
  quality: 'flac',
  free_upload_slots: 1,
  upload_speed: 100,
  queue_length: 0,
  title: 'Xtal',
  artist: 'Aphex Twin',
  quality_score: 0.9,
  ...over,
});

const albumRow = (over: Record<string, unknown> = {}) => ({
  result_type: 'album',
  username: 'peer',
  album_path: '/p',
  album_title: 'SAW',
  artist: 'Aphex Twin',
  track_count: 1,
  total_size: 5000,
  tracks: [trackRow()],
  dominant_quality: 'flac',
  free_upload_slots: 1,
  upload_speed: 100,
  queue_length: 0,
  quality_score: 0.8,
  ...over,
});

/** Search handler you settle by hand, so in-flight state is observable. */
function deferredSearch() {
  const pending: { source?: string; settle: (body: Record<string, unknown>) => void }[] = [];
  server.use(
    http.post('/api/search', async ({ request }) => {
      const body = (await request.json()) as { source?: string };
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

function stubSearch(results: unknown[]) {
  const bodies: { query: string; source?: string }[] = [];
  server.use(
    http.post('/api/search', async ({ request }) => {
      bodies.push((await request.json()) as { query: string; source?: string });
      return HttpResponse.json({ results });
    }),
  );
  return bodies;
}

async function mounted() {
  const view = renderHook(() => useBasicSearchController());
  // The sources request fires on mount; let it land so no test races it.
  await waitFor(() => expect(view.result.current.state.sources.length).toBeGreaterThan(0));
  return view;
}

describe('initial state', () => {
  it('starts idle with the prompt the vanilla shipped', async () => {
    const { result } = await mounted();
    expect(result.current.state.status).toBe(IDLE_STATUS);
    expect(result.current.state.searching).toBe(false);
    expect(result.current.visible).toEqual([]);
  });

  it('hides the filter pills until a search finds something', async () => {
    const { result } = await mounted();
    expect(result.current.state.filtersVisible).toBe(false);
  });

  it('loads the source chips', async () => {
    hybridSources();
    const { result } = await mounted();
    expect(result.current.state.sources.map((s) => s.name)).toEqual(['soulseek', 'tidal']);
    expect(result.current.state.singleSource).toBe(false);
    expect(result.current.state.activeSource).toBe('soulseek');
  });

  it('leaves the source null in single-source mode', async () => {
    // Null is not "unset" — it is what makes the request omit `source` so the
    // server routes with the orchestrator's own selection.
    const { result } = await mounted();
    expect(result.current.state.singleSource).toBe(true);
    expect(result.current.state.activeSource).toBeNull();
  });
});

describe('search', () => {
  it('refuses an empty query with a toast and no request', async () => {
    const bodies = stubSearch([]);
    const { result } = await mounted();

    act(() => result.current.search('   '));

    expect(bodies).toEqual([]);
    expect(toasts).toEqual([{ message: 'Please enter a search term', type: 'error' }]);
    expect(result.current.state.searching).toBe(false);
  });

  it('trims the query before sending it', async () => {
    const bodies = stubSearch([]);
    const { result } = await mounted();

    await act(async () => result.current.search('  aphex  '));

    expect(bodies[0].query).toBe('aphex');
  });

  it('shows the searching state while in flight, and drops it after', async () => {
    const pending = deferredSearch();
    const { result } = await mounted();

    act(() => result.current.search('aphex'));
    await waitFor(() => expect(result.current.state.searching).toBe(true));
    expect(result.current.state.status).toBe("Searching for 'aphex'...");

    await act(async () => {
      pending[0].settle({ results: [trackRow()] });
    });
    expect(result.current.state.searching).toBe(false);
  });

  it('clears the previous results before the new ones land', async () => {
    // Leaving the old list up under a "Searching for..." status reads as though
    // the new search already answered.
    let pending = deferredSearch();
    const { result } = await mounted();

    act(() => result.current.search('aphex'));
    await waitFor(() => expect(pending).toHaveLength(1));
    await act(async () => pending[0].settle({ results: [trackRow()] }));
    await waitFor(() => expect(result.current.visible).toHaveLength(1));

    pending = deferredSearch();
    act(() => result.current.search('boards'));
    await waitFor(() => expect(result.current.visible).toEqual([]));
  });

  it('reports the album/single split', async () => {
    stubSearch([albumRow(), trackRow(), trackRow()]);
    const { result } = await mounted();

    await act(async () => result.current.search('aphex'));

    expect(result.current.state.status).toBe('✨ Found 3 results • 1 albums, 2 singles');
    expect(toasts).toContainEqual({ message: 'Found 3 results', type: 'success' });
  });

  it('names the query when nothing is found', async () => {
    stubSearch([]);
    const { result } = await mounted();

    await act(async () => result.current.search('zzzz'));

    expect(result.current.state.status).toBe("No results found for 'zzzz'");
    expect(toasts).toContainEqual({ message: 'No results found', type: 'error' });
    expect(result.current.state.filtersVisible).toBe(false);
  });

  it('reveals the filter pills once something is found', async () => {
    stubSearch([trackRow()]);
    const { result } = await mounted();

    await act(async () => result.current.search('aphex'));

    expect(result.current.state.filtersVisible).toBe(true);
  });

  it('keeps the pills visible when a later search finds nothing', async () => {
    // Taking the pills away would strand a user whose filter is what emptied
    // the list, with no control left to undo it.
    const { result } = await mounted();
    stubSearch([trackRow()]);
    await act(async () => result.current.search('aphex'));

    stubSearch([]);
    await act(async () => result.current.search('zzzz'));

    expect(result.current.state.filtersVisible).toBe(true);
  });

  it('surfaces a server error message in the status bar', async () => {
    server.use(
      http.post('/api/search', () => HttpResponse.json({ error: "SoundCloud isn't connected" })),
    );
    const { result } = await mounted();

    await act(async () => result.current.search('https://soundcloud.com/x'));

    expect(result.current.state.status).toBe("Search failed: SoundCloud isn't connected");
    expect(toasts).toContainEqual({ message: 'Search failed', type: 'error' });
    expect(result.current.state.searching).toBe(false);
  });

  it('recovers from a transport failure', async () => {
    server.use(http.post('/api/search', () => HttpResponse.error()));
    const { result } = await mounted();

    await act(async () => result.current.search('aphex'));

    expect(result.current.state.status).toMatch(/^Search failed: /);
    expect(result.current.state.searching).toBe(false);
  });

  it('resets the filters on every search', async () => {
    // A format filter left over from the last search would silently hide the
    // new one's results — `resetFilters()` ran before every render.
    const { result } = await mounted();
    stubSearch([trackRow()]);
    await act(async () => result.current.search('aphex'));

    act(() => result.current.setFilters({ format: 'mp3', type: 'album' }));
    act(() => result.current.toggleSortOrder());
    expect(result.current.state.filters.format).toBe('mp3');

    await act(async () => result.current.search('boards'));

    expect(result.current.state.filters).toEqual({
      type: 'all',
      format: 'all',
      sort: 'quality_score',
      reversed: false,
    });
  });
});

describe('cancel', () => {
  it('reports the cancellation once and clears the results', async () => {
    const pending = deferredSearch();
    const { result } = await mounted();

    act(() => result.current.search('aphex'));
    await waitFor(() => expect(pending).toHaveLength(1));

    await act(async () => {
      result.current.cancel();
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.state.status).toBe('Search was cancelled.'));
    expect(result.current.visible).toEqual([]);
    expect(result.current.state.searching).toBe(false);
    // Exactly one — the abort handler owns the message, so cancel() must not
    // also write it.
    expect(toasts.filter((t) => t.message === 'Search cancelled')).toHaveLength(1);
  });

  it('does nothing when there is no search in flight', async () => {
    const { result } = await mounted();
    act(() => result.current.cancel());
    expect(result.current.state.status).toBe(IDLE_STATUS);
    expect(toasts).toEqual([]);
  });

  it('does not let an aborted search overwrite the one that replaced it', async () => {
    // The abort rejects asynchronously, so its catch can run AFTER the next
    // search is already showing "Searching for...". Without the generation
    // guard it would stamp "Search was cancelled." over a live search.
    const pending = deferredSearch();
    const { result } = await mounted();

    act(() => result.current.search('first'));
    await waitFor(() => expect(pending).toHaveLength(1));

    act(() => result.current.search('second'));
    await waitFor(() => expect(result.current.state.query).toBe('second'));

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.state.status).toBe("Searching for 'second'...");
    expect(result.current.state.searching).toBe(true);
  });
});

describe('filters', () => {
  it('filters and sorts what the page renders, leaving the raw results alone', async () => {
    stubSearch([albumRow(), trackRow()]);
    const { result } = await mounted();
    await act(async () => result.current.search('aphex'));

    act(() => result.current.setFilters({ type: 'album' }));

    expect(result.current.visible).toHaveLength(1);
    expect(result.current.state.results).toHaveLength(2);
  });

  it('toggles the sort direction', async () => {
    stubSearch([
      trackRow({ title: 'low', quality_score: 0.1 }),
      trackRow({ title: 'high', quality_score: 0.9 }),
    ]);
    const { result } = await mounted();
    await act(async () => result.current.search('aphex'));

    expect(result.current.visible.map((r) => (r as { title: string }).title)).toEqual([
      'high',
      'low',
    ]);

    act(() => result.current.toggleSortOrder());

    expect(result.current.visible.map((r) => (r as { title: string }).title)).toEqual([
      'low',
      'high',
    ]);
  });
});

describe('source picker', () => {
  it('sends the chosen source and re-runs the query', async () => {
    hybridSources();
    const { result } = await mounted();
    let bodies = stubSearch([trackRow()]);
    await act(async () => result.current.search('aphex'));
    expect(bodies[0].source).toBe('soulseek');

    bodies = stubSearch([trackRow()]);
    await act(async () => result.current.selectSource('tidal'));

    expect(result.current.state.activeSource).toBe('tidal');
    expect(bodies).toEqual([{ query: 'aphex', source: 'tidal' }]);
  });

  it('arms the picker without searching when nothing is on screen yet', async () => {
    // Switching source before any search must not fire one the user never
    // asked for (downloads.js:4319 gates on query AND existing results).
    hybridSources();
    const bodies = stubSearch([trackRow()]);
    const { result } = await mounted();

    await act(async () => result.current.selectSource('tidal'));

    expect(bodies).toEqual([]);
    expect(result.current.state.activeSource).toBe('tidal');
  });

  it('ignores a re-click on the source already active', async () => {
    hybridSources();
    const { result } = await mounted();
    await act(async () => result.current.search('aphex'));

    const bodies = stubSearch([trackRow()]);
    await act(async () => result.current.selectSource('soulseek'));

    expect(bodies).toEqual([]);
  });

  it('ignores a pick in single-source mode', async () => {
    const bodies = stubSearch([trackRow()]);
    const { result } = await mounted();

    await act(async () => result.current.selectSource('tidal'));

    expect(result.current.state.activeSource).toBeNull();
    expect(bodies).toEqual([]);
  });

  it('omits the source entirely in single-source mode', async () => {
    const bodies = stubSearch([]);
    const { result } = await mounted();

    await act(async () => result.current.search('aphex'));

    expect(bodies).toEqual([{ query: 'aphex' }]);
  });

  it('survives an unreachable sources endpoint', async () => {
    server.use(http.get('/api/search/sources', () => HttpResponse.error()));
    const bodies = stubSearch([trackRow()]);
    const { result } = renderHook(() => useBasicSearchController());

    await act(async () => result.current.search('aphex'));

    expect(result.current.visible).toHaveLength(1);
    expect(bodies[0]).toEqual({ query: 'aphex' });
  });
});

describe('window.currentSearchResults', () => {
  // The vanilla matched-download modal reads this by index, and skipMatching
  // reads it by indexOf on the object — so it has to be the rendered array,
  // with the rendered order and the same object references.
  it('publishes what is on screen, by identity', async () => {
    stubSearch([albumRow(), trackRow()]);
    const { result } = await mounted();

    await act(async () => result.current.search('aphex'));

    expect(window.currentSearchResults).toBe(result.current.visible);
    expect(window.currentSearchResults?.[0]).toBe(result.current.visible[0]);
  });

  it('follows a filter change, so indices keep matching the rendered rows', async () => {
    stubSearch([albumRow(), trackRow()]);
    const { result } = await mounted();
    await act(async () => result.current.search('aphex'));

    act(() => result.current.setFilters({ type: 'track' }));

    expect(window.currentSearchResults).toHaveLength(1);
    expect(window.currentSearchResults?.[0]).toBe(result.current.visible[0]);
  });
});
