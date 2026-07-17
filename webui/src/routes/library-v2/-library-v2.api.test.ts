import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import {
  analyzeLibraryV2TrackReplayGain,
  applyLibraryV2AlbumArt,
  applyLibraryV2AlbumReorganize,
  applyLibraryV2ArtistArt,
  applyLibraryV2ArtistReorganizeAll,
  blacklistLibraryV2Source,
  deleteLibraryV2Files,
  enrichLibraryV2Entity,
  fetchLibraryV2AlbumArtOptions,
  fetchLibraryV2AlbumMatchStatus,
  fetchLibraryV2AlbumReorganizeSources,
  fetchLibraryV2ArtistArtOptions,
  fetchLibraryV2ArtistSettings,
  fetchLibraryV2ArtistMatchStatus,
  fetchLibraryV2ArtistTrackFiles,
  fetchLibraryV2FileDeletePreview,
  fetchLibraryV2Playlist,
  fetchLibraryV2Playlists,
  fetchLibraryV2ReorganizeSourcesGlobal,
  fetchLibraryV2TrackHistory,
  fetchLibraryV2TrackSourceInfo,
  fetchLibraryV2UiPreferences,
  manualMatchLibraryV2Entity,
  materializeLibraryV2MissingTrack,
  previewLibraryV2AlbumReorganize,
  removeLibraryV2FileRecords,
  runLibraryV2PlaylistPipeline,
  runRepairJob,
  searchLibraryV2MatchService,
  startLibraryV2AlbumReplayGain,
  startLibraryV2ScopedSearch,
  updateLibraryV2MetadataOverrides,
  updateLibraryV2ArtistSettings,
  updateLibraryV2UiPreferences,
} from './-library-v2.api';

describe('library v2 metadata api', () => {
  it('sends one batch command for set and clear operations', async () => {
    server.use(
      http.patch('/api/library/v2/metadata-overrides/release_group/42', async ({ request }) => {
        expect(await request.json()).toEqual({
          set: { title: 'Corrected', year: 2024 },
          clear: ['album_type'],
        });
        return HttpResponse.json({
          success: true,
          overrides: { title: 'Corrected', year: 2024 },
        });
      }),
    );

    await expect(
      updateLibraryV2MetadataOverrides('release_group', 42, { title: 'Corrected', year: 2024 }, [
        'album_type',
      ]),
    ).resolves.toEqual({ title: 'Corrected', year: 2024 });
  });

  it('sends a track-level metadata correction to the track override endpoint', async () => {
    server.use(
      http.patch('/api/library/v2/metadata-overrides/track/900', async ({ request }) => {
        expect(await request.json()).toEqual({
          set: { title: 'Correct Title', track_number: 3 },
          clear: [],
        });
        return HttpResponse.json({
          success: true,
          overrides: { title: 'Correct Title', track_number: 3 },
        });
      }),
    );

    await expect(
      updateLibraryV2MetadataOverrides('track', 900, { title: 'Correct Title', track_number: 3 }),
    ).resolves.toEqual({ title: 'Correct Title', track_number: 3 });
  });

  it('surfaces a rejected metadata correction', async () => {
    server.use(
      http.patch('/api/library/v2/metadata-overrides/artist/7', () =>
        HttpResponse.json({ success: false, error: 'metadata override cannot be empty' }),
      ),
    );

    await expect(updateLibraryV2MetadataOverrides('artist', 7, { name: '' })).rejects.toThrow(
      'metadata override cannot be empty',
    );
  });

  it('sends lib2 artist identity for repair file-scope resolution', async () => {
    server.use(
      http.post('/api/repair/jobs/library_reorganize/run', async ({ request }) => {
        expect(await request.json()).toEqual({ artist_id: 17, artist_name: 'Corrected Artist' });
        return HttpResponse.json({ success: true, scope_files: 12 });
      }),
    );

    await expect(
      runRepairJob('library_reorganize', { id: 17, name: 'Corrected Artist' }),
    ).resolves.toBeUndefined();
  });
});

