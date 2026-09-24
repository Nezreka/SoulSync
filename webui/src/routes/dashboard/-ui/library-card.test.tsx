/**
 * LibraryCard — artefact differential + the state-machine rendering + the two
 * scan flows (start/stop/deep, the 2s poll, terminal toasts).
 */

import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchReviewQueueSummary } from '@/routes/active-downloads/-adl.api';

import { LibraryCard } from './library-card';

vi.mock('@/routes/active-downloads/-adl.api', () => ({
  fetchReviewQueueSummary: vi.fn(async () => null),
}));

const reviewSummary = vi.mocked(fetchReviewQueueSummary);

const fetchMock = vi.fn();
const showToast = vi.fn();

function routes(map: Record<string, unknown>) {
  fetchMock.mockImplementation((url: string) => {
    const hit = Object.keys(map)
      .filter((key) => String(url).includes(key))
      .sort((a, b) => b.length - a.length)[0];
    if (!hit) return Promise.reject(new Error('down'));
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => map[hit],
    } as never);
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(() => Promise.reject(new Error('down')));
  showToast.mockReset();
  reviewSummary.mockReset();
  reviewSummary.mockResolvedValue(null);
  vi.stubGlobal('fetch', fetchMock);
  window.showToast = showToast;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  delete window.showToast;
  Reflect.deleteProperty(window, 'showConfirmDialog');
});

async function mountCard() {
  let view: ReturnType<typeof render>;
  await act(async () => {
    view = render(<LibraryCard />);
  });
  return view!;
}

const CONNECTED = { media_server: { connected: true }, active_media_server: 'plex' };

function fireStatus(payload: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new CustomEvent('ss:service-status', { detail: payload }));
  });
}

function fireDbStats(stats: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new CustomEvent('ss:dashboard-db-stats', { detail: stats }));
  });
}

describe('the hero status line', () => {
  // The Sept 2026 dashboard redesign: the library is the hero's status line,
  // one primary action (Scan) and the rest behind "⋯". The page links that
  // only repeated the sidebar (wishlist, downloads, discover, sync) are gone.
  // What must survive is pinned: every id the state machine + scan flows
  // write into (their tests below all target them), menu items included.
  it('keeps the state-machine ids, the extra actions in the menu', async () => {
    const view = await mountCard();
    const root = view.container.firstElementChild!;
    expect(root.getAttribute('data-card')).toBe('library');
    expect(root.className).toBe('dash-hero-library');
    for (const id of [
      'library-status-card',
      'library-status-title',
      'library-status-subtitle',
      'library-status-scan-btn',
      'library-status-deep-btn',
      'library-status-browse-btn',
      'library-status-verify-btn',
      'library-status-repair-btn',
      'library-status-backup-btn',
      'library-status-review-btn',
      // the four-stat row retired with the header's hello strip —
      // albums + db size live in the subtitle now
      'library-status-progress',
      'library-status-message',
    ]) {
      expect(root.querySelector(`#${id}`)).not.toBeNull();
    }
    for (const id of [
      'library-status-wishlist-btn',
      'library-status-downloads-btn',
      'library-status-discover-btn',
      'library-status-sync-btn',
    ]) {
      expect(root.querySelector(`#${id}`)).toBeNull();
    }
    const menu = root.querySelector('.library-status-menu')!;
    for (const id of ['deep', 'browse', 'verify', 'repair', 'backup', 'review']) {
      expect(menu.querySelector(`#library-status-${id}-btn`)).not.toBeNull();
    }
  });

  it('the ⋯ button opens the menu, and Escape or a pick closes it', async () => {
    const view = await mountCard();
    const more = view.container.querySelector<HTMLElement>('.library-status-more')!;
    const menu = view.container.querySelector<HTMLElement>('.library-status-menu')!;
    expect(menu.hidden).toBe(true);
    fireEvent.click(more);
    expect(menu.hidden).toBe(false);
    expect(more.getAttribute('aria-expanded')).toBe('true');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(menu.hidden).toBe(true);
    fireEvent.click(more);
    fireEvent.click(menu.querySelector('#library-status-browse-btn')!);
    expect(menu.hidden).toBe(true);
  });
});

