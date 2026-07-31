import { QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { DISCOVER_REQUEST_LIMIT, discoverLimiter } from './-discover.limiter';
import { useDiscoverPage } from './-discover.use-page';

/** Endpoints the above-the-fold tier hits. */
const ABOVE_FOLD = [
  '/api/discover/hero',
  '/api/discover/adventurousness',
  '/api/discover/genre-explorer',
  '/api/discover/listening-recommendations',
  '/api/discover/similar-artists',
  '/api/discover/personalized/popular-picks',
  '/api/discover/personalized/hidden-gems',
  '/api/discover/personalized/discovery-shuffle',
  '/api/discover/personalized/listening-mix',
  '/api/discover/recent-releases',
  '/api/discover/genre-new-releases',
];

/** Endpoints only the below-the-fold tier hits. */
const BELOW_FOLD = [
  '/api/discover/seasonal/current',
  '/api/discover/undiscovered-albums',
  '/api/discover/label-explorer',
  '/api/discover/your-albums',
  '/api/discover/your-artists',
  '/api/discover/deep-cuts',
  '/api/discover/decades/available',
];

let hits: string[] = [];
/** Gate that holds every above-the-fold response until released. */
let releaseAboveFold: () => void = () => {};

function stub({ holdAboveFold = false } = {}) {
  hits = [];
  const gate = new Promise<void>((resolve) => {
    releaseAboveFold = resolve;
  });
  server.use(
    ...ABOVE_FOLD.map((path) =>
      http.get(path, async () => {
        hits.push(path);
        if (holdAboveFold) await gate;
        return HttpResponse.json({
          success: true,
          artists: [],
          tracks: [],
          albums: [],
          genres: [],
        });
      }),
    ),
    ...BELOW_FOLD.map((path) =>
      http.get(path, () => {
        hits.push(path);
        return HttpResponse.json({
          success: true,
          albums: [],
          artists: [],
          tracks: [],
          decades: [],
        });
      }),
    ),
  );
}

function render() {
  const queryClient = createTestQueryClient();
  return renderHook(() => useDiscoverPage(), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
}

beforeEach(() => {
  stub();
});

afterEach(() => {
  // The limiter is a module-level singleton, so anything it still has queued
  // would otherwise fire AFTER this test tore its MSW handlers down and land
  // as an "unhandled request" against the next test.
  discoverLimiter.reset();
});

describe('the load tiering', () => {
  it('does not touch a below-the-fold endpoint until the top has settled', async () => {
    // The whole point: the top of the page is usable in a couple of seconds
    // instead of after the full ~20-request storm.
    stub({ holdAboveFold: true });
    const { result } = render();

    await waitFor(() => expect(hits.length).toBeGreaterThan(0));
    expect(result.current.aboveFoldSettled).toBe(false);
    expect(hits.filter((h) => BELOW_FOLD.includes(h))).toEqual([]);

    releaseAboveFold();
    await waitFor(() => expect(result.current.aboveFoldSettled).toBe(true));
    await waitFor(() =>
      expect(hits.filter((h) => BELOW_FOLD.includes(h)).length).toBe(BELOW_FOLD.length),
    );
  });

  it('eventually loads both tiers', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.aboveFoldSettled).toBe(true));
    await waitFor(() => {
      for (const path of [...ABOVE_FOLD, ...BELOW_FOLD]) expect(hits).toContain(path);
    });
  });

  it('releases the second tier even when the first tier FAILS', async () => {
    // Settled, not successful. Gating on success would strand the bottom half
    // of the page forever behind one dead endpoint — the opposite of the
    // vanilla's fail-soft behaviour.
    server.use(...ABOVE_FOLD.map((path) => http.get(path, () => HttpResponse.error())));
    const { result } = render();
    await waitFor(() => expect(result.current.aboveFoldSettled).toBe(true));
    await waitFor(() =>
      expect(hits.filter((h) => BELOW_FOLD.includes(h)).length).toBeGreaterThan(0),
    );
  });

  it('never has more than the pool size in flight at once', async () => {
    // Proves the hook actually goes THROUGH the limiter rather than merely
    // importing it. Without this, removing `discoverLimiter.run` from
    // shelfQuery passes the whole suite — it did, until this test existed.
    //
    // Tier 1 alone is 11 queries. Unlimited, all 11 land on Flask together,
    // which is the contention the vanilla's pool was added to prevent.
    let inFlight = 0;
    let peak = 0;
    const hold: (() => void)[] = [];
    server.use(
      ...[...ABOVE_FOLD, ...BELOW_FOLD].map((path) =>
        http.get(path, async () => {
          inFlight++;
          peak = Math.max(peak, inFlight);
          await new Promise<void>((resolve) => hold.push(resolve));
          inFlight--;
          return HttpResponse.json({ success: true, artists: [], tracks: [], albums: [] });
        }),
      ),
    );

    render();
    await waitFor(() => expect(hold.length).toBeGreaterThan(0));
    // Let every queued request that COULD start, start.
    await new Promise((r) => setTimeout(r, 50));
    expect(peak).toBeLessThanOrEqual(DISCOVER_REQUEST_LIMIT);

    hold.forEach((release) => release());
  });

  it('survives every endpoint failing', async () => {
    server.use(
      ...[...ABOVE_FOLD, ...BELOW_FOLD].map((p) => http.get(p, () => HttpResponse.error())),
    );
    const { result } = render();
    await waitFor(() => expect(result.current.aboveFoldSettled).toBe(true));
    expect(result.current.hero.data).toEqual({ success: false, artists: [] });
  });
});

