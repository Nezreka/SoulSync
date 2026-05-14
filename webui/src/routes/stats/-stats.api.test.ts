import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import {
  fetchListeningStatsStatus,
  fetchStatsCached,
  fetchStatsDbStorage,
  fetchStatsLibraryDiskUsage,
  resolveStatsTrack,
  streamStatsTrack,
  triggerListeningStatsSync,
} from './-stats.api';

describe('stats api', () => {
  it('fetches the cached stats payload for a range', async () => {
    server.use(
      http.get('/api/stats/cached', ({ request }) => {
        const url = new URL(request.url);
        expect(url.searchParams.get('range')).toBe('30d');

        return HttpResponse.json({
          success: true,
          overview: { total_plays: 12 },
          top_artists: [],
          top_albums: [],
          top_tracks: [],
          timeline: [],
          genres: [],
          recent: [],
          health: {},
        });
      }),
    );

    await expect(fetchStatsCached('30d')).resolves.toMatchObject({
      overview: { total_plays: 12 },
    });
  });

  it('surfaces db storage and disk usage errors', async () => {
    server.use(
      http.get('/api/stats/db-storage', () =>
        HttpResponse.json({ error: 'db unavailable' }, { status: 500 }),
      ),
      http.get('/api/stats/library-disk-usage', () =>
        HttpResponse.json({ error: 'disk unavailable' }, { status: 500 }),
      ),
    );

    await expect(fetchStatsDbStorage()).rejects.toThrow('db unavailable');
    await expect(fetchStatsLibraryDiskUsage()).rejects.toThrow('disk unavailable');
  });

  it('reads listening status and triggers manual sync', async () => {
    server.use(
      http.get('/api/listening-stats/status', () =>
        HttpResponse.json({ stats: { last_poll: '2026-05-14 10:00:00' } }),
      ),
      http.post('/api/listening-stats/sync', () => HttpResponse.json({ success: true })),
    );

    await expect(fetchListeningStatsStatus()).resolves.toEqual({
      stats: { last_poll: '2026-05-14 10:00:00' },
    });
    await expect(triggerListeningStatsSync()).resolves.toBeUndefined();
  });

  it('resolves and streams tracks through the stats playback helpers', async () => {
    server.use(
      http.post('/api/stats/resolve-track', async ({ request }) => {
        await expect(request.json()).resolves.toEqual({
          title: 'Track',
          artist: 'Artist',
        });
        return HttpResponse.json({
          success: true,
          track: { id: 1, title: 'Track', file_path: '/music/track.flac' },
        });
      }),
      http.post('/api/enhanced-search/stream-track', async ({ request }) => {
        await expect(request.json()).resolves.toEqual({
          track_name: 'Track',
          artist_name: 'Artist',
          album_name: 'Album',
          duration_ms: 0,
        });
        return HttpResponse.json({
          success: true,
          result: { stream_url: '/api/stream/1' },
        });
      }),
    );

    await expect(resolveStatsTrack('Track', 'Artist')).resolves.toMatchObject({
      id: 1,
      title: 'Track',
    });
    await expect(streamStatsTrack('Track', 'Artist', 'Album')).resolves.toEqual({
      stream_url: '/api/stream/1',
    });
  });
});
