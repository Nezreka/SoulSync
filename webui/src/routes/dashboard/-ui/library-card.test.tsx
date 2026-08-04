/**
 * LibraryCard — artefact differential + the state-machine rendering + the two
 * scan flows (start/stop/deep, the 2s poll, terminal toasts).
 */

import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { compareTrees, extractDashArticle, parseVanilla } from './dash-artefact';
import { LibraryCard } from './library-card';

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
  vi.stubGlobal('fetch', fetchMock);
  window.showToast = showToast;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  delete window.showToast;
  delete window.showConfirmDialog;
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

describe('the artefact differential', () => {
  it('renders the vanilla library card 1:1 in its Checking state', async () => {
    const vanilla = parseVanilla(
      extractDashArticle('<article class="dash-card" data-card="library">'),
    );
    const view = await mountCard();
    compareTrees(vanilla, view.container.firstElementChild!, 'library');
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
    expect(view.container.querySelector('#library-status-tracks')!.textContent).toBe(
      (300).toLocaleString(),
    );
    expect(view.container.querySelector('#library-status-size')!.textContent).toBe('5.5 MB');
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
