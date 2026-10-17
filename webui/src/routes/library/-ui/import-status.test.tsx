import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2ImportState } from '../-library-v2.types';

import {
  describeLibraryV2ImportCompletion,
  describeLibraryV2ArtworkCacheProgress,
  describeLibraryV2ImportProgress,
  describeLibraryV2Migration,
  ImportButton,
  LibraryV2CanWriteContext,
  LibraryEmptyState,
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
    artwork_cache: {
      running: false,
      current: 0,
      total: 0,
      stats: null,
      error: null,
      started_at: null,
      finished_at: null,
    },
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

  it('labels artwork as non-blocking background work with bounded progress', () => {
    expect(
      describeLibraryV2ArtworkCacheProgress(
        importState({
          running: false,
          stage: 'done',
          artwork_cache: {
            running: true,
            current: 9,
            total: 6,
            stats: null,
            error: null,
            started_at: 1,
            finished_at: null,
          },
        }),
      ),
    ).toBe('Library ready to browse · Caching artwork in the background · 6/6 · 100%');
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
            ? importState({ stage: 'tracks', current: 6, total: 8 })
            : runningPolls === 2
              ? importState({
                  running: false,
                  stage: 'done',
                  current: 0,
                  total: 0,
                  stats: { artists: 2, albums: 4, tracks: 8 },
                  artwork_cache: {
                    running: true,
                    current: 3,
                    total: 6,
                    stats: null,
                    error: null,
                    started_at: 1,
                    finished_at: null,
                  },
                })
              : importState({
                  running: false,
                  stage: 'done',
                  current: 6,
                  total: 6,
                  stats: { artists: 2, albums: 4, tracks: 8 },
                  artwork_cache: {
                    running: false,
                    current: 6,
                    total: 6,
                    stats: { artists: 2, albums: 4 },
                    error: null,
                    started_at: 1,
                    finished_at: 2,
                  },
                }),
        );
      }),
    );
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <LibraryV2CanWriteContext.Provider value>
          <ImportButton hasArtists={false} pollIntervalMs={50} />
        </LibraryV2CanWriteContext.Provider>
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Import library' }));

    expect(
      await screen.findByText(/Library ready to browse · Caching artwork in the background/),
    ).toBeInTheDocument();
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
        <LibraryV2CanWriteContext.Provider value>
          <ImportButton hasArtists={false} pollIntervalMs={20} />
        </LibraryV2CanWriteContext.Provider>
      </QueryClientProvider>,
    );

    expect(
      await screen.findByText('Failed: Legacy database became unavailable'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import library' })).toBeEnabled();
  });

  it('removes the import action once lib2 already contains artists', async () => {
    server.use(
      http.get('/api/library/v2/import/status', () =>
        HttpResponse.json(importState({ running: false, stage: 'done' })),
      ),
    );

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <LibraryV2CanWriteContext.Provider value>
          <ImportButton hasArtists pollIntervalMs={20} />
        </LibraryV2CanWriteContext.Provider>
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /import library/i })).not.toBeInTheDocument(),
    );
  });
});

