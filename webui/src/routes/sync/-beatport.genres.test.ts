/**
 * The genre browser's data layer.
 *
 * The image loader is where the assertions earn their keep. It is two workers
 * sharing a mutable queue, pausable mid-flight, resumable across modal opens —
 * and every one of those properties is invisible in a test that only checks
 * "did the images arrive".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  GENRE_IMAGE_MIN_GENRES,
  areGenreImagesLoaded,
  beatportGenreKey,
  filterGenresBySearch,
  getCachedGenreImages,
  getCachedGenres,
  isGenreImageLoadingActive,
  loadBeatportGenreList,
  loadGenreImagesProgressively,
  pauseGenreImageLoading,
  resetGenreBrowserCache,
  shouldLoadGenreImages,
} from './-beatport.genres';

beforeEach(() => {
  resetGenreBrowserCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetGenreBrowserCache();
});

function genre(slug: string, id: string | number = 1, name = slug) {
  return { slug, id, name };
}

/* ── The list ─────────────────────────────────────────────────────────────── */

describe('loadBeatportGenreList', () => {
  function stubGenres(body: unknown, ok = true) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify(body), { status: ok ? 200 : 503 })),
    );
  }

  it('drops the nine section headings Beatport returns as genres', async () => {
    stubGenres({
      genres: [genre('tech-house', 1, 'Tech House'), genre('charts', 2, 'Charts')],
    });
    const out = await loadBeatportGenreList();
    expect(out.map((g) => g.name)).toEqual(['Tech House']);
  });

  it('caches the FILTERED list, not the raw one', async () => {
    stubGenres({ genres: [genre('techno', 1, 'Techno'), genre('trending', 2, 'Trending')] });
    await loadBeatportGenreList();
    expect(getCachedGenres()?.map((g) => g.name)).toEqual(['Techno']);
  });

  it('throws with the status line, which this endpoint alone reports', async () => {
    // 2362-2364: the only Beatport fetch that checks response.ok, and it puts
    // the status into the message the user reads.
    stubGenres({}, false);
    await expect(loadBeatportGenreList()).rejects.toThrow(/503/);
  });

  it('CLEARS the images-loaded flag, so a fresh list re-queues its images', async () => {
    // Asserted against a cache that was already complete — starting from the
    // reset state, "false afterwards" is true whether or not the line exists.
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ success: true, image_url: 'i' }), { status: 200 }),
      ),
    );
    await loadGenreImagesProgressively([genre('a')], () => {}, { sleep: async () => {} });
    expect(areGenreImagesLoaded()).toBe(true);

    stubGenres({ genres: [genre('b', 2, 'B')] });
    await loadBeatportGenreList();
    expect(areGenreImagesLoaded()).toBe(false);
  });
});

/* ── The image threshold ──────────────────────────────────────────────────── */

describe('the image threshold', () => {
  it('is a strict greater-than, so exactly five genres get NO images', () => {
    // 2433. Transcribed, not corrected — see the note on the constant.
    expect(GENRE_IMAGE_MIN_GENRES).toBe(5);
    expect(shouldLoadGenreImages(5)).toBe(false);
    expect(shouldLoadGenreImages(6)).toBe(true);
    expect(shouldLoadGenreImages(0)).toBe(false);
  });
});

/* ── The progressive image loader ─────────────────────────────────────────── */

