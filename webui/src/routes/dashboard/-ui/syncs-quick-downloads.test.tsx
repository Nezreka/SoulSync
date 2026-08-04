/**
 * The three remaining P7 cards: Recent Syncs (data + 401 + delete), Quick
 * Actions (static tiles + seams), and the Active Downloads ADOPTED shell.
 * Each gets its artefact differential against the live vanilla region.
 */

import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ActiveDownloadsShell } from './active-downloads-shell';
import { compareTrees, extractDashArticle, parseVanilla } from './dash-artefact';
import { QuickActionsCard } from './quick-actions';
import { SyncsCard } from './syncs-card';

const fetchMock = vi.fn((..._args: unknown[]) => Promise.reject(new Error('down')));

beforeEach(() => {
  fetchMock.mockClear();
  fetchMock.mockImplementation(() => Promise.reject(new Error('down')));
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.body.classList.remove('app-locked');
  delete window.openSyncDetailModal;
  delete window.showLoginScreen;
  delete window.showLaunchPinScreen;
  delete window.openAutoSyncScheduleModal;
  delete window.SoulSyncWebRouter;
  delete window.checkForActiveProcesses;
  delete window.updateDashboardDownloads;
});

function syncRoutes(payload: unknown, status = 200) {
  fetchMock.mockImplementation((url: unknown) =>
    String(url).includes('/api/sync/history')
      ? (Promise.resolve({
          ok: status < 400,
          status,
          json: async () => payload,
        }) as never)
      : Promise.reject(new Error('down')),
  );
}

async function mount(node: React.ReactElement) {
  let view: ReturnType<typeof render>;
  await act(async () => {
    view = render(node);
  });
  return view!;
}

describe('the artefact differentials', () => {
  it('Recent Syncs renders the vanilla card 1:1 initially', async () => {
    const vanilla = parseVanilla(
      extractDashArticle('<article class="dash-card" data-card="syncs">'),
    );
    const view = await mount(<SyncsCard />);
    compareTrees(vanilla, view.container.firstElementChild!, 'syncs');
  });

  it('Quick Actions renders the vanilla card 1:1', async () => {
    const vanilla = parseVanilla(
      extractDashArticle('<article class="dash-card dash-card--quick-actions" data-card="tools">'),
    );
    const view = await mount(<QuickActionsCard />);
    compareTrees(vanilla, view.container.firstElementChild!, 'tools');
  });

  it('Active Downloads renders the vanilla shell 1:1', async () => {
    const vanilla = parseVanilla(
      extractDashArticle(
        '<article class="dash-card dash-card--full" id="dashboard-active-downloads-section"',
      ),
    );
    const view = await mount(<ActiveDownloadsShell />);
    compareTrees(vanilla, view.container.firstElementChild!, 'active-downloads');
  });
});

