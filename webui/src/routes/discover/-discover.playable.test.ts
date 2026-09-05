/** the play-now bridge: resolve-against-library + hand-off to the player. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
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
        new Promise<void>((resolve) => {
          release = resolve;
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

describe('playTrackNow', () => {
  it('plays one row and names it', async () => {
    stubFetch({ success: true, tracks: [{ file_path: '/m/a' }], matched: 1, total: 1 });
    const outcome = await playTrackNow({ title: 'A', artist: 'X' }, 'A');
    expect(outcome).toBe('played');
    expect(played[0].tracks).toHaveLength(1);
    expect(toasts[0].msg).toBe('Playing A');
  });

  it('an unowned row says it will be fetched, not that it is playing', async () => {
    stubFetch({
      success: true,
      tracks: [],
      queue_tracks: [{ title: 'A', artist: 'X', playback_status: 'missing' }],
      matched: 0,
      total: 1,
    });
    await playTrackNow({ title: 'A', artist: 'X' }, 'A');
    expect(toasts[0].msg).toContain('downloading it first');
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
