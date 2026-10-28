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
    guessed_name: null,
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
        name: 'loose.flac',
        guessed_name: 'Loose Song',
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
      http.post('/api/import/album/preview', async ({ request }) => {
        const body = (await request.json()) as {
          matches: { staging_file: { full_path: string } }[];
        };
        return HttpResponse.json({
          success: true,
          tracks: body.matches.map((m, i) => ({
            file: m.staging_file.full_path.split('/').pop(),
            full_path: m.staging_file.full_path,
            destination: `/lib/Artist A/Artist A - 2026 Album A/0${i + 1} - Track.flac`,
            path_error: null,
            before: {
              title: 'Old',
              artist: 'Artist A',
              albumartist: 'Artist A',
              album: 'Album A',
              track_number: null,
              disc_number: 1,
              year: '',
            },
            after: {
              title: `Track ${i + 1}`,
              artist: 'Artist A',
              albumartist: 'Artist A',
              album: 'Album A',
              track_number: i + 1,
              disc_number: 1,
              year: '2026',
            },
            changed: ['title', 'track_number', 'year'],
          })),
        });
      }),
      http.post('/api/library/check-tracks', () =>
        HttpResponse.json({
          success: true,
          owned_tracks: {
            'Track One': { owned: true, format: '.mp3', file_path: '/lib/a.mp3' },
            'Track Two': { owned: false },
          },
        }),
      ),
      http.post('/api/import/upload', () =>
        HttpResponse.json({ success: true, saved: [{ file: 'x.flac', size: 1 }], skipped: [] }),
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
    // needs attention is the default: the review item. the waiting single is
    // about to be picked up (auto-import is on), so it is not attention.
    const rows = screen.getAllByTestId('import-inbox-row');
    expect(rows).toHaveLength(1);
    expect(within(rows[0]).getByText('Needs review')).toBeInTheDocument();
    expect(within(rows[0]).getByText('82%')).toBeInTheDocument();
    expect(within(rows[0]).getByText('2 tracks · FLAC · 7:22 · 62 MB')).toBeInTheDocument();
    expect(within(rows[0]).getByText('2/2 tracks matched')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Needs attention\s*1/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Everything\s*3/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /History\s*1/ })).toBeInTheDocument();
    expect(screen.getByText(/next scan in \d+s/)).toBeInTheDocument();
    expect(history.location.pathname).toBe('/import');
    expect(window.SoulSyncWebShellBridge?.showReactHost).toHaveBeenCalledWith('import');
  });

  it('a finished import says what it did with each track', async () => {
    const outcome = (
      track_name: string,
      track_number: number,
      import_status: 'completed' | 'skipped_owned',
      import_reason: string,
    ) => ({
      track_name,
      track_number,
      file: `${track_number}.flac`,
      file_path: `/music/Staging/Album/${track_number}.flac`,
      confidence: 1,
      import_status,
      import_reason,
    });
    inbox = inboxPayload([
      albumItem({
        status: 'imported',
        in_staging: false,
        processed_at: '2026-10-15T08:00:00Z',
        match: {
          matched_count: 2,
          total_tracks: 2,
          matches: [
            outcome('Hell Above', 2, 'skipped_owned', 'already in the library, copy is fine'),
            outcome('King For A Day', 4, 'skipped_owned', 'already in the library, copy is fine'),
          ],
          summary: { imported: 0, upgraded: 0, skipped_owned: 2, failed: 0 },
        },
      }),
    ]);
    renderImportRoute(['/import?filter=history']);

    const row = (await screen.findAllByTestId('import-inbox-row'))[0];
    // nothing was imported, so the pill doesn't claim it was
    expect(within(row).getByText('Already in library')).toBeInTheDocument();
    expect(within(row).getByText('2 already in library')).toBeInTheDocument();
    expect(within(row).queryByTestId('import-outcome')).toBeNull();

    fireEvent.click(within(row).getByRole('button', { name: 'What happened' }));
    const detail = within(row).getByTestId('import-outcome');
    expect(within(detail).getByText('Hell Above')).toBeInTheDocument();
    expect(within(detail).getAllByText('already in the library, copy is fine')).toHaveLength(2);
  });

  it('singles lead with the title tag, not the filename (#1289)', async () => {
    renderImportRoute(['/import?filter=all']);
    // The single has title tag 'Loose Song' on files[0]; the backend falls back
    // to the title for guessed_name when there's no album guess — the title
    // leads, the filename is the subtitle.
    const rows = await screen.findAllByTestId('import-inbox-row');
    const singleRow = rows.find((r) => within(r).queryByText('Loose Song'));
    expect(singleRow).toBeTruthy();
    // No "tags say" subtitle: guessed_name equals the displayed title.
    expect(within(singleRow!).queryByText(/tags say/)).toBeNull();
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
    // #1671: a folder mid-scan is not an unconfigured folder
    expect(screen.queryByText('Import folder not configured')).toBeNull();
  });

  it('keeps the path, the worker switch and history visible while scanning (#1671)', async () => {
    inbox = {
      success: true,
      scanning: true,
      staging_path: '/music/Staging',
      progress: { scanned: 181, total: 252 },
      items: [
        albumItem({
          key: 'hash-3',
          name: 'Old Album',
          in_staging: false,
          files: [],
          status: 'imported',
          history_id: 2,
        }),
      ],
      worker: {
        available: true,
        running: true,
        paused: false,
        current_status: 'processing',
        last_scan_time: null,
        stats: {},
      },
    };
    renderImportRoute(['/import?filter=all']);
    expect(await screen.findByText('Old Album')).toBeInTheDocument();
    expect(screen.getByText('/music/Staging')).toBeInTheDocument();
    expect(screen.queryByText('Import folder not configured')).toBeNull();
    expect(screen.getByText(/181 of 252 files/)).toBeInTheDocument();
    expect(screen.getByLabelText('Auto-import')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /History/ })).toHaveTextContent('1');
  });

  it('a cold open mid-scan still shows the batch stats (#1874)', async () => {
    // the page was NOT open when processing began, so there is no
    // client-side snapshot: the stats can only come from the summary the
    // server carries in its scanning reply (the last complete build's).
    // Note the deliberate, documented mismatch this locks in: the header
    // counts the last build's STAGED rows, while the filter pills count
    // the history rows in this payload's items — they disagree until the
    // scan lands and reconciles them.
    inbox = {
      success: true,
      scanning: true,
      staging_path: '/music/Staging',
      progress: { scanned: 3, total: 4 },
      summary: { items: 2, files: 4, size: 65_011_712, attention: 1, by_status: {} },
      items: [
        albumItem({
          key: 'hash-3',
          name: 'Old Album',
          in_staging: false,
          files: [],
          status: 'imported',
          history_id: 2,
        }),
      ],
      worker: {
        available: true,
        running: true,
        paused: false,
        current_status: 'processing',
        last_scan_time: null,
        stats: {},
      },
    };
    renderImportRoute(['/import?filter=all']);
    expect(await screen.findByText('Old Album')).toBeInTheDocument();
    const stats = document.getElementById('import-page-staging-stats');
    expect(stats).not.toBeNull();
    expect(stats).toHaveTextContent('2 items');
    expect(stats).toHaveTextContent('4 files');
    expect(stats).toHaveTextContent('62 MB');
  });

  it('a re-scan with seq-less summaries prefers the server summary (tie-break) (#1874)', async () => {
    const { queryClient } = renderImportRoute();
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    // a complete build landed elsewhere after this tab's snapshot; the
    // scanning reply carries its (fresher) summary, and the strip must
    // show that, not this tab's older snapshot numbers
    inbox = {
      success: true,
      scanning: true,
      staging_path: '/music/Staging',
      progress: { scanned: 90, total: 99 },
      summary: { items: 9, files: 99, size: 2_147_483_648, attention: 9, by_status: {} },
      items: [],
      worker: {
        available: true,
        running: true,
        paused: false,
        current_status: 'scanning',
        last_scan_time: null,
        stats: {},
      },
    };
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    await waitFor(() => {
      const stats = document.getElementById('import-page-staging-stats');
      expect(stats).toHaveTextContent('9 items');
      expect(stats).toHaveTextContent('99 files');
      expect(stats).toHaveTextContent('2.0 GB');
    });
  });

  it('a server summary from a later build (higher seq) wins over the tab snapshot (#1874)', async () => {
    inbox = inboxPayload([albumItem()], {
      summary: { items: 7, files: 7, size: 7_340_032, attention: 0, by_status: {}, seq: 5 },
    });
    const { queryClient } = renderImportRoute();
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    expect(document.getElementById('import-page-staging-stats')).toHaveTextContent('7 items');
    inbox = {
      success: true,
      scanning: true,
      staging_path: '/music/Staging',
      progress: { scanned: 90, total: 99 },
      summary: { items: 9, files: 99, size: 2_147_483_648, attention: 9, by_status: {}, seq: 10 },
      items: [],
      worker: {
        available: true,
        running: true,
        paused: false,
        current_status: 'scanning',
        last_scan_time: null,
        stats: {},
      },
    };
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    await waitFor(() => {
      const stats = document.getElementById('import-page-staging-stats');
      expect(stats).toHaveTextContent('9 items');
      expect(stats).toHaveTextContent('99 files');
      expect(stats).toHaveTextContent('2.0 GB');
    });
  });

  it('equal seqs: the server summary wins the tie (#1874)', async () => {
    // pins the >= tie-break: the server reuses a seq exactly when the
    // numbers are identical, so a tie with different numbers can only
    // come from an edge the server side already resolved — the freshest
    // server word stands.
    inbox = inboxPayload([albumItem()], {
      summary: { items: 7, files: 7, size: 7_340_032, attention: 0, by_status: {}, seq: 5 },
    });
    const { queryClient } = renderImportRoute();
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    inbox = {
      success: true,
      scanning: true,
      staging_path: '/music/Staging',
      progress: { scanned: 90, total: 99 },
      summary: { items: 9, files: 99, size: 2_147_483_648, attention: 9, by_status: {}, seq: 5 },
      items: [],
      worker: {
        available: true,
        running: true,
        paused: false,
        current_status: 'scanning',
        last_scan_time: null,
        stats: {},
      },
    };
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    await waitFor(() => {
      expect(document.getElementById('import-page-staging-stats')).toHaveTextContent('9 items');
    });
  });

  it('a server summary older than the tab snapshot loses to it (#1874)', async () => {
    // this tab snapshotted build seq 10; the scanning reply carries the
    // server's remembered summary from build seq 5 (e.g. this tab's build
    // was skipped by the server's invalidation guard). Build order, not
    // blind preference, decides: the snapshot's numbers stay.
    inbox = inboxPayload([albumItem()], {
      summary: { items: 7, files: 7, size: 7_340_032, attention: 0, by_status: {}, seq: 10 },
    });
    const { queryClient } = renderImportRoute();
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    expect(document.getElementById('import-page-staging-stats')).toHaveTextContent('7 items');
    inbox = {
      success: true,
      scanning: true,
      staging_path: '/music/Staging',
      progress: { scanned: 90, total: 99 },
      summary: { items: 9, files: 99, size: 2_147_483_648, attention: 9, by_status: {}, seq: 5 },
      items: [],
      worker: {
        available: true,
        running: true,
        paused: false,
        current_status: 'scanning',
        last_scan_time: null,
        stats: {},
      },
    };
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    expect(await screen.findByText(/90 of 99 files/)).toBeInTheDocument();
    const stats = document.getElementById('import-page-staging-stats');
    expect(stats).toHaveTextContent('7 items');
    expect(stats).toHaveTextContent('7 files');
    expect(stats).toHaveTextContent('7 MB');
  });

  it('mixed seq presence: a seq-less snapshot loses, a seq-less server summary loses (#1874)', async () => {
    const scanningWith = (summary: unknown) => ({
      success: true,
      scanning: true,
      staging_path: '/music/Staging',
      progress: { scanned: 90, total: 99 },
      summary,
      items: [],
      worker: {
        available: true,
        running: true,
        paused: false,
        current_status: 'scanning',
        last_scan_time: null,
        stats: {},
      },
    });
    // snapshot without a seq (the default fixture), server summary with one
    const first = renderImportRoute();
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    inbox = scanningWith({
      items: 9,
      files: 99,
      size: 2_147_483_648,
      attention: 9,
      by_status: {},
      seq: 3,
    }) as typeof inbox;
    await first.queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    await waitFor(() =>
      expect(document.getElementById('import-page-staging-stats')).toHaveTextContent('9 items'),
    );
    first.unmount();

    // snapshot with a seq, server summary without one
    inbox = inboxPayload([albumItem()], {
      summary: { items: 7, files: 7, size: 7_340_032, attention: 0, by_status: {}, seq: 10 },
    });
    const second = renderImportRoute();
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    inbox = scanningWith({
      items: 9,
      files: 99,
      size: 2_147_483_648,
      attention: 9,
      by_status: {},
    }) as typeof inbox;
    await second.queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    expect(await screen.findByText(/90 of 99 files/)).toBeInTheDocument();
    expect(document.getElementById('import-page-staging-stats')).toHaveTextContent('7 items');
    second.unmount();
  });

  it('a clear newer than the snapshot drops its stats; an older clear does not (#1874)', async () => {
    inbox = inboxPayload([albumItem()], {
      summary: { items: 7, files: 7, size: 7_340_032, attention: 0, by_status: {}, seq: 5 },
    });
    const { queryClient } = renderImportRoute();
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    const scanningCleared = (clearedSeq: number) => ({
      success: true,
      scanning: true,
      staging_path: '/music/Staging',
      progress: { scanned: 1, total: 3 },
      summary_cleared_seq: clearedSeq,
      items: [],
      worker: {
        available: true,
        running: true,
        paused: false,
        current_status: 'scanning',
        last_scan_time: null,
        stats: {},
      },
    });
    // a clear from build 3 predates this tab's build-5 snapshot: stats stay
    inbox = scanningCleared(3) as typeof inbox;
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    expect(await screen.findByText(/1 of 3 files/)).toBeInTheDocument();
    expect(document.getElementById('import-page-staging-stats')).toHaveTextContent('7 items');
    // a clear from build 9 supersedes the snapshot: the batch it counted
    // was emptied by a later build, so its numbers must go
    inbox = scanningCleared(9) as typeof inbox;
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    await waitFor(() => expect(document.getElementById('import-page-staging-stats')).toBeNull());
  });

  it('a clear also drops a seq-less (pre-upgrade) snapshot (#1874)', async () => {
    // a tab opened before summaries carried a seq: its snapshot counts as
    // seq -1, so any reported clear supersedes it
    const { queryClient } = renderImportRoute();
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    expect(document.getElementById('import-page-staging-stats')).toHaveTextContent('124 MB');
    inbox = {
      success: true,
      scanning: true,
      staging_path: '/music/Staging',
      progress: { scanned: 1, total: 3 },
      summary_cleared_seq: 1,
      items: [],
      worker: {
        available: true,
        running: true,
        paused: false,
        current_status: 'scanning',
        last_scan_time: null,
        stats: {},
      },
    };
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    await waitFor(() => expect(document.getElementById('import-page-staging-stats')).toBeNull());
  });

  it('a re-scan with no server summary falls back to the tab snapshot (#1874)', async () => {
    const { queryClient } = renderImportRoute();
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    // the default complete payload's stats: 2 staged items, 3 files, 124 MB
    expect(document.getElementById('import-page-staging-stats')).toHaveTextContent('124 MB');
    inbox = {
      success: true,
      scanning: true,
      staging_path: '/music/Staging',
      progress: { scanned: 1, total: 3 },
      items: [],
      worker: {
        available: true,
        running: true,
        paused: false,
        current_status: 'scanning',
        last_scan_time: null,
        stats: {},
      },
    };
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    expect(await screen.findByText(/1 of 3 files/)).toBeInTheDocument();
    const stats = document.getElementById('import-page-staging-stats');
    expect(stats).toHaveTextContent('2 items');
    expect(stats).toHaveTextContent('3 files');
    expect(stats).toHaveTextContent('124 MB');
  });

  it('a cold open mid-scan with no summary on record shows no stats (#1874)', async () => {
    // nothing counted yet anywhere: the strip must not fabricate numbers
    inbox = {
      success: true,
      scanning: true,
      staging_path: '/music/Staging',
      progress: { scanned: 181, total: 252 },
      items: [
        albumItem({
          key: 'hash-3',
          name: 'Old Album',
          in_staging: false,
          files: [],
          status: 'imported',
          history_id: 2,
        }),
      ],
      worker: {
        available: true,
        running: true,
        paused: false,
        current_status: 'processing',
        last_scan_time: null,
        stats: {},
      },
    };
    renderImportRoute(['/import?filter=all']);
    expect(await screen.findByText('Old Album')).toBeInTheDocument();
    expect(document.getElementById('import-page-staging-stats')).toBeNull();
  });

  it('a re-scan keeps the last complete list under the progress banner (#1671)', async () => {
    const { queryClient } = renderImportRoute();
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    inbox = {
      success: true,
      scanning: true,
      staging_path: '/music/Staging',
      progress: { scanned: 5, total: 10 },
      items: [],
      worker: {
        available: true,
        running: true,
        paused: false,
        current_status: 'scanning',
        last_scan_time: null,
        stats: {},
      },
    };
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    expect(await screen.findByText(/5 of 10 files/)).toBeInTheDocument();
    expect(screen.getByText('Album A')).toBeInTheDocument();
    expect(screen.getByText('/music/Staging')).toBeInTheDocument();
    expect(screen.queryByText('Import folder not configured')).toBeNull();
  });

  it('a re-scan merges fresh rows over the snapshot instead of freezing it (#1671)', async () => {
    const { queryClient } = renderImportRoute(['/import?filter=all']);
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    inbox = {
      success: true,
      scanning: true,
      staging_path: '/music/Staging',
      progress: { scanned: 5, total: 10 },
      items: [
        albumItem({
          key: 'hash-9',
          name: 'Just Finished',
          in_staging: false,
          files: [],
          status: 'imported',
          history_id: 9,
        }),
      ],
      worker: {
        available: true,
        running: true,
        paused: false,
        current_status: 'scanning',
        last_scan_time: null,
        stats: {},
      },
    };
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    // finished during the scan: the fresh history row shows…
    expect(await screen.findByText('Just Finished')).toBeInTheDocument();
    // …and the staged row the scan cannot see yet survives from the snapshot
    expect(screen.getByText('Album A')).toBeInTheDocument();
  });

  it('the scan merge never shows one folder twice when its hash moved (#1671)', async () => {
    const { queryClient } = renderImportRoute(['/import?filter=all']);
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    // the folder's files changed, so the fresh scanning row carries the
    // same history row under a NEW key; identity is history_id/path too
    inbox = {
      success: true,
      scanning: true,
      staging_path: '/music/Staging',
      progress: { scanned: 5, total: 10 },
      items: [albumItem({ key: 'hash-1-rehashed' })],
      worker: {
        available: true,
        running: true,
        paused: false,
        current_status: 'scanning',
        last_scan_time: null,
        stats: {},
      },
    };
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    expect(await screen.findByText(/5 of 10 files/)).toBeInTheDocument();
    expect(screen.getAllByTestId('import-inbox-row')).toHaveLength(3);
    expect(screen.getAllByText('Album A')).toHaveLength(1);
  });

  it('announces import complete when the worker empties the folder (#1671)', async () => {
    const { queryClient } = renderImportRoute();
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    expect(window.showToast).not.toHaveBeenCalled();
    inbox = inboxPayload([], {
      worker: {
        available: true,
        running: true,
        paused: false,
        current_status: 'idle',
        last_scan_time: new Date().toISOString(),
        stats: { auto_processed: 2 },
      },
    });
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        'Import complete — nothing left to import',
        'success',
      ),
    );
  });

  it('does not announce import complete when the files just vanished (#1671)', async () => {
    const { queryClient } = renderImportRoute();
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    // the folder empties but the worker imported nothing (files deleted or
    // moved by hand): no success claim the page cannot back up
    inbox = inboxPayload([]);
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    expect(await screen.findByText('Nothing to import')).toBeInTheDocument();
    expect(window.showToast).not.toHaveBeenCalled();
  });

  it('a worker restart cannot poison the completion baseline (#1671)', async () => {
    const workerWith = (autoProcessed: number) => ({
      available: true,
      running: true,
      paused: false,
      current_status: 'idle' as const,
      last_scan_time: new Date().toISOString(),
      stats: { auto_processed: autoProcessed },
    });
    // the batch is first seen when the worker's counter is already at 100
    inbox = inboxPayload([albumItem()], { worker: workerWith(100) });
    const { queryClient } = renderImportRoute();
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    // the worker restarts (counter resets) and imports the batch: the count
    // can never pass the stale baseline, so no announcement — but the arm
    // must clear rather than poison every later batch
    inbox = inboxPayload([albumItem()], { worker: workerWith(0) });
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    inbox = inboxPayload([], { worker: workerWith(1) });
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    expect(await screen.findByText('Nothing to import')).toBeInTheDocument();
    expect(window.showToast).not.toHaveBeenCalled();
    // the next batch arms against the fresh counter and announces normally
    inbox = inboxPayload([albumItem()], { worker: workerWith(1) });
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    expect(await screen.findByText('Album A')).toBeInTheDocument();
    inbox = inboxPayload([], { worker: workerWith(2) });
    await queryClient.invalidateQueries({ queryKey: ['import', 'inbox'] });
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith(
        'Import complete — nothing left to import',
        'success',
      ),
    );
  });

  it('the filter lives in the url', async () => {
    renderImportRoute(['/import?filter=history']);
    expect(await screen.findByText('Old Album')).toBeInTheDocument();
    expect(screen.getAllByTestId('import-inbox-row')).toHaveLength(1);
    expect(screen.getByRole('tab', { name: /History/ })).toHaveAttribute('aria-selected', 'true');
  });

  it('history can be cleared, and only from the history filter', async () => {
    let cleared = 0;
    server.use(
      http.post('/api/auto-import/clear-completed', () => {
        cleared += 1;
        return HttpResponse.json({ success: true, count: 1 });
      }),
    );
    renderImportRoute(['/import?filter=history']);
    expect(await screen.findByText('Old Album')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Clear history' }));
    await waitFor(() => expect(cleared).toBe(1));
    expect(window.showConfirmDialog).toHaveBeenCalled();

    fireEvent.click(screen.getByRole('tab', { name: /Needs attention/ }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Clear history' })).toBeNull());
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

  it('a waiting item is attention only when auto-import is off', async () => {
    inbox = inboxPayload(inbox.items!, {
      worker: { ...inbox.worker!, running: false },
    });
    renderImportRoute();
    const rows = await screen.findAllByTestId('import-inbox-row');
    expect(rows).toHaveLength(2);
    expect(within(rows[1]).getByText('Waiting')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Needs attention\s*2/ })).toBeInTheDocument();
  });

  it('a waiting item offers Identify, which opens the matcher', async () => {
    const { history } = renderImportRoute(['/import?filter=all']);
    const rows = await screen.findAllByTestId('import-inbox-row');
    const waiting = rows.find((row) => within(row).queryByText('Waiting'))!;
    expect(within(waiting).queryByRole('button', { name: 'Approve' })).toBeNull();
    fireEvent.click(within(waiting).getByRole('button', { name: 'Identify' }));
    await waitFor(() => expect(history.location.pathname).toBe('/import/match/hash-2'));
    expect(await screen.findByText('Which track is this?')).toBeInTheDocument();
    // the search runs from the file's own tags, and picking a result names the import
    expect(await screen.findByText('Loose Song · Artist B')).toBeInTheDocument();
    expect(screen.getByText('No track picked: imported from its own tags')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Loose Song · Artist B/ }));
    expect(screen.getByText(/Tagged as/)).toHaveTextContent('Tagged as Loose Song by Artist B');
  });

  it('importing a single with no track picked sends from_own_tags and no manual match (#1772)', async () => {
    let bodies: Record<string, unknown>[] = [];
    server.use(
      http.post('/api/import/singles/process', async ({ request }) => {
        bodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ success: true, processed: 1, errors: [] });
      }),
    );
    renderImportRoute(['/import/match/hash-2']);
    expect(
      await screen.findByText('No track picked: imported from its own tags'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    const files = bodies[0].files as Record<string, unknown>[];
    expect(files).toHaveLength(1);
    expect(files[0].from_own_tags).toBe(true);
    expect(files[0].manual_match).toBeUndefined();
    expect(files[0].title).toBe('Loose Song');
    expect(files[0].artist).toBe('Artist B');
  });

  it('importing a single with a track picked sends the manual match and no from_own_tags (#1772)', async () => {
    let bodies: Record<string, unknown>[] = [];
    server.use(
      http.post('/api/import/singles/process', async ({ request }) => {
        bodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ success: true, processed: 1, errors: [] });
      }),
    );
    renderImportRoute(['/import/match/hash-2']);
    expect(
      await screen.findByText('No track picked: imported from its own tags'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Loose Song · Artist B/ }));
    expect(screen.getByText(/Tagged as/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    const files = bodies[0].files as Record<string, unknown>[];
    expect(files[0].from_own_tags).toBeUndefined();
    expect(files[0].manual_match).toMatchObject({ name: 'Loose Song' });
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

    // the library check marks what you already have, and the footer counts it
    expect(await screen.findByText(/in library · MP3/)).toBeInTheDocument();
    expect(screen.getByText(/1 already in your library/)).toBeInTheDocument();

    // the preview says where it lands and what changes, before anything moves
    expect(await screen.findByText(/Lands in/)).toBeInTheDocument();
    expect(screen.getByText('/lib/Artist A/Artist A - 2026 Album A')).toBeInTheDocument();
    expect(screen.getByText(/tags change on 1 file/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show details' }));
    expect(screen.getByText('01 - Track.flac')).toBeInTheDocument();
    expect(screen.getByText('Old')).toBeInTheDocument();

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

  describe('phase 2: upload, bulk singles, keyboard', () => {
    it('bulk-imports waiting singles from their own tags', async () => {
      let bodies: Record<string, unknown>[] = [];
      server.use(
        http.post('/api/import/singles/process', async ({ request }) => {
          bodies.push((await request.json()) as Record<string, unknown>);
          return HttpResponse.json({ success: true, processed: 1, errors: [] });
        }),
      );
      inbox = inboxPayload([
        albumItem(),
        ...['s1', 's2'].map((key) =>
          albumItem({
            key,
            kind: 'single',
            name: `Song ${key}`,
            status: 'waiting',
            history_id: null,
            confidence: null,
            match: null,
            files: [
              {
                ...FILE_ONE,
                filename: `${key}.flac`,
                full_path: `/music/Staging/${key}.flac`,
                title: `Song ${key}`,
              },
            ],
            file_count: 1,
          }),
        ),
      ]);
      renderImportRoute(['/import?filter=all']);
      await screen.findByText('Song s1');
      fireEvent.click(screen.getByLabelText('Select Song s1'));
      fireEvent.click(screen.getByLabelText('Select Song s2'));
      fireEvent.click(screen.getByRole('button', { name: 'Import 2 from tags' }));
      await waitFor(() => expect(bodies).toHaveLength(2));
      expect((bodies[0].files as { full_path: string }[])[0].full_path).toBe(
        '/music/Staging/s1.flac',
      );
      // "from tags" must reach the server: no catalogue rematch (#1772)
      expect((bodies[0].files as { from_own_tags?: boolean }[])[0].from_own_tags).toBe(true);
      expect(await screen.findByText('2 of 2 imported')).toBeInTheDocument();
    });

    it('keyboard: j moves, x ticks, enter opens the matcher', async () => {
      const { history } = renderImportRoute(['/import?filter=all']);
      await screen.findAllByTestId('import-inbox-row');
      fireEvent.keyDown(window, { key: 'j' });
      fireEvent.keyDown(window, { key: 'j' });
      const rows = screen.getAllByTestId('import-inbox-row');
      expect(rows[1].className).toContain('focused');
      fireEvent.keyDown(window, { key: 'x' });
      expect(screen.getByText('1 selected')).toBeInTheDocument();
      fireEvent.keyDown(window, { key: 'Enter' });
      await waitFor(() => expect(history.location.pathname).toBe('/import/match/hash-2'));
    });

    it('keyboard shortcuts stay out of text fields', async () => {
      renderImportRoute(['/import/match/hash-1']);
      const input = await screen.findByPlaceholderText('Artist and album');
      fireEvent.keyDown(input, { key: 'j' });
      // nothing to assert crashed; the inbox is not mounted on the matcher route
      expect(input).toBeInTheDocument();
    });

    it('fingerprint fills the search from what the audio is', async () => {
      let searched: string[] = [];
      server.use(
        http.post('/api/import/fingerprint', () =>
          HttpResponse.json({
            success: true,
            recognised: 2,
            artist: 'Artist A',
            title: null,
            results: [
              {
                file: '01-track.flac',
                status: 'ok',
                title: 'Track One',
                artist: 'Artist A',
                score: 0.97,
              },
              {
                file: '02-track.flac',
                status: 'ok',
                title: 'Track Two',
                artist: 'Artist A',
                score: 0.9,
              },
            ],
          }),
        ),
        http.get('/api/import/search/albums', ({ request }) => {
          searched.push(new URL(request.url).searchParams.get('q') ?? '');
          return HttpResponse.json({ success: true, primary_source: 'spotify', albums: [] });
        }),
      );
      renderImportRoute(['/import/match/hash-1']);
      await screen.findByPlaceholderText('Artist and album');
      fireEvent.click(await screen.findByRole('button', { name: 'Identify by fingerprint' }));
      expect(
        await screen.findByText('Sounds like Artist A (Track One, Track Two)'),
      ).toBeInTheDocument();
      await waitFor(() => expect(searched).toContain('Artist A Album A'));
    });

    it('fingerprint says so when acoustid is not set up', async () => {
      server.use(
        http.post('/api/import/fingerprint', () =>
          HttpResponse.json(
            {
              success: false,
              error: 'No AcoustID API key configured',
              code: 'acoustid_unavailable',
            },
            { status: 503 },
          ),
        ),
      );
      renderImportRoute(['/import/match/hash-2']);
      fireEvent.click(await screen.findByRole('button', { name: 'Identify by fingerprint' }));
      expect(await screen.findByText('No AcoustID API key configured')).toBeInTheDocument();
    });

    it('uploads a picked file in pieces and re-reads the inbox', async () => {
      // The upload posts via raw XHR. MSW intercepts it, but request.formData()
      // throws in this environment: undici's multipart parser rejects the jsdom
      // Blob parts XHR produces, so read the fields from the raw multipart text.
      const chunks: { index: string; total: string; path: string; id: string }[] = [];
      const field = (text: string, name: string): string => {
        const match = text.match(new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r\\n]*)`));
        return match ? match[1] : '';
      };
      server.use(
        http.post('/api/import/upload/chunk', async ({ request }) => {
          const text = await request.text();
          const index = field(text, 'index');
          const total = field(text, 'total');
          const path = field(text, 'path');
          chunks.push({ index, total, path, id: field(text, 'upload_id') });
          const last = index === String(Number(total) - 1);
          return HttpResponse.json({
            success: true,
            received: Number(index) + 1,
            total: Number(total),
            ...(last ? { saved: { file: path, size: 3 } } : {}),
          });
        }),
      );
      const { container } = renderImportRoute();
      expect(await screen.findByRole('button', { name: 'Add files' })).toBeInTheDocument();
      const input = container.querySelector('input[type="file"][accept]') as HTMLInputElement;
      const file = new File([new Uint8Array(3)], 'new.flac', { type: 'audio/flac' });
      fireEvent.change(input, { target: { files: [file] } });
      await waitFor(() => expect(chunks).toHaveLength(1));
      expect(chunks[0]).toMatchObject({ index: '0', total: '1', path: 'new.flac' });
      expect(chunks[0].id).toMatch(/^[0-9a-f]{24}$/);
      expect(await screen.findByText('1 uploaded')).toBeInTheDocument();
      await waitFor(() =>
        expect(
          getFetchUrls().filter((url) => url.includes('/api/import/inbox')).length,
        ).toBeGreaterThan(1),
      );
      expect(window.showToast).toHaveBeenCalledWith('1 file in the import folder', 'success');
    });

    it('offers upload buttons on the inbox', async () => {
      renderImportRoute();
      expect(await screen.findByRole('button', { name: 'Add files' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Add a folder' })).toBeInTheDocument();
    });
  });
});
