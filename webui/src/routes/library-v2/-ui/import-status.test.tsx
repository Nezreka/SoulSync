import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2ImportState } from '../-library-v2.types';

import {
  describeLibraryV2ImportCompletion,
  describeLibraryV2ImportProgress,
  ImportButton,
} from './library-v2-page';

function importState(overrides: Partial<LibraryV2ImportState> = {}): LibraryV2ImportState {
  return {
    running: true,
    stage: 'albums',
    current: 5,
    total: 10,
    stats: null,
    error: null,
    finished_at: null,
    ...overrides,
  };
}

describe('library v2 import progress', () => {
  it('formats the live backend stage, bounded counters, and percentage', () => {
    expect(describeLibraryV2ImportProgress(importState())).toBe('Importing albums · 5/10 · 50%');
    expect(
      describeLibraryV2ImportProgress(importState({ stage: 'tags', current: 14, total: 10 })),
    ).toBe('Reading file tags · 10/10 · 100%');
    expect(describeLibraryV2ImportProgress(importState({ stage: 'starting', total: 0 }))).toBe(
      'Starting import…',
    );
  });

  it('summarizes imported entities when terminal stats are available', () => {
    expect(
      describeLibraryV2ImportCompletion(
        importState({
          running: false,
          stage: 'done',
          stats: { artists: 1, albums: 2, tracks: 3 },
        }),
      ),
    ).toBe('Import complete — 1 artist · 2 albums · 3 tracks.');
  });

  it('reattaches to a running import and refreshes queries after completion', async () => {
    let polls = 0;
    server.use(
      http.get('/api/library/v2/import/status', () => {
        polls += 1;
        return HttpResponse.json(
          polls === 1
            ? importState({ stage: 'tracklists', current: 2, total: 4 })
            : importState({
                running: false,
                stage: 'done',
                current: 4,
                total: 4,
                stats: { artists: 1, albums: 2, tracks: 3 },
              }),
        );
      }),
    );
    const queryClient = createTestQueryClient();
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');

    render(
      <QueryClientProvider client={queryClient}>
        <ImportButton hasArtists pollIntervalMs={20} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Resolving tracklists · 2/4 · 50%')).toBeInTheDocument();
    expect(
      await screen.findByText('Import complete — 1 artist · 2 albums · 3 tracks.'),
    ).toBeInTheDocument();
    expect(polls).toBeGreaterThanOrEqual(2);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['library-v2'] });
  });

  it('starts an import and shares its live status instead of reloading the page', async () => {
    let started = false;
    let runningPolls = 0;
    server.use(
      http.post('/api/library/v2/import', () => {
        started = true;
        return HttpResponse.json({ success: true, started: true });
      }),
      http.get('/api/library/v2/import/status', () => {
        if (!started) return HttpResponse.json(importState({ running: false, stage: null }));
        runningPolls += 1;
        return HttpResponse.json(
          runningPolls === 1
            ? importState({ stage: 'artwork', current: 3, total: 6 })
            : importState({
                running: false,
                stage: 'done',
                current: 6,
                total: 6,
                stats: { artists: 2, albums: 4, tracks: 8 },
              }),
        );
      }),
    );
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <ImportButton hasArtists={false} pollIntervalMs={50} />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Import library' }));

    expect(await screen.findByText('Caching artwork · 3/6 · 50%')).toBeInTheDocument();
    expect(
      await screen.findByText('Import complete — 2 artists · 4 albums · 8 tracks.'),
    ).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button')).toBeEnabled());
  });

  it('surfaces a terminal backend failure and makes the import retryable', async () => {
    let polls = 0;
    server.use(
      http.get('/api/library/v2/import/status', () => {
        polls += 1;
        return HttpResponse.json(
          polls === 1
            ? importState({ stage: 'tracks', current: 2, total: 3 })
            : importState({
                running: false,
                stage: 'failed',
                error: 'Legacy database became unavailable',
              }),
        );
      }),
    );
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <ImportButton hasArtists pollIntervalMs={20} />
      </QueryClientProvider>,
    );

    expect(
      await screen.findByText('Failed: Legacy database became unavailable'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Re-import library' })).toBeEnabled();
  });
});
