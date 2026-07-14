import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import {
  blacklistLibraryV2Source,
  deleteLibraryV2Files,
  fetchLibraryV2FileDeletePreview,
  fetchLibraryV2Playlist,
  fetchLibraryV2Playlists,
  fetchLibraryV2TrackSourceInfo,
  materializeLibraryV2MissingTrack,
  runLibraryV2PlaylistPipeline,
  runRepairJob,
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

    const rows = await fetchLibraryV2TrackSourceInfo(55);
    expect(rows).toHaveLength(2);
    expect(rows[0].source_username).toBe('user');
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
