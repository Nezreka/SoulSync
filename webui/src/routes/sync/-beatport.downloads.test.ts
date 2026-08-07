/**
 * The download bridge. This is the path that queues files, so the assertions
 * are on the ARGUMENTS handed to the download engine, not on whether it was
 * called — a chart opened as an artist_album, or a release credited to 'Various
 * Artists', would still "pass" a called/not-called test and would put the wrong
 * folders on disk.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  type BeatportDownloadEnv,
  defaultBeatportDownloadEnv,
  isBeatportModalOpening,
  openBeatportChartAsDownloadModal,
  openBeatportChartCard,
  openBeatportRelease,
  openBeatportTop100,
  resetBeatportModalLatch,
} from './-beatport.downloads';

/** Every call the bridge makes to the outside world, recorded in order. */
function makeEnv() {
  const scheduled: { callback: () => void; ms: number }[] = [];
  const slept: number[] = [];
  const env: BeatportDownloadEnv = {
    showToast: vi.fn(),
    showLoadingOverlay: vi.fn(),
    hideLoadingOverlay: vi.fn(),
    setOverlayMessage: vi.fn(),
    openDownloadModal: vi.fn(),
    registerDownload: vi.fn(),
    // Pinned so the generated ids are literals in the assertions below.
    now: () => 1700000000000,
    random: () => 0.5,
    schedule: (callback, ms) => {
      scheduled.push({ callback, ms });
    },
    // The interval is still ASSERTED (see `slept`) — it is recorded rather
    // than waited on, so the poll's timing stays covered without the wall time.
    sleep: async (ms) => {
      slept.push(ms);
    },
  };
  return { env, scheduled, slept };
}

function stubFetch(handler: (url: string, init?: RequestInit) => unknown) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return new Response(JSON.stringify(handler(url, init)), { status: 200 });
    }),
  );
  return calls;
}

beforeEach(() => {
  resetBeatportModalLatch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetBeatportModalLatch();
});

/* ── Releases ─────────────────────────────────────────────────────────────── */

const RELEASE = {
  title: 'Nights',
  artist: 'Frank Ocean',
  url: 'https://beatport.com/release/nights/1',
  image_url: 'http://card.jpg',
};

const RELEASE_METADATA = {
  success: true,
  tracks: [{ name: 'A', artists: [{ name: 'Frank Ocean' }, 'Guest'] }],
  album: { name: 'Blonde', images: [{ url: 'http://album.jpg' }] },
  artist: { id: 'a1', name: 'Frank Ocean' },
};