describe('library v2 artist settings api', () => {
  it('reads and writes the shared Watchlist Artist Settings contract', async () => {
    const settings = {
      artist_id: 7,
      watchlist_row_id: 11,
      watchlist_name: 'Drake',
      watchlist_image_url: null,
      provider_ids: { spotify: 'sp-drake' },
      monitor_new_items: 'all' as const,
      include_albums: true,
      include_eps: true,
      include_singles: true,
      include_live: false,
      include_remixes: false,
      include_acoustic: false,
      include_compilations: false,
      include_instrumentals: false,
      auto_download: true,
      lookback_days: null,
      preferred_metadata_source: null,
    };
    server.use(
      http.get('/api/library/v2/artists/7/settings', () =>
        HttpResponse.json({
          success: true,
          settings,
          metadata_sources: ['spotify', 'deezer'],
          global_metadata_source: 'spotify',
        }),
      ),
      http.put('/api/library/v2/artists/7/settings', async ({ request }) => {
        expect(await request.json()).toMatchObject({
          auto_download: false,
          preferred_metadata_source: 'deezer',
        });
        return HttpResponse.json({
          success: true,
          settings: { ...settings, auto_download: false, preferred_metadata_source: 'deezer' },
          metadata_sources: ['spotify', 'deezer'],
          global_metadata_source: 'spotify',
        });
      }),
    );

    const loaded = await fetchLibraryV2ArtistSettings(7);
    expect(loaded.settings.watchlist_row_id).toBe(11);
    await expect(
      updateLibraryV2ArtistSettings(7, {
        ...loaded.settings,
        auto_download: false,
        preferred_metadata_source: 'deezer',
      }),
    ).resolves.toMatchObject({
      settings: { auto_download: false, preferred_metadata_source: 'deezer' },
    });
  });
});

describe('library v2 source info api', () => {
  it('returns the download provenance rows for a track', async () => {
    server.use(
      http.get('/api/library/v2/tracks/55/source-info', () =>
        HttpResponse.json({
          success: true,
          downloads: [
            {
              id: 2,
              source_service: 'soulseek',
              source_username: 'user',
              source_filename: 'a.flac',
            },
            { id: 1, source_service: 'deezer' },
          ],
        }),
      ),
    );

    const info = await fetchLibraryV2TrackSourceInfo(55);
    expect(info.downloads).toHaveLength(2);
    expect(info.downloads[0].source_username).toBe('user');
    expect(info.manual_skips).toEqual([]);
  });

  it('blacklists a source through the app-wide route', async () => {
    server.use(
      http.post('/api/library/blacklist', async ({ request }) => {
        expect(await request.json()).toEqual({
          reason: 'user_rejected',
          track_artist: 'Drake',
          track_title: 'One Dance',
          blocked_filename: 'a.flac',
          blocked_username: 'user',
        });
        return HttpResponse.json({ success: true });
      }),
    );

    await expect(
      blacklistLibraryV2Source({
        track_title: 'One Dance',
        track_artist: 'Drake',
        blocked_filename: 'a.flac',
        blocked_username: 'user',
      }),
    ).resolves.toBeUndefined();
  });
});

describe('library v2 track history api (§52.9)', () => {
  it('returns the merged pipeline events for a track', async () => {
    server.use(
      http.get('/api/library/v2/tracks/55/history', () =>
        HttpResponse.json({
          success: true,
          history: [
            {
              date: '2026-07-17 10:00:00',
              event_type: 'import_file_quarantined',
              category: 'quarantined',
              title: 'Quarantined',
              detail: 'acoustid mismatch',
              source: 'acquisition',
            },
          ],
        }),
      ),
    );

    const history = await fetchLibraryV2TrackHistory(55);
    expect(history).toHaveLength(1);
    expect(history[0].event_type).toBe('import_file_quarantined');
  });

  it('throws when the backend reports failure', async () => {
    server.use(
      http.get('/api/library/v2/tracks/55/history', () =>
        HttpResponse.json({ success: false, error: 'Track not found' }, { status: 404 }),
      ),
    );

    await expect(fetchLibraryV2TrackHistory(55)).rejects.toThrow('Track not found');
  });
});

