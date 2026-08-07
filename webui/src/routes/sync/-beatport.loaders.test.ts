/**
 * The five loaders. The assertions that matter are the error strings: the two
 * error-block sections have a DIFFERENT message for "the API said no" and for
 * "the fetch threw", and the three silent ones must produce no message at all.
 * Getting one of these wrong shows a plausible sentence to the user that is
 * about the wrong failure.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  heroClickRelease,
  isBeatportReleaseClickable,
  loadBeatportDJCharts,
  loadBeatportFeaturedCharts,
  loadBeatportHero,
  loadBeatportHypePicks,
  loadBeatportNewReleases,
} from './-beatport.loaders';

afterEach(() => {
  vi.unstubAllGlobals();
});

const SIGNAL = new AbortController().signal;

function stubJson(body: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })),
  );
}

function stubThrow(error: Error) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.reject(error)),
  );
}

describe('the hero loader', () => {
  it('returns the tracks field', async () => {
    stubJson({ success: true, tracks: [{ title: 'a' }] });
    await expect(loadBeatportHero(SIGNAL)).resolves.toEqual([{ title: 'a' }]);
  });

  it('returns null rather than throwing, because it has no error renderer', async () => {
    stubJson({ success: false });
    await expect(loadBeatportHero(SIGNAL)).resolves.toBeNull();
    stubJson({ success: true, tracks: [] });
    await expect(loadBeatportHero(SIGNAL)).resolves.toBeNull();
  });
});

describe('the new-releases loader', () => {
  it('returns the releases field', async () => {
    stubJson({ success: true, releases: [{ title: 'a' }] });
    await expect(loadBeatportNewReleases(SIGNAL)).resolves.toEqual([{ title: 'a' }]);
  });

  it("forwards the backend's own message when the API says no", async () => {
    stubJson({ success: false, error: 'beatport is rate limiting us' });
    await expect(loadBeatportNewReleases(SIGNAL)).rejects.toThrow('beatport is rate limiting us');
  });

  it('falls back to its own copy when the backend sent none', async () => {
    stubJson({ success: true, releases: [] });
    await expect(loadBeatportNewReleases(SIGNAL)).rejects.toThrow('No releases available');
  });

  it('uses a DIFFERENT message when the fetch throws', async () => {
    // 408 does not surface the exception's message — the two failure arms say
    // different things, and swapping them would misreport the cause.
    stubThrow(new Error('NetworkError: connection refused'));
    await expect(loadBeatportNewReleases(SIGNAL)).rejects.toThrow('Failed to load releases');
  });

  it('lets an abort through untouched', async () => {
    // The hook tests error.name to tell a page-leave from a real failure;
    // re-wrapping it would render an error block for a navigation.
    stubThrow(Object.assign(new Error('aborted'), { name: 'AbortError' }));
    await expect(loadBeatportNewReleases(SIGNAL)).rejects.toThrowError(
      expect.objectContaining({ name: 'AbortError' }),
    );
  });
});

describe('the hype-picks loader', () => {
  it('returns the releases field', async () => {
    stubJson({ success: true, releases: [{ title: 'a' }] });
    await expect(loadBeatportHypePicks(SIGNAL)).resolves.toEqual([{ title: 'a' }]);
  });

  it("uses 'available', which is the DISPLAYED string, not the logged one", async () => {
    // 749 logs 'No hype picks found'; 750 shows 'No hype picks available'.
    stubJson({ success: true, releases: [] });
    await expect(loadBeatportHypePicks(SIGNAL)).rejects.toThrow('No hype picks available');
  });

  it('has its own thrown-fetch copy too', async () => {
    stubThrow(new Error('boom'));
    await expect(loadBeatportHypePicks(SIGNAL)).rejects.toThrow('Failed to load hype picks');
  });

  it('does not borrow the releases section copy', async () => {
    stubJson({ success: true, releases: [] });
    await expect(loadBeatportHypePicks(SIGNAL)).rejects.not.toThrow('No releases available');
  });
});

describe('the two chart loaders', () => {
  it('return the charts field', async () => {
    stubJson({ success: true, charts: [{ name: 'a' }] });
    await expect(loadBeatportFeaturedCharts(SIGNAL)).resolves.toEqual([{ name: 'a' }]);
    await expect(loadBeatportDJCharts(SIGNAL)).resolves.toEqual([{ name: 'a' }]);
  });

  it('return null on failure and never throw a message', async () => {
    // Neither has an error renderer, so there is no message to carry. Throwing
    // one here would invite someone to render it later and diverge quietly.
    stubJson({ success: false, error: 'ignored' });
    await expect(loadBeatportFeaturedCharts(SIGNAL)).resolves.toBeNull();
    await expect(loadBeatportDJCharts(SIGNAL)).resolves.toBeNull();
    stubJson({ success: true, charts: [] });
    await expect(loadBeatportFeaturedCharts(SIGNAL)).resolves.toBeNull();
    await expect(loadBeatportDJCharts(SIGNAL)).resolves.toBeNull();
  });
});

describe("the hero's click payload", () => {
  it('defaults the three text fields and the image', () => {
    expect(heroClickRelease({ url: 'http://u' })).toEqual({
      url: 'http://u',
      title: 'Unknown Title',
      artist: 'Unknown Artist',
      label: 'Unknown Label',
      image_url: '',
    });
  });

  it('keeps real values', () => {
    expect(
      heroClickRelease({ url: 'http://u', title: 'T', artist: 'A', label: 'L' }),
    ).toMatchObject({ title: 'T', artist: 'A', label: 'L' });
  });

  it('does NOT default the url — an url-less slide is simply not clickable', () => {
    expect(heroClickRelease({}).url).toBeUndefined();
  });
});

describe('the clickability test', () => {
  it("rejects nothing, the empty string and the '#' placeholder", () => {
    expect(isBeatportReleaseClickable('http://u')).toBe(true);
    expect(isBeatportReleaseClickable('#')).toBe(false);
    expect(isBeatportReleaseClickable('')).toBe(false);
    expect(isBeatportReleaseClickable(undefined)).toBe(false);
    expect(isBeatportReleaseClickable(null)).toBe(false);
  });
});