describe('opening a release', () => {
  it('opens it as an ALBUM, with its real artist and the artist_album context', async () => {
    const calls = stubFetch(() => RELEASE_METADATA);
    const { env } = makeEnv();
    await openBeatportRelease(RELEASE, env);

    expect(calls[0].url).toBe('/api/beatport/release-metadata');
    expect(JSON.parse(calls[0].init?.body as string)).toEqual({ release_url: RELEASE.url });

    expect(env.openDownloadModal).toHaveBeenCalledWith(
      'beatport_release_1700000000000_i',
      'Blonde',
      // Artists flattened to strings whether they arrived as objects or not.
      [{ name: 'A', artists: ['Frank Ocean', 'Guest'] }],
      RELEASE_METADATA.album,
      RELEASE_METADATA.artist,
      false,
      // NOT 'playlist' — a release is a real album, and this argument decides
      // the folder structure the download engine builds.
      'artist_album',
    );
  });

  it("registers the bubble with the ALBUM's art, not the card's", async () => {
    stubFetch(() => RELEASE_METADATA);
    const { env } = makeEnv();
    await openBeatportRelease(RELEASE, env);
    expect(env.registerDownload).toHaveBeenCalledWith(
      'Blonde',
      'http://album.jpg',
      'beatport_release_1700000000000_i',
    );
  });

  it("falls back to the card's thumbnail when the album has no art", async () => {
    stubFetch(() => ({ ...RELEASE_METADATA, album: { name: 'Blonde', images: [] } }));
    const { env } = makeEnv();
    await openBeatportRelease(RELEASE, env);
    expect(env.registerDownload).toHaveBeenCalledWith(
      'Blonde',
      'http://card.jpg',
      expect.any(String),
    );
  });

  it('refuses a release with no url, and does not fetch', async () => {
    const calls = stubFetch(() => RELEASE_METADATA);
    const { env } = makeEnv();
    await openBeatportRelease({ title: 'x' }, env);
    expect(env.showToast).toHaveBeenCalledWith('No release URL available', 'error');
    expect(calls).toHaveLength(0);
    // Reachable only from the top-10 release cards; the other three call sites
    // never attach a handler to an url-less card at all.
    expect(env.openDownloadModal).not.toHaveBeenCalled();
    // And the latch is RELEASED on the way out (1865). Leaking it here would
    // swallow every release click for the rest of the session — silently, since
    // a swallowed click does nothing at all.
    expect(isBeatportModalOpening()).toBe(false);
  });

  it("refuses the placeholder url '#' too", async () => {
    const calls = stubFetch(() => RELEASE_METADATA);
    const { env } = makeEnv();
    await openBeatportRelease({ title: 'x', url: '#' }, env);
    expect(env.showToast).toHaveBeenCalledWith('No release URL available', 'error');
    expect(calls).toHaveLength(0);
  });

  it("surfaces the backend's own error message", async () => {
    stubFetch(() => ({ success: false, error: 'release is region locked' }));
    const { env } = makeEnv();
    await openBeatportRelease(RELEASE, env);
    expect(env.showToast).toHaveBeenCalledWith(
      'Error loading Nights: release is region locked',
      'error',
    );
    expect(env.hideLoadingOverlay).toHaveBeenCalled();
    expect(env.openDownloadModal).not.toHaveBeenCalled();
  });

  it('treats an empty track list as a failure', async () => {
    stubFetch(() => ({ success: true, tracks: [], album: { name: 'Blonde' } }));
    const { env } = makeEnv();
    await openBeatportRelease(RELEASE, env);
    expect(env.showToast).toHaveBeenCalledWith(
      'Error loading Nights: No tracks found in this release',
      'error',
    );
  });

  it('swallows a second click while the first is still in flight', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let fetches = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        fetches++;
        await gate;
        return new Response(JSON.stringify(RELEASE_METADATA), { status: 200 });
      }),
    );
    const { env } = makeEnv();

    const first = openBeatportRelease(RELEASE, env);
    const second = openBeatportRelease(RELEASE, env);
    await second;
    // The latch is held for the whole of the first click's work — unlike the
    // Top 100 buttons, which release it on a blind timer.
    expect(fetches).toBe(1);
    release();
    await first;
    expect(env.openDownloadModal).toHaveBeenCalledTimes(1);
  });

  it('releases the latch after a failure, so a retry works', async () => {
    stubFetch(() => ({ success: false, error: 'boom' }));
    const { env } = makeEnv();
    await openBeatportRelease(RELEASE, env);
    expect(isBeatportModalOpening()).toBe(false);

    stubFetch(() => RELEASE_METADATA);
    await openBeatportRelease(RELEASE, env);
    expect(env.openDownloadModal).toHaveBeenCalledTimes(1);
  });
});

/* ── Chart cards ──────────────────────────────────────────────────────────── */

const CHART = {
  name: 'Peak Hour',
  creator: 'DJ X',
  url: 'https://beatport.com/chart/peak-hour/1',
  image: 'http://chart.jpg',
};

const SCRAPED = {
  success: true,
  tracks: [{ title: 'A', artist: 'X, Y', duration: '3:20' }],
};

