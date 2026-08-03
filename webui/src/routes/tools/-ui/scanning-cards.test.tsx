/**
 * The three progress cards and the polling lifecycle behind them.
 *
 * These assert the STATE MACHINE, not the styling: the button text is what the
 * vanilla branched on, the bar-clearing rules are what #859 was about, and the
 * "did we actually see it running" flag is what stops a completion toast firing
 * every time you open the page after a finished scan.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DbUpdaterCard, DuplicateCleanerCard, ReconcileIdsCard } from './scanning-cards';

const fetchMock = vi.fn();
const toastSpy = vi.fn();
const confirmSpy = vi.fn();

/** Route by URL so a card's stats and status calls can differ. */
function routes(map: Record<string, unknown>, fallback: unknown = {}) {
  fetchMock.mockImplementation((url: string) => {
    const hit = Object.keys(map).find((key) => url.includes(key));
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => (hit ? map[hit] : fallback),
    } as never);
  });
}

async function flush() {
  await act(async () => {});
}

beforeEach(() => {
  fetchMock.mockReset();
  toastSpy.mockReset();
  confirmSpy.mockReset().mockResolvedValue(true);
  routes({});
  vi.stubGlobal('fetch', fetchMock);
  Object.assign(window, { showToast: toastSpy, showConfirmDialog: confirmSpy });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('DbUpdaterCard', () => {
  it('titles itself after the active server once stats land', async () => {
    routes({ '/api/database/stats': { artists: 1, albums: 2, tracks: 3, database_size_mb: 4, server_source: 'plex' } });
    render(<DbUpdaterCard />);
    await waitFor(() => expect(screen.getByText('Plex Database Updater')).toBeTruthy());
  });

  it('falls back to the plain title when no server is reported', async () => {
    routes({ '/api/database/stats': { artists: 0, albums: 0, tracks: 0, database_size_mb: 0 } });
    render(<DbUpdaterCard />);
    await waitFor(() => expect(screen.getByText('Database Updater')).toBeTruthy());
  });

  it('formats the stat row the way the vanilla did', async () => {
    routes({
      '/api/database/stats': {
        artists: 1234,
        albums: 5678,
        tracks: 91011,
        database_size_mb: 42.129,
        last_full_refresh: null,
      },
    });
    const { container } = render(<DbUpdaterCard />);
    await waitFor(() =>
      expect(container.querySelector('#db-stat-artists')?.textContent).toBe('1,234'),
    );
    expect(container.querySelector('#db-stat-size')?.textContent).toBe('42.13 MB');
    expect(container.querySelector('#db-last-refresh')?.textContent).toBe('Never');
  });

  it('confirms before a full refresh and does nothing when declined', async () => {
    confirmSpy.mockResolvedValue(false);
    const { container } = render(<DbUpdaterCard />);
    await flush();
    fireEvent.change(container.querySelector('#db-refresh-type') as Element, {
      target: { value: 'full' },
    });
    fireEvent.click(container.querySelector('#db-update-button') as Element);
    await flush();
    expect(confirmSpy).toHaveBeenCalledWith(expect.objectContaining({ title: 'Full Refresh' }));
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/database/update')).toBe(false);
  });

  it('confirms with the deep-scan copy for a deep scan', async () => {
    confirmSpy.mockResolvedValue(false);
    const { container } = render(<DbUpdaterCard />);
    await flush();
    fireEvent.change(container.querySelector('#db-refresh-type') as Element, {
      target: { value: 'deep' },
    });
    fireEvent.click(container.querySelector('#db-update-button') as Element);
    await flush();
    expect(confirmSpy).toHaveBeenCalledWith(expect.objectContaining({ title: 'Deep Scan' }));
  });

  it('does NOT confirm for an incremental update', async () => {
    const { container } = render(<DbUpdaterCard />);
    await flush();
    fireEvent.click(container.querySelector('#db-update-button') as Element);
    await flush();
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it('keeps the button ENABLED while Starting… so a wedged start stays cancellable (#859)', async () => {
    routes({ '/api/database/update': { success: true }, '/api/database/update/status': null });
    fetchMock.mockImplementation((url: string) => {
      if (url === '/api/database/update') {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ success: true }) } as never);
      }
      // status keeps failing -> the card is stuck on "Starting..."
      return Promise.resolve({ ok: false, status: 500, json: async () => ({}) } as never);
    });
    const { container } = render(<DbUpdaterCard />);
    await flush();
    fireEvent.click(container.querySelector('#db-update-button') as Element);
    await flush();
    const button = container.querySelector('#db-update-button') as HTMLButtonElement;
    expect(button.textContent).toBe('Starting...');
    expect(button.disabled).toBe(false);
  });

  it('surfaces a 200-with-success:false as a failed start, not a started job (#859)', async () => {
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: async () =>
          url === '/api/database/update' ? { success: false, error: 'already running' } : {},
      } as never),
    );
    const { container } = render(<DbUpdaterCard />);
    await flush();
    fireEvent.click(container.querySelector('#db-update-button') as Element);
    await flush();
    expect(toastSpy).toHaveBeenCalledWith('Error: already running', 'error');
    expect((container.querySelector('#db-update-button') as HTMLElement).textContent).toBe(
      'Update Database',
    );
  });

  it('shows Stop Update and locks the mode select while running', async () => {
    routes({
      '/api/database/update/status': {
        status: 'running',
        processed: 5,
        total: 10,
        progress: 50,
        phase: 'Fetching artists',
      },
    });
    const { container } = render(<DbUpdaterCard />);
    await waitFor(() =>
      expect((container.querySelector('#db-update-button') as HTMLElement).textContent).toBe(
        'Stop Update',
      ),
    );
    expect((container.querySelector('#db-refresh-type') as HTMLSelectElement).disabled).toBe(true);
    expect(container.querySelector('#db-phase-label')?.textContent).toBe('Fetching artists');
    expect(container.querySelector('#db-progress-label')?.textContent).toBe(
      '5 / 10 artists (50.0%)',
    );
    expect((container.querySelector('#db-progress-bar') as HTMLElement).style.width).toBe('50%');
  });

  it('leaves a FULL bar on finished and an EMPTY one on idle (#859 frozen bar)', async () => {
    routes({ '/api/database/update/status': { status: 'finished', phase: 'Complete' } });
    const { container } = render(<DbUpdaterCard />);
    await waitFor(() =>
      expect((container.querySelector('#db-progress-bar') as HTMLElement).style.width).toBe('100%'),
    );
    // the details line is cleared so no stale "2/3 artists" survives
    expect(container.querySelector('#db-progress-label')?.textContent).toBe('');
    cleanup();

    routes({ '/api/database/update/status': { status: 'idle', phase: 'Idle' } });
    const second = render(<DbUpdaterCard />);
    await waitFor(() =>
      expect((second.container.querySelector('#db-progress-bar') as HTMLElement).style.width).toBe(
        '0%',
      ),
    );
  });

  it('turns the bar red and shows the message on error', async () => {
    routes({
      '/api/database/update/status': { status: 'error', error_message: 'disk full' },
    });
    const { container } = render(<DbUpdaterCard />);
    await waitFor(() =>
      expect(container.querySelector('#db-phase-label')?.textContent).toBe('Error: disk full'),
    );
    expect((container.querySelector('#db-progress-bar') as HTMLElement).style.backgroundColor).toBe(
      'rgb(255, 68, 68)',
    );
  });
});

