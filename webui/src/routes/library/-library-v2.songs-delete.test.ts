import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { deleteLibraryTrackRequest, type LibrarySong } from './-library-v2.songs';

const song = { album_id: 3, file_id: 9, file_ids: [9] } as unknown as LibrarySong;

describe('deleteLibraryTrackRequest', () => {
  it('a delete the disk refused says the track is still in the library (review 5 W2-WL follow-up)', async () => {
    // a failed item keeps its file row, so the track stays; the toast used to
    // say it was removed from the library
    server.use(
      http.post('/api/library/v2/albums/3/file-remove', () =>
        HttpResponse.json({
          success: true,
          operation: { items: [{ status: 'failed', error: 'Permission denied' }] },
        }),
      ),
    );
    const toast = await deleteLibraryTrackRequest(song, 'db_only');
    expect(toast.tone).toBe('warning');
    expect(toast.message).not.toMatch(/removed from library/i);
    expect(toast.message).toMatch(/still in your library/);
    expect(toast.extra).toBe('Permission denied');
  });
});