describe('hasContent drives which sections render', () => {
  it('reports nothing for empty shelves', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.aboveFoldSettled).toBe(true));
    expect(result.current.hasContent('cache-genre-explorer')).toBe(false);
    expect(result.current.hasContent('listening-recs-section')).toBe(false);
    expect(result.current.hasContent('your-albums-section')).toBe(false);
  });

  it('always renders the dial, which is a control rather than a shelf', async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.aboveFoldSettled).toBe(true));
    expect(result.current.hasContent('adv-wave')).toBe(true);
  });

  it('reports content once a shelf returns rows', async () => {
    server.use(
      http.get('/api/discover/genre-explorer', () =>
        HttpResponse.json({ success: true, genres: ['techno'] }),
      ),
      http.get('/api/discover/listening-recommendations', () =>
        HttpResponse.json({ success: true, artists: [{ name: 'Aphex Twin' }] }),
      ),
    );
    const { result } = render();
    await waitFor(() => expect(result.current.hasContent('cache-genre-explorer')).toBe(true));
    await waitFor(() => expect(result.current.hasContent('listening-recs-section')).toBe(true));
  });

  it('treats the your-mixes shelf as present if ANY of its three sources has rows', async () => {
    // It is one section fed by three endpoints; requiring all three would hide
    // it whenever one happened to be empty.
    server.use(
      http.get('/api/discover/personalized/hidden-gems', () =>
        HttpResponse.json({ success: true, tracks: [{ track_name: 'A' }] }),
      ),
    );
    const { result } = render();
    await waitFor(() => expect(result.current.hasContent('your-mixes-section')).toBe(true));
  });

  it('loads the dial value, which the vanilla put above the fold', async () => {
    // hasContent('adv-wave') is always true, so the dial always renders — which
    // means its saved value has to be fetched or it renders at a default and
    // then snaps. It is the vanilla's 11th above-fold loader.
    server.use(
      http.get('/api/discover/adventurousness', () =>
        HttpResponse.json({ success: true, value: 0.42 }),
      ),
    );
    const { result } = render();
    await waitFor(() =>
      expect(
        (result.current.sections.adventurousness.data as { value?: number } | undefined)?.value,
      ).toBe(0.42),
    );
  });

  it('accepts a dial value of 0, which is a real setting', async () => {
    // Least adventurous. The vanilla guarded with `typeof value === 'number'`
    // precisely so a falsy check could not drop it.
    server.use(
      http.get('/api/discover/adventurousness', () =>
        HttpResponse.json({ success: true, value: 0 }),
      ),
    );
    const { result } = render();
    await waitFor(() => expect(result.current.aboveFoldSettled).toBe(true));
    expect(
      (result.current.sections.adventurousness.data as { value?: number } | undefined)?.value,
    ).toBe(0);
  });

  it('reports false for sections whose phases have not landed yet', async () => {
    // Better an absent section than an empty frame.
    const { result } = render();
    await waitFor(() => expect(result.current.aboveFoldSettled).toBe(true));
    for (const id of ['lastfm-radio', 'listenbrainz', 'build-a-playlist'] as const) {
      expect(result.current.hasContent(id)).toBe(false);
    }
  });
});
