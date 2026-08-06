/**
 * The server tab's pure core, pinned against pages-extra.js 59-94 and 484.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  fetchMirroredMatches,
  fetchMirroredPlaylistById,
  fetchServerPlaylistData,
  formatDurationMs,
  serverCardHue,
  serverTabTitle,
  splitServerPlaylists,
} from './-sync.server';

const PL = (id: string, name: string) => ({ id, name, track_count: 10 });

describe('splitServerPlaylists (59-73)', () => {
  it('marks a playlist synced from EITHER the mirrored list or the history', () => {
    const { synced, unsynced } = splitServerPlaylists(
      [PL('1', 'Road Trip'), PL('2', 'From History'), PL('3', 'Neither')],
      ['Road Trip'],
      ['From History'],
    );
    expect(synced.map((p) => p.name)).toEqual(['Road Trip', 'From History']);
    expect(unsynced.map((p) => p.name)).toEqual(['Neither']);
    expect(synced[0]._synced).toBe(true);
    expect(unsynced[0]._synced).toBe(false);
  });

  it('matches trimmed and case-insensitively, on BOTH sides', () => {
    const { synced } = splitServerPlaylists([PL('1', '  ROAD trip  ')], ['road TRIP'], []);
    expect(synced).toHaveLength(1);
  });

  it('is not fooled by a partial name', () => {
    const { unsynced } = splitServerPlaylists([PL('1', 'Road')], ['Road Trip'], []);
    expect(unsynced).toHaveLength(1);
  });

  it('does not mutate the rows it was given', () => {
    const rows = [PL('1', 'Road Trip')];
    splitServerPlaylists(rows, ['Road Trip'], []);
    expect('_synced' in rows[0]).toBe(false);
  });
});

describe('serverTabTitle (76-78)', () => {
  it('upper-cases only the first letter', () => {
    expect(serverTabTitle('plex')).toBe('Server Playlists (Plex)');
    expect(serverTabTitle('navidrome')).toBe('Server Playlists (Navidrome)');
  });

  it('leaves the parens empty when the type is unknown', () => {
    expect(serverTabTitle(undefined)).toBe('Server Playlists ()');
    expect(serverTabTitle('')).toBe('Server Playlists ()');
  });
});

describe('serverCardHue (94)', () => {
  it('steps by 37 from 200 and wraps at 360', () => {
    expect(serverCardHue(0)).toBe(200);
    expect(serverCardHue(1)).toBe(237);
    // 4*37+200 = 348; the next one wraps.
    expect(serverCardHue(4)).toBe(348);
    expect(serverCardHue(5)).toBe(25);
  });
});

describe('formatDurationMs (484)', () => {
  it('ROUNDS to the nearest second — not floor', () => {
    expect(formatDurationMs(1500)).toBe('0:02');
    expect(formatDurationMs(1400)).toBe('0:01');
  });

  it('pads the seconds and does not roll into hours', () => {
    expect(formatDurationMs(219000)).toBe('3:39');
    expect(formatDurationMs(61000)).toBe('1:01');
    expect(formatDurationMs(3600000)).toBe('60:00');
  });

  it('is empty for nothing', () => {
    expect(formatDurationMs(0)).toBe('');
    expect(formatDurationMs(null)).toBe('');
    expect(formatDurationMs(undefined)).toBe('');
  });
});

describe('fetchServerPlaylistData (41-52)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('loads all three in parallel and returns the names', async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        urls.push(url);
        if (url === '/api/server/playlists') {
          return new Response(
            JSON.stringify({ success: true, server_type: 'plex', playlists: [PL('1', 'A')] }),
          );
        }
        if (url === '/api/mirrored-playlists') {
          return new Response(JSON.stringify([{ name: 'A' }]));
        }
        return new Response(JSON.stringify(['B']));
      }),
    );
    const result = await fetchServerPlaylistData();
    expect(urls).toEqual([
      '/api/server/playlists',
      '/api/mirrored-playlists',
      '/api/sync/history/names',
    ]);
    expect(result.mirroredNames).toEqual(['A']);
    expect(result.historyNames).toEqual(['B']);
  });

  it('a broken mirrored or history response still yields the playlists (48, 51)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === '/api/server/playlists') {
          return new Response(JSON.stringify({ success: true, playlists: [PL('1', 'A')] }));
        }
        // Not JSON — .json() rejects, which the vanilla swallows.
        return new Response('<html>502</html>');
      }),
    );
    const result = await fetchServerPlaylistData();
    expect(result.data.playlists).toHaveLength(1);
    expect(result.mirroredNames).toEqual([]);
    expect(result.historyNames).toEqual([]);
  });

  it('a non-array mirrored payload is ignored, not spread', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url === '/api/server/playlists'
          ? new Response(JSON.stringify({ success: true, playlists: [] }))
          : new Response(JSON.stringify({ error: 'nope' })),
      ),
    );
    const result = await fetchServerPlaylistData();
    expect(result.mirroredNames).toEqual([]);
  });
});

describe('fetchMirroredMatches (158-171)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('keeps only exact name matches, trimmed and case-insensitive', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify([
              { id: 1, name: 'Road Trip' },
              { id: 2, name: '  road trip ' },
              { id: 3, name: 'Road Trip 2' },
            ]),
          ),
      ),
    );
    expect((await fetchMirroredMatches('Road Trip')).map((p) => p.id)).toEqual([1, 2]);
  });

  it('SWALLOWS a failure and answers empty — the server-only path (168-170)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('network down');
      }),
    );
    await expect(fetchMirroredMatches('Road Trip')).resolves.toEqual([]);
  });
});

describe('fetchMirroredPlaylistById (237-239)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('fetches the ONE row by id, not the whole list', async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        urls.push(url);
        return new Response(JSON.stringify({ id: 10, name: 'Road Trip', source: 'tidal' }));
      }),
    );
    expect(await fetchMirroredPlaylistById(10)).toMatchObject({ id: 10, source: 'tidal' });
    expect(urls).toEqual(['/api/mirrored-playlists/10']);
  });
});