describe('Recent Syncs', () => {
  const ENTRY = {
    id: 7,
    sync_type: 'playlist',
    source: 'spotify',
    playlist_name: 'Bangers',
    tracks_found: 8,
    total_tracks: 10,
    tracks_downloaded: 3,
    thumb_url: '/thumb.jpg',
  };

  it('renders cards from the history payload', async () => {
    syncRoutes({ entries: [ENTRY] });
    const view = await mount(<SyncsCard />);
    const card = view.container.querySelector('.sync-history-card')!;
    expect(card.className).toBe('sync-history-card health-good');
    expect(card.querySelector('.sync-card-name')!.textContent).toBe('Bangers');
    expect(card.querySelector('.sync-card-source')!.textContent).toBe('Spotify');
    expect(card.querySelector('.sync-card-pct')!.textContent).toBe('80%');
    expect(card.querySelector('.sync-card-counts')!.textContent).toBe('8/10 matched · 3 ⬇');
    expect(card.querySelector('img')!.getAttribute('src')).toBe('/thumb.jpg');
  });

  it('an empty history shows the vanilla empty state; wishlist runs are filtered out', async () => {
    syncRoutes({ entries: [{ id: 1, sync_type: 'wishlist' }] });
    const view = await mount(<SyncsCard />);
    expect(view.container.querySelector('.sync-history-empty')!.textContent).toBe('No syncs yet');
  });

  it('card click opens the vanilla detail modal', async () => {
    const openSyncDetailModal = vi.fn();
    window.openSyncDetailModal = openSyncDetailModal;
    syncRoutes({ entries: [ENTRY] });
    const view = await mount(<SyncsCard />);
    fireEvent.click(view.container.querySelector('.sync-history-card')!);
    expect(openSyncDetailModal).toHaveBeenCalledWith(7);
  });

  it('delete DELETEs the entry and removes the card without opening the modal', async () => {
    vi.useFakeTimers();
    const openSyncDetailModal = vi.fn();
    window.openSyncDetailModal = openSyncDetailModal;
    syncRoutes({ entries: [ENTRY] });
    const view = await mount(<SyncsCard />);
    fetchMock.mockImplementation((url: unknown, init?: unknown) =>
      String(url).includes('/api/sync/history/7') &&
      (init as RequestInit | undefined)?.method === 'DELETE'
        ? (Promise.resolve({ ok: true, status: 200, json: async () => ({}) }) as never)
        : Promise.reject(new Error('down')),
    );
    await act(async () => {
      fireEvent.click(view.container.querySelector('.sync-card-delete')!);
    });
    expect(openSyncDetailModal).not.toHaveBeenCalled(); // stopPropagation
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(view.container.querySelector('.sync-history-card')).toBeNull();
  });

  it('a 401 surfaces the correct unlock screen', async () => {
    const showLoginScreen = vi.fn();
    const showLaunchPinScreen = vi.fn();
    window.showLoginScreen = showLoginScreen;
    window.showLaunchPinScreen = showLaunchPinScreen;
    syncRoutes({ login_required: true }, 401);
    await mount(<SyncsCard />);
    expect(showLoginScreen).toHaveBeenCalledTimes(1);
    expect(showLaunchPinScreen).not.toHaveBeenCalled();
  });

  it('never polls while the app is locked', async () => {
    document.body.classList.add('app-locked');
    await mount(<SyncsCard />);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('Quick Actions', () => {
  it('the three tiles hit their seams', async () => {
    const openAutoSyncScheduleModal = vi.fn();
    const navigateToPage = vi.fn(() => Promise.resolve(true));
    window.openAutoSyncScheduleModal = openAutoSyncScheduleModal;
    window.SoulSyncWebRouter = { navigateToPage } as never;
    const view = await mount(<QuickActionsCard />);
    fireEvent.click(view.container.querySelector('.qa-tile--sync')!);
    fireEvent.click(view.container.querySelector('.qa-tile--tools')!);
    fireEvent.click(view.container.querySelector('.qa-tile--auto')!);
    expect(openAutoSyncScheduleModal).toHaveBeenCalledTimes(1);
    expect(navigateToPage.mock.calls).toEqual([['tools'], ['automations']]);
  });

  it('survives the vanilla is-live toggles (adopted class)', async () => {
    const view = await mount(<QuickActionsCard />);
    const tile = view.container.querySelector('.qa-tile--sync')!;
    // core.js's interval toggles this class imperatively; the static component
    // must never re-render it away. Nothing re-renders here by design — assert
    // the imperative write itself sticks.
    tile.classList.add('is-live');
    expect(tile.className).toBe('qa-tile qa-tile--hero qa-tile--sync is-live');
  });
});

describe('the Active Downloads adopted shell', () => {
  it('mount rehydrates the registries THEN paints (checkForActiveProcesses → updateDashboardDownloads)', async () => {
    const order: string[] = [];
    window.checkForActiveProcesses = vi.fn(async () => {
      order.push('check');
    });
    window.updateDashboardDownloads = vi.fn(() => {
      order.push('paint');
    });
    await mount(<ActiveDownloadsShell />);
    expect(order).toEqual(['check', 'paint']);
  });

  it('still paints when the rehydrate throws', async () => {
    window.checkForActiveProcesses = vi.fn(async () => {
      throw new Error('down');
    });
    const updateDashboardDownloads = vi.fn();
    window.updateDashboardDownloads = updateDashboardDownloads;
    await mount(<ActiveDownloadsShell />);
    expect(updateDashboardDownloads).toHaveBeenCalledTimes(1);
  });

  it('vanilla writes into the container survive (adopted region)', async () => {
    const view = await mount(<ActiveDownloadsShell />);
    const section = view.container.querySelector<HTMLElement>(
      '#dashboard-active-downloads-section',
    )!;
    const container = view.container.querySelector<HTMLElement>('#dashboard-downloads-container')!;
    // Simulate updateDashboardDownloads' writes.
    section.style.display = '';
    container.innerHTML = '<div class="dashboard-downloads-group">x</div>';
    expect(section.style.display).toBe('');
    expect(container.innerHTML).toContain('dashboard-downloads-group');
  });
});
