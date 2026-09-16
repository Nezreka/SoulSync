import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import type { ImportInboxItem, ImportInboxPayload } from './-import.types';

import { resetImportWorkflowStore } from './-import.store';

function renderImportRoute(initialEntries = ['/import']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries });
  const router = createAppRouter({ history, queryClient });

  return {
    history,
    router,
    queryClient,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

function getFetchUrls() {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => (input instanceof Request ? input.url : String(input)));
}

const FILE_ONE = {
  filename: '01-track.flac',
  full_path: '/music/Staging/Album/01-track.flac',
  rel_path: 'Album/01-track.flac',
  title: 'Track One',
  artist: 'Artist A',
  album: 'Album A',
  track_number: 1,
  disc_number: 1,
  extension: '.flac',
  format: 'FLAC',
  duration_ms: 221_000,
  bitrate: 1_013_000,
  size: 32_505_856,
};
const FILE_TWO = {
  ...FILE_ONE,
  filename: '02-track.flac',
  full_path: '/music/Staging/Album/02-track.flac',
  rel_path: 'Album/02-track.flac',
  title: 'Track Two',
  track_number: 2,
};

function albumItem(over: Partial<ImportInboxItem> = {}): ImportInboxItem {
  return {
    key: 'hash-1',
    kind: 'album',
    name: 'Album A',
    artist: 'Artist A',
    folder_name: 'Album',
    folder_path: '/music/Staging/Album',
    rel_path: 'Album',
    in_staging: true,
    files: [FILE_ONE, FILE_TWO],
    file_count: 2,
    total_duration_ms: 442_000,
    total_size: 65_011_712,
    formats: ['FLAC'],
    status: 'needs_review',
    confidence: 0.82,
    image_url: null,
    album_id: 'album-1',
    identification_method: 'tags',
    error_message: null,
    match: { matched_count: 2, total_tracks: 2, matches: [] },
    history_id: 4,
    created_at: '2026-09-16T10:00:00Z',
    processed_at: null,
    live: null,
    ...over,
  };
}

function inboxPayload(
  items: ImportInboxItem[],
  over: Partial<ImportInboxPayload> = {},
): ImportInboxPayload {
  const staged = items.filter((i) => i.in_staging);
  return {
    success: true,
    staging_path: '/music/Staging',
    items,
    summary: {
      items: staged.length,
      files: staged.reduce((n, i) => n + i.file_count, 0),
      size: staged.reduce((n, i) => n + i.total_size, 0),
      attention: staged.filter((i) =>
        ['needs_review', 'needs_identify', 'failed', 'waiting'].includes(i.status),
      ).length,
      by_status: {},
    },
    problems: [],
    worker: {
      available: true,
      running: true,
      paused: false,
      current_status: 'idle',
      last_scan_time: new Date().toISOString(),
      stats: {},
    },
    ...over,
  };
}

