import { afterEach, describe, expect, it, vi } from 'vitest';

import { getCachedTrackBlobUrl, prefetchUpcomingTracks, setPrefetchCount } from './crossfade';
import { cuesAt } from './vtt';

const cue = (start: number, end: number, text = `${start}`) => ({ start, end, text });

describe('subtitles on screen', () => {
  it('a long cue still shows minutes in', () => {
    const cues = [
      cue(0, 300, 'sign'),
      ...Array.from({ length: 40 }, (_, i) => cue(10 + i, 10.5 + i)),
    ];
    expect(cuesAt(cues as never, 200).map((c) => c.text)).toEqual(['sign']);
  });

  it('many cues at once (typesetting) all show', () => {
    const cues = Array.from({ length: 14 }, (_, i) => cue(5, 20, `line ${i}`));
    expect(cuesAt(cues as never, 10)).toHaveLength(14);
  });
});

describe('fetching songs ahead', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setPrefetchCount(0);
  });

  function stubFetch(sizeOf: (id: string) => number) {
    let n = 0;
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: () => `blob:${n++}`,
      revokeObjectURL: vi.fn(),
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const id = decodeURIComponent(url.split('/').pop()!);
        return { ok: true, blob: async () => ({ size: sizeOf(id) }) };
      }),
    );
  }

  const tracks = (ids: string[]) => ids.map((id) => ({ type: 'track' as const, id, title: id }));
  const settle = () => new Promise((r) => setTimeout(r, 20));

  it('a file too big to hold is left to stream', async () => {
    stubFetch(() => 120 * 1024 * 1024);
    setPrefetchCount(3);
    prefetchUpcomingTracks(tracks(['now', 'big1']), 0);
    await settle();
    expect(getCachedTrackBlobUrl('big1')).toBeNull();
  });

  it('the cache stays under its byte budget', async () => {
    stubFetch(() => 70 * 1024 * 1024);
    setPrefetchCount(10);
    prefetchUpcomingTracks(tracks(['now', 'a', 'b', 'c', 'd', 'e']), 0);
    await settle();
    const held = ['a', 'b', 'c', 'd', 'e'].filter((id) => getCachedTrackBlobUrl(id));
    expect(held.length).toBeLessThanOrEqual(2);
  });

  it('nothing ahead when the browser is saving data', async () => {
    stubFetch(() => 1024);
    vi.stubGlobal('navigator', { userAgent: navigator.userAgent, connection: { saveData: true } });
    setPrefetchCount(3);
    prefetchUpcomingTracks(tracks(['now', 'x']), 0);
    await settle();
    expect(fetch).not.toHaveBeenCalled();
  });
});
