/**
 * The Dashboard page shell — the flip invariants:
 * - id="dashboard-page" on the page-shell div (worker-orbs' anchor, the CSS
 *   overrides, the tour), WITHOUT the `page` class (the label-detail trap:
 *   `.page` is display:none unless the vanilla shell adds `.active`).
 * - the eight bento cards in the vanilla dash-grid order.
 * - the worker-orbs re-ping on mount (the shell bridge's setPage fires before
 *   React paints; the orb layer re-anchors lazily on this second call).
 */

import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DashboardPage } from './dashboard-page';

const fetchMock = vi.fn((..._args: unknown[]) => Promise.reject(new Error('down')));

beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete window.workerOrbs;
});

describe('the page shell', () => {
  it('keeps the id, drops the .page class, and orders the grid like the vanilla', async () => {
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<DashboardPage />);
    });
    const root = view.container.firstElementChild!;
    expect(root.id).toBe('dashboard-page');
    expect(root.className).toBe('page-shell dashboard-container');

    expect(root.firstElementChild!.className).toBe('dashboard-header');

    const grid = root.querySelector('.dash-grid')!;
    expect(Array.from(grid.children).map((card) => card.getAttribute('data-card'))).toEqual([
      // 3.2.0 calm grid: the ContentBand renders NOTHING here (its fetches
      // fail under the test's dead fetch mock). The Library strip LEADS
      // (whose collection this is, then what's new in it), then one ops row
      // — Services beside Recent Syncs (span 2). The System Stats strip is
      // RETIRED: its numbers moved to the notification tray / downloads
      // page / the syncs card's live pill. Services CONTAINS the old
      // enrichment equalizer; Recent Activity (tray owns it) and Quick
      // Actions (sidebar duplicate) are deliberately gone.
      'library',
      'services',
      'syncs',
      'active-downloads',
    ]);

    // worker-orbs' anchor selector must resolve against this tree.
    expect(view.container.querySelector('#dashboard-page .dashboard-header')).not.toBeNull();
    expect(view.container.querySelector('#dashboard-page .header-actions')).not.toBeNull();
  });

  it('re-pings worker-orbs after mount so the lazy re-anchor finds the header', async () => {
    const setPage = vi.fn();
    window.workerOrbs = { setPage, onStatus: vi.fn() } as never;
    await act(async () => {
      render(<DashboardPage />);
    });
    expect(setPage).toHaveBeenCalledWith('dashboard');
  });
});
