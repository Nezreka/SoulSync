/**
 * #2333: the bulk bar's "Delete files…". it says what goes, with counts and
 * size and whether it can be restored, asks for DELETE on a big one, then
 * runs the bulk route one artist at a time and reports what failed.
 */
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2ArtistSummary } from '../-library-v2.types';

const profile = vi.hoisted(() => ({ isAdmin: true }));
vi.mock('@/platform/shell/bridge', async (original) => ({
  ...(await original<typeof import('@/platform/shell/bridge')>()),
  getShellProfileContext: () => profile,
}));

import { ArtistBulkBar, dismissBulkRun, type ArtistSelection } from './library-bulk';
import { restoreNote } from './library-bulk-delete-files';

// the whole library suite runs these under load
const WAIT = { timeout: 10000 };

afterEach(() => {
  dismissBulkRun();
  profile.isAdmin = true;
  Reflect.deleteProperty(window, 'showToast');
});

function bar(ids = [5, 6], clear = vi.fn()) {
  const selection: ArtistSelection = {
    selected: new Set(ids),
    toggle: vi.fn(),
    setMany: vi.fn(),
    clear,
  };
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <ArtistBulkBar
        selection={selection}
        pageArtists={
          ids.map((id) => ({
            id,
            name: `Artist ${String.fromCharCode(60 + id)}`,
          })) as LibraryV2ArtistSummary[]
        }
        totalMatching={ids.length}
        filters={{ q: '', sort: 'name', monitored: 'all' } as never}
        canWrite
        canWish
        canWatch
        canSearch
      />
    </QueryClientProvider>,
  );
  return clear;
}

function preview(overrides: Record<string, unknown> = {}) {
  server.use(
    http.post('*/api/library/v2/artists/delete-files/preview', () =>
      HttpResponse.json({
        success: true,
        artists: 2,
        albums: 41,
        files: 512,
        total_size: 18.2 * 1024 ** 3,
        missing: 0,
        blocked: [],
        not_found: 0,
        mode: 'quarantine',
        keep_days: 30,
        ...overrides,
      }),
    ),
  );
}

function deletes(fail: number[] = []) {
  const sent: number[][] = [];
  server.use(
    http.post('*/api/library/v2/artists/delete-files', async ({ request }) => {
      const { ids } = (await request.json()) as { ids: number[] };
      sent.push(ids);
      const results = ids.map((id) =>
        fail.includes(id)
          ? { id, ok: false, error: 'a file sits outside your library folders' }
          : { id, ok: true, files_deleted: 3 },
      );
      const failed = results.filter((r) => !r.ok).length;
      return HttpResponse.json({
        success: true,
        deleted: ids.length - failed,
        failed,
        files_deleted: 3,
        results,
      });
    }),
  );
  return sent;
}

describe('deleting the files of several artists', () => {
  it('is for an admin only', () => {
    profile.isAdmin = false;
    bar();
    expect(screen.queryByRole('button', { name: 'Delete files…' })).toBeNull();
  });

  it('says what goes and how long it can be restored, then deletes each artist', async () => {
    preview({ files: 12 });
    const sent = deletes();
    const confirm = vi.fn();
    window.confirm = confirm;
    window.showToast = vi.fn() as never;
    const clear = bar();
    fireEvent.click(screen.getByRole('button', { name: 'Delete files…' }));
    expect(
      await screen.findByText('Delete 2 artists: 41 albums, 12 files, 18.2 GB.', {}, WAIT),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Files go to the deleted-files folder and can be restored for 30 days.'),
    ).toBeInTheDocument();
    // a small delete needs no typing
    expect(screen.queryByLabelText('Type DELETE to confirm')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Delete 12 files' }));
    await waitFor(() => expect(sent).toEqual([[5], [6]]), WAIT);
    await waitFor(() => expect(clear).toHaveBeenCalled(), WAIT);
    expect(confirm).not.toHaveBeenCalled();
    expect(window.showToast).toHaveBeenCalledWith('Delete files: 2 artists done', 'success');
  });

  it('asks for DELETE typed out on a big delete', async () => {
    preview();
    const sent = deletes();
    bar();
    fireEvent.click(screen.getByRole('button', { name: 'Delete files…' }));
    const input = await screen.findByLabelText('Type DELETE to confirm', {}, WAIT);
    const go = screen.getByRole('button', { name: 'Delete 512 files' });
    expect(go).toBeDisabled();
    fireEvent.change(input, { target: { value: 'delete' } });
    expect(go).toBeDisabled();
    fireEvent.change(input, { target: { value: 'DELETE' } });
    expect(go).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await new Promise((r) => setTimeout(r, 30));
    expect(sent).toEqual([]);
  });

  it('reports the artists that failed and carries on', async () => {
    preview({ files: 4 });
    deletes([5]);
    window.showToast = vi.fn() as never;
    bar();
    fireEvent.click(screen.getByRole('button', { name: 'Delete files…' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete 4 files' }, WAIT));
    expect(await screen.findByText(/1 failed, finished/, {}, WAIT)).toBeInTheDocument();
    expect(
      screen.getByText('Artist A: a file sits outside your library folders'),
    ).toBeInTheDocument();
    expect(window.showToast).toHaveBeenCalledWith(
      'Delete files: 1 artist done, 1 failed',
      'warning',
    );
  });

  it('skips an artist the preview already refuses', async () => {
    preview({ files: 4, blocked: [{ id: 6, name: 'Artist B', reason: 'outside' }] });
    const sent = deletes();
    bar();
    fireEvent.click(screen.getByRole('button', { name: 'Delete files…' }));
    expect(await screen.findByText(/1 artist will be skipped/, {}, WAIT)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Delete 4 files' }));
    await waitFor(() => expect(sent).toEqual([[5]]), WAIT);
  });

  it('says when the files cannot come back', () => {
    const base = {
      artists: 1,
      albums: 1,
      files: 1,
      total_size: 1,
      missing: 0,
      blocked: [],
      not_found: 0,
    };
    expect(restoreNote({ ...base, mode: 'permanent', keep_days: 0 })).toMatch(/for good/);
    expect(restoreNote({ ...base, mode: 'quarantine', keep_days: 0 })).toMatch(
      /until you empty it/,
    );
  });
});