describe('loadGenreImagesProgressively', () => {
  /** Resolves each genre-image request only when the test says so. */
  function gatedImageApi() {
    const pending = new Map<string, (body: unknown) => void>();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (url: string) =>
          new Promise<Response>((resolve) => {
            const slug = url.split('/')[4];
            pending.set(slug, (body) =>
              resolve(new Response(JSON.stringify(body), { status: 200 })),
            );
          }),
      ),
    );
    return pending;
  }

  function stubImages(byslug: Record<string, unknown>) {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        calls.push(url);
        const slug = url.split('/')[4];
        return new Response(JSON.stringify(byslug[slug] ?? { success: false }), { status: 200 });
      }),
    );
    return calls;
  }

  const noSleep = async () => {};

  it('reports each image and keys it by slug AND id', async () => {
    stubImages({ a: { success: true, image_url: 'http://a.jpg' } });
    const seen: [string, string][] = [];
    await loadGenreImagesProgressively([genre('a', 7)], (key, url) => seen.push([key, url]), {
      sleep: noSleep,
    });
    // The vanilla matches its card on both attributes at once, so the pair is
    // the identity — a slug alone would let two genres share one image.
    expect(seen).toEqual([['a:7', 'http://a.jpg']]);
    expect(getCachedGenreImages().get('a:7')).toBe('http://a.jpg');
  });

  it('needs BOTH success and a url — either alone is ignored', async () => {
    // 2555. Tested in both directions: a stub that omits the url whenever it
    // reports failure cannot tell the two clauses apart.
    stubImages({
      a: { success: true },
      b: { success: false, image_url: 'http://b.jpg' },
    });
    const seen: string[] = [];
    await loadGenreImagesProgressively([genre('a', 1), genre('b', 2)], (key) => seen.push(key), {
      sleep: noSleep,
    });
    expect(seen).toEqual([]);
    expect(getCachedGenreImages().size).toBe(0);
  });

  it('counts a failed request as done and never retries it', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('boom'))),
    );
    const seen: string[] = [];
    await loadGenreImagesProgressively([genre('a'), genre('b', 2)], (key) => seen.push(key), {
      sleep: noSleep,
    });
    // A failure costs a picture; retrying costs the scraper.
    expect(seen).toEqual([]);
    expect(areGenreImagesLoaded()).toBe(true);
  });

  it('runs exactly TWO requests at a time', async () => {
    const pending = gatedImageApi();
    const all = [genre('a', 1), genre('b', 2), genre('c', 3), genre('d', 4)];
    const run = loadGenreImagesProgressively(all, () => {}, { sleep: noSleep });

    await vi.waitFor(() => expect(pending.size).toBe(2));
    // Two workers sharing one queue: a third must not start until one finishes.
    expect(pending.has('c')).toBe(false);

    pending.get('a')?.({ success: true, image_url: 'http://a.jpg' });
    await vi.waitFor(() => expect(pending.has('c')).toBe(true));

    for (const slug of ['b', 'c', 'd']) {
      await vi.waitFor(() => expect(pending.has(slug)).toBe(true));
      pending.get(slug)?.({ success: true, image_url: `http://${slug}.jpg` });
    }
    await run;
    expect(getCachedGenreImages().size).toBe(4);
  });

  it('honours the worker count it is given', async () => {
    const pending = gatedImageApi();
    const run = loadGenreImagesProgressively(
      [genre('a', 1), genre('b', 2), genre('c', 3)],
      () => {},
      { workers: 1, sleep: noSleep },
    );
    await vi.waitFor(() => expect(pending.size).toBe(1));
    for (const slug of ['a', 'b', 'c']) {
      await vi.waitFor(() => expect(pending.has(slug)).toBe(true));
      pending.get(slug)?.({ success: true, image_url: `http://${slug}.jpg` });
    }
    await run;
  });

  it('sleeps between requests, at the vanilla interval', async () => {
    stubImages({ a: { success: true, image_url: 'http://a.jpg' } });
    const slept: number[] = [];
    await loadGenreImagesProgressively([genre('a')], () => {}, {
      sleep: async (ms) => {
        slept.push(ms);
      },
    });
    // 2593 — a deliberate throttle, not an accident of the loop.
    expect(slept).toEqual([100]);
  });

  it('marks the images loaded when the queue drains', async () => {
    stubImages({ a: { success: true, image_url: 'http://a.jpg' } });
    await loadGenreImagesProgressively([genre('a')], () => {}, { sleep: noSleep });
    expect(areGenreImagesLoaded()).toBe(true);
    expect(isGenreImageLoadingActive()).toBe(false);
  });

  it('treats an EMPTY genre list as complete, not as paused', async () => {
    // 2539-2544, reached on the first call rather than after one that already
    // set the flag — otherwise the assertion passes on the earlier call's work.
    const calls = stubImages({});
    await loadGenreImagesProgressively([], () => {}, { sleep: noSleep });
    expect(calls).toHaveLength(0);
    expect(areGenreImagesLoaded()).toBe(true);
    expect(isGenreImageLoadingActive()).toBe(false);
  });

  it('treats an already-complete list as complete without fetching', async () => {
    const calls = stubImages({ a: { success: true, image_url: 'http://a.jpg' } });
    await loadGenreImagesProgressively([genre('a')], () => {}, { sleep: noSleep });
    const before = calls.length;

    await loadGenreImagesProgressively([genre('a')], () => {}, { sleep: noSleep });
    // 2539-2544: nothing to do is COMPLETE, not paused.
    expect(calls).toHaveLength(before);
    expect(areGenreImagesLoaded()).toBe(true);
  });

  it('STOPS when paused, and does NOT mark itself loaded', async () => {
    const pending = gatedImageApi();
    const all = [genre('a', 1), genre('b', 2), genre('c', 3), genre('d', 4)];
    const run = loadGenreImagesProgressively(all, () => {}, { sleep: noSleep });
    await vi.waitFor(() => expect(pending.size).toBe(2));

    // Closing the modal, mid-flight — the usual case.
    pauseGenreImageLoading();
    pending.get('a')?.({ success: true, image_url: 'http://a.jpg' });
    pending.get('b')?.({ success: true, image_url: 'http://b.jpg' });
    await run;

    expect(pending.has('c')).toBe(false);
    // The distinction that matters: a paused run recorded as complete would
    // leave the remaining genres on their emoji for the rest of the session.
    expect(areGenreImagesLoaded()).toBe(false);
    expect(isGenreImageLoadingActive()).toBe(false);
  });

  it('RESUMES from where the pause left off, re-queueing only what is missing', async () => {
    const pending = gatedImageApi();
    const all = [genre('a', 1), genre('b', 2), genre('c', 3), genre('d', 4)];
    const first = loadGenreImagesProgressively(all, () => {}, { sleep: noSleep });
    await vi.waitFor(() => expect(pending.size).toBe(2));
    pauseGenreImageLoading();
    pending.get('a')?.({ success: true, image_url: 'http://a.jpg' });
    pending.get('b')?.({ success: true, image_url: 'http://b.jpg' });
    await first;
    expect(getCachedGenreImages().size).toBe(2);

    const calls = stubImages({
      c: { success: true, image_url: 'http://c.jpg' },
      d: { success: true, image_url: 'http://d.jpg' },
    });
    await loadGenreImagesProgressively(all, () => {}, { sleep: noSleep });

    // Only the two that were still missing — reopening the modal must not
    // re-scrape what it already has.
    expect(calls.map((url) => url.split('/')[4]).sort()).toEqual(['c', 'd']);
    expect(getCachedGenreImages().size).toBe(4);
    expect(areGenreImagesLoaded()).toBe(true);
  });

  it('reports cached urls to a NEW subscriber via the cache, not the callback', async () => {
    stubImages({ a: { success: true, image_url: 'http://a.jpg' } });
    await loadGenreImagesProgressively([genre('a')], () => {}, { sleep: noSleep });

    const seen: string[] = [];
    await loadGenreImagesProgressively([genre('a')], (key) => seen.push(key), { sleep: noSleep });
    // A second open re-reads getCachedGenreImages rather than being re-told,
    // which is why the callback stays silent here.
    expect(seen).toEqual([]);
    expect(getCachedGenreImages().get('a:1')).toBe('http://a.jpg');
  });

  it('hands out a COPY of the cache', async () => {
    stubImages({ a: { success: true, image_url: 'http://a.jpg' } });
    await loadGenreImagesProgressively([genre('a')], () => {}, { sleep: noSleep });
    getCachedGenreImages().clear();
    expect(getCachedGenreImages().get('a:1')).toBe('http://a.jpg');
  });
});