describe('the state machine in the DOM', () => {
  it('stays on Checking until db stats arrive, then renders the machine state', async () => {
    const view = await mountCard();
    expect(view.container.querySelector('#library-status-subtitle')!.textContent).toBe(
      'Checking status...',
    );
    fireStatus(CONNECTED);
    // status alone must NOT run the machine (the vanilla only renders on
    // db-stats arrival).
    expect(view.container.querySelector('#library-status-subtitle')!.textContent).toBe(
      'Checking status...',
    );
    fireDbStats({ artists: 10, albums: 20, tracks: 300, database_size_mb: 5.5 });
    expect(view.container.querySelector('#library-status-title')!.textContent).toBe('Plex Library');
    expect(view.container.querySelector('#library-status-card')!.className).toBe(
      'library-status-card has-data',
    );
    // Albums + db size fold into the subtitle now that the stat row is gone.
    expect(view.container.querySelector('#library-status-subtitle')!.textContent).toContain(
      '5.5 MB db',
    );
  });

  it('renders the empty-library CTA with the Scan Now button', async () => {
    const view = await mountCard();
    fireStatus(CONNECTED);
    fireDbStats({ tracks: 0 });
    expect(view.container.querySelector('#library-status-scan-label')!.textContent).toBe(
      'Scan Now',
    );
    expect(view.container.querySelector('#library-status-message')!.textContent).toContain(
      'Click Scan Now to pull your artists',
    );
  });
});

