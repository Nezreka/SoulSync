import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import { UnifiedFileRemovalDialog, returnsOnRescanNotice } from './library-v2-page';

/** 3.6.1 said a media-server artist comes back on the next scan, after the
 *  delete. the v2 dialog says it before you confirm a database-only removal. */
describe('removing a media-server artist', () => {
  it('names the server and what to do instead', () => {
    expect(returnsOnRescanNotice('Drake', ['plex'])).toBe(
      'Drake is in your Plex library, so the next Plex sync adds it back. ' +
        'To keep it out, remove it there too, or choose Delete files.',
    );
    expect(returnsOnRescanNotice('Drake', ['jellyfin', 'navidrome', 'plex'])).toContain(
      'in your Jellyfin, Navidrome and Plex libraries, so their next sync adds it back.',
    );
    expect(returnsOnRescanNotice('', [])).toContain('This artist is in your media server library');
  });

  function renderArtistDialog(returnsOnRescan: boolean) {
    server.use(
      http.get('/api/library/v2/artists/7/delete-preview', () =>
        HttpResponse.json({
          success: true,
          artist: 'Drake',
          albums: 2,
          tracks: 20,
          file_links: 20,
          detached_albums: 0,
          media_servers: returnsOnRescan ? ['plex'] : [],
          returns_on_rescan: returnsOnRescan,
        }),
      ),
      http.get('/api/library/v2/artists/7/file-delete-preview', () =>
        HttpResponse.json({
          success: true,
          entity: 'artists',
          entity_id: 7,
          title: 'Drake',
          configured_roots: ['/music'],
          files: [
            {
              file_ids: [1],
              track_ids: [1],
              stored_paths: ['/music/a.flac'],
              path: '/music/a.flac',
              root: '/music',
              size: 10,
              deletable: true,
              reason: null,
              album_title: 'Views',
              track_titles: ['One Dance'],
            },
          ],
          file_count: 1,
          deletable_count: 1,
          unsafe_count: 0,
          total_size: 10,
          preview_token: 't',
        }),
      ),
    );
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <UnifiedFileRemovalDialog
          entity="artists"
          eid={7}
          removeWholeEntity
          onDone={vi.fn()}
          onCancel={vi.fn()}
        />
      </QueryClientProvider>,
    );
  }

  it('warns before a database-only removal, and not when the files go', async () => {
    renderArtistDialog(true);
    const note = await screen.findByRole('note');
    expect(note).toHaveTextContent('the next Plex sync adds it back');

    const deleteFiles = screen.getByRole('radio', { name: /Delete files/ });
    await waitFor(() => expect(deleteFiles).toBeEnabled());
    fireEvent.click(deleteFiles);
    expect(screen.queryByRole('note')).toBeNull();
  });

  it('says nothing for an artist no media server knows', async () => {
    renderArtistDialog(false);
    await screen.findByText(/owned release/);
    expect(screen.queryByRole('note')).toBeNull();
  });
});