/* ── Search ───────────────────────────────────────────────────────────────── */

describe('filterGenresBySearch', () => {
  const GENRES = [{ name: 'Tech House' }, { name: 'Deep House' }, { name: 'Drum & Bass' }];

  it('matches a case-insensitive substring of the NAME', () => {
    expect(filterGenresBySearch(GENRES, 'house').map((g) => g.name)).toEqual([
      'Tech House',
      'Deep House',
    ]);
    expect(filterGenresBySearch(GENRES, 'TECH').map((g) => g.name)).toEqual(['Tech House']);
  });

  it('returns everything for an empty term', () => {
    expect(filterGenresBySearch(GENRES, '')).toHaveLength(3);
  });

  it('does not search the slug, even when the genre carries one', () => {
    // 2632 reads dataset.genreName only, so the hyphenated slug form finds
    // nothing — worth knowing before someone "fixes" a bug report about it.
    // The fixture must HAVE a slug, or this passes for the wrong reason.
    const withSlug = [{ name: 'Deep House', slug: 'deep-house', id: 1 }];
    expect(filterGenresBySearch(withSlug, 'deep-house')).toEqual([]);
    expect(filterGenresBySearch(withSlug, 'deep')).toHaveLength(1);
  });

  it('matches nothing rather than everything when there is no match', () => {
    expect(filterGenresBySearch(GENRES, 'zzz')).toEqual([]);
  });
});

describe('beatportGenreKey', () => {
  it('is the slug and the id together', () => {
    expect(beatportGenreKey({ slug: 'tech-house', id: 11 })).toBe('tech-house:11');
    expect(beatportGenreKey({ slug: 'tech-house', id: '11' })).toBe('tech-house:11');
  });
});
