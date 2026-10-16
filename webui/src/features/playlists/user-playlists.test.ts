import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  addToUserPlaylist,
  clearPlaylistCover,
  createUserPlaylist,
  duplicateUserPlaylist,
  isUserPlaylist,
  movedOrder,
  setPlaylistCoverFromTrack,
  setPlaylistCoverUrl,
  setPlaylistDescription,
  uploadPlaylistCover,
  copyMirrorToUserPlaylist,
  fetchMembership,
  isLikedPlaylist,
  likeSongs,
  pinLikedFirst,
  removeSongsFromUserPlaylist,
  unlikeSongs,
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

describe('playlist meta', () => {
  function okFetch(body: unknown) {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
  }

  it('saves the description with PATCH', async () => {
    const fetchMock = okFetch({ success: true, description: 'no skips' });
    await expect(setPlaylistDescription(7, 'no skips')).resolves.toEqual({
      success: true,
      description: 'no skips',
    });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/user-playlists/7');
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body as string)).toEqual({ description: 'no skips' });
  });

  it('duplicates with and without a name', async () => {
    const fetchMock = okFetch({ success: true, id: 9, name: 'Mix (copy)' });
    await duplicateUserPlaylist(4);
    let [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({});
    await duplicateUserPlaylist(4, 'V2');
    const [dupUrl, dupInit] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(JSON.parse(dupInit.body as string)).toEqual({ name: 'V2' });
    expect(dupUrl).toBe('/api/user-playlists/4/duplicate');
  });

  it('sets the cover from a url or a track position', async () => {
    const fetchMock = okFetch({ success: true, image_url: 'https://img/x.jpg' });
    await setPlaylistCoverUrl(4, 'https://img/x.jpg');
    let [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/user-playlists/4/cover');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(init.body as string)).toEqual({ image_url: 'https://img/x.jpg' });
    await setPlaylistCoverFromTrack(4, 2);
    [url, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ from_track: 2 });
  });

  it('clears the cover with DELETE', async () => {
    const fetchMock = okFetch({ success: true });
    await clearPlaylistCover(4);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/user-playlists/4/cover');
    expect(init.method).toBe('DELETE');
  });

  it('uploads the file as multipart form data', async () => {
    const fetchMock = okFetch({ success: true, image_url: '/api/user-playlists/4/cover?v=1' });
    const file = new File(['bytes'], 'cover.png', { type: 'image/png' });
    await uploadPlaylistCover(4, file);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/user-playlists/4/cover');
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get('file')).toBe(file);
  });
});

describe('round two: ticks, likes, copying a mirror', () => {
  const SONG = [{ track_name: 'T', artist_name: 'A' }];

  function capture(reply: unknown = { success: true }) {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(reply)));
    vi.stubGlobal('fetch', fetchMock);
    return () => {
      const [url, init] = fetchMock.mock.calls.at(-1) as unknown as [string, RequestInit];
      return {
        url,
        method: init.method,
        body: init.body ? JSON.parse(init.body as string) : undefined,
      };
    };
  }

  it.each([
    ['membership', () => fetchMembership(SONG), '/api/user-playlists/membership', { tracks: SONG }],
    [
      'untick',
      () => removeSongsFromUserPlaylist(4, SONG),
      '/api/user-playlists/4/tracks/remove',
      { tracks: SONG },
    ],
    ['like', () => likeSongs(SONG), '/api/user-playlists/liked', { tracks: SONG }],
    ['unlike', () => unlikeSongs(SONG), '/api/user-playlists/liked/remove', { tracks: SONG }],
    ['copy a mirror', () => copyMirrorToUserPlaylist(3), '/api/user-playlists/from-mirror/3', {}],
    [
      'copy a mirror, renamed',
      () => copyMirrorToUserPlaylist(3, 'Mine'),
      '/api/user-playlists/from-mirror/3',
      { name: 'Mine' },
    ],
  ])('%s posts where the server listens', async (_name, call, url, body) => {
    const last = capture();
    await call();
    expect(last()).toEqual({ url, method: 'POST', body });
  });

  it('isLikedPlaylist is a user playlist with the liked id, nothing else', () => {
    expect(isLikedPlaylist({ source: 'soulsync', source_playlist_id: 'liked' })).toBe(true);
    expect(isLikedPlaylist({ source: 'spotify', source_playlist_id: 'liked' })).toBe(false);
    expect(isLikedPlaylist({ source: 'soulsync', source_playlist_id: 'abc' })).toBe(false);
    expect(isLikedPlaylist(null)).toBe(false);
  });

  it('pinLikedFirst moves Liked Songs up and keeps the rest in order', () => {
    const rows = [
      { id: 1, source: 'soulsync' },
      { id: 2, source: 'soulsync', source_playlist_id: 'liked' },
      { id: 3, source: 'spotify' },
    ];
    expect(pinLikedFirst(rows).map((r) => r.id)).toEqual([2, 1, 3]);
  });
});