describe('opening a chart card', () => {
  it('opens it as a COMPILATION under the playlist context', async () => {
    const calls = stubFetch(() => SCRAPED);
    const { env } = makeEnv();
    await openBeatportChartCard(CHART, 'chart', env);

    // The scraper gets the PREFIXED name; the download gets 'name - creator'.
    expect(JSON.parse(calls[0].init?.body as string)).toEqual({
      chart_url: CHART.url,
      chart_name: 'Featured Chart: Peak Hour',
      limit: 100,
      enrich: false,
    });

    const args = (env.openDownloadModal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(args[1]).toBe('Peak Hour - DJ X');
    expect(args[4]).toEqual({ id: 'beatport_various', name: 'Various Artists' });
    expect(args[6]).toBe('playlist');
    // Comma-split artists, so the engine builds one folder per artist.
    expect((args[2] as { artists: string[] }[])[0].artists).toEqual(['X', 'Y']);
    expect((args[3] as { album_type: string }).album_type).toBe('compilation');
  });

  it('uses the DJ prefix and the DJ copy for the DJ variant', async () => {
    const calls = stubFetch(() => SCRAPED);
    const { env } = makeEnv();
    await openBeatportChartCard(CHART, 'dj', env);
    expect(JSON.parse(calls[0].init?.body as string).chart_name).toBe('DJ Chart: Peak Hour');
  });

  it('refuses an url-less chart with variant-specific copy', async () => {
    const calls = stubFetch(() => SCRAPED);
    const { env } = makeEnv();
    await openBeatportChartCard({ name: 'n', creator: 'c' }, 'chart', env);
    await openBeatportChartCard({ name: 'n', creator: 'c' }, 'dj', env);
    expect(env.showToast).toHaveBeenNthCalledWith(1, 'No chart URL available', 'error');
    expect(env.showToast).toHaveBeenNthCalledWith(2, 'No DJ chart URL available', 'error');
    expect(calls).toHaveLength(0);
  });

  it('reports the variant-specific empty-chart message', async () => {
    stubFetch(() => ({ success: true, tracks: [] }));
    const { env } = makeEnv();
    await openBeatportChartCard(CHART, 'dj', env);
    expect(env.showToast).toHaveBeenCalledWith(
      'Error loading Peak Hour: No tracks found in this DJ chart',
      'error',
    );
  });

  it('is NOT double-click guarded, which is what the vanilla does', async () => {
    stubFetch(() => SCRAPED);
    const { env } = makeEnv();
    await Promise.all([
      openBeatportChartCard(CHART, 'chart', env),
      openBeatportChartCard(CHART, 'chart', env),
    ]);
    // Neither chart handler touches _beatportModalOpening. Adding the guard
    // here would be an improvement, not a transcription — recorded, not made.
    expect(env.openDownloadModal).toHaveBeenCalledTimes(2);
  });

  it('writes enrichment progress into the loading overlay', async () => {
    let polls = 0;
    stubFetch((url) => {
      if (url.startsWith('/api/beatport/enrich-tracks')) return { success: true, async: true };
      if (url.startsWith('/api/beatport/enrich-progress')) {
        polls++;
        return polls === 1
          ? { success: true, completed: 1, total: 2, current_track: 'A' }
          : { success: true, done: true, completed: 2, total: 2, tracks: [{ title: 'A+' }] };
      }
      return SCRAPED;
    });
    const { env, slept } = makeEnv();
    await openBeatportChartCard(CHART, 'chart', env);

    expect(env.setOverlayMessage).toHaveBeenCalledWith('Fetching track metadata... (1/2) A');
    // 1957's fixed 800ms gap, once per poll.
    expect(slept).toEqual([800, 800]);
    // The enriched tracks reach the modal, not the scraped ones.
    const args = (env.openDownloadModal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect((args[2] as { name: string }[])[0].name).toBe('A+');
  });

  it('still downloads the SCRAPED tracks when enrichment fails outright', async () => {
    stubFetch((url) => {
      if (url.startsWith('/api/beatport/enrich-tracks')) return { success: false };
      return SCRAPED;
    });
    const { env } = makeEnv();
    await openBeatportChartCard(CHART, 'chart', env);
    // A failed enrichment must never block the download — it only means less
    // metadata.
    const args = (env.openDownloadModal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect((args[2] as { name: string }[])[0].name).toBe('A');
  });
});

/* ── The chart modal opener ───────────────────────────────────────────────── */

describe('openBeatportChartAsDownloadModal', () => {
  it('clears the latch unconditionally, even when it did not set it', async () => {
    stubFetch(() => SCRAPED);
    const { env } = makeEnv();
    // A Top 100 click that latched and has not timed out yet.
    const inFlight = openBeatportTop100('beatport', env);
    expect(isBeatportModalOpening()).toBe(true);

    openBeatportChartAsDownloadModal([{ title: 'A' }], 'Chart', null, env);
    // 2002's comment: so a cached (fast) enrichment can still open the modal.
    expect(isBeatportModalOpening()).toBe(false);
    await inFlight;
  });

  it('registers the bubble with an empty image when the chart has none', () => {
    const { env } = makeEnv();
    openBeatportChartAsDownloadModal([{ title: 'A' }], 'Beatport Top 100', null, env);
    expect(env.registerDownload).toHaveBeenCalledWith(
      'Beatport Top 100',
      '',
      'beatport_chart_1700000000000_i',
    );
  });

  it('gives the compilation album the timestamp-only id, not the suffixed one', () => {
    const { env } = makeEnv();
    openBeatportChartAsDownloadModal([{ title: 'A' }], 'Chart', 'http://i.jpg', env);
    const args = (env.openDownloadModal as ReturnType<typeof vi.fn>).mock.calls[0];
    // The album id and the virtual playlist id are DIFFERENT shapes in the
    // vanilla; collapsing them would change the key the engine tracks.
    expect((args[3] as { id: string }).id).toBe('beatport_chart_1700000000000');
    expect(args[0]).toBe('beatport_chart_1700000000000_i');
  });
});

/* ── Top 100 ──────────────────────────────────────────────────────────────── */

describe('the two Top 100 buttons', () => {
  it('hit the right endpoint and name the download after the list', async () => {
    const calls = stubFetch(() => SCRAPED);
    const { env } = makeEnv();
    await openBeatportTop100('beatport', env);
    expect(calls[0].url).toBe('/api/beatport/top-100?enrich=false');
    expect(env.showLoadingOverlay).toHaveBeenCalledWith('Scraping Beatport Top 100...');
    const args = (env.openDownloadModal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(args[1]).toBe('Beatport Top 100');
  });

  it('hits the hype endpoint for the hype variant', async () => {
    const calls = stubFetch(() => SCRAPED);
    const { env } = makeEnv();
    await openBeatportTop100('hype', env);
    expect(calls[0].url).toBe('/api/beatport/hype-top-100?enrich=false');
    expect(env.registerDownload).toHaveBeenCalledWith('Hype Top 100', '', expect.any(String));
  });

  it('shows NO loading toast, unlike every other flow', async () => {
    stubFetch(() => SCRAPED);
    const { env } = makeEnv();
    await openBeatportTop100('beatport', env);
    expect(env.showToast).not.toHaveBeenCalled();
  });

  it('releases the latch on a blind 2s timer rather than when the work ends', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        await gate;
        return new Response(JSON.stringify(SCRAPED), { status: 200 });
      }),
    );
    const { env, scheduled } = makeEnv();
    const inFlight = openBeatportTop100('beatport', env);

    expect(scheduled).toEqual([{ callback: expect.any(Function), ms: 2000 }]);
    expect(isBeatportModalOpening()).toBe(true);

    // The timer fires while the scrape is still running — so a scrape slower
    // than 2s can be started a second time. Transcribed, not fixed.
    scheduled[0].callback();
    expect(isBeatportModalOpening()).toBe(false);
    release();
    await inFlight;
  });

  it('swallows a second click before the timer fires', async () => {
    const calls = stubFetch(() => SCRAPED);
    const { env } = makeEnv();
    const first = openBeatportTop100('beatport', env);
    const second = openBeatportTop100('beatport', env);
    await Promise.all([first, second]);
    expect(calls.filter((c) => c.url.includes('top-100'))).toHaveLength(1);
  });

  it('reports a failure with the list name', async () => {
    stubFetch(() => ({ success: true, tracks: [] }));
    const { env } = makeEnv();
    await openBeatportTop100('hype', env);
    expect(env.showToast).toHaveBeenCalledWith(
      'Error loading Hype Top 100: No tracks found in Hype Top 100',
      'error',
    );
  });
});

