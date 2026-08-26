/** the play-now bridge: resolve-against-library + hand-off to the player. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { playMixNow, resolveMixPlayable, toPlayablePairs } from './-discover.playable';

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
      matched: 1,
      total: 2,
    });
    const outcome = await playMixNow([{ title: 'A', artist: 'X' }], 'Daily Mix 1');
    expect(outcome).toBe('played');
    expect(played).toHaveLength(1);
    expect(played[0].name).toBe('Daily Mix 1');
    expect(toasts[0].msg).toContain('Playing 1 of 2');
  });

  it('says all-owned when everything matched', async () => {
    stubFetch({ success: true, tracks: [{ file_path: '/m/a' }], matched: 1, total: 1 });
    await playMixNow([{ title: 'A', artist: 'X' }], 'Mix');
    expect(toasts[0].msg).toContain('Playing all 1');
  });

  it('nothing owned: honest toast, no playback', async () => {
    stubFetch({ success: true, tracks: [], matched: 0, total: 5 });
    const outcome = await playMixNow([{ title: 'A', artist: 'X' }], 'Mix');
    expect(outcome).toBe('empty');
    expect(played).toHaveLength(0);
    expect(toasts[0].msg).toContain('library yet');
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
});

describe('resolveMixPlayable', () => {
  it('an empty tracklist resolves without a network call', async () => {
    const spy = vi.fn();
    vi.stubGlobal('fetch', spy);
    const res = await resolveMixPlayable([]);
    expect(res).toEqual({ rows: [], matched: 0, total: 0 });
    expect(spy).not.toHaveBeenCalled();
  });

  it('a non-success payload resolves null', async () => {
    stubFetch({ success: false, error: 'nope' });
    expect(await resolveMixPlayable([{ title: 'A', artist: 'X' }])).toBeNull();
  });
});