describe('library v2 missing-track add api', () => {
  it('materializes a missing slot and returns the new track id', async () => {
    server.use(
      http.post('/api/library/v2/albums/42/missing-tracks/materialize', async ({ request }) => {
        expect(await request.json()).toEqual({
          track_number: 7,
          disc_number: 2,
          title: 'Hidden Track',
        });
        return HttpResponse.json({ success: true, track_id: 501, created: true });
      }),
    );

    await expect(
      materializeLibraryV2MissingTrack(42, {
        track_number: 7,
        disc_number: 2,
        title: 'Hidden Track',
      }),
    ).resolves.toEqual({ track_id: 501, created: true });
  });

  it('surfaces a rejected materialization', async () => {
    server.use(
      http.post('/api/library/v2/albums/42/missing-tracks/materialize', () =>
        HttpResponse.json({ success: false, error: 'Album not found' }, { status: 404 }),
      ),
    );

    await expect(materializeLibraryV2MissingTrack(42, { track_number: 1 })).rejects.toThrow(
      'Album not found',
    );
  });
});

describe('library v2 match-status api', () => {
  it('fetches artist provider match chips', async () => {
    server.use(
      http.get('/api/library/v2/artists/7/match-status', () =>
        HttpResponse.json({
          success: true,
          services: [
            {
              service: 'spotify',
              label: 'Spotify',
              status: 'matched',
              external_id: 'sp1',
              last_attempted: null,
              legacy_entity_id: 3,
            },
          ],
        }),
      ),
    );
    const rows = await fetchLibraryV2ArtistMatchStatus(7);
    expect(rows[0].status).toBe('matched');
    expect(rows[0].legacy_entity_id).toBe(3);
  });

  it('fetches album + per-track match bundle', async () => {
    server.use(
      http.get('/api/library/v2/albums/9/match-status', () =>
        HttpResponse.json({ success: true, album: [], tracks: { 100: [] } }),
      ),
    );
    const bundle = await fetchLibraryV2AlbumMatchStatus(9);
    expect(bundle.tracks).toHaveProperty('100');
  });

  it('searches a provider and applies a manual match via the legacy endpoint', async () => {
    server.use(
      http.post('/api/library/search-service', async ({ request }) => {
        expect(await request.json()).toEqual({
          service: 'deezer',
          entity_type: 'album',
          query: 'Views',
        });
        return HttpResponse.json({ success: true, results: [{ id: 'dz1', name: 'Views' }] });
      }),
      http.put('/api/library/manual-match', async ({ request }) => {
        expect(await request.json()).toEqual({
          entity_type: 'album',
          entity_id: 42,
          service: 'deezer',
          service_id: 'dz1',
        });
        return HttpResponse.json({ success: true });
      }),
    );
    const results = await searchLibraryV2MatchService({
      service: 'deezer',
      entity_type: 'album',
      query: 'Views',
    });
    expect(results[0].id).toBe('dz1');
    await expect(
      manualMatchLibraryV2Entity({
        entity_type: 'album',
        legacy_entity_id: 42,
        service: 'deezer',
        service_id: 'dz1',
      }),
    ).resolves.toBeUndefined();
  });
});

describe('library v2 replaygain api', () => {
  it('starts an album ReplayGain job and returns the job id', async () => {
    server.use(
      http.post('/api/library/v2/albums/42/replaygain', () =>
        HttpResponse.json({ success: true, started: true, job_id: 'rg-1' }),
      ),
    );
    await expect(startLibraryV2AlbumReplayGain(42)).resolves.toBe('rg-1');
  });

  it('surfaces a missing ffmpeg error', async () => {
    server.use(
      http.post('/api/library/v2/albums/42/replaygain', () =>
        HttpResponse.json({ success: false, error: 'ffmpeg not found on PATH' }, { status: 500 }),
      ),
    );
    await expect(startLibraryV2AlbumReplayGain(42)).rejects.toThrow('ffmpeg not found on PATH');
  });

  it('analyzes a single track synchronously and returns its gain', async () => {
    server.use(
      http.post('/api/library/v2/tracks/12/replaygain', () =>
        HttpResponse.json({ success: true, analyzed: true, track_gain_db: -3.2 }),
      ),
    );
    await expect(analyzeLibraryV2TrackReplayGain(12)).resolves.toBe(-3.2);
  });
});

