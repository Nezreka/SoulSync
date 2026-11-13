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
  it('keeps the id, drops the .page class, hero then music and the system rail', async () => {
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<DashboardPage />);
    });
    const root = view.container.firstElementChild!;
    expect(root.id).toBe('dashboard-page');
    expect(root.className).toBe('page-shell dashboard-container dash-room');

    // the hero: greeting + library on the left, the orb stage on the right
    const hero = root.firstElementChild!;
    expect(hero.className).toBe('dashboard-header dash-hero');
    expect(hero.querySelector('.dash-hero-main [data-card="library"]')).not.toBeNull();
    expect(hero.querySelector(':scope > .orb-stage > .header-actions')).not.toBeNull();

    // The calm page: the AlertsBand renders NOTHING while healthy (and under
    // the test's dead fetch mock — no payload, no false alarms); the content
    // and history bands render nothing until a feed has rows.
    const cards = (sel: string) =>
      Array.from(root.querySelectorAll(`${sel} > [data-card]`)).map((card) =>
        card.getAttribute('data-card'),
      );
    // what you came to play is the first thing under the hero
    expect(cards('.room-top')).toEqual(['active-downloads', 'listen']);
    // the system is a board, not a side column
    expect(cards('.room-board')).toEqual(['sync', 'automations']);
    expect(root.querySelector('.dash-side')).toBeNull();
    expect(root.querySelector('.dash-layout')).toBeNull();
    // the order down the page: hero, the mixes, what just arrived, the
    // system, what you played, then the switches. the wrap is what the board
    // measures itself against
    expect(
      Array.from(root.children)
        .map((child) => child.className)
        .filter((name) => name.startsWith('room-') || name === 'dash-footer'),
    ).toEqual([
      'room-top',
      'room-shelves',
      'room-board-wrap',
      'room-shelves room-shelves--last',
      'dash-footer',
    ]);
    expect(root.querySelector('.room-board-wrap > .room-board')).not.toBeNull();
    // watchlist + wishlist are tiles on the board, keeping the tour's ids
    expect(root.querySelector('.room-board #watchlist-button')).not.toBeNull();
    expect(root.querySelector('.room-board #wishlist-button')).not.toBeNull();
    // Boulder's quick switches: their own footer, not inside a card
    expect(root.querySelector('.dash-footer .dash-quick-settings')).not.toBeNull();
    expect(root.querySelector('[data-card="automations"] .dash-quick-settings')).toBeNull();

    // worker-orbs' anchor selector must resolve against this tree.
    expect(
      view.container.querySelector('#dashboard-page .orb-stage .header-actions'),
    ).not.toBeNull();
  });

  it('re-pings worker-orbs after mount so the lazy re-anchor finds the header', async () => {
    const setPage = vi.fn();
    window.workerOrbs = { setPage, onStatus: vi.fn() } as never;
    await act(async () => {
      render(<DashboardPage />);
    });
    expect(setPage).toHaveBeenCalledWith('dashboard');
  });

  it('leaves out the cards of what the profile switched off (#1909)', async () => {
    const off = new Set(['playlists', 'automations']);
    window.SoulSyncFeatures = { isOn: (id: string) => !off.has(id) } as never;
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<DashboardPage />);
    });
    delete window.SoulSyncFeatures;
    const board = Array.from(view.container.querySelectorAll('.room-board > [data-card]')).map(
      (card) => card.getAttribute('data-card'),
    );
    expect(board).toEqual([]);
    // watchlist and wishlist are the core, they stay
    expect(view.container.querySelector('.room-board #watchlist-button')).not.toBeNull();
  });
});
