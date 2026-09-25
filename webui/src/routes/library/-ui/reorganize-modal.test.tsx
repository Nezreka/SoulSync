import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import {
  AlbumReorganizeModal,
  ArtistReorganizeAllModal,
  ArtistRenamePreviewModal,
} from './reorganize-modal';

const PREVIEW_RESPONSE = {
  success: true,
  status: 'planned',
  source: null,
  album: 'Views',
  artist: 'Drake',
  transfer_dir: '/music',
  tracks: [
    {
      track_id: 1,
      title: 'One Dance',
      track_number: 1,
      disc_number: 1,
      current_path: '/old/One Dance.flac',
      new_path: '/new/One Dance.flac',
      file_exists: true,
      unchanged: false,
      collision: false,
      matched: true,
      reason: null,
    },
  ],
};

describe('library v2 album reorganize queue status', () => {
  it.each([
    ['unchanged', { unchanged: true }],
    ['path conflict', { collision: true }],
    ['unresolved destination', { new_path: '', reason: 'Template could not be resolved' }],
  ])('does not offer an apply action for %s files', async (_label, overrides) => {
    server.use(
      http.post('/api/library/v2/albums/42/reorganize/preview', () =>
        HttpResponse.json({
          ...PREVIEW_RESPONSE,
          tracks: [{ ...PREVIEW_RESPONSE.tracks[0], ...overrides }],
        }),
      ),
    );
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AlbumReorganizeModal albumId={42} albumTitle="Views" onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    await screen.findByText('One Dance');
    expect(screen.getByRole('button', { name: 'Rename / Organize (0)' })).toBeDisabled();
  });

  it('polls the shared queue by queue id and shows live progress through to done', async () => {
    server.use(
      http.get('/api/library/v2/albums/42/reorganize/sources', () =>
        HttpResponse.json({ success: true, sources: [] }),
      ),
      http.post('/api/library/v2/albums/42/reorganize/preview', () =>
        HttpResponse.json(PREVIEW_RESPONSE),
      ),
      http.post('/api/library/v2/albums/42/reorganize', () =>
        HttpResponse.json({ success: true, queued: true, queue_id: 'q-1' }),
      ),
      http.get('/api/library/reorganize/queue', () =>
        HttpResponse.json({
          success: true,
          active: null,
          queued: [],
          recent: [
            {
              queue_id: 'q-1',
              album_id: '99',
              album_title: 'Views',
              artist_name: 'Drake',
              status: 'done',
              result_status: 'moved',
              current_track: null,
              progress_total: 1,
              progress_processed: 1,
              finished_at: 1700000000,
            },
          ],
        }),
      ),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <AlbumReorganizeModal albumId={42} albumTitle="Views" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Rename \/ Organize \(1\)/ }));

    expect(await screen.findByText('Rename / Organize finished (moved).')).toBeInTheDocument();
  });

  it('offers no mode switch, because there is only one thing it does', async () => {
    // The full mode copied the file into staging and ran it back through the
    // download post-processing pipeline — an admission check for a file the
    // user already owns. It fingerprinted a library file and quarantined it
    // over its own audio. Re-tagging is its own job now, so a reorganize
    // reorganizes and there is nothing left to choose between.
    server.use(
      http.get('/api/library/v2/albums/42/reorganize/sources', () =>
        HttpResponse.json({ success: true, sources: [] }),
      ),
      http.post('/api/library/v2/albums/42/reorganize/preview', () =>
        HttpResponse.json(PREVIEW_RESPONSE),
      ),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <AlbumReorganizeModal albumId={42} albumTitle="Views" onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    await screen.findByRole('button', { name: /Rename \/ Organize \(1\)/ });

    expect(screen.queryByLabelText(/Rename only/)).toBeNull();
    expect(screen.queryByLabelText(/Metadata source/)).toBeNull();
    expect(screen.getByText(/Tags are left alone/)).toBeInTheDocument();
  });

  it('asks the backend for a plan without naming a metadata source', async () => {
    // No source, no api-vs-tags mode: the destination comes from the catalogue,
    // so the request carries nothing that could make two previews of the same
    // album disagree.
    let body: Record<string, unknown> = { sentinel: true };
    server.use(
      http.get('/api/library/v2/albums/42/reorganize/sources', () =>
        HttpResponse.json({ success: true, sources: [] }),
      ),
      http.post('/api/library/v2/albums/42/reorganize/preview', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(PREVIEW_RESPONSE);
      }),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <AlbumReorganizeModal albumId={42} albumTitle="Views" onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    await screen.findByRole('button', { name: /Rename \/ Organize \(1\)/ });

    expect(body.source ?? null).toBeNull();
    expect(body.mode ?? null).toBeNull();
  });

  it('hands back the whole path from the copy button, not the shortened one', async () => {
    // The column is 260px wide and the identifying part of a path is at its
    // END, so what it shows is never the whole thing. Copying has to be the
    // way out of that, and it has to copy the real stored path.
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });

    server.use(
      http.get('/api/library/v2/albums/42/reorganize/sources', () =>
        HttpResponse.json({ success: true, sources: [] }),
      ),
      http.post('/api/library/v2/albums/42/reorganize/preview', () =>
        HttpResponse.json(PREVIEW_RESPONSE),
      ),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <AlbumReorganizeModal albumId={42} albumTitle="Views" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    const buttons = await screen.findAllByRole('button', { name: 'Copy full path' });
    fireEvent.click(buttons[0]);

    expect(writeText).toHaveBeenCalledWith('/old/One Dance.flac');
  });

  it('surfaces an already-queued response without a live-status crash', async () => {
    server.use(
      http.get('/api/library/v2/albums/42/reorganize/sources', () =>
        HttpResponse.json({ success: true, sources: [] }),
      ),
      http.post('/api/library/v2/albums/42/reorganize/preview', () =>
        HttpResponse.json(PREVIEW_RESPONSE),
      ),
      http.post('/api/library/v2/albums/42/reorganize', () =>
        HttpResponse.json({ success: true, queued: false, reason: 'already_queued' }),
      ),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <AlbumReorganizeModal albumId={42} albumTitle="Views" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Rename \/ Organize \(1\)/ }));

    expect(await screen.findByText('Not queued (already queued).')).toBeInTheDocument();
  });
});