describe('library v2 scoped search api', () => {
  it('starts a scoped search job for the given entity and id', async () => {
    server.use(
      http.post('/api/library/v2/albums/7/search', () =>
        HttpResponse.json({ success: true, started: true, job_id: 'search-1' }),
      ),
    );
    await expect(startLibraryV2ScopedSearch('albums', 7)).resolves.toBe('search-1');
  });

  it('surfaces a server error', async () => {
    server.use(
      http.post('/api/library/v2/tracks/9/search', () =>
        HttpResponse.json({ success: false, error: 'Not found' }, { status: 404 }),
      ),
    );
    await expect(startLibraryV2ScopedSearch('tracks', 9)).rejects.toThrow('Not found');
  });
});

describe('library v2 enrich api', () => {
  it('sends the chosen service and returns whether the row was resynced', async () => {
    server.use(
      http.post('/api/library/v2/artists/7/enrich', async ({ request }) => {
        expect(await request.json()).toEqual({ service: 'lastfm' });
        return HttpResponse.json({
          success: true,
          message: 'lastfm lookup complete for artist',
          resynced: true,
        });
      }),
    );
    await expect(enrichLibraryV2Entity('artists', 7, 'lastfm')).resolves.toEqual({
      message: 'lastfm lookup complete for artist',
      resynced: true,
    });
  });

  it('surfaces the "no legacy record" error', async () => {
    server.use(
      http.post('/api/library/v2/albums/9/enrich', () =>
        HttpResponse.json(
          {
            success: false,
            error:
              'This entry has no legacy library record to enrich (it was added via Update Discography).',
          },
          { status: 409 },
        ),
      ),
    );
    await expect(enrichLibraryV2Entity('albums', 9, 'deezer')).rejects.toThrow(
      'no legacy library record',
    );
  });
});

describe('library v2 reorganize api', () => {
  it('fetches global reorganize sources', async () => {
    server.use(
      http.get('/api/library/v2/reorganize/sources', () =>
        HttpResponse.json({ success: true, sources: [{ source: 'deezer', label: 'Deezer' }] }),
      ),
    );
    await expect(fetchLibraryV2ReorganizeSourcesGlobal()).resolves.toEqual([
      { source: 'deezer', label: 'Deezer' },
    ]);
  });

  it('fetches per-album reorganize sources', async () => {
    server.use(
      http.get('/api/library/v2/albums/42/reorganize/sources', () =>
        HttpResponse.json({ success: true, sources: [{ source: 'spotify', label: 'Spotify' }] }),
      ),
    );
    await expect(fetchLibraryV2AlbumReorganizeSources(42)).resolves.toEqual([
      { source: 'spotify', label: 'Spotify' },
    ]);
  });

  it('previews a reorganize with the chosen source/mode', async () => {
    server.use(
      http.post('/api/library/v2/albums/42/reorganize/preview', async ({ request }) => {
        expect(await request.json()).toEqual({ source: 'spotify', mode: 'tags' });
        return HttpResponse.json({
          success: true,
          status: 'planned',
          source: 'spotify',
          album: 'Views',
          artist: 'Drake',
          transfer_dir: '/Transfer',
          tracks: [],
        });
      }),
    );
    await expect(
      previewLibraryV2AlbumReorganize(42, { source: 'spotify', mode: 'tags' }),
    ).resolves.toMatchObject({ status: 'planned', album: 'Views' });
  });

  it('surfaces the "no legacy record" preview error', async () => {
    server.use(
      http.post('/api/library/v2/albums/9/reorganize/preview', () =>
        HttpResponse.json(
          { success: false, error: 'This album has no legacy library record to reorganize.' },
          { status: 409 },
        ),
      ),
    );
    await expect(previewLibraryV2AlbumReorganize(9)).rejects.toThrow('no legacy library record');
  });

  it('enqueues an album reorganize', async () => {
    server.use(
      http.post('/api/library/v2/albums/42/reorganize', async ({ request }) => {
        expect(await request.json()).toEqual({ source: null, mode: 'api', rename_only: false });
        return HttpResponse.json({ success: true, queued: true, queue_id: 'q-1' });
      }),
    );
    await expect(applyLibraryV2AlbumReorganize(42)).resolves.toEqual({
      queued: true,
      queueId: 'q-1',
      reason: undefined,
    });
  });

  it('enqueues every album for an artist', async () => {
    server.use(
      http.post('/api/library/v2/artists/7/reorganize-all', () =>
        HttpResponse.json({ success: true, enqueued: 3, already_queued: 1, total_albums: 4 }),
      ),
    );
    await expect(applyLibraryV2ArtistReorganizeAll(7)).resolves.toEqual({
      enqueued: 3,
      alreadyQueued: 1,
      totalAlbums: 4,
    });
  });
});

