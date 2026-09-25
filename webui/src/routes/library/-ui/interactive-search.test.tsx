import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2HistoryEntry, SourceSearchResult } from '../-library-v2.api';

import {
  classifyGrabOutcome,
  InteractiveSearchModal,
  sortSourceSearchResults,
} from './interactive-search';

describe('classifyGrabOutcome', () => {
  const NOW = Date.parse('2026-01-01T00:00:00Z');

  function entry(overrides: Partial<LibraryV2HistoryEntry>): LibraryV2HistoryEntry {
    return {
      date: new Date(NOW).toISOString(),
      event_type: 'x',
      category: 'info',
      title: null,
      detail: null,
      source: null,
      ...overrides,
    };
  }

  it('reports pending when there is no fresh terminal event', () => {
    expect(classifyGrabOutcome([], NOW)).toEqual({ status: 'pending' });
    expect(classifyGrabOutcome([entry({ category: 'info' })], NOW)).toEqual({
      status: 'pending',
    });
  });

  it('ignores a stale event left over from an earlier grab of the same track', () => {
    const stale = entry({ category: 'quarantined', date: new Date(NOW - 60_000).toISOString() });
    expect(classifyGrabOutcome([stale], NOW)).toEqual({ status: 'pending' });
  });

  it('reports failed for a fresh quarantined event, combining title + detail', () => {
    const fresh = entry({
      category: 'quarantined',
      title: 'Quarantined',
      detail: 'AcoustID mismatch',
    });
    expect(classifyGrabOutcome([fresh], NOW)).toEqual({
      status: 'failed',
      message: 'Quarantined: AcoustID mismatch',
    });
  });

  it('reports failed for a fresh failed-category event with no detail', () => {
    const fresh = entry({ category: 'failed', title: 'Grab failed', detail: null });
    expect(classifyGrabOutcome([fresh], NOW)).toEqual({
      status: 'failed',
      message: 'Grab failed',
    });
  });

  it('reports imported for a fresh imported event', () => {
    expect(classifyGrabOutcome([entry({ category: 'imported' })], NOW)).toEqual({
      status: 'imported',
    });
  });

  it('tolerates ~10s of clock skew around the grab start time', () => {
    const justInside = entry({ category: 'imported', date: new Date(NOW - 9_000).toISOString() });
    expect(classifyGrabOutcome([justInside], NOW)).toEqual({ status: 'imported' });

    const tooOld = entry({ category: 'imported', date: new Date(NOW - 11_000).toISOString() });
    expect(classifyGrabOutcome([tooOld], NOW)).toEqual({ status: 'pending' });
  });
});

