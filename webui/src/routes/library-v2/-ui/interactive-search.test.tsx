import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { SourceSearchResult } from '../-library-v2.api';

import { InteractiveSearchModal, sortSourceSearchResults } from './interactive-search';

describe('library v2 interactive grab', () => {
  it('keeps unknown publish dates behind known releases in both age directions', () => {
    const result = (title: string, publishDate?: string): SourceSearchResult => ({
      result_type: 'track',
      username: 'usenet',
      filename: `${title}.flac`,
      title,
      size: 1,
      _source_metadata: { publish_date: publishDate },
    });
    const rows = [
      result('Unknown'),
      result('Older', '2020-01-01T00:00:00Z'),
      result('Newer', '2025-01-01T00:00:00Z'),
      result('Invalid', 'not-a-date'),
    ];

    expect(sortSourceSearchResults(rows, 'age', -1).map((row) => row.title)).toEqual([
      'Older',
      'Newer',
      'Unknown',
      'Invalid',
    ]);
    expect(sortSourceSearchResults(rows, 'age', 1).map((row) => row.title)).toEqual([
      'Newer',
      'Older',
      'Unknown',
      'Invalid',
    ]);
  });

  it('shows the candidate download error and retries the same result', async () => {
    let attempts = 0;
    const submitted: unknown[] = [];
    server.use(
      http.get('/api/search/sources', () =>
        HttpResponse.json({
          mode: 'hybrid',
          sources: [
            { name: 'soulseek', display_name: 'Soulseek' },
            { name: 'usenet', display_name: 'Usenet' },
          ],
        }),
      ),
      http.post('/api/search', () =>
        HttpResponse.json({
          results: [
            {
              result_type: 'track',
              username: 'peer-one',
              filename: 'Artist/Selected.flac',
              title: 'Selected',
              artist: 'Artist',
              quality: 'flac',
              size: 4096,
              free_upload_slots: 1,
              queue_length: 0,
            },
          ],
        }),
      ),
      http.post('/api/download', async ({ request }) => {
        attempts += 1;
        submitted.push(await request.json());
        return HttpResponse.json(
          attempts === 1
            ? { success: false, error: 'Download client rejected the transfer' }
            : { success: true },
        );
      }),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <InteractiveSearchModal initialQuery="Artist Selected" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Download' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Download client rejected the transfer',
    );
    const retry = screen.getByRole('button', { name: 'Retry' });
    expect(retry).toBeEnabled();

    fireEvent.click(retry);

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Grabbed ✓' })).toBeDisabled();
    expect(attempts).toBe(2);
    expect(submitted[1]).toEqual(submitted[0]);
  });

  it('passes an explicitly selected configured source to the shared search endpoint', async () => {
    const searches: unknown[] = [];
    server.use(
      http.get('/api/search/sources', () =>
        HttpResponse.json({
          mode: 'hybrid',
          sources: [
            { name: 'soulseek', display_name: 'Soulseek' },
            { name: 'usenet', display_name: 'Usenet' },
          ],
        }),
      ),
      http.post('/api/search', async ({ request }) => {
        searches.push(await request.json());
        return HttpResponse.json({ results: [] });
      }),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <InteractiveSearchModal initialQuery="Artist Selected" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    const source = await screen.findByLabelText('Download source');
    await waitFor(() => expect(source).toHaveTextContent('Usenet'));
    fireEvent.change(source, { target: { value: 'usenet' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() => expect(searches).toHaveLength(2));
    expect(searches[0]).toEqual({ query: 'Artist Selected' });
    expect(searches[1]).toEqual({ query: 'Artist Selected', source: 'usenet' });
  });

  it('filters to only results meeting the quality profile cutoff (deep-dive D3)', async () => {
    server.use(
      http.get('/api/search/sources', () =>
        HttpResponse.json({
          mode: 'hybrid',
          sources: [{ name: 'soulseek', display_name: 'Soulseek' }],
        }),
      ),
      http.get('/api/library/v2/quality-profiles', () =>
        HttpResponse.json({
          success: true,
          profiles: [
            {
              id: 1,
              name: 'Lossless',
              description: null,
              upgrade_policy: 'until_cutoff',
              upgrade_cutoff_index: 0,
              ranked_targets: [{ label: 'FLAC', format: 'flac' }],
              repair_job_id: 'x',
            },
          ],
        }),
      ),
      http.post('/api/search', () =>
        HttpResponse.json({
          results: [
            {
              result_type: 'track',
              username: 'peer',
              filename: 'Good.flac',
              title: 'Good',
              quality: 'flac',
              size: 10,
            },
            {
              result_type: 'track',
              username: 'peer',
              filename: 'Bad.mp3',
              title: 'Bad',
              quality: 'mp3',
              bitrate: 128,
              size: 5,
            },
          ],
        }),
      ),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <InteractiveSearchModal
          initialQuery="Artist Selected"
          entity={{ qualityProfileId: 1 }}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    await screen.findByText('Good');
    expect(screen.getByText('Bad')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Only show results meeting cutoff' }));

    await waitFor(() => expect(screen.queryByText('Bad')).not.toBeInTheDocument());
    expect(screen.getByText('Good')).toBeInTheDocument();
  });

  it('§52.12.4: requires an explicit Force confirmation before grabbing a below-profile candidate with Quality check off', async () => {
    let downloadCalls = 0;
    server.use(
      http.get('/api/search/sources', () =>
        HttpResponse.json({
          mode: 'hybrid',
          sources: [{ name: 'soulseek', display_name: 'Soulseek' }],
        }),
      ),
      http.get('/api/library/v2/quality-profiles', () =>
        HttpResponse.json({
          success: true,
          profiles: [
            {
              id: 1,
              name: 'Lossless',
              description: null,
              upgrade_policy: 'until_cutoff',
              upgrade_cutoff_index: 0,
              ranked_targets: [{ label: 'FLAC', format: 'flac' }],
              repair_job_id: 'x',
            },
          ],
        }),
      ),
      http.post('/api/search', () =>
        HttpResponse.json({
          results: [
            {
              result_type: 'track',
              username: 'peer',
              filename: 'Bad.mp3',
              title: 'Bad',
              quality: 'mp3',
              bitrate: 128,
              size: 5,
            },
          ],
        }),
      ),
      http.post('/api/download', () => {
        downloadCalls += 1;
        return HttpResponse.json({ success: true });
      }),
    );

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <InteractiveSearchModal
          initialQuery="Artist Selected"
          entity={{ qualityProfileId: 1 }}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    await screen.findByText('Bad');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Quality check' }));
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));

    // Declined the Force confirmation — nothing dispatched.
    await waitFor(() => expect(confirmSpy).toHaveBeenCalled());
    expect(confirmSpy.mock.calls[0]?.[0]).toMatch(/below.*profile/i);
    expect(downloadCalls).toBe(0);

    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));

    await waitFor(() => expect(downloadCalls).toBe(1));
    confirmSpy.mockRestore();
  });
});