describe('library v2 art picker api', () => {
  it('fetches candidate covers', async () => {
    server.use(
      http.get('/api/library/v2/albums/42/art-options', () =>
        HttpResponse.json({
          success: true,
          count: 1,
          candidates: [{ url: 'https://example.com/a.jpg', source: 'deezer', front: true }],
        }),
      ),
    );
    await expect(fetchLibraryV2AlbumArtOptions(42)).resolves.toEqual([
      { url: 'https://example.com/a.jpg', source: 'deezer', front: true },
    ]);
  });

  it('requests a refresh when asked', async () => {
    server.use(
      http.get('/api/library/v2/albums/42/art-options', ({ request }) => {
        expect(new URL(request.url).searchParams.get('refresh')).toBe('1');
        return HttpResponse.json({ success: true, candidates: [] });
      }),
    );
    await expect(fetchLibraryV2AlbumArtOptions(42, { refresh: true })).resolves.toEqual([]);
  });

  it('applies the chosen cover and returns the local artwork url', async () => {
    server.use(
      http.post('/api/library/v2/albums/42/art', async ({ request }) => {
        expect(await request.json()).toEqual({ url: 'https://example.com/pick.jpg' });
        return HttpResponse.json({
          success: true,
          album_id: 42,
          image_url: '/api/library/v2/artwork/album/42',
        });
      }),
    );
    await expect(applyLibraryV2AlbumArt(42, 'https://example.com/pick.jpg')).resolves.toBe(
      '/api/library/v2/artwork/album/42',
    );
  });

  it('surfaces an unresolvable-image error', async () => {
    server.use(
      http.post('/api/library/v2/albums/42/art', () =>
        HttpResponse.json(
          { success: false, error: 'Could not download or validate that image URL' },
          { status: 400 },
        ),
      ),
    );
    await expect(applyLibraryV2AlbumArt(42, 'https://example.com/dead.jpg')).rejects.toThrow(
      'Could not download or validate',
    );
  });
});

