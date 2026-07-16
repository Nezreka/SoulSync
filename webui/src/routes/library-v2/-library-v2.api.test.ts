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
  fetchLibraryV2ArtistMatchStatus,
  fetchLibraryV2FileDeletePreview,
  fetchLibraryV2Playlist,
  fetchLibraryV2Playlists,
  fetchLibraryV2ReorganizeSourcesGlobal,
  fetchLibraryV2TrackSourceInfo,
  manualMatchLibraryV2Entity,
  materializeLibraryV2MissingTrack,
  previewLibraryV2AlbumReorganize,
  runLibraryV2PlaylistPipeline,
  runRepairJob,
  searchLibraryV2MatchService,
  startLibraryV2AlbumReplayGain,
  startLibraryV2ScopedSearch,
  updateLibraryV2MetadataOverrides,
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