describe('library v2 interactive grab', () => {
  it('labels and initially focuses the search field, and Escape closes the dialog', async () => {
    server.use(
      http.get('/api/search/sources', () => HttpResponse.json({ mode: 'soulseek', sources: [] })),
      http.post('/api/search', () => HttpResponse.json({ results: [] })),
    );
    const onClose = vi.fn();
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <InteractiveSearchModal canWrite initialQuery="Artist" onClose={onClose} />
      </QueryClientProvider>,
    );

    const input = screen.getByRole('textbox', { name: 'Search query' });
    await waitFor(() => expect(input).toHaveFocus());
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it('does not let a track-scoped dialog attach an album result', async () => {
    server.use(
      http.get('/api/search/sources', () =>
        HttpResponse.json({
          mode: 'usenet',
          sources: [{ name: 'usenet', display_name: 'Usenet' }],
        }),
      ),
      http.post('/api/search', () =>
        HttpResponse.json({
          results: [{ result_type: 'album', title: 'Whole Album', artist: 'Artist', size: 10 }],
        }),
      ),
    );
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <InteractiveSearchModal
          canWrite
          initialQuery="Artist Track"
          entity={{ trackId: 42 }}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    const blocked = await screen.findByRole('button', { name: 'Album result' });
    expect(blocked).toBeDisabled();
    expect(blocked).toHaveAttribute(
      'title',
      'An album result cannot be attached to one library track',
    );
  });

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

  it('keeps Prowlarr release/context fields separate and never shows seeders for Usenet', async () => {
    server.use(
      http.get('/api/search/sources', () =>
        HttpResponse.json({
          mode: 'usenet',
          sources: [{ name: 'usenet', display_name: 'Usenet' }],
        }),
      ),
      http.post('/api/search', () =>
        HttpResponse.json({
          results: [
            {
              result_type: 'track',
              source: 'usenet',
              username: 'usenet',
              filename: 'opaque',
              release_title: 'TEAM-WEB-Various.Artists-Compilation-FLAC-2026',
              artist: 'Real Artist',
              matched_album_title: 'Real Album',
              size: 4096,
              _source_metadata: { protocol: 'usenet', indexer: 'NZBGeek', grabs: 42, seeders: 999 },
            },
          ],
        }),
      ),
    );
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <InteractiveSearchModal canWrite initialQuery="Real Artist Real Album" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    await screen.findByText('TEAM-WEB-Various.Artists-Compilation-FLAC-2026');
    expect(screen.getByText('Real Artist')).toBeInTheDocument();
    expect(screen.getByText('Matched to Real Album')).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Peers' })).not.toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Grabs' })).toBeInTheDocument();
    expect(screen.queryByText(/999 seeders/i)).not.toBeInTheDocument();
    const grabs = screen.getByRole('checkbox', { name: 'Grabs' });
    fireEvent.click(grabs);
    expect(screen.queryByRole('columnheader', { name: 'Grabs' })).not.toBeInTheDocument();
    fireEvent.click(grabs); // restore persisted preference for following tests
  });

  it('shows the candidate download error and retries the same result', async () => {
    let attempts = 0;
    const submitted: unknown[] = [];
    server.use(
      http.get('/api/search/sources', () =>
        HttpResponse.json({
          mode: 'soulseek',
          sources: [{ name: 'soulseek', display_name: 'Soulseek' }],
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
        <InteractiveSearchModal canWrite initialQuery="Artist Selected" onClose={vi.fn()} />
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
    expect(screen.getByRole('button', { name: 'Started ✓' })).toBeDisabled();
    expect(attempts).toBe(2);
    expect(submitted[1]).toEqual(submitted[0]);
  });

  it('iss27-12: searches every source by default and clicking Usenet selects Usenet', async () => {
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
        <InteractiveSearchModal
          canWrite
          initialQuery="Artist Selected"
          entity={{ trackId: 321, albumId: 12 }}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    // The initial auto-run waits for the source list, then fans out to
    // every configured source instead of relying on a single fallback pick.
    await waitFor(() => expect(searches).toHaveLength(2));
    expect(searches).toEqual(
      expect.arrayContaining([
        {
          query: 'Artist Selected',
          source: 'soulseek',
          lib2_track_id: 321,
          lib2_album_id: 12,
        },
        {
          query: 'Artist Selected',
          source: 'usenet',
          lib2_track_id: 321,
          lib2_album_id: 12,
        },
      ]),
    );

    // Regression: the selected-looking Usenet chip must INCLUDE Usenet. The
    // old exclusion-set implementation did the opposite and made live
    // Usenet results appear to have disappeared.
    fireEvent.click(await screen.findByRole('button', { name: 'Usenet' }));
    expect(screen.getByRole('button', { name: 'Usenet' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() => expect(searches).toHaveLength(3));
    expect(searches[2]).toEqual({
      query: 'Artist Selected',
      source: 'usenet',
      lib2_track_id: 321,
      lib2_album_id: 12,
    });
  });

  it('iss27-01: merges results from the sources that succeed when one source search fails', async () => {
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
        const body = (await request.json()) as { source?: string };
        if (body.source === 'usenet') {
          return HttpResponse.json({ error: 'Usenet indexer timed out' }, { status: 500 });
        }
        return HttpResponse.json({
          results: [
            {
              result_type: 'track',
              username: 'peer',
              filename: 'Found.flac',
              title: 'Found',
              quality: 'flac',
              size: 10,
            },
          ],
        });
      }),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <InteractiveSearchModal canWrite initialQuery="Artist Selected" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    // Soulseek's result still renders even though Usenet's search failed —
    // one source failing must not blank the whole result set (iss27-01).
    await screen.findByText('Found');
    expect(screen.queryByText(/search failed/i)).not.toBeInTheDocument();
    // dd28-06: ...but the failure must still be SAID. Previously a Usenet
    // timeout or 500 produced no banner, no chip state and no log the user
    // could see, so "Usenet never finds anything" looked like a property of
    // Usenet instead of a broken call.
    await screen.findByText(/Usenet could not be searched/i);
  });

  it('iss27-12: source chips expose an intuitive exact subset and normalize all', async () => {
    server.use(
      http.get('/api/search/sources', () =>
        HttpResponse.json({
          mode: 'hybrid',
          sources: [
            { name: 'soulseek', display_name: 'Soulseek' },
            { name: 'usenet', display_name: 'Usenet' },
            { name: 'tidal', display_name: 'Tidal' },
          ],
        }),
      ),
      http.post('/api/search', () => HttpResponse.json({ results: [] })),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <InteractiveSearchModal canWrite initialQuery="Artist Selected" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    await screen.findByRole('button', { name: 'Tidal' });
    expect(screen.getByRole('button', { name: 'All sources' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: 'Soulseek' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );

    // From "All", the first individual click selects exactly that source;
    // further clicks build a true multi-select subset.
    fireEvent.click(screen.getByRole('button', { name: 'Soulseek' }));
    expect(screen.getByRole('button', { name: 'Soulseek' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: 'Usenet' })).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(screen.getByRole('button', { name: 'Usenet' }));
    expect(screen.getByRole('button', { name: 'All sources' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    expect(screen.getByRole('button', { name: 'Tidal' })).toHaveAttribute('aria-pressed', 'false');

    // Selecting the final source is equivalent to "All sources" and is
    // normalized back to that one canonical state.
    fireEvent.click(screen.getByRole('button', { name: 'Tidal' }));
    expect(screen.getByRole('button', { name: 'All sources' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: 'Usenet' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('iss27-14: renders a fast source before a slow source finishes', async () => {
    let releaseSlow!: () => void;
    const slowGate = new Promise<void>((resolve) => {
      releaseSlow = resolve;
    });
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
        const body = (await request.json()) as { source?: string };
        if (body.source === 'soulseek') {
          await slowGate;
          return HttpResponse.json({
            results: [
              {
                result_type: 'track',
                username: 'peer',
                filename: 'Slow.flac',
                title: 'Slow Soulseek result',
                size: 10,
              },
            ],
          });
        }
        return HttpResponse.json({
          results: [
            {
              result_type: 'track',
              username: 'usenet',
              filename: 'Fast.flac',
              title: 'Fast Usenet result',
              size: 10,
            },
          ],
        });
      }),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <InteractiveSearchModal canWrite initialQuery="Artist Selected" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Fast Usenet result')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/remaining sources are still searching/i);
    const clientStatus = screen.getByLabelText('Search client status');
    expect(clientStatus).toHaveTextContent(/Usenet.*Finished/);
    expect(clientStatus).toHaveTextContent(/Soulseek.*Searching/);
    expect(screen.queryByText('Slow Soulseek result')).not.toBeInTheDocument();

    releaseSlow();
    expect(await screen.findByText('Slow Soulseek result')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
  });

  it('iss27-13: renders switch chrome as a sibling of the accessible checkbox', async () => {
    server.use(
      http.get('/api/search/sources', () =>
        HttpResponse.json({
          mode: 'soulseek',
          sources: [{ name: 'soulseek', display_name: 'Soulseek' }],
        }),
      ),
      http.post('/api/search', () => HttpResponse.json({ results: [] })),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <InteractiveSearchModal canWrite initialQuery="Artist Selected" onClose={vi.fn()} />
      </QueryClientProvider>,
    );

    const quality = await screen.findByRole('checkbox', { name: 'Quality check' });
    expect(quality).toBeChecked();
    expect(quality.nextElementSibling).toHaveAttribute('aria-hidden', 'true');
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
          canWrite
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
          canWrite
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

  it('shows the selected client download progress after dispatch', async () => {
    server.use(
      http.get('/api/search/sources', () =>
        HttpResponse.json({ mode: 'hifi', sources: [{ name: 'hifi', display_name: 'HiFi' }] }),
      ),
      http.post('/api/search', () =>
        HttpResponse.json({
          results: [
            {
              result_type: 'track',
              source: 'hifi',
              username: 'hifi',
              filename: 'Track.flac',
              title: 'Track',
              artist: 'Artist',
              quality: 'flac',
              size: 4096,
            },
          ],
        }),
      ),
      http.post('/api/download', () => HttpResponse.json({ success: true })),
      http.get('/api/library/v2/tracks/42/queue-status', () =>
        HttpResponse.json({
          tracks: { 42: { status: 'downloading', progress_pct: 37 } },
          albums: { 12: 1 },
        }),
      ),
      http.get('/api/library/v2/tracks/42/history', () =>
        HttpResponse.json({ success: true, history: [] }),
      ),
    );
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <InteractiveSearchModal
          canWrite
          initialQuery="Artist Track"
          entity={{ trackId: 42, albumId: 12 }}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Download' }));
    expect(
      await screen.findByRole('button', { name: 'Downloading 37%' }, { timeout: 5000 }),
    ).toBeDisabled();
  }, 7000);

  it('surfaces a quarantine outcome that lands after the grab dispatch resolves', async () => {
    server.use(
      http.get('/api/search/sources', () =>
        HttpResponse.json({
          mode: 'soulseek',
          sources: [{ name: 'soulseek', display_name: 'Soulseek' }],
        }),
      ),
      http.post('/api/search', () =>
        HttpResponse.json({
          results: [
            {
              result_type: 'track',
              username: 'peer',
              filename: 'Artist/Track.flac',
              title: 'Track',
              artist: 'Artist',
              quality: 'flac',
              size: 4096,
            },
          ],
        }),
      ),
      http.post('/api/download', () => HttpResponse.json({ success: true })),
      http.get('/api/library/v2/tracks/42/queue-status', () =>
        HttpResponse.json({ tracks: {}, albums: {} }),
      ),
      http.get('/api/library/v2/tracks/42/history', () =>
        HttpResponse.json({
          success: true,
          history: [
            {
              date: new Date().toISOString(),
              event_type: 'import_file_quarantined',
              category: 'quarantined',
              title: 'Quarantined',
              detail: 'AcoustID mismatch',
              source: 'acquisition',
            },
          ],
        }),
      ),
    );

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <InteractiveSearchModal
          canWrite
          initialQuery="Artist Track"
          entity={{ trackId: 42 }}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Download' }));

    // Dispatch resolving is NOT the real outcome — a grab naming a library
    // entity must wait for the pipeline's verdict instead of claiming
    // success immediately.
    expect(await screen.findByRole('button', { name: 'Verifying…' })).toBeDisabled();

    expect(await screen.findByRole('alert', {}, { timeout: 8000 })).toHaveTextContent(
      'AcoustID mismatch',
    );
    expect(screen.getByRole('button', { name: 'Retry' })).toBeEnabled();
  }, 10_000);

  it('§35: invalidates stale library rows as soon as autolink reports imported', async () => {
    server.use(
      http.get('/api/search/sources', () =>
        HttpResponse.json({
          mode: 'soulseek',
          sources: [{ name: 'soulseek', display_name: 'Soulseek' }],
        }),
      ),
      http.post('/api/search', () =>
        HttpResponse.json({
          results: [
            {
              result_type: 'track',
              username: 'peer',
              filename: 'Artist/Track.flac',
              title: 'Track',
              artist: 'Artist',
              quality: 'flac',
              size: 4096,
            },
          ],
        }),
      ),
      http.post('/api/download', () => HttpResponse.json({ success: true })),
      http.get('/api/library/v2/tracks/42/queue-status', () =>
        HttpResponse.json({ tracks: {}, albums: {} }),
      ),
      http.get('/api/library/v2/tracks/42/history', () =>
        HttpResponse.json({
          success: true,
          history: [
            {
              date: new Date().toISOString(),
              event_type: 'import_file_completed',
              category: 'imported',
              title: 'Imported',
              detail: null,
              source: 'acquisition',
            },
          ],
        }),
      ),
    );

    const queryClient = createTestQueryClient();
    const staleAlbumKey = ['library-v2', 'album', 12, false] as const;
    queryClient.setQueryData(staleAlbumKey, { tracks_present: 1 });
    render(
      <QueryClientProvider client={queryClient}>
        <InteractiveSearchModal
          canWrite
          initialQuery="Artist Track"
          entity={{ trackId: 42, albumId: 12 }}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Download' }));
    expect(
      await screen.findByRole('button', { name: 'Imported ✓' }, { timeout: 8000 }),
    ).toBeDisabled();
    expect(queryClient.getQueryState(staleAlbumKey)?.isInvalidated).toBe(true);
  }, 10_000);
});
