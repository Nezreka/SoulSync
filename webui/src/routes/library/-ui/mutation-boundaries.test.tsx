import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import {
  ActionButton,
  AlbumOverflowMenu,
  ArtistAliases,
  LibraryV2CanWriteContext,
  MirrorStatusBanner,
} from './library-v2-page';

function renderWithQueryClient(node: React.ReactNode, canWrite = true) {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <LibraryV2CanWriteContext.Provider value={canWrite}>{node}</LibraryV2CanWriteContext.Provider>
    </QueryClientProvider>,
  );
}

describe('library v2 remaining mutation boundaries', () => {
  it('keeps view-only toolbar actions available while write actions fail closed', () => {
    const openManageTracks = vi.fn();
    const openHistory = vi.fn();
    const mutate = vi.fn();

    renderWithQueryClient(
      <>
        <ActionButton
          icon="tracks"
          label="Manage Tracks"
          requiresWrite={false}
          onClick={openManageTracks}
        />
        <ActionButton icon="history" label="History" requiresWrite={false} onClick={openHistory} />
        <ActionButton icon="delete" label="Delete" onClick={mutate} />
      </>,
      false,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Manage Tracks' }));
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    expect(openManageTracks).toHaveBeenCalledOnce();
    expect(openHistory).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled();
    expect(mutate).not.toHaveBeenCalled();
  });

  it('keeps album details and history visible but disables album mutations read-only', () => {
    const album = {
      id: 12,
      title: 'Album',
      year: 2026,
      album_type: 'album',
      release_date: '2026-01-01',
      explicit: false,
      label: null,
      style: null,
      mood: null,
      user_overrides: {},
      quality_profile_id: 1,
    } as React.ComponentProps<typeof AlbumOverflowMenu>['album'];

    renderWithQueryClient(<AlbumOverflowMenu album={album} />, false);
    fireEvent.click(screen.getByTitle('More actions'));

    expect(screen.getByRole('button', { name: 'Album details' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'History' })).toBeEnabled();
    for (const name of [
      'Preview Retag',
      'Analyze ReplayGain',
      'Preview Rename / Organize',
      /Reassign to another artist/,
      'Change Cover',
      /Enrich/,
      'Delete',
    ]) {
      expect(screen.getByRole('button', { name })).toBeDisabled();
    }
  });

  it('offers reassign only for a release that actually owns files', () => {
    // A discography row owns nothing to move, so the preview can only answer
    // "That album has no files on disk to reassign" — a dead end the menu
    // should not walk the user into.
    const base = {
      id: 12,
      title: 'Album',
      year: 2026,
      album_type: 'album',
      release_date: '2026-01-01',
      explicit: false,
      label: null,
      style: null,
      mood: null,
      user_overrides: {},
      quality_profile_id: 1,
    };
    const reassign = /Reassign to another artist/;

    const { unmount } = renderWithQueryClient(
      <AlbumOverflowMenu
        album={
          { ...base, owns_files: false } as React.ComponentProps<typeof AlbumOverflowMenu>['album']
        }
      />,
    );
    fireEvent.click(screen.getByTitle('More actions'));
    expect(screen.getByRole('button', { name: reassign })).toBeDisabled();
    unmount();

    renderWithQueryClient(
      <AlbumOverflowMenu
        album={
          { ...base, owns_files: true } as React.ComponentProps<typeof AlbumOverflowMenu>['album']
        }
      />,
    );
    fireEvent.click(screen.getByTitle('More actions'));
    expect(screen.getByRole('button', { name: reassign })).toBeEnabled();
  });

  it('does not unlink or open alias linking for read-only profiles', async () => {
    let writes = 0;
    server.use(
      http.get('/api/library/v2/artists/7/aliases', () =>
        HttpResponse.json({
          success: true,
          canonical_artist_id: 7,
          aliases: [
            { id: 7, name: 'Canonical' },
            { id: 8, name: 'Provider Alias' },
          ],
        }),
      ),
      http.delete('/api/library/v2/artists/8/link-alias', () => {
        writes += 1;
        return HttpResponse.json({ success: true });
      }),
    );

    renderWithQueryClient(<ArtistAliases artistId={7} artistName="Canonical" />, false);

    const unlink = await screen.findByTitle(/Unlink Provider Alias/);
    const link = screen.getByRole('button', { name: '+ Link alias' });
    expect(unlink).toBeDisabled();
    expect(link).toBeDisabled();
    fireEvent.click(unlink);
    fireEvent.click(link);
    expect(writes).toBe(0);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('surfaces an alias-unlink 4xx and retries the same alias', async () => {
    let attempts = 0;
    server.use(
      http.get('/api/library/v2/artists/7/aliases', () =>
        HttpResponse.json({
          success: true,
          canonical_artist_id: 7,
          aliases: [
            { id: 7, name: 'Canonical' },
            { id: 8, name: 'Provider Alias' },
          ],
        }),
      ),
      http.delete('/api/library/v2/artists/8/link-alias', () => {
        attempts += 1;
        return HttpResponse.json(
          attempts === 1
            ? { success: false, error: 'Alias relation is locked' }
            : { success: true },
          { status: attempts === 1 ? 409 : 200 },
        );
      }),
    );

    renderWithQueryClient(<ArtistAliases artistId={7} artistName="Canonical" />);

    fireEvent.click(await screen.findByTitle(/Unlink Provider Alias/));
    expect(await screen.findByRole('alert')).toHaveTextContent('Alias relation is locked');

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(attempts).toBe(2);
  });

  it('surfaces a failed album ReplayGain job and offers retry', async () => {
    let starts = 0;
    server.use(
      http.post('/api/library/v2/albums/12/replaygain', () => {
        starts += 1;
        return HttpResponse.json({ success: true, job_id: `rg-${starts}` });
      }),
      http.get('/api/library/v2/jobs/status', ({ request }) => {
        const jobId = new URL(request.url).searchParams.get('job_id');
        return HttpResponse.json({
          running: false,
          error: jobId === 'rg-1' ? 'ReplayGain scanner crashed' : null,
        });
      }),
    );
    const album = {
      id: 12,
      title: 'Album',
      year: 2026,
      album_type: 'album',
      release_date: '2026-01-01',
      explicit: false,
      label: null,
      style: null,
      mood: null,
      user_overrides: {},
      quality_profile_id: 1,
    } as React.ComponentProps<typeof AlbumOverflowMenu>['album'];

    renderWithQueryClient(<AlbumOverflowMenu album={album} />);
    fireEvent.click(screen.getByTitle('More actions'));
    fireEvent.click(screen.getByRole('button', { name: 'Analyze ReplayGain' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('ReplayGain scanner crashed');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(starts).toBe(2);
  });

  it('shows a failed mirror retry and lets the user retry again', async () => {
    let attempts = 0;
    server.use(
      http.get('/api/library/v2/mirror-status', () =>
        HttpResponse.json({
          success: true,
          pending: 0,
          failed: attempts >= 2 ? 0 : 1,
        }),
      ),
      http.post('/api/library/v2/mirror-retry', () => {
        attempts += 1;
        return HttpResponse.json(
          attempts === 1 ? { success: false, error: 'Mirror database is busy' } : { success: true },
        );
      }),
    );

    renderWithQueryClient(<MirrorStatusBanner />);

    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Mirror database is busy');
    fireEvent.click(screen.getByRole('button', { name: 'Retry again' }));

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(attempts).toBe(2);
  });
});
