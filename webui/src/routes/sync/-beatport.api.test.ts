/**
 * The Beatport wire layer, pinned against beatport-ui.js.
 *
 * The URLs are asserted as literals because they are the only contract between
 * this file and the backend, and a typo in one produces a silently empty
 * section rather than an error.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  VANILLA_ABORTED_ENDPOINTS,
  enrichBeatportTracks,
  extractBeatportChart,
  fetchBeatportDJCharts,
  fetchBeatportEnrichProgress,
  fetchBeatportFeaturedCharts,
  fetchBeatportGenreHero,
  fetchBeatportGenreImage,
  fetchBeatportGenreTop10Lists,
  fetchBeatportGenreTop10Releases,
  fetchBeatportGenreTracks,
  fetchBeatportGenres,
  fetchBeatportHeroTracks,
  fetchBeatportHypePicks,
  fetchBeatportNewReleases,
  fetchBeatportReleaseMetadata,
  fetchBeatportTop100,
  fetchBeatportTop10Lists,
  fetchBeatportTop10Releases,
  pollBeatportEnrichment,
  startBeatportEnrichment,
} from './-beatport.api';

afterEach(() => {
  vi.unstubAllGlobals();
});

function stub(body: unknown, init?: { ok?: boolean; status?: number }) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, requestInit?: RequestInit) => {
      calls.push({ url, init: requestInit });
      return new Response(JSON.stringify(body), { status: init?.ok === false ? 500 : 200 });
    }),
  );
  return calls;
}

describe('the seven homepage loads', () => {
  it('hit the URLs the vanilla hits', async () => {
    const calls = stub({ success: true });
    await fetchBeatportHeroTracks();
    await fetchBeatportNewReleases();
    await fetchBeatportHypePicks();
    await fetchBeatportFeaturedCharts();
    await fetchBeatportDJCharts();
    await fetchBeatportTop10Lists();
    await fetchBeatportTop10Releases();
    expect(calls.map((c) => c.url)).toEqual([
      '/api/beatport/hero-tracks',
      '/api/beatport/new-releases',
      '/api/beatport/hype-picks',
      '/api/beatport/featured-charts',
      '/api/beatport/dj-charts',
      '/api/beatport/homepage/top-10-lists',
      '/api/beatport/homepage/top-10-releases-cards',
    ]);
  });

  it('pass the abort signal through when given one', async () => {
    const calls = stub({ success: true });
    const controller = new AbortController();
    await fetchBeatportHeroTracks(controller.signal);
    expect(calls[0].init?.signal).toBe(controller.signal);
  });

  it('omit the init object entirely when there is no signal', async () => {
    // The vanilla writes `signal ? { signal } : undefined`, so an absent signal
    // means a bare fetch(url) — not fetch(url, { signal: undefined }).
    const calls = stub({ success: true });
    await fetchBeatportHeroTracks();
    expect(calls[0].init).toBeUndefined();
  });
});

describe('VANILLA_ABORTED_ENDPOINTS', () => {
  it('is exactly the seven the vanilla cancels, and they are the seven above', async () => {
    const calls = stub({ success: true });
    const controller = new AbortController();
    await fetchBeatportHeroTracks(controller.signal);
    await fetchBeatportNewReleases(controller.signal);
    await fetchBeatportHypePicks(controller.signal);
    await fetchBeatportFeaturedCharts(controller.signal);
    await fetchBeatportDJCharts(controller.signal);
    await fetchBeatportTop10Lists(controller.signal);
    await fetchBeatportTop10Releases(controller.signal);
    // The list is the checkable form of the file header: if a homepage loader
    // is ever added or renamed, this pins the two together.
    expect(calls.map((c) => c.url)).toEqual([...VANILLA_ABORTED_ENDPOINTS]);
  });

  it('excludes everything that leads to a download', () => {
    // Cancelling a scrape because the user changed tab would be a behaviour
    // change, not a fix — so these must stay off the list.
    for (const url of [
      '/api/beatport/top-100?enrich=false',
      '/api/beatport/chart/extract',
      '/api/beatport/release-metadata',
      '/api/beatport/enrich-tracks',
    ]) {
      expect(VANILLA_ABORTED_ENDPOINTS).not.toContain(url);
    }
  });
});

describe('the chart and download endpoints', () => {
  it('asks for the two Top 100s unenriched', async () => {
    const calls = stub({ success: true });
    await fetchBeatportTop100('beatport');
    await fetchBeatportTop100('hype');
    expect(calls.map((c) => c.url)).toEqual([
      '/api/beatport/top-100?enrich=false',
      '/api/beatport/hype-top-100?enrich=false',
    ]);
  });

  it('posts a chart extraction with the fixed limit and enrich flag', async () => {
    const calls = stub({ success: true });
    await extractBeatportChart('http://chart', 'Featured Chart: X');
    expect(calls[0].url).toBe('/api/beatport/chart/extract');
    expect(calls[0].init?.method).toBe('POST');
    expect(JSON.parse(calls[0].init?.body as string)).toEqual({
      chart_url: 'http://chart',
      chart_name: 'Featured Chart: X',
      limit: 100,
      enrich: false,
    });
  });

  it('posts a release metadata lookup', async () => {
    const calls = stub({ success: true });
    await fetchBeatportReleaseMetadata('http://release');
    expect(calls[0].url).toBe('/api/beatport/release-metadata');
    expect(JSON.parse(calls[0].init?.body as string)).toEqual({ release_url: 'http://release' });
  });
});

describe('the genre endpoints', () => {
  it('hit the URLs the vanilla hits', async () => {
    const calls = stub({ success: true });
    await fetchBeatportGenreHero('tech-house', 11);
    await fetchBeatportGenreTop10Lists('tech-house', 11);
    await fetchBeatportGenreTop10Releases('tech-house', 11);
    await fetchBeatportGenreTracks('tech-house', 11);
    await fetchBeatportGenreImage('tech-house', 11);
    expect(calls.map((c) => c.url)).toEqual([
      '/api/beatport/genre/tech-house/11/hero',
      '/api/beatport/genre/tech-house/11/top-10-lists',
      '/api/beatport/genre/tech-house/11/top-10-releases',
      '/api/beatport/genre/tech-house/11/tracks?enrich=false',
      '/api/beatport/genre-image/tech-house/11',
    ]);
  });

  it('throws on a non-ok genre list, which the vanilla checks explicitly', async () => {
    stub({ genres: [] }, { ok: false });
    await expect(fetchBeatportGenres()).rejects.toThrow(/API returned 500/);
  });

  it('treats a non-ok genre IMAGE as simply no image', async () => {
    // 2552: the card keeps its emoji rather than the whole load failing.
    stub({ image_url: 'x' }, { ok: false });
    await expect(fetchBeatportGenreImage('a', 1)).resolves.toBeNull();
  });
});

describe('enrichment', () => {
  const TRACKS = [{ title: 'A' }, { title: 'B' }];

  it('posts the tracks with the enrichment id', async () => {
    const calls = stub({ success: true, tracks: TRACKS });
    await startBeatportEnrichment(TRACKS, 'enr_1');
    expect(calls[0].url).toBe('/api/beatport/enrich-tracks');
    expect(JSON.parse(calls[0].init?.body as string)).toEqual({
      tracks: TRACKS,
      enrichment_id: 'enr_1',
    });
  });

  it('carries the vanilla cache-buster on the progress poll', async () => {
    const calls = stub({ success: true });
    await fetchBeatportEnrichProgress('enr_1', 1234);
    expect(calls[0].url).toBe('/api/beatport/enrich-progress/enr_1?_=1234');
  });

  it('takes the synchronous answer when everything was cached', async () => {
    const enriched = [{ title: 'A+' }];
    const calls = stub({ success: true, tracks: enriched });
    const out = await enrichBeatportTracks(TRACKS, 'enr_1', () => 1);
    expect(out).toEqual(enriched);
    // No poll: one request only.
    expect(calls).toHaveLength(1);
  });

  it('returns the ORIGINALS when the start call fails outright', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('offline');
      }),
    );
    // The contract that matters: a failed enrichment must still let the
    // download proceed with what was scraped.
    await expect(enrichBeatportTracks(TRACKS, 'enr_1', () => 1)).resolves.toEqual(TRACKS);
  });

  it('returns the originals when the backend reports no success and no async', async () => {
    stub({ success: false });
    await expect(enrichBeatportTracks(TRACKS, 'enr_1', () => 1)).resolves.toEqual(TRACKS);
  });

  it('polls to completion and reports progress on the way', async () => {
    const seen: number[] = [];
    let call = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        call++;
        if (call === 1)
          return new Response(JSON.stringify({ success: true, completed: 1, total: 2 }));
        return new Response(
          JSON.stringify({ success: true, done: true, tracks: [{ title: 'A+' }] }),
        );
      }),
    );
    const out = await pollBeatportEnrichment('enr_1', TRACKS, () => 1, {
      sleep: async () => {},
      onProgress: (p) => seen.push(p.completed ?? -1),
    });
    expect(out).toEqual([{ title: 'A+' }]);
    // TWO updates, not one. 1963-1969 paints the overlay BEFORE testing `done`,
    // so the final poll emits as well — carrying undefined counts, since a
    // finished job reports no completed/total. Transcribed rather than tidied:
    // whatever renders this has to tolerate the blank.
    expect(seen).toEqual([1, -1]);
  });

  it('returns the originals when done arrives with no tracks', async () => {
    stub({ success: true, done: true });
    await expect(
      pollBeatportEnrichment('enr_1', TRACKS, () => 1, { sleep: async () => {} }),
    ).resolves.toEqual(TRACKS);
  });

  it('stops on a well-formed not-successful answer (1961)', async () => {
    const calls = stub({ success: false });
    await pollBeatportEnrichment('enr_1', TRACKS, () => 1, { sleep: async () => {} });
    // One poll, then out — the backend saying the job is gone is not a hiccup.
    expect(calls).toHaveLength(1);
  });

  /* The three exits below are the DELIBERATE DIVERGENCE. The vanilla polls in
     an unbounded `while (true)` with the catch inside the loop, ignores the
     page-leave signal, and so spins every 800ms for the life of the tab when
     the progress endpoint keeps throwing. */

  it('gives up after a run of consecutive failures instead of spinning forever', async () => {
    let calls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        calls++;
        throw new Error('boom');
      }),
    );
    const out = await pollBeatportEnrichment('enr_1', TRACKS, () => 1, {
      sleep: async () => {},
      maxConsecutiveErrors: 3,
    });
    expect(out).toEqual(TRACKS);
    expect(calls).toBe(3);
  });

  it('resets the failure run after a good poll, so a blip does not end it', async () => {
    let call = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        call++;
        if (call === 1 || call === 3) throw new Error('blip');
        if (call === 2) return new Response(JSON.stringify({ success: true, completed: 1 }));
        return new Response(
          JSON.stringify({ success: true, done: true, tracks: [{ title: 'A+' }] }),
        );
      }),
    );
    const out = await pollBeatportEnrichment('enr_1', TRACKS, () => 1, {
      sleep: async () => {},
      maxConsecutiveErrors: 2,
    });
    expect(out).toEqual([{ title: 'A+' }]);
  });

  it('stops when the page-leave signal aborts, which the vanilla ignores', async () => {
    const controller = new AbortController();
    controller.abort();
    const calls = stub({ success: true });
    const sleep = vi.fn(async () => {});
    const out = await pollBeatportEnrichment(
      'enr_1',
      TRACKS,
      () => 1,
      { sleep },
      controller.signal,
    );
    expect(out).toEqual(TRACKS);
    expect(calls).toHaveLength(0);
    // …and it does not wait out an 800ms tick first. The signal is checked on
    // BOTH sides of the sleep: before, so an already-cancelled poll leaves
    // immediately, and after, so one cancelled mid-tick does not then fetch.
    expect(sleep).not.toHaveBeenCalled();
  });

  it('does not fetch when the signal aborts DURING the interval', async () => {
    const controller = new AbortController();
    const calls = stub({ success: true });
    const out = await pollBeatportEnrichment(
      'enr_1',
      TRACKS,
      () => 1,
      {
        sleep: async () => {
          controller.abort();
        },
      },
      controller.signal,
    );
    expect(out).toEqual(TRACKS);
    expect(calls).toHaveLength(0);
  });

  it('has an overall ceiling even when every poll answers politely', async () => {
    let calls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        calls++;
        // Always successful, never done — the shape that hangs the vanilla.
        return new Response(JSON.stringify({ success: true, done: false }));
      }),
    );
    const out = await pollBeatportEnrichment('enr_1', TRACKS, () => 1, {
      sleep: async () => {},
      maxAttempts: 4,
    });
    expect(out).toEqual(TRACKS);
    expect(calls).toBe(4);
  });
});
