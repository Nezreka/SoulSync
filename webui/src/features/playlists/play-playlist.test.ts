import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { playPlaylist, resolvePlaylist, shuffled, toResolvePairs } from './play-playlist';

/**
 * playing a whole playlist. the rows are artist + title, so they go through
 * the same resolve-playable bridge the discover mixes use, in chunks it takes.
 */

const T = (n: number) => ({
  track_name: `t${n}`,
  artist_name: 'a',
  album_name: 'al',
  image_url: '/i.jpg',
});

let bodies: { tracks: { title: string }[] }[] = [];

beforeEach(() => {
  bodies = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(init?.body as string);
      bodies.push(body);
      // own the first one of each chunk, the rest are missing rows
      const queue_tracks = body.tracks.map((t: { title: string }, i: number) =>
        i === 0 ? { ...t, file_path: `/m/${t.title}.flac` } : { ...t, file_path: '' },
      );
      return new Response(JSON.stringify({ success: true, queue_tracks, matched: 1 }));
    }),
  );
  window.playTrackList = vi.fn(async () => ({ status: 'played' }));
  window.addTracksToQueue = vi.fn();
  window.showToast = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.playTrackList;
  delete window.addTracksToQueue;
});

describe('toResolvePairs', () => {
  it('keeps album and art, drops a row without a title or artist', () => {
    expect(
      toResolvePairs([T(1), { track_name: '', artist_name: 'x' }, { track_name: 'y' }]),
    ).toEqual([{ title: 't1', artist: 'a', album: 'al', image_url: '/i.jpg' }]);
  });
});

describe('shuffled', () => {
  it('keeps every item and leaves the input alone', () => {
    const input = [1, 2, 3, 4, 5];
    const out = shuffled(input, () => 0);
    expect([...out].sort((a, b) => a - b)).toEqual(input);
    expect(input).toEqual([1, 2, 3, 4, 5]);
    expect(out).not.toEqual(input);
  });
});

describe('resolvePlaylist', () => {
  it('asks in chunks of 250 and keeps the order', async () => {
    const tracks = Array.from({ length: 260 }, (_, i) => T(i));
    const res = await resolvePlaylist(tracks);
    expect(bodies.map((b) => b.tracks.length)).toEqual([250, 10]);
    expect(res?.rows).toHaveLength(260);
    expect(res?.rows[250]).toMatchObject({ title: 't250' });
    expect(res?.owned).toBe(2);
  });

  it('is null when the library check fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{}', { status: 500 })),
    );
    expect(await resolvePlaylist([T(1)])).toBeNull();
  });
});

describe('playPlaylist', () => {
  it('plays it as the queue, named for the playlist, and says what will download', async () => {
    expect(await playPlaylist([T(1), T(2), T(3)], 'Vibes')).toBe(true);
    const [rows, name] = vi.mocked(window.playTrackList!).mock.calls[0];
    expect((rows as { title: string }[]).map((r) => r.title)).toEqual(['t1', 't2', 't3']);
    expect(name).toBe('Vibes');
    expect(window.showToast).toHaveBeenCalledWith(
      'Playing Vibes. 2 songs will download first',
      'success',
    );
  });

  it('shuffle plays every song, in another order', async () => {
    vi.spyOn(Math, 'random').mockReturnValue(0);
    await playPlaylist([T(1), T(2), T(3)], 'Vibes', 'shuffle');
    const [rows] = vi.mocked(window.playTrackList!).mock.calls[0];
    const order = (rows as { title: string }[]).map((r) => r.title);
    expect([...order].sort((a, b) => a.localeCompare(b))).toEqual(['t1', 't2', 't3']);
    expect(order).not.toEqual(['t1', 't2', 't3']);
    vi.mocked(Math.random).mockRestore();
  });

  it('queue puts the whole playlist on the end of the queue in one go', async () => {
    await playPlaylist([T(1), T(2)], 'Vibes', 'queue');
    expect(window.addTracksToQueue).toHaveBeenCalledTimes(1);
    expect(window.playTrackList).not.toHaveBeenCalled();
  });

  it('says so when there is nothing to play, without asking the server', async () => {
    expect(await playPlaylist([], 'Empty')).toBe(false);
    expect(bodies).toEqual([]);
    expect(window.showToast).toHaveBeenCalledWith('Empty has nothing to play yet', 'info');
  });

  it('does not claim it played when the player could not start', async () => {
    window.playTrackList = vi.fn(async () => ({ status: 'failed' }));
    expect(await playPlaylist([T(1)], 'Vibes')).toBe(false);
    expect(window.showToast).toHaveBeenCalledWith('Playback could not start. Try again.', 'error');
  });

  it('a page with no player says that', async () => {
    delete window.playTrackList;
    expect(await playPlaylist([T(1)], 'Vibes')).toBe(false);
    expect(window.showToast).toHaveBeenCalledWith(
      'The player is not available on this page',
      'error',
    );
  });
});