/* ── The default environment ──────────────────────────────────────────────── */

describe('defaultBeatportDownloadEnv', () => {
  afterEach(() => {
    delete window.showToast;
    delete window.showLoadingOverlay;
    delete window.hideLoadingOverlay;
    delete window.openDownloadMissingModalForArtistAlbum;
    delete window.registerBeatportDownload;
    document.body.innerHTML = '';
  });

  it('routes each call to the vanilla global it belongs to', async () => {
    const seen: string[] = [];
    window.showToast = (message) => seen.push(`toast:${message}`);
    window.showLoadingOverlay = (message) => seen.push(`overlay:${message}`);
    window.hideLoadingOverlay = () => seen.push('hide');
    window.openDownloadMissingModalForArtistAlbum = (id) => {
      seen.push(`modal:${id}`);
    };
    window.registerBeatportDownload = (name) => seen.push(`bubble:${name}`);

    const env = defaultBeatportDownloadEnv();
    env.showToast('t');
    env.showLoadingOverlay('o');
    env.hideLoadingOverlay();
    await env.openDownloadModal('vp', 'n', [], {}, {}, false);
    env.registerDownload('c', 'i', 'vp');

    expect(seen).toEqual(['toast:t', 'overlay:o', 'hide', 'modal:vp', 'bubble:c']);
  });

  it('does not throw when the vanilla globals are absent', () => {
    const env = defaultBeatportDownloadEnv();
    // Every one is optional-chained: on a page where downloads.js has not
    // loaded, a Beatport click must be inert rather than throwing.
    expect(() => {
      env.showToast('t');
      env.showLoadingOverlay('o');
      env.hideLoadingOverlay();
      env.registerDownload('c', 'i', 'vp');
    }).not.toThrow();
  });

  it("writes progress into the overlay's message node, and tolerates its absence", () => {
    expect(() => defaultBeatportDownloadEnv().setOverlayMessage('x')).not.toThrow();

    document.body.innerHTML =
      '<div id="loading-overlay"><div class="loading-message">Processing...</div></div>';
    defaultBeatportDownloadEnv().setOverlayMessage('Fetching track metadata... (1/2)');
    expect(document.querySelector('#loading-overlay .loading-message')?.textContent).toBe(
      'Fetching track metadata... (1/2)',
    );
  });

  it('generates a fresh id per call', () => {
    const env = defaultBeatportDownloadEnv();
    // now/random are the real ones here — the point is only that they are wired
    // to something live, not pinned constants.
    expect(env.now()).toBeGreaterThan(1_600_000_000_000);
    const first = env.random();
    expect(first).toBeGreaterThanOrEqual(0);
    expect(first).toBeLessThan(1);
  });
});
