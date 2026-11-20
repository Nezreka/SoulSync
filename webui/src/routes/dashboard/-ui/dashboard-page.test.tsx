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
      view = render(<DashboardPage isAdmin />);
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
      render(<DashboardPage isAdmin />);
    });
    expect(setPage).toHaveBeenCalledWith('dashboard');
  });

  it('leaves out the cards of what the profile switched off (#1909)', async () => {
    const off = new Set(['playlists', 'automations']);
    window.SoulSyncFeatures = { isOn: (id: string) => !off.has(id) } as never;
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<DashboardPage isAdmin />);
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

// #2196: the enrichment workers are the admins'. a member's dashboard has no
// orbs, no worker sheet, no Manage Workers, and never asks the worker routes
// (they refuse a member, so a poll would only make 403s).
const WORKER_URLS = /\/api\/(enrichment|hydrabase-worker|soulid|dev-mode|repair\/status)/;

function urlsAsked(): string[] {
  return fetchMock.mock.calls.map((call) => String(call[0]));
}

describe('the workers are the admins (#2196)', () => {
  it('a member sees no orbs, no Manage Workers, and the hero is the greeting alone', async () => {
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<DashboardPage isAdmin={false} />);
    });
    const hero = view.container.querySelector('.dashboard-header.dash-hero')!;
    expect(hero).not.toBeNull();
    expect(hero.classList.contains('dash-hero--solo')).toBe(true);
    expect(view.container.querySelector('.orb-stage')).toBeNull();
    expect(view.container.querySelector('.header-actions')).toBeNull();
    expect(view.container.querySelector('#manage-enrichment-btn')).toBeNull();
    expect(view.container.querySelector('.orb-stage-caption')).toBeNull();
    expect(view.container.querySelector('#library-status-verify-btn')).toBeNull();
    // the greeting and the board are still theirs; the library line too,
    // when they may open the library page (#2207, below)
    expect(view.container.querySelector('.room-board #watchlist-button')).not.toBeNull();
  });

  it("a member's dashboard never asks the workers, and the orb layer is not pinged", async () => {
    const setPage = vi.fn();
    window.workerOrbs = { setPage, onStatus: vi.fn() } as never;
    vi.useFakeTimers();
    try {
      await act(async () => {
        render(<DashboardPage isAdmin={false} />);
      });
      // past the 10s fallback poll
      await act(async () => {
        vi.advanceTimersByTime(25000);
      });
    } finally {
      vi.useRealTimers();
    }
    expect(urlsAsked().filter((url) => WORKER_URLS.test(url))).toEqual([]);
    expect(setPage).not.toHaveBeenCalled();
  });

  it('an admin keeps the orbs, Manage Workers and the status poll', async () => {
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<DashboardPage isAdmin />);
    });
    expect(view.container.querySelector('.dash-hero--solo')).toBeNull();
    expect(view.container.querySelector('.orb-stage .header-actions')).not.toBeNull();
    expect(view.container.querySelector('#manage-enrichment-btn')).not.toBeNull();
    expect(view.container.querySelector('#library-status-verify-btn')).not.toBeNull();
    expect(urlsAsked()).toContain('/api/enrichment/status-all');
  });
});

// #2207 #2208: the server's library status, its size, the review queue and
// the alerts band (a down server, a spotify ban, with a jump to Settings)
// are the admin's. a member keeps a way into the library page.
const ADMIN_URLS = /^\/status$|\/api\/review-queue|\/api\/database\/update/;

describe("the server's state is the admin's (#2207 #2208)", () => {
  afterEach(() => {
    Reflect.deleteProperty(window, 'SoulSyncWebShellBridge');
    fetchMock.mockImplementation(() => Promise.reject(new Error('down')));
  });

  it("a member's hero has Browse library and nothing of the server's", async () => {
    window.SoulSyncWebShellBridge = {
      isPageAllowed: (id: string) => id === 'library',
    } as never;
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<DashboardPage isAdmin={false} />);
    });
    const line = view.container.querySelector('.dash-hero-main [data-card="library"]')!;
    expect(line).not.toBeNull();
    expect(line.querySelector('#library-status-browse-btn')).not.toBeNull();
    expect(line.querySelector('#library-status-review-btn')).toBeNull();
    expect(line.querySelector('#library-status-scan-btn')).toBeNull();
    expect(view.container.querySelector('[data-card="alerts"]')).toBeNull();
    expect(urlsAsked().filter((url) => ADMIN_URLS.test(url))).toEqual([]);
  });

  it('a member who may not open the library has no library line', async () => {
    window.SoulSyncWebShellBridge = { isPageAllowed: () => false } as never;
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<DashboardPage isAdmin={false} />);
    });
    expect(view.container.querySelector('[data-card="library"]')).toBeNull();
  });

  it('a member sees no alerts band, even with the server down', async () => {
    fetchMock.mockImplementation((url: unknown) =>
      String(url) === '/status'
        ? Promise.resolve({
            ok: true,
            json: async () => ({ media_server: { connected: false, type: 'plex' } }),
          } as never)
        : Promise.reject(new Error('down')),
    );
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<DashboardPage isAdmin={false} />);
    });
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent('ss:service-status', {
          detail: { media_server: { connected: false, type: 'plex' } },
        }),
      );
    });
    expect(view.container.querySelector('[data-card="alerts"]')).toBeNull();
  });

  it('an admin keeps the alerts band and the review queue', async () => {
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<DashboardPage isAdmin />);
    });
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent('ss:service-status', {
          detail: { media_server: { connected: false, type: 'plex' } },
        }),
      );
    });
    expect(view.container.querySelector('[data-card="alerts"]')).not.toBeNull();
    expect(view.container.querySelector('#library-status-review-btn')).not.toBeNull();
    expect(urlsAsked()).toContain('/status');
  });
});