describe('library v2 artist reorganize-all queue progress', () => {
  it('watches the shared queue by artist name until nothing of this artist is left', async () => {
    server.use(
      http.get('/api/library/v2/reorganize/sources', () =>
        HttpResponse.json({ success: true, sources: [] }),
      ),
      http.post('/api/library/v2/artists/7/reorganize-all', () =>
        HttpResponse.json({ success: true, enqueued: 2, already_queued: 0, total_albums: 2 }),
      ),
      http.get('/api/library/reorganize/queue', () =>
        HttpResponse.json({ success: true, active: null, queued: [], recent: [] }),
      ),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <ArtistReorganizeAllModal artistId={7} artistName="Drake" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Rename / Organize All Releases' }));

    expect(await screen.findByText('2 of 2 album(s) queued.')).toBeInTheDocument();
    expect(
      await screen.findByText('No queued or running releases remain for this artist.'),
    ).toBeInTheDocument();
  });

  it('closes the tool from the bulk view once the releases are queued', async () => {
    // Opened from the per-release preview, the bulk dialog's only exit went
    // BACK to that preview -- so the button labelled "Close" could not close
    // anything, and the tool had no exit from this view at all.
    server.use(
      http.post('/api/library/v2/artists/7/reorganize-all', () =>
        HttpResponse.json({ success: true, enqueued: 1, already_queued: 0, total_albums: 1 }),
      ),
      http.get('/api/library/reorganize/queue', () =>
        HttpResponse.json({ success: true, active: null, queued: [], recent: [] }),
      ),
    );
    const onClose = vi.fn();
    const onBack = vi.fn();

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <ArtistReorganizeAllModal
          artistId={7}
          artistName="Drake"
          onBack={onBack}
          onClose={onClose}
        />
      </QueryClientProvider>,
    );

    // Before queueing it is a step backwards, and says so.
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(onBack).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Rename / Organize All Releases' }));
    expect(await screen.findByText('1 of 1 album(s) queued.')).toBeInTheDocument();

    // Queued: there is nothing to go back for, so Close closes. (Two buttons
    // carry that name -- the header's x and the footer's; the footer one is
    // the one the finding was about.)
    const closers = screen.getAllByRole('button', { name: 'Close' });
    fireEvent.click(closers[closers.length - 1]);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onBack).toHaveBeenCalledTimes(1);
  });
});

describe('artist rename preview scope', () => {
  it('previews all releases together, narrows scope, and applies the complete artist scope', async () => {
    const applyAll = vi.fn();
    server.use(
      http.post('/api/library/v2/artists/7/reorganize-all', () => {
        applyAll();
        return HttpResponse.json({
          success: true,
          enqueued: 2,
          already_queued: 0,
          total_albums: 2,
        });
      }),
      http.get('/api/library/reorganize/queue', () =>
        HttpResponse.json({ success: true, active: null, queued: [], recent: [] }),
      ),
      http.post('/api/library/v2/albums/:id/reorganize/preview', ({ params }) =>
        HttpResponse.json({
          ...PREVIEW_RESPONSE,
          tracks: [
            {
              ...PREVIEW_RESPONSE.tracks[0],
              track_id: Number(params.id),
              title: `Track ${params.id}`,
            },
          ],
        }),
      ),
    );
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <ArtistRenamePreviewModal
          artistId={7}
          artistName="Drake"
          albums={[
            { id: 42, title: 'Album release', tracks_present: 1 },
            { id: 43, title: 'Single release', tracks_present: 1 },
            { id: 44, title: 'No files', tracks_present: 0 },
          ]}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Track 42')).toBeInTheDocument();
    expect(await screen.findByText('Track 43')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Preview release' })).toHaveValue('all');
    expect(screen.getByRole('button', { name: 'Rename / Organize (2)' })).toBeEnabled();
    expect(screen.queryByText('Track 44')).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Preview release' }), {
      target: { value: '43' },
    });
    expect(await screen.findByText('Track 43')).toBeInTheDocument();
    expect(screen.queryByText('Track 42')).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Preview release' }), {
      target: { value: 'all' },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Rename / Organize (2)' }));
    expect(await screen.findByText('2 releases queued.')).toBeInTheDocument();
    expect(applyAll).toHaveBeenCalledTimes(1);
  });
  it('blocks applying all when one release preview fails', async () => {
    server.use(
      http.post('/api/library/v2/albums/42/reorganize/preview', () =>
        HttpResponse.json(PREVIEW_RESPONSE),
      ),
      http.post('/api/library/v2/albums/43/reorganize/preview', () =>
        HttpResponse.json({ success: false, error: 'Preview unavailable' }),
      ),
    );
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <ArtistRenamePreviewModal
          artistId={7}
          artistName="Drake"
          albums={[
            { id: 42, title: 'Album release', tracks_present: 1 },
            { id: 43, title: 'Single release', tracks_present: 1 },
          ]}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Preview unavailable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Rename \/ Organize \(/ })).toBeDisabled();
  });
});