describe('import route', () => {
  let inbox: ImportInboxPayload;
  let albumMatchBodies: Record<string, unknown>[];

  beforeEach(() => {
    albumMatchBodies = [];
    inbox = inboxPayload([
      albumItem(),
      albumItem({
        key: 'hash-2',
        name: 'Loose Song',
        artist: 'Artist B',
        kind: 'single',
        folder_name: 'loose.flac',
        folder_path: '/music/Staging/loose.flac',
        rel_path: 'loose.flac',
        files: [
          {
            ...FILE_ONE,
            filename: 'loose.flac',
            full_path: '/music/Staging/loose.flac',
            rel_path: 'loose.flac',
            title: 'Loose Song',
            artist: 'Artist B',
            album: '',
          },
        ],
        file_count: 1,
        status: 'waiting',
        confidence: null,
        album_id: null,
        identification_method: null,
        match: null,
        history_id: null,
        created_at: null,
      }),
      albumItem({
        key: 'hash-3',
        name: 'Old Album',
        in_staging: false,
        files: [],
        status: 'imported',
        confidence: 0.97,
        history_id: 2,
        processed_at: '2026-09-15T10:00:00Z',
      }),
    ]);
    resetImportWorkflowStore();
    window.SoulSyncWebShellBridge = createShellBridge();
    window.showToast = vi.fn();
    window.showConfirmDialog = vi.fn(async () => true);
    vi.spyOn(globalThis, 'fetch');

    server.use(
      http.get('/api/import/inbox', () => HttpResponse.json(inbox)),
      http.get('/api/auto-import/settings', () =>
        HttpResponse.json({ success: true, scan_interval: 60, confidence_threshold: 0.9 }),
      ),
      http.get('/api/import/search/sources', () =>
        HttpResponse.json({
          success: true,
          sources: [{ source: 'spotify', label: 'Spotify', active: true }],
        }),
      ),
      http.get('/api/import/search/albums', () =>
        HttpResponse.json({
          success: true,
          primary_source: 'spotify',
          albums: [
            {
              id: 'album-1',
              name: 'Album A',
              artist: 'Artist A',
              source: 'deezer',
              total_tracks: 2,
              release_date: '2026-01-01',
              format: 'CD',
              country: 'US',
              label: 'MusicBrainz',
            },
            {
              id: 'album-2',
              name: 'Album A (Live)',
              artist: 'Artist A',
              source: 'deezer',
              total_tracks: 2,
            },
          ],
        }),
      ),
      http.post('/api/import/album/match', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        albumMatchBodies.push(body);
        return HttpResponse.json({
          success: true,
          album: {
            id: 'album-1',
            name: 'Album A',
            artist: 'Artist A',
            source: 'deezer',
            total_tracks: 2,
          },
          matches: [
            {
              track: { name: 'Track One', track_number: 1, duration_ms: 221_000 },
              staging_file: {
                filename: '01-track.flac',
                full_path: FILE_ONE.full_path,
                duration_ms: 221_000,
              },
              confidence: 0.95,
            },
            {
              track: { name: 'Track Two', track_number: 2, duration_ms: 200_000 },
              staging_file: null,
              confidence: 0,
            },
          ],
        });
      }),
      http.get('/api/import/search/tracks', () =>
        HttpResponse.json({
          success: true,
          tracks: [
            {
              id: 't-1',
              name: 'Loose Song',
              artist: 'Artist B',
              album: 'Singles',
              source: 'spotify',
              duration_ms: 221_000,
            },
          ],
        }),
      ),
      http.get('/api/issues/counts', () =>
        HttpResponse.json({
          success: true,
          counts: { open: 0, in_progress: 0, resolved: 0, dismissed: 0, total: 0 },
        }),
      ),
    );
  });

  it('renders the inbox: one row per staging item, with the folder and the worker', async () => {
    const { history } = renderImportRoute();

    expect(await screen.findByTestId('import-page')).toBeInTheDocument();
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    expect(screen.getByText('/music/Staging')).toBeInTheDocument();
    // needs attention is the default: the review item and the waiting single, not history
    const rows = screen.getAllByTestId('import-inbox-row');
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText('Needs review')).toBeInTheDocument();
    expect(within(rows[0]).getByText('82%')).toBeInTheDocument();
    expect(within(rows[0]).getByText('2 tracks · FLAC · 7:22 · 62 MB')).toBeInTheDocument();
    expect(within(rows[0]).getByText('2/2 tracks matched')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Waiting')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Needs attention\s*2/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /History\s*1/ })).toBeInTheDocument();
    expect(screen.getByText(/next scan in \d+s/)).toBeInTheDocument();
    expect(history.location.pathname).toBe('/import');
    expect(window.SoulSyncWebShellBridge?.showReactHost).toHaveBeenCalledWith('import');
  });

  it('the old tabs redirect into the inbox', async () => {
    const { history } = renderImportRoute(['/import/album']);
    await waitFor(() => expect(history.location.pathname).toBe('/import'));
    expect(await screen.findByText('Album A')).toBeInTheDocument();
  });

  it('keeps the page up and shows the reason when the inbox fails to load', async () => {
    server.use(
      http.get('/api/import/inbox', () =>
        HttpResponse.json(
          {
            success: false,
            error: 'Import folder is not readable: Permission denied (/app/Staging)',
          },
          { status: 500 },
        ),
      ),
    );
    renderImportRoute();
    expect(await screen.findByTestId('import-page')).toBeInTheDocument();
    expect(
      await screen.findByText(/Import folder: Import folder is not readable/),
    ).toBeInTheDocument();
  });

  it('lists unreadable subfolders beside the rows', async () => {
    inbox = inboxPayload([albumItem()], {
      problems: [{ path: '/music/Staging/Locked', error: 'Permission denied' }],
    });
    renderImportRoute();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'A folder in the import folder could not be read',
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      '/music/Staging/Locked (Permission denied)',
    );
  });

  it('shows scan progress while a large staging folder is still scanning (#947)', async () => {
    inbox = { success: true, scanning: true, progress: { scanned: 120, total: 6000 } };
    renderImportRoute();
    expect(await screen.findByText('Reading the import folder…')).toBeInTheDocument();
    expect(screen.getByText('120 of 6000 files')).toBeInTheDocument();
  });

  it('the filter lives in the url', async () => {
    renderImportRoute(['/import?filter=history']);
    expect(await screen.findByText('Old Album')).toBeInTheDocument();
    expect(screen.getAllByTestId('import-inbox-row')).toHaveLength(1);
    expect(screen.getByRole('tab', { name: /History/ })).toHaveAttribute('aria-selected', 'true');
  });

  it('approve posts to the history row and re-reads the inbox', async () => {
    let approved: string[] = [];
    server.use(
      http.post('/api/auto-import/approve/:id', ({ params }) => {
        approved.push(String(params.id));
        return HttpResponse.json({ success: true });
      }),
    );
    renderImportRoute();
    const row = (await screen.findAllByTestId('import-inbox-row'))[0];
    fireEvent.click(within(row).getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(approved).toEqual(['4']));
    await waitFor(() =>
      expect(
        getFetchUrls().filter((url) => url.includes('/api/import/inbox')).length,
      ).toBeGreaterThan(1),
    );
  });

  it('a waiting item offers Identify, which opens the matcher', async () => {
    const { history } = renderImportRoute();
    const rows = await screen.findAllByTestId('import-inbox-row');
    expect(within(rows[1]).queryByRole('button', { name: 'Approve' })).toBeNull();
    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Identify' }));
    await waitFor(() => expect(history.location.pathname).toBe('/import/match/hash-2'));
    expect(await screen.findByText('Which track is this?')).toBeInTheDocument();
    // the search runs from the file's own tags, and picking a result names the import
    expect(await screen.findByText('Loose Song · Artist B')).toBeInTheDocument();
    expect(screen.getByText('No track picked: imported from its own tags')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Loose Song · Artist B/ }));
    expect(screen.getByText(/Tagged as/)).toHaveTextContent('Tagged as Loose Song by Artist B');
  });

  it('the matcher opens on the worker release, keeps the source, and imports through the history row', async () => {
    let resolved: string[] = [];
    let processBodies: Record<string, unknown>[] = [];
    server.use(
      http.post('/api/import/album/process', async ({ request }) => {
        processBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ success: true, processed: 1, errors: [] });
      }),
      http.post('/api/auto-import/resolve/:id', ({ params }) => {
        resolved.push(String(params.id));
        return HttpResponse.json({ success: true });
      }),
    );
    const { history } = renderImportRoute(['/import/match/hash-1']);

    // the worker's pick is opened without a click, with the same provider it came from
    expect(await screen.findByText('Track One')).toBeInTheDocument();
    expect(albumMatchBodies[0]).toMatchObject({
      album_id: 'album-1',
      source: 'deezer',
      file_paths: [FILE_ONE.full_path, FILE_TWO.full_path],
    });
    expect(screen.getByText('1 file without a track')).toBeInTheDocument();
    expect(screen.getByText('Album A (Live)')).toBeInTheDocument();

    // tap the loose file, then the empty track
    fireEvent.click(screen.getByRole('button', { name: /02-track\.flac/ }));
    fireEvent.click(screen.getByText('tap to place here'));
    expect(screen.getByText('Every file has a track')).toBeInTheDocument();
    const importButton = screen.getByRole('button', { name: 'Import 2 tracks' });
    fireEvent.click(importButton);

    await waitFor(() => expect(history.location.pathname).toBe('/import'));
    await waitFor(() => expect(processBodies).toHaveLength(2));
    expect(processBodies[0]).toMatchObject({ album: { id: 'album-1', source: 'deezer' } });
    await waitFor(() => expect(resolved).toEqual(['4']));
    expect(await screen.findByText('2 of 2 imported')).toBeInTheDocument();
  });

  it('shows a Settings link and stops the batch when the media server is not connected', async () => {
    let processCalls = 0;
    server.use(
      http.post('/api/import/album/process', () => {
        processCalls += 1;
        return HttpResponse.json(
          {
            success: false,
            error:
              "Plex isn't connected, so importing now would copy files into place without adding them to your Library. Connect Plex in Settings, or switch to Standalone mode, then try again.",
            error_code: 'media_server_not_connected',
          },
          { status: 503 },
        );
      }),
    );
    renderImportRoute(['/import/match/hash-1']);
    await screen.findByText('Track One');
    fireEvent.click(screen.getByRole('button', { name: /02-track\.flac/ }));
    fireEvent.click(screen.getByText('tap to place here'));
    fireEvent.click(screen.getByRole('button', { name: 'Import 2 tracks' }));

    expect(await screen.findByRole('link', { name: 'Go to Settings' })).toHaveAttribute(
      'href',
      '/settings',
    );
    expect(screen.getByText(/Plex isn't connected/)).toBeInTheDocument();
    // the gate rejects the whole batch identically: stop after the first file
    expect(processCalls).toBe(1);
  });

  it('the matcher says so when the item is gone', async () => {
    renderImportRoute(['/import/match/nope']);
    expect(
      await screen.findByText('This item is no longer in the import folder'),
    ).toBeInTheDocument();
  });
});