describe('automatic migration of an upgrading installation', () => {
  function bootstrapState(
    overrides: Partial<NonNullable<LibraryV2ImportState['bootstrap']>> = {},
  ): NonNullable<LibraryV2ImportState['bootstrap']> {
    return {
      status: 'running',
      attempts: 1,
      stage: 'albums',
      current: 40,
      total: 100,
      last_error: null,
      started_at: '2026-07-28T10:00:00+00:00',
      finished_at: null,
      heartbeat_at: '2026-07-28T10:00:10+00:00',
      ...overrides,
    };
  }

  it('reports a migration this browser session never started', () => {
    expect(
      describeLibraryV2Migration(
        importState({
          running: false,
          stage: null,
          bootstrap: bootstrapState(),
        }),
      ),
    ).toEqual({
      tone: 'busy',
      text: 'Migrating your library · Importing albums · 40/100 · 40%',
    });
  });

  it('says nothing once the migration is done', () => {
    expect(
      describeLibraryV2Migration(
        importState({
          running: false,
          bootstrap: bootstrapState({ status: 'done' }),
        }),
      ),
    ).toBeNull();
    expect(describeLibraryV2Migration(importState({ running: false }))).toBeNull();
  });

  it('explains a failed migration and that it keeps retrying', () => {
    const described = describeLibraryV2Migration(
      importState({
        running: false,
        bootstrap: bootstrapState({
          status: 'failed',
          last_error: 'disk full',
        }),
      }),
    );
    expect(described?.tone).toBe('error');
    expect(described?.text).toContain('disk full');
    expect(described?.text).toContain('retries on its own');
  });

  it('invalidates the catalogue when the automatic migration finishes', async () => {
    // UI-01: the automatic migration reports itself as `bootstrap.running`,
    // never as `importState.running` — that flag belongs to a manual import
    // started from this browser. Watching only the manual flag meant the
    // completion branch was never reached for a migration: the page kept
    // whatever the first artist query returned (usually nothing) and went on
    // offering "Import library" after the catalogue had finished importing.
    let polls = 0;
    server.use(
      http.get('/api/library/v2/import/status', () => {
        polls += 1;
        return HttpResponse.json(
          importState({
            running: false,
            stage: null,
            bootstrap:
              polls === 1
                ? bootstrapState()
                : bootstrapState({ status: 'done', current: 100, finished_at: 'now' }),
          }),
        );
      }),
    );
    const queryClient = createTestQueryClient();
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');

    render(
      <QueryClientProvider client={queryClient}>
        <ImportButton hasArtists={false} pollIntervalMs={20} />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['library-v2'] }));
  });

  it('keeps watching a failed migration the server is still retrying', async () => {
    // UI-03: `failed` is not terminal — the server loop retries with backoff.
    // Stopping the poll left the page on the failure forever (refetch-on-focus
    // is globally off), showing "It retries on its own" next to a retry the
    // user could not observe.
    const { libraryV2ImportStatusQueryOptions } = await import('../-library-v2.api');
    const options = libraryV2ImportStatusQueryOptions(1000);
    const interval = options.refetchInterval as (query: {
      state: { data: LibraryV2ImportState | undefined; dataUpdateCount: number };
    }) => number | false;

    const failed = importState({
      running: false,
      stage: null,
      bootstrap: bootstrapState({ status: 'failed', last_error: 'disk full' }),
    });
    const next = interval({ state: { data: failed, dataUpdateCount: 1 } });
    expect(next).not.toBe(false);
    expect(typeof next).toBe('number');
    expect(next).toBeGreaterThanOrEqual(5_000);
    expect(next).toBeLessThanOrEqual(60_000);
    // It backs off rather than hammering the endpoint at the live interval.
    expect(interval({ state: { data: failed, dataUpdateCount: 8 } })).toBeGreaterThanOrEqual(
      next as number,
    );
    // A genuinely finished migration still stops the timer.
    expect(
      interval({
        state: {
          data: importState({ running: false, bootstrap: bootstrapState({ status: 'done' }) }),
          dataUpdateCount: 2,
        },
      }),
    ).toBe(false);
  });

  it('blocks the Import button while the background migration holds the lock', async () => {
    server.use(
      http.get('/api/library/v2/import/status', () =>
        HttpResponse.json(
          importState({
            running: false,
            stage: null,
            bootstrap: bootstrapState(),
          }),
        ),
      ),
    );
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <ImportButton hasArtists={false} pollIntervalMs={20} />
      </QueryClientProvider>,
    );

    const button = await screen.findByRole('button', { name: 'Migrating…' });
    expect(button).toBeDisabled();
    expect(
      await screen.findByText('Migrating your library · Importing albums · 40/100 · 40%'),
    ).toBeInTheDocument();
  });

  it('tells an upgrading user why the library still looks empty', async () => {
    server.use(
      http.get('/api/library/v2/import/status', () =>
        HttpResponse.json(
          importState({
            running: false,
            stage: null,
            bootstrap: bootstrapState(),
          }),
        ),
      ),
    );
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <LibraryEmptyState pollIntervalMs={20} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Migrating your library…')).toBeInTheDocument();
    expect(screen.queryByText('Your library is empty')).not.toBeInTheDocument();
  });

  it('keeps the plain empty state when there is nothing to migrate', async () => {
    server.use(
      http.get('/api/library/v2/import/status', () =>
        HttpResponse.json(
          importState({
            running: false,
            stage: null,
            bootstrap: bootstrapState({
              status: 'waiting_for_source',
              stage: null,
            }),
          }),
        ),
      ),
    );
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <LibraryEmptyState pollIntervalMs={20} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Your library is empty')).toBeInTheDocument();
  });

  it('offers the force the refusal names when a repeat run is declined', async () => {
    // The endpoint declines a re-run over a converged library and tells the
    // user to "send force". Nothing in the UI could send it, and the button is
    // only rendered for an empty library -- so the one user who legitimately
    // needs the re-run (catalogue gone, bootstrap row still says done) was
    // reading a remedy they had no way to apply.
    const posted: Array<{ reset: boolean; force: boolean }> = [];
    server.use(
      http.get('/api/library/v2/import/status', () =>
        HttpResponse.json(importState({ running: false, stage: null })),
      ),
      http.post('/api/library/v2/import', async ({ request }) => {
        const body = (await request.json()) as { reset: boolean; force: boolean };
        posted.push(body);
        if (!body.force) {
          return HttpResponse.json(
            {
              success: false,
              code: 'already_completed',
              error:
                'Library import already completed and the legacy catalogue has not changed since.',
            },
            { status: 409 },
          );
        }
        return HttpResponse.json({ success: true, started: true });
      }),
    );
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <LibraryV2CanWriteContext.Provider value>
          <ImportButton hasArtists={false} pollIntervalMs={0} />
        </LibraryV2CanWriteContext.Provider>
      </QueryClientProvider>,
    );

    expect(screen.queryByRole('button', { name: 'Import anyway' })).toBeNull();

    fireEvent.click(await screen.findByRole('button', { name: 'Import library' }));

    expect(await screen.findByText(/already completed/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: 'Import anyway' }));

    await waitFor(() => expect(posted).toHaveLength(2));
    expect(posted[0]).toEqual({ reset: false, force: false });
    expect(posted[1]).toEqual({ reset: false, force: true });
  });
});
