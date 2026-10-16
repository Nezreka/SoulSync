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
  fetchBeatportDJCharts,
  fetchBeatportFeaturedCharts,
  fetchBeatportGenreHero,
  fetchBeatportGenreImage,
  fetchBeatportGenreTop10Lists,
  fetchBeatportGenreTop10Releases,
  fetchBeatportGenres,
  fetchBeatportHeroTracks,
  fetchBeatportHypePicks,
  fetchBeatportNewReleases,
  fetchBeatportReleaseMetadata,
  fetchBeatportTop10Lists,
  fetchBeatportTop10Releases,
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

describe('the release download endpoint', () => {
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
    await fetchBeatportGenreImage('tech-house', 11);
    expect(calls.map((c) => c.url)).toEqual([
      '/api/beatport/genre/tech-house/11/hero',
      '/api/beatport/genre/tech-house/11/top-10-lists',
      '/api/beatport/genre/tech-house/11/top-10-releases',
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