describe('library v2 artist image picker api (deep-dive A9)', () => {
  it('fetches candidate photos', async () => {
    server.use(
      http.get('/api/library/v2/artists/7/art-options', () =>
        HttpResponse.json({
          success: true,
          count: 1,
          candidates: [{ url: 'https://example.com/a.jpg', source: 'spotify' }],
        }),
      ),
    );
    await expect(fetchLibraryV2ArtistArtOptions(7)).resolves.toEqual([
      { url: 'https://example.com/a.jpg', source: 'spotify' },
    ]);
  });

  it('requests a refresh when asked', async () => {
    server.use(
      http.get('/api/library/v2/artists/7/art-options', ({ request }) => {
        expect(new URL(request.url).searchParams.get('refresh')).toBe('1');
        return HttpResponse.json({ success: true, candidates: [] });
      }),
    );
    await expect(fetchLibraryV2ArtistArtOptions(7, { refresh: true })).resolves.toEqual([]);
  });

  it('applies the chosen photo and returns the local artwork url', async () => {
    server.use(
      http.post('/api/library/v2/artists/7/art', async ({ request }) => {
        expect(await request.json()).toEqual({ url: 'https://example.com/pick.jpg' });
        return HttpResponse.json({
          success: true,
          artist_id: 7,
          image_url: '/api/library/v2/artwork/artist/7',
        });
      }),
    );
    await expect(applyLibraryV2ArtistArt(7, 'https://example.com/pick.jpg')).resolves.toBe(
      '/api/library/v2/artwork/artist/7',
    );
  });

  it('surfaces an unresolvable-image error', async () => {
    server.use(
      http.post('/api/library/v2/artists/7/art', () =>
        HttpResponse.json(
          { success: false, error: 'Could not download or validate that image URL' },
          { status: 400 },
        ),
      ),
    );
    await expect(applyLibraryV2ArtistArt(7, 'https://example.com/dead.jpg')).rejects.toThrow(
      'Could not download or validate',
    );
  });
});

describe('library v2 physical file delete api', () => {
  it('removes selected file records without calling the physical delete route', async () => {
    server.use(
      http.post('/api/library/v2/albums/42/file-remove', async ({ request }) => {
        expect(await request.json()).toEqual({ file_ids: [101] });
        return HttpResponse.json({
          success: true,
          operation: {
            id: 'db-only-1',
            status: 'completed',
            mode: 'database_only',
            actor: 'user',
            actor_profile_id: 1,
            file_count: 1,
            total_size: 4096,
            items: [],
          },
        });
      }),
    );

    await expect(removeLibraryV2FileRecords('albums', 42, [101])).resolves.toMatchObject({
      id: 'db-only-1',
      mode: 'database_only',
    });
  });

  it('previews and executes with the exact server token', async () => {
    server.use(
      http.get('/api/library/v2/albums/42/file-delete-preview', () =>
        HttpResponse.json({
          success: true,
          entity: 'albums',
          entity_id: 42,
          title: 'Views',
          configured_roots: ['/music'],
          files: [],
          file_count: 1,
          deletable_count: 1,
          unsafe_count: 0,
          total_size: 4096,
          preview_token: 'snapshot-token',
        }),
      ),
      http.post('/api/library/v2/albums/42/file-delete', async ({ request }) => {
        expect(await request.json()).toEqual({ preview_token: 'snapshot-token' });
        return HttpResponse.json({
          success: true,
          operation: {
            id: 'journal-1',
            status: 'completed',
            file_count: 1,
            total_size: 4096,
            items: [],
          },
        });
      }),
    );

    const preview = await fetchLibraryV2FileDeletePreview('albums', 42);
    await expect(deleteLibraryV2Files('albums', 42, preview.preview_token)).resolves.toMatchObject({
      id: 'journal-1',
      status: 'completed',
    });
  });

  it('narrows preview and delete to a caller-selected file_ids subset (C2)', async () => {
    server.use(
      http.get('/api/library/v2/artists/7/file-delete-preview', ({ request }) => {
        const url = new URL(request.url);
        expect(url.searchParams.get('file_ids')).toBe('101,102');
        return HttpResponse.json({
          success: true,
          entity: 'artists',
          entity_id: 7,
          title: 'Drake',
          configured_roots: ['/music'],
          files: [],
          file_count: 2,
          deletable_count: 2,
          unsafe_count: 0,
          total_size: 2048,
          preview_token: 'scoped-token',
        });
      }),
      http.post('/api/library/v2/artists/7/file-delete', async ({ request }) => {
        expect(await request.json()).toEqual({
          preview_token: 'scoped-token',
          file_ids: [101, 102],
        });
        return HttpResponse.json({
          success: true,
          operation: {
            id: 'journal-2',
            status: 'completed',
            file_count: 2,
            total_size: 2048,
            items: [],
          },
        });
      }),
    );

    const preview = await fetchLibraryV2FileDeletePreview('artists', 7, [101, 102]);
    await expect(
      deleteLibraryV2Files('artists', 7, preview.preview_token, [101, 102]),
    ).resolves.toMatchObject({ id: 'journal-2', status: 'completed' });
  });
});

