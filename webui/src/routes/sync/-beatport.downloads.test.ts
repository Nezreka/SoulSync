/**
 * The release download. This is the path that queues files, so the assertions
 * are on the ARGUMENTS handed to the download engine, not on whether it was
 * called: a release credited to 'Various Artists' would still "pass" a
 * called/not-called test and would put the wrong folders on disk. (chart-type
 * lists open as discoveries now, see -beatport.lists.test.ts.)
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  type BeatportDownloadEnv,
  defaultBeatportDownloadEnv,
  isBeatportModalOpening,
  openBeatportRelease,
  resetBeatportModalLatch,
} from './-beatport.downloads';

/** Every call the bridge makes to the outside world, recorded in order. */
function makeEnv() {
  const env: BeatportDownloadEnv = {
    showToast: vi.fn(),
    showLoadingOverlay: vi.fn(),
    hideLoadingOverlay: vi.fn(),
    openDownloadModal: vi.fn(),
    registerDownload: vi.fn(),
    // Pinned so the generated ids are literals in the assertions below.
    now: () => 1700000000000,
    random: () => 0.5,
  };
  return { env };
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
