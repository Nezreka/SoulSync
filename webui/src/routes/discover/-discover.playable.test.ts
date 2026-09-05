/** the play-now bridge: resolve-against-library + hand-off to the player. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  beginPlayIntent,
  playerBridgeAvailable,
  playMixNow,
  playTrackNow,
  resolveMixPlayable,
  toPlayablePairs,
} from './-discover.playable';

let toasts: { msg: string; type?: string }[] = [];
let played: { tracks: unknown[]; name?: string }[] = [];

beforeEach(() => {
  toasts = [];
  played = [];
  window.showToast = vi.fn((msg: string, type?: string) => {
    toasts.push({ msg, type });
  });
  window.playTrackList = vi.fn((tracks: unknown[], name?: string) => {
    played.push({ tracks, name });
    return { status: 'played' };
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(response: unknown, ok = true) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok, json: async () => response })),
  );
}

describe('toPlayablePairs', () => {
  it('normalizes any mix track shape to artist/title', () => {
    const pairs = toPlayablePairs([
      { name: 'One More Time', artists: [{ name: 'Daft Punk' }] },
      { title: 'Genesis', artist: 'Justice' },
    ]);
    expect(pairs).toEqual([
      { artist: 'Daft Punk', title: 'One More Time' },
      { artist: 'Justice', title: 'Genesis' },
    ]);
  });
});

describe('playMixNow', () => {
  it('plays the resolved rows with the mix title as context', async () => {
    stubFetch({
      success: true,
      tracks: [{ id: 1, file_path: '/m/a.flac', title: 'A' }],
      queue_tracks: [
        { id: 1, file_path: '/m/a.flac', title: 'A' },
        { title: 'B', artist: 'X', playback_status: 'missing' },
      ],
      matched: 1,
      total: 2,
    });
    const outcome = await playMixNow([{ title: 'A', artist: 'X' }], 'Daily Mix 1');
    expect(outcome).toBe('played');
    expect(played).toHaveLength(1);
    expect(played[0].name).toBe('Daily Mix 1');
    expect(played[0].tracks).toHaveLength(2);
    expect(toasts[0].msg).toContain('1 will download first');
  });

  it('says all-owned when everything matched', async () => {
    stubFetch({ success: true, tracks: [{ file_path: '/m/a' }], matched: 1, total: 1 });
    await playMixNow([{ title: 'A', artist: 'X' }], 'Mix');
    expect(toasts[0].msg).toContain('Playing all 1');
  });

  it('nothing owned: queues the missing rows for automatic acquisition', async () => {
    stubFetch({
      success: true,
      tracks: [],
      queue_tracks: [{ title: 'A', artist: 'X', playback_status: 'missing' }],
      matched: 0,
      total: 1,
    });
    const outcome = await playMixNow([{ title: 'A', artist: 'X' }], 'Mix');
    expect(outcome).toBe('played');
    expect(played).toHaveLength(1);
    expect(toasts[0].msg).toContain('1 will download first');
  });

  it('a failed resolve never plays and says so', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('down');
      }),
    );
    const outcome = await playMixNow([{ title: 'A', artist: 'X' }], 'Mix');
    expect(outcome).toBe('failed');
    expect(played).toHaveLength(0);
  });

  // The five outcomes have to be DISTINGUISHABLE. The old code returned
  // 'played' and toasted "Playing all N tracks" with no player on the page.
  it('no player bridge: unsupported, no success toast, no network call', async () => {
    const spy = vi.fn();
    vi.stubGlobal('fetch', spy);
    delete (window as { playTrackList?: unknown }).playTrackList;
    expect(playerBridgeAvailable()).toBe(false);
    const outcome = await playMixNow([{ title: 'A', artist: 'X' }], 'Mix');
    expect(outcome).toBe('unsupported');
    expect(spy).not.toHaveBeenCalled();
    expect(toasts.map((t) => t.type)).toEqual(['error']);
  });

  it('a rejected hand-off is failed, not played', async () => {
    stubFetch({ success: true, tracks: [{ file_path: '/m/a' }], matched: 1, total: 1 });
    window.playTrackList = vi.fn(async () => {
      throw new Error('no audio device');
    });
    const outcome = await playMixNow([{ title: 'A', artist: 'X' }], 'Mix');
    expect(outcome).toBe('failed');
    expect(toasts.every((t) => t.type !== 'success')).toBe(true);
  });

  it('a skipped first track is not reported as a failure', async () => {
    // playQueueItem catches a bad track, toasts "Skipping track", schedules
    // the next one 500ms out and answers 'skipped'. Calling that a failure
    // told the user playback could not start, and then music started.
    stubFetch({
      success: true,
      tracks: [{ file_path: '/m/a' }, { file_path: '/m/b' }],
      queue_tracks: [{ file_path: '/m/a' }, { file_path: '/m/b' }],
      matched: 2,
      total: 2,
    });
    window.playTrackList = vi.fn(async () => ({ status: 'skipped', error: 'bad file' }));
    const outcome = await playMixNow([{ title: 'A', artist: 'X' }], 'Mix');
    expect(outcome).toBe('played');
    expect(toasts.every((t) => t.type !== 'error')).toBe(true);
    expect(toasts[0].msg).toContain('Skipped a track');
  });

  it('an empty resolution is empty, not played', async () => {
    stubFetch({ success: true, tracks: [], queue_tracks: [], matched: 0, total: 0 });
    const outcome = await playMixNow([{ title: 'A', artist: 'X' }], 'Mix');
    expect(outcome).toBe('empty');
    expect(played).toHaveLength(0);
  });

  it('waits for the player before saying anything', async () => {
    stubFetch({ success: true, tracks: [{ file_path: '/m/a' }], matched: 1, total: 1 });
    let release: (() => void) | undefined;
    window.playTrackList = vi.fn(
      () =>
        new Promise<{ status: string }>((resolve) => {
          release = () => resolve({ status: 'played' });
        }),
    );
    const pending = playMixNow([{ title: 'A', artist: 'X' }], 'Mix');
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(toasts).toHaveLength(0);
    release?.();
    expect(await pending).toBe('played');
    expect(toasts[0].msg).toContain('Playing all 1');
  });
});

describe('beginPlayIntent', () => {
  // The coordinator behind "the newest Play wins". Without it, a slow first
  // mix could resolve after a second one and replace the queue behind it.
  it('only the newest intent is current, and it cancels the player first', () => {
    const cancel = vi.fn();
    window.cancelPendingPlayback = cancel;

    const first = beginPlayIntent();
    expect(first.isCurrent()).toBe(true);
    expect(cancel).toHaveBeenCalledTimes(1);

    const second = beginPlayIntent();
    expect(second.isCurrent()).toBe(true);
    expect(first.isCurrent()).toBe(false);
    expect(cancel).toHaveBeenCalledTimes(2);
  });

  it('a superseded mix never reaches the player', async () => {
    stubFetch({ success: true, tracks: [{ file_path: '/m/a' }], matched: 1, total: 1 });
    const stale = beginPlayIntent();
    beginPlayIntent(); // a second click takes ownership
    const outcome = await playMixNow([{ title: 'A', artist: 'X' }], 'Mix', stale);
    expect(outcome).toBe('superseded');
    expect(played).toHaveLength(0);
    expect(toasts).toHaveLength(0);
  });
});

describe('playTrackNow', () => {
  it('plays one row and names it', async () => {
    stubFetch({ success: true, tracks: [{ file_path: '/m/a' }], matched: 1, total: 1 });
    const outcome = await playTrackNow({ title: 'A', artist: 'X' }, 'A');
    expect(outcome).toBe('played');
    expect(played[0].tracks).toHaveLength(1);
    expect(toasts[0].msg).toBe('Playing A');
  });

  it('a prepared row reports playing only after the player acknowledges it', async () => {
    stubFetch({
      success: true,
      tracks: [],
      queue_tracks: [{ title: 'A', artist: 'X', playback_status: 'missing' }],
      matched: 0,
      total: 1,
    });
    await playTrackNow({ title: 'A', artist: 'X' }, 'A');
    expect(toasts[0].msg).toContain('Playing A');
  });
});

describe('resolveMixPlayable', () => {
  it('an empty tracklist resolves without a network call', async () => {
    const spy = vi.fn();
    vi.stubGlobal('fetch', spy);
    const res = await resolveMixPlayable([]);
    expect(res).toEqual({ rows: [], queueRows: [], matched: 0, total: 0 });
    expect(spy).not.toHaveBeenCalled();
  });

  it('a non-success payload resolves null', async () => {
    stubFetch({ success: false, error: 'nope' });
    expect(await resolveMixPlayable([{ title: 'A', artist: 'X' }])).toBeNull();
  });
});

describe('realistic player outcomes and competing requests', () => {
  it.each([undefined, { status: 'failed' }, { status: 'busy' }, { status: 'empty' }])(
    'does not infer playback from %j',
    async (outcome) => {
      stubFetch({ success: true, tracks: [{ file_path: '/m/a' }], matched: 1, total: 1 });
      window.playTrackList = vi.fn(async () => outcome);
      expect(await playTrackNow({ title: 'A', artist: 'X' }, 'A')).toBe('failed');
      expect(toasts.some((t) => t.type === 'success')).toBe(false);
    },
  );

  it('a slow old resolution cannot replace the latest play choice', async () => {
    const answers: ((value: unknown) => void)[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise((resolve) => answers.push(resolve))),
    );
    const a = playTrackNow({ title: 'A', artist: 'X' }, 'A');
    const b = playTrackNow({ title: 'B', artist: 'X' }, 'B');
    const response = (title: string) => ({
      ok: true,
      json: async () => ({
        success: true,
        tracks: [{ title, file_path: '/m/' + title }],
        matched: 1,
        total: 1,
      }),
    });
    answers[1](response('B'));
    expect(await b).toBe('played');
    answers[0](response('A'));
    expect(await a).toBe('superseded');
    expect(played.map((p) => p.name)).toEqual(['B']);
  });
});