describe('library v2 artist track files api (C2 — Manage Track Files)', () => {
  it('sends pagination/search params and returns the file list', async () => {
    server.use(
      http.get('/api/library/v2/artists/7/track-files', ({ request }) => {
        const url = new URL(request.url);
        expect(url.searchParams.get('search')).toBe('Nonstop');
        expect(url.searchParams.get('page')).toBe('2');
        expect(url.searchParams.get('limit')).toBe('50');
        return HttpResponse.json({
          success: true,
          files: [
            {
              file_id: 1,
              track_id: 2,
              track_title: 'Nonstop',
              track_number: 1,
              disc_number: 1,
              album_id: 3,
              album_title: 'Scorpion',
              path: '/m/a.flac',
              size: 4096,
              format: 'flac',
              bitrate: null,
              sample_rate: 44100,
              bit_depth: 16,
              quality_tier: 'lossless',
              file_state: 'active',
              is_primary: true,
              added_at: '2026-01-01T00:00:00',
            },
          ],
          pagination: {
            page: 2,
            limit: 50,
            total_count: 51,
            total_pages: 2,
            has_prev: true,
            has_next: false,
          },
        });
      }),
    );

    const result = await fetchLibraryV2ArtistTrackFiles(7, {
      search: 'Nonstop',
      page: 2,
      limit: 50,
    });
    expect(result.files).toHaveLength(1);
    expect(result.files[0]).toMatchObject({ file_id: 1, track_title: 'Nonstop' });
    expect(result.pagination).toMatchObject({ page: 2, total_pages: 2 });
  });
});

describe('library v2 playlist api', () => {
  it('reuses the mirrored-playlist list and detail reads', async () => {
    server.use(
      http.get('/api/mirrored-playlists', () =>
        HttpResponse.json([{ id: 9, display_name: 'Road Trip', pipeline_state: null }]),
      ),
      http.get('/api/mirrored-playlists/9', () =>
        HttpResponse.json({
          id: 9,
          display_name: 'Road Trip',
          pipeline_state: null,
          tracks: [{ id: 1, position: 1, track_name: 'One' }],
        }),
      ),
    );

    await expect(fetchLibraryV2Playlists()).resolves.toMatchObject([
      { id: 9, display_name: 'Road Trip' },
    ]);
    await expect(fetchLibraryV2Playlist(9)).resolves.toMatchObject({
      id: 9,
      tracks: [{ position: 1, track_name: 'One' }],
    });
  });

  it('starts the one existing mirrored-playlist pipeline', async () => {
    server.use(
      http.post('/api/mirrored-playlists/9/pipeline/run', async ({ request }) => {
        expect(await request.json()).toEqual({});
        return HttpResponse.json({
          success: true,
          state: { run_id: 'mirrored_9', playlist_id: 9, status: 'running', progress: 0 },
        });
      }),
    );

    await expect(runLibraryV2PlaylistPipeline(9)).resolves.toMatchObject({
      playlist_id: 9,
      status: 'running',
    });
  });
});

