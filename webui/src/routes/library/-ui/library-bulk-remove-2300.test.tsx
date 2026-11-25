/**
 * #2300: remove several artists at once from the Library. the bulk bar's
 * Remove from library asks first, then runs the single remove for each.
 */
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2ArtistSummary } from '../-library-v2.types';

vi.mock('@/platform/shell/bridge', async (original) => ({
  ...(await original<typeof import('@/platform/shell/bridge')>()),
  getShellProfileContext: () => ({ isAdmin: true }),
}));

import { ArtistBulkBar, dismissBulkRun, type ArtistSelection } from './library-bulk';

afterEach(() => {
  dismissBulkRun();
  Reflect.deleteProperty(window, 'showToast');
  Reflect.deleteProperty(window, 'showConfirmDialog');
});

function bar(clear = vi.fn()) {
  const selection: ArtistSelection = {
    selected: new Set([5, 6]),
    toggle: vi.fn(),
    setMany: vi.fn(),
    clear,
  };
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <ArtistBulkBar
        selection={selection}
        pageArtists={
          [
            { id: 5, name: 'A' },
            { id: 6, name: 'B' },
          ] as LibraryV2ArtistSummary[]
        }
        totalMatching={2}
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

describe('removing several artists at once', () => {
  it('asks first, then removes each one and clears the selection', async () => {
    const deleted: string[] = [];
    server.use(
      http.delete('*/api/library/v2/artists/:id', ({ params }) => {
        deleted.push(String(params.id));
        return HttpResponse.json({ success: true });
      }),
    );
    const confirm = vi.fn(async () => true);
    window.showConfirmDialog = confirm as never;
    window.showToast = vi.fn() as never;
    const clear = bar();
    fireEvent.click(screen.getByRole('button', { name: 'Remove from library' }));
    await waitFor(() => expect(deleted.sort()).toEqual(['5', '6']));
    expect(confirm).toHaveBeenCalledWith(
      expect.objectContaining({ destructive: true, confirmText: 'Remove 2 artists' }),
    );
    await waitFor(() => expect(clear).toHaveBeenCalled());
  });

  it('removes nothing when the answer is no', async () => {
    const deleted: string[] = [];
    server.use(
      http.delete('*/api/library/v2/artists/:id', ({ params }) => {
        deleted.push(String(params.id));
        return HttpResponse.json({ success: true });
      }),
    );
    window.showConfirmDialog = vi.fn(async () => false) as never;
    bar();
    fireEvent.click(screen.getByRole('button', { name: 'Remove from library' }));
    await new Promise((r) => setTimeout(r, 50));
    expect(deleted).toEqual([]);
  });
});
