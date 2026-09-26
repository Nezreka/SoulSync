import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  applyReidentifyRequest,
  fetchReidentifySources,
  rankReidentifyResults,
  reidentifyResultBits,
} from './-artist-detail.reidentify';

/** Re-identify (#889): ISRC-first ranking + the staging request. */

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('rankReidentifyResults', () => {
  it('floats ISRC-bearing rows first, keeping each group stable', () => {
    const ranked = rankReidentifyResults([
      { album_name: 'a' },
      { album_name: 'b', isrc: 'X1' },
      { album_name: 'c' },
      { album_name: 'd', isrc: 'X2' },
    ]);
    expect(ranked.map((r) => r.album_name)).toEqual(['b', 'd', 'a', 'c']);
  });
});

describe('reidentifyResultBits', () => {
  it('joins year and track count, singular-aware', () => {
    expect(reidentifyResultBits({ year: 1992, total_tracks: 13 })).toBe('1992 · 13 tracks');
    expect(reidentifyResultBits({ total_tracks: 1 })).toBe('1 track');
    expect(reidentifyResultBits({})).toBe('');
  });
});

describe('requests', () => {
  it('sources: an unreachable endpoint degrades to an empty list', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
        throw new Error('offline');
      }),
    );
    expect(await fetchReidentifySources()).toEqual([]);
  });

  it('apply: posts the staging hint and resolves to the success toast line', async () => {
    const spy = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ success: true, album_name: 'SAW 85-92' })),
    );
    vi.stubGlobal('fetch', spy);
    const message = await applyReidentifyRequest(9, { source: 'spotify', track_id: 'sp-t' }, true);
    expect(message).toBe("Re-filing under “SAW 85-92” — it'll update after the next import pass.");
    expect(JSON.parse(String(spy.mock.calls[0]?.[1]?.body))).toEqual({
      library_track_id: 9,
      source: 'spotify',
      track_id: 'sp-t',
      replace: true,
    });
  });

  it('apply: a failure throws with the server message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async (_input: RequestInfo | URL, _init?: RequestInit) =>
          new Response(JSON.stringify({ success: false, error: 'no staging dir' }), {
            status: 500,
          }),
      ),
    );
    await expect(applyReidentifyRequest(9, { source: 's', track_id: 't' }, false)).rejects.toThrow(
      'no staging dir',
    );
  });
});
