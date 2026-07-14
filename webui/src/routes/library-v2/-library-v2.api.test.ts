import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { runRepairJob, updateLibraryV2MetadataOverrides } from './-library-v2.api';

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