describe('ReconcileIdsCard', () => {
  it('confirms before scanning and does nothing when declined', async () => {
    confirmSpy.mockResolvedValue(false);
    const { container } = render(<ReconcileIdsCard />);
    await flush();
    fireEvent.click(container.querySelector('#reconcile-ids-button') as Element);
    await flush();
    expect(confirmSpy).toHaveBeenCalledWith(
      expect.objectContaining({ confirmText: 'Scan Library' }),
    );
    expect(
      fetchMock.mock.calls.some(([url]) => url === '/api/library/reconcile-embedded-ids'),
    ).toBe(false);
  });

  it('shows the running phase with the current file', async () => {
    routes({
      '/status': { status: 'running', current: 'Zeppelin/IV/01.flac', processed: 3, total: 12 },
    });
    const { container } = render(<ReconcileIdsCard />);
    await waitFor(() =>
      expect(container.querySelector('#reconcile-phase-label')?.textContent).toBe(
        'Scanning: Zeppelin/IV/01.flac',
      ),
    );
    expect(container.querySelector('#reconcile-progress-label')?.textContent).toBe(
      '3 / 12 files scanned (25.0%)',
    );
    expect((container.querySelector('#reconcile-ids-button') as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it('pluralises the done summary correctly', async () => {
    routes({
      '/status': {
        status: 'done',
        ids_filled: 1,
        entities_updated: 1,
        processed: 10,
        total: 10,
      },
    });
    const { container } = render(<ReconcileIdsCard />);
    await waitFor(() =>
      expect(container.querySelector('#reconcile-phase-label')?.textContent).toBe(
        'Done — filled 1 ID across 1 row',
      ),
    );
    cleanup();

    routes({
      '/status': { status: 'done', ids_filled: 7, entities_updated: 3, processed: 10, total: 10 },
    });
    const second = render(<ReconcileIdsCard />);
    await waitFor(() =>
      expect(second.container.querySelector('#reconcile-phase-label')?.textContent).toBe(
        'Done — filled 7 IDs across 3 rows',
      ),
    );
  });

  it('does NOT toast a completion it never saw running', async () => {
    // Opening the page after a finished scan must be silent — otherwise the
    // same scan announces itself on every visit.
    routes({ '/status': { status: 'done', ids_filled: 5, entities_updated: 2 } });
    render(<ReconcileIdsCard />);
    await flush();
    expect(toastSpy).not.toHaveBeenCalled();
  });
});

describe('DuplicateCleanerCard', () => {
  it('toggles between Clean Duplicates and Stop Cleaning', async () => {
    routes({ '/api/duplicate-cleaner/status': { status: 'running', progress: 20, files_scanned: 2, total_files: 10 } });
    const { container } = render(<DuplicateCleanerCard />);
    await waitFor(() =>
      expect((container.querySelector('#duplicate-clean-button') as HTMLElement).textContent).toBe(
        'Stop Cleaning',
      ),
    );
    expect(container.querySelector('#duplicate-progress-label')?.textContent).toBe(
      '2 / 10 files scanned (20.0%)',
    );
  });

  it('posts stop when clicked while running', async () => {
    routes({ '/api/duplicate-cleaner/status': { status: 'running', progress: 0 } });
    const { container } = render(<DuplicateCleanerCard />);
    await waitFor(() =>
      expect((container.querySelector('#duplicate-clean-button') as HTMLElement).textContent).toBe(
        'Stop Cleaning',
      ),
    );
    fireEvent.click(container.querySelector('#duplicate-clean-button') as Element);
    await flush();
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/duplicate-cleaner/stop')).toBe(true);
  });

  it('renders freed space with TWO decimals in the stat row', async () => {
    routes({
      '/api/duplicate-cleaner/status': { status: 'idle', space_freed_mb: 12.345, deleted: 3 },
    });
    const { container } = render(<DuplicateCleanerCard />);
    await waitFor(() =>
      expect(container.querySelector('#duplicate-stat-space')?.textContent).toBe('12.35 MB'),
    );
  });

  it('announces completion ONCE, with ONE decimal in the toast', async () => {
    routes({
      '/api/duplicate-cleaner/status': {
        status: 'finished',
        deleted: 9,
        space_freed_mb: 12.345,
      },
    });
    render(<DuplicateCleanerCard />);
    await waitFor(() => expect(toastSpy).toHaveBeenCalled());
    expect(toastSpy).toHaveBeenCalledWith(
      'Cleaning complete! 9 files removed, 12.3 MB freed',
      'success',
    );
    expect(toastSpy).toHaveBeenCalledTimes(1);
  });

  it('turns the bar red on error', async () => {
    routes({
      '/api/duplicate-cleaner/status': { status: 'error', error_message: 'permission denied' },
    });
    const { container } = render(<DuplicateCleanerCard />);
    await waitFor(() =>
      expect(container.querySelector('#duplicate-phase-label')?.textContent).toBe(
        'Error: permission denied',
      ),
    );
    expect(
      (container.querySelector('#duplicate-progress-bar') as HTMLElement).style.backgroundColor,
    ).toBe('rgb(255, 68, 68)');
  });
});

describe('polling lifecycle', () => {
  it('arms a repeating poll when the first hydrate reports running', async () => {
    vi.useFakeTimers();
    routes({ '/api/duplicate-cleaner/status': { status: 'running', progress: 10 } });
    render(<DuplicateCleanerCard />);
    await act(async () => {});
    const afterMount = fetchMock.mock.calls.length;
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    expect(fetchMock.mock.calls.length).toBeGreaterThan(afterMount);
  });

  it('does NOT poll when the first hydrate reports idle', async () => {
    vi.useFakeTimers();
    routes({ '/api/duplicate-cleaner/status': { status: 'idle' } });
    render(<DuplicateCleanerCard />);
    await act(async () => {});
    const afterMount = fetchMock.mock.calls.length;
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(fetchMock.mock.calls.length).toBe(afterMount);
  });

  it('keeps polling through a failed tick rather than declaring the job done', async () => {
    vi.useFakeTimers();
    let calls = 0;
    fetchMock.mockImplementation(() => {
      calls += 1;
      // first hydrate running, then a blip, then still running
      if (calls === 1) {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'running', progress: 5 }) } as never);
      }
      if (calls === 2) return Promise.reject(new Error('offline'));
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'running', progress: 15 }) } as never);
    });
    const { container } = render(<DuplicateCleanerCard />);
    await act(async () => {});
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    // still running, and the bar advanced — the blip did not end the poll
    expect((container.querySelector('#duplicate-clean-button') as HTMLElement).textContent).toBe(
      'Stop Cleaning',
    );
    expect(calls).toBeGreaterThanOrEqual(3);
  });

  it('stops polling once unmounted', async () => {
    vi.useFakeTimers();
    routes({ '/api/duplicate-cleaner/status': { status: 'running', progress: 10 } });
    const { unmount } = render(<DuplicateCleanerCard />);
    await act(async () => {});
    unmount();
    const afterUnmount = fetchMock.mock.calls.length;
    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    expect(fetchMock.mock.calls.length).toBe(afterUnmount);
  });
});
