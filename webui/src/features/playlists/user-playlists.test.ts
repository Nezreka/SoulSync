import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  addToUserPlaylist,
  createUserPlaylist,
  isUserPlaylist,
  movedOrder,
} from './user-playlists';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('movedOrder', () => {
  it('moves one row and keeps everything else in order', () => {
    expect(movedOrder(4, 3, 0)).toEqual([4, 1, 2, 3]);
    expect(movedOrder(4, 0, 3)).toEqual([2, 3, 4, 1]);
    expect(movedOrder(4, 1, 2)).toEqual([1, 3, 2, 4]);
  });

  it('a no-op or out-of-range move leaves the order alone', () => {
    expect(movedOrder(3, 1, 1)).toEqual([1, 2, 3]);
    expect(movedOrder(3, -1, 2)).toEqual([1, 2, 3]);
    expect(movedOrder(3, 0, 3)).toEqual([1, 2, 3]);
  });
});

describe('isUserPlaylist', () => {
  it('is the soulsync source and nothing else', () => {
    expect(isUserPlaylist({ source: 'soulsync' })).toBe(true);
    expect(isUserPlaylist({ source: 'spotify' })).toBe(false);
    expect(isUserPlaylist(null)).toBe(false);
  });
});

describe('the requests', () => {
  it('sends the duplicate flag and throws the server message on an error', async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ error: 'Playlist not found' }), { status: 404 }),
    );
    vi.stubGlobal('fetch', fetchMock);
    await expect(
      addToUserPlaylist(4, [{ track_name: 'T', artist_name: 'A' }], true),
    ).rejects.toThrow('Playlist not found');
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/user-playlists/4/tracks');
    expect(JSON.parse(init.body as string)).toEqual({
      tracks: [{ track_name: 'T', artist_name: 'A' }],
      allow_duplicates: true,
    });
  });

  it('a non-json failure still throws', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<html>502</html>', { status: 502 })),
    );
    await expect(createUserPlaylist('x')).rejects.toThrow('Request failed (502)');
  });
});