describe('library v2 ui preferences api (B5)', () => {
  it('fetches the stored/default preferences', async () => {
    server.use(
      http.get('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({
          success: true,
          preferences: {
            track_table: {
              columns: {
                disc: false,
                artists: true,
                duration: true,
                bpm: true,
                match: true,
                quality: true,
                features: true,
                metadata: true,
                file_path: false,
                play: false,
              },
              column_order: [
                'play',
                'disc',
                'artists',
                'duration',
                'bpm',
                'match',
                'quality',
                'features',
                'metadata',
                'file_path',
              ],
              show_all_match_providers: false,
              visible_match_providers: {
                spotify: true,
                musicbrainz: true,
                deezer: true,
                itunes: true,
                audiodb: true,
                discogs: true,
                lastfm: true,
                genius: true,
                tidal: true,
                qobuz: true,
                amazon: true,
                jiosaavn: true,
                bandcamp: true,
              },
              quality_show_format: true,
              quality_show_resolution: true,
              quality_show_bitrate: true,
            },
            artist_table: {
              columns: {
                quality_profile: false,
                genres: false,
                added: false,
              },
              column_order: ['quality_profile', 'genres', 'added'],
            },
          },
        }),
      ),
    );

    await expect(fetchLibraryV2UiPreferences()).resolves.toMatchObject({
      track_table: { show_all_match_providers: false, columns: { bpm: true } },
    });
  });

  it('sends a partial patch and returns the merged result', async () => {
    server.use(
      http.put('/api/library/v2/ui-preferences', async ({ request }) => {
        expect(await request.json()).toEqual({
          track_table: { columns: { file_path: true } },
        });
        return HttpResponse.json({
          success: true,
          preferences: {
            track_table: {
              columns: {
                bpm: true,
                file_path: true,
                disc: false,
                artists: true,
                duration: true,
                match: true,
                quality: true,
                features: true,
                metadata: true,
                play: false,
              },
              column_order: [
                'play',
                'disc',
                'artists',
                'duration',
                'bpm',
                'match',
                'quality',
                'features',
                'metadata',
                'file_path',
              ],
              show_all_match_providers: false,
              visible_match_providers: {
                spotify: true,
                musicbrainz: true,
                deezer: true,
                itunes: true,
                audiodb: true,
                discogs: true,
                lastfm: true,
                genius: true,
                tidal: true,
                qobuz: true,
                amazon: true,
                jiosaavn: true,
                bandcamp: true,
              },
              quality_show_format: true,
              quality_show_resolution: true,
              quality_show_bitrate: true,
            },
            artist_table: {
              columns: {
                quality_profile: false,
                genres: false,
                added: false,
              },
              column_order: ['quality_profile', 'genres', 'added'],
            },
          },
        });
      }),
    );

    await expect(
      updateLibraryV2UiPreferences({ track_table: { columns: { file_path: true } } }),
    ).resolves.toMatchObject({ track_table: { columns: { file_path: true } } });
  });

  it('surfaces a rejected update', async () => {
    server.use(
      http.put('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({
          success: false,
          error: 'Library v2 changes require the admin profile',
        }),
      ),
    );

    await expect(updateLibraryV2UiPreferences({ track_table: {} })).rejects.toThrow(
      'admin profile',
    );
  });

  it('round-trips an artist_table column patch (round 5, D6)', async () => {
    server.use(
      http.put('/api/library/v2/ui-preferences', async ({ request }) => {
        expect(await request.json()).toEqual({
          artist_table: { columns: { genres: true } },
        });
        return HttpResponse.json({
          success: true,
          preferences: {
            track_table: {
              columns: {
                bpm: true,
                file_path: false,
                disc: false,
                artists: true,
                duration: true,
                match: true,
                quality: true,
                features: true,
                metadata: true,
                play: false,
              },
              column_order: [
                'play',
                'disc',
                'artists',
                'duration',
                'bpm',
                'match',
                'quality',
                'features',
                'metadata',
                'file_path',
              ],
              show_all_match_providers: false,
              visible_match_providers: {
                spotify: true,
                musicbrainz: true,
                deezer: true,
                itunes: true,
                audiodb: true,
                discogs: true,
                lastfm: true,
                genius: true,
                tidal: true,
                qobuz: true,
                amazon: true,
                jiosaavn: true,
                bandcamp: true,
              },
              quality_show_format: true,
              quality_show_resolution: true,
              quality_show_bitrate: true,
            },
            artist_table: {
              columns: { quality_profile: false, genres: true, added: false },
              column_order: ['quality_profile', 'genres', 'added'],
            },
          },
        });
      }),
    );

    await expect(
      updateLibraryV2UiPreferences({ artist_table: { columns: { genres: true } } }),
    ).resolves.toMatchObject({ artist_table: { columns: { genres: true } } });
  });
});