describe('the scan flow', () => {
  it('start → poll → complete, with the vanilla toasts', async () => {
    vi.useFakeTimers();
    const view = await mountCard();
    fireStatus(CONNECTED);
    fireDbStats({ tracks: 0 });
    routes({
      '/api/database/update/status': {
        status: 'running',
        phase: 'Reading albums',
        progress: 40,
        processed: 12,
        total: 99,
      },
      '/api/database/update': { success: true },
      '/api/database/stats': { tracks: 500, artists: 1, albums: 2, database_size_mb: 2 },
    });

    await act(async () => {
      fireEvent.click(view.container.querySelector('#library-status-scan-btn')!);
    });
    expect(showToast).toHaveBeenCalledWith('Library scan started', 'success');
    expect(view.container.querySelector('#library-status-title')!.textContent).toBe('Library Scan');
    expect(view.container.querySelector('#library-status-scan-label')!.textContent).toBe('Stop');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(view.container.querySelector('#library-status-phase')!.textContent).toBe(
      'Reading albums',
    );
    expect(view.container.querySelector<HTMLElement>('#library-status-bar-fill')!.style.width).toBe(
      '40%',
    );
    expect(view.container.querySelector('#library-status-progress-detail')!.textContent).toBe(
      '12 / 99',
    );

    routes({
      '/api/database/update/status': { status: 'completed' },
      '/api/database/stats': { tracks: 500, artists: 1, albums: 2, database_size_mb: 2 },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(showToast).toHaveBeenCalledWith('Library scan complete', 'success');
    // Refetched stats land: back to the healthy state.
    expect(view.container.querySelector('#library-status-title')!.textContent).toBe('Plex Library');
  });

  it('a second click while scanning STOPS the scan', async () => {
    vi.useFakeTimers();
    const view = await mountCard();
    fireStatus(CONNECTED);
    fireDbStats({ tracks: 0 });
    routes({
      '/api/database/update/status': { status: 'running' },
      '/api/database/update/stop': {},
      '/api/database/update': { success: true },
      '/api/database/stats': { tracks: 0 },
    });
    await act(async () => {
      fireEvent.click(view.container.querySelector('#library-status-scan-btn')!);
    });
    fetchMock.mockClear();
    await act(async () => {
      fireEvent.click(view.container.querySelector('#library-status-scan-btn')!);
    });
    expect(fetchMock.mock.calls.map((call) => String(call[0]))).toContain(
      '/api/database/update/stop',
    );
    expect(showToast).toHaveBeenCalledWith('Library scan stopped', 'info');
    expect(view.container.querySelector('#library-status-scan-label')!.textContent).toBe(
      'Scan Now',
    );
  });

  it('a failed start unwinds with the backend error', async () => {
    const view = await mountCard();
    fireStatus(CONNECTED);
    fireDbStats({ tracks: 0 });
    routes({ '/api/database/update': { success: false, error: 'server busy' } });
    await act(async () => {
      fireEvent.click(view.container.querySelector('#library-status-scan-btn')!);
    });
    expect(showToast).toHaveBeenCalledWith('server busy', 'error');
    expect(view.container.querySelector('#library-status-scan-label')!.textContent).toBe(
      'Scan Now',
    );
  });

  it('deep scan confirms first and posts deep_scan', async () => {
    const confirm = vi.fn(() => Promise.resolve(true));
    window.showConfirmDialog = confirm;
    const view = await mountCard();
    fireStatus(CONNECTED);
    fireDbStats({ tracks: 300 });
    routes({
      '/api/database/update/status': { status: 'running' },
      '/api/database/update': { success: true },
    });
    await act(async () => {
      fireEvent.click(view.container.querySelector('#library-status-deep-btn')!);
    });
    expect(confirm).toHaveBeenCalledWith(expect.objectContaining({ title: 'Deep Scan Library' }));
    const updateCall = fetchMock.mock.calls.find(
      (call) => String(call[0]) === '/api/database/update',
    )!;
    expect(JSON.parse((updateCall[1] as RequestInit).body as string)).toEqual({
      deep_scan: true,
    });
    expect(showToast).toHaveBeenCalledWith('Deep scan started — this may take a while', 'success');
  });

  it('a declined confirm does nothing', async () => {
    window.showConfirmDialog = vi.fn(() => Promise.resolve(false));
    const view = await mountCard();
    fireStatus(CONNECTED);
    fireDbStats({ tracks: 300 });
    fetchMock.mockClear();
    await act(async () => {
      fireEvent.click(view.container.querySelector('#library-status-deep-btn')!);
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(showToast).not.toHaveBeenCalled();
  });
});

/**
 * TheHomeGuy asked for a way to know there is something to review without
 * going to the downloads page and clicking into the tab. Plus the strip picked
 * up the rest of the links people actually want.
 */
describe('the quick access links', () => {
  const navigate = vi.fn();

  beforeEach(() => {
    navigate.mockReset();
    window.navigateToPage = navigate;
  });

  afterEach(() => {
    Reflect.deleteProperty(window, 'navigateToPage');
  });

  it.each([
    ['library-status-browse-btn', 'library'],
    ['library-status-repair-btn', 'tools'],
    ['library-status-review-btn', 'active-downloads'],
  ])('%s goes to %s', async (id, page) => {
    const view = await mountCard();
    fireEvent.click(view.container.querySelector(`#${id}`)!);
    expect(navigate).toHaveBeenCalledWith(page);
  });

  it('shows the waiting count on Review and calls it out', async () => {
    reviewSummary.mockResolvedValue({ quarantine: 72, unverified: 2, total: 74 });
    const view = await mountCard();

    const btn = view.container.querySelector('#library-status-review-btn')!;
    // the real class list, not just "the rule exists". a badge nobody can
    // select is the shape that has shipped here before.
    expect(btn.className).toContain('library-status-btn-attention');
    // it lives in the menu now, so the menu button carries the dot
    expect(view.container.querySelector('.library-status-more')!.className).toContain(
      'library-status-more--attention',
    );
    expect(btn.querySelector('.library-status-btn-badge')?.textContent).toBe('74');
  });

  it('stays quiet when there is nothing waiting', async () => {
    reviewSummary.mockResolvedValue({ quarantine: 0, unverified: 0, total: 0 });
    const view = await mountCard();

    const btn = view.container.querySelector('#library-status-review-btn')!;
    expect(btn.className).not.toContain('library-status-btn-attention');
    expect(btn.querySelector('.library-status-btn-badge')).toBeNull();
  });

  it('stays quiet when the count cannot be read at all', async () => {
    reviewSummary.mockResolvedValue(null);
    const view = await mountCard();

    const btn = view.container.querySelector('#library-status-review-btn')!;
    expect(btn.querySelector('.library-status-btn-badge')).toBeNull();
  });
});
