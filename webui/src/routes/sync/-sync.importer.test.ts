import { afterEach, describe, expect, it, vi } from 'vitest';

import { canCopyAsMine, copyPlaylistAsMine, type ImportSpec } from './-sync.importer';

/** #1913: keep a playlist as your own copy in My Playlists. */

const tracks = [
  {
    track_name: 'Nightcall',
    artist_name: 'Kavinsky',
    album_name: 'OutRun',
    duration_ms: 1,
    source_track_id: 'a',
  },
  { track_name: 'Untitled', artist_name: '', album_name: '', duration_ms: 1, source_track_id: 'b' },
  { track_name: 'Odd Look', artist_name: 'Kavinsky', album_name: 'OutRun', duration_ms: 2 },
];

function spec(over: Partial<ImportSpec> = {}): ImportSpec {
  return {
    source: 'listenbrainz',
    sourceId: 'mbid-1',
    name: 'Weekly Jams',
    loadTracks: async () => ({ tracks }),
    toMirrorTracks: (t) => t as Record<string, unknown>[],
    ...over,
  };
}

let posted: unknown[] = [];

function stubFetch(response: unknown = { success: true, id: 5 }) {
  posted = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.body) posted.push({ url, body: JSON.parse(init.body as string) });
      return new Response(JSON.stringify(response));
    }),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe('canCopyAsMine', () => {
  it('needs a playlist that loads its own songs', () => {
    expect(canCopyAsMine(spec())).toBe(true);
    // Beatport and SoulSync Discovery save server-side: no copy
    expect(canCopyAsMine(spec({ save: async () => 1 }))).toBe(false);
    expect(canCopyAsMine(spec({ loadTracks: undefined }))).toBe(false);
  });
});

describe('copyPlaylistAsMine', () => {
  it('keeps the songs that have an artist and title, with their origin when there is one', async () => {
    stubFetch();
    await expect(copyPlaylistAsMine(spec())).resolves.toBe(5);
    expect(posted).toEqual([
      {
        url: '/api/user-playlists',
        body: {
          name: 'Weekly Jams',
          tracks: [
            {
              track_name: 'Nightcall',
              artist_name: 'Kavinsky',
              album_name: 'OutRun',
              duration_ms: 1,
              image_url: null,
              source: 'listenbrainz',
              source_id: 'a',
            },
            // no id, no origin: identify searches for it like a typed song
            {
              track_name: 'Odd Look',
              artist_name: 'Kavinsky',
              album_name: 'OutRun',
              duration_ms: 2,
              image_url: null,
            },
          ],
        },
      },
    ]);
  });

  it('nothing to keep is an error, not an empty playlist', async () => {
    stubFetch();
    await expect(
      copyPlaylistAsMine(spec({ loadTracks: async () => ({ tracks: [] }) })),
    ).rejects.toThrow('Weekly Jams has no tracks to copy');
    expect(posted).toEqual([]);
  });

  it('refuses a playlist that saves itself', async () => {
    stubFetch();
    await expect(copyPlaylistAsMine(spec({ save: async () => 1 }))).rejects.toThrow(
      'Weekly Jams can only be imported, not copied',
    );
  });
});
