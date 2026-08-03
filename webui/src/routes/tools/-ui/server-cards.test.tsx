/**
 * Media scan, metadata updater, backup manager.
 *
 * The media scan tests are the important ones: this card is where the two bugs
 * from the bugfix PR lived, and the port has to carry the FIXED behaviour
 * across rather than faithfully reproducing the break.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BackupManagerCard, MediaScanCard, MetadataUpdaterCard } from './server-cards';

const fetchMock = vi.fn();
const toastSpy = vi.fn();
const confirmSpy = vi.fn();

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

const server = (name: string) => ({ success: true, active_server: name });

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

describe('MediaScanCard visibility', () => {
  it.each([
    ['plex', false],
    ['jellyfin', true],
    ['navidrome', true],
  ])('server=%s -> hidden=%s', async (name, hidden) => {
    routes({ 'active-media-server': server(name) });
    const { container } = render(<MediaScanCard />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await flush();
    const card = container.querySelector('#media-scan-card') as HTMLElement;
    expect(card.style.display === 'none').toBe(hidden);
  });

  it('starts hidden before the server check resolves', () => {
    const { container } = render(<MediaScanCard />);
    expect((container.querySelector('#media-scan-card') as HTMLElement).style.display).toBe('none');
  });
});

describe('MediaScanCard — the two bugs the bugfix PR fixed must stay fixed', () => {
  async function startScan(container: HTMLElement) {
    fireEvent.click(container.querySelector('#media-scan-button') as Element);
    await flush();
  }

  it('re-enables the button when the scan reports idle', async () => {
    // Bug 1a: the vanilla looked up `media-scan-btn` (a CLASS) instead of
    // `media-scan-button`, so the button was disabled on click and never
    // re-enabled — dead after one scan.
    vi.useFakeTimers();
    routes({
      'active-media-server': server('plex'),
      '/api/scan/request': { success: true, scan_info: { delay_seconds: 1 } },
      '/api/scan/status': { success: true, status: { is_scanning: false, status: 'idle' } },
    });
    const { container } = render(<MediaScanCard />);
    await act(async () => {});
    await startScan(container);
    expect((container.querySelector('#media-scan-button') as HTMLButtonElement).disabled).toBe(
      true,
    );

    await act(async () => {
      vi.advanceTimersByTime(1000); // countdown ends -> polling starts
    });
    await act(async () => {
      vi.advanceTimersByTime(2000); // first poll -> idle -> finish
    });
    expect((container.querySelector('#media-scan-button') as HTMLButtonElement).disabled).toBe(
      false,
    );
    expect(container.querySelector('#media-scan-phase-label')?.textContent).toBe(
      'Scan completed successfully',
    );
    expect(container.querySelector('#media-scan-status')?.textContent).toBe('Idle');
  });

  it('BOUNDS the poll instead of leaking a 2s timer forever', async () => {
    // Bug 1b: the vanilla incremented its counter AFTER an early return, so on a
    // socket-connected client the counter never advanced and its clearInterval
    // was unreachable. Here every tick counts, so the poll always terminates.
    vi.useFakeTimers();
    routes({
      'active-media-server': server('plex'),
      '/api/scan/request': { success: true, scan_info: { delay_seconds: 1 } },
      '/api/scan/status': { success: true, status: { is_scanning: true } },
    });
    const { container } = render(<MediaScanCard />);
    await act(async () => {});
    await startScan(container);
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    // run well past the 150-poll ceiling
    await act(async () => {
      vi.advanceTimersByTime(2000 * 155);
    });
    const afterCeiling = fetchMock.mock.calls.length;
    await act(async () => {
      vi.advanceTimersByTime(2000 * 20);
    });
    expect(fetchMock.mock.calls.length).toBe(afterCeiling);
    expect((container.querySelector('#media-scan-button') as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('stops its timers on unmount', async () => {
    vi.useFakeTimers();
    routes({
      'active-media-server': server('plex'),
      '/api/scan/request': { success: true, scan_info: { delay_seconds: 5 } },
    });
    const { container, unmount } = render(<MediaScanCard />);
    await act(async () => {});
    await startScan(container);
    unmount();
    const afterUnmount = fetchMock.mock.calls.length;
    await act(async () => {
      vi.advanceTimersByTime(60000);
    });
    expect(fetchMock.mock.calls.length).toBe(afterUnmount);
  });

  it('counts down before the scan starts', async () => {
    vi.useFakeTimers();
    routes({
      'active-media-server': server('plex'),
      '/api/scan/request': { success: true, scan_info: { delay_seconds: 3 } },
    });
    const { container } = render(<MediaScanCard />);
    await act(async () => {});
    await startScan(container);
    expect(container.querySelector('#media-scan-phase-label')?.textContent).toBe(
      'Scan scheduled...',
    );
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(container.querySelector('#media-scan-progress-label')?.textContent).toBe(
      'Starting scan in 2s...',
    );
  });

  it('reports a refused scan request', async () => {
    routes({
      'active-media-server': server('plex'),
      '/api/scan/request': { success: false, error: 'plex unreachable' },
    });
    const { container } = render(<MediaScanCard />);
    await flush();
    fireEvent.click(container.querySelector('#media-scan-button') as Element);
    await flush();
    expect(toastSpy).toHaveBeenCalledWith(
      '❌ Scan request failed: plex unreachable',
      'error',
      5000,
    );
    expect((container.querySelector('#media-scan-button') as HTMLButtonElement).disabled).toBe(
      false,
    );
    expect(container.querySelector('#media-scan-status')?.textContent).toBe('Error');
  });
});

describe('MetadataUpdaterCard', () => {
  it.each([
    ['plex', false],
    ['jellyfin', false],
    ['navidrome', true],
  ])('server=%s -> hidden=%s', async (name, hidden) => {
    routes({ 'active-media-server': server(name) });
    const { container } = render(<MetadataUpdaterCard />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await flush();
    const card = container.querySelector('#metadata-updater-card') as HTMLElement;
    expect(card.style.display === 'none').toBe(hidden);
  });

  it('is VISIBLE before the check resolves — the markup has no display:none', async () => {
    const { container } = render(<MetadataUpdaterCard />);
    expect((container.querySelector('#metadata-updater-card') as HTMLElement).style.display).toBe(
      '',
    );
    await flush();
  });

  it('renames itself and swaps the description per server', async () => {
    routes({ 'active-media-server': server('jellyfin') });
    const { container } = render(<MetadataUpdaterCard />);
    await waitFor(() => expect(screen.getByText('Jellyfin Metadata Updater')).toBeTruthy());
    expect(container.querySelector('.metadata-updater-description')?.textContent).toContain(
      'Jellyfin server',
    );
    cleanup();

    routes({ 'active-media-server': server('plex') });
    const second = render(<MetadataUpdaterCard />);
    await waitFor(() => expect(screen.getByText('Plex Metadata Updater')).toBeTruthy());
    expect(second.container.querySelector('.metadata-updater-description')?.textContent).toContain(
      'Plex server',
    );
  });

  it('offers the six refresh intervals with 1 month selected', async () => {
    const { container } = render(<MetadataUpdaterCard />);
    await flush();
    const select = container.querySelector('#metadata-refresh-interval') as HTMLSelectElement;
    expect([...select.options].map((option) => option.value)).toEqual([
      '180',
      '90',
      '30',
      '14',
      '7',
      '0',
    ]);
    expect(select.value).toBe('30');
  });

  it('shows the running progress line', async () => {
    routes({
      'active-media-server': server('plex'),
      '/api/metadata/status': {
        success: true,
        status: {
          status: 'running',
          current_artist: 'Boards of Canada',
          processed: 4,
          total: 20,
          percentage: 20,
        },
      },
    });
    const { container } = render(<MetadataUpdaterCard />);
    await waitFor(() =>
      expect(container.querySelector('#metadata-phase-label')?.textContent).toBe(
        'Current Artist: Boards of Canada',
      ),
    );
    expect(container.querySelector('#metadata-progress-label')?.textContent).toBe(
      '4 / 20 artists (20.0%)',
    );
    expect((container.querySelector('#metadata-update-button') as HTMLElement).textContent).toBe(
      'Stop Update',
    );
    expect(
      (container.querySelector('#metadata-refresh-interval') as HTMLSelectElement).disabled,
    ).toBe(true);
  });

  it('summarises a completed run and toasts once', async () => {
    routes({
      'active-media-server': server('plex'),
      '/api/metadata/status': {
        success: true,
        status: { status: 'completed', processed: 10, successful: 8, failed: 2 },
      },
    });
    const { container } = render(<MetadataUpdaterCard />);
    await waitFor(() =>
      expect(container.querySelector('#metadata-progress-label')?.textContent).toBe(
        'Completed: 10 processed, 8 successful, 2 failed',
      ),
    );
    expect((container.querySelector('#metadata-progress-bar') as HTMLElement).style.width).toBe(
      '100%',
    );
    expect(toastSpy).toHaveBeenCalledWith(
      'Metadata update completed: 8 artists updated, 2 failed',
      'success',
    );
    expect(toastSpy).toHaveBeenCalledTimes(1);
  });

  it('shows the error message on a failed run', async () => {
    routes({
      'active-media-server': server('plex'),
      '/api/metadata/status': {
        success: true,
        status: { status: 'error', error: 'spotify auth expired' },
      },
    });
    const { container } = render(<MetadataUpdaterCard />);
    await waitFor(() =>
      expect(container.querySelector('#metadata-phase-label')?.textContent).toBe(
        'Current Artist: Error occurred',
      ),
    );
    expect(container.querySelector('#metadata-progress-label')?.textContent).toBe(
      'spotify auth expired',
    );
  });

  it('freezes the progress and keeps the select locked while stopping', async () => {
    // The vanilla's `stopping` branch touches only the button and the phase
    // line. Re-enabling the select or blanking the progress there would let the
    // user change the interval mid-stop and would lose the last known counts.
    let phase = 'running';
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('active-media-server')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => server('plex'),
        } as never);
      }
      const status =
        phase === 'running'
          ? {
              status: 'running',
              current_artist: 'Aphex Twin',
              processed: 7,
              total: 10,
              percentage: 70,
            }
          : { status: 'stopping' };
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ success: true, status }),
      } as never);
    });

    vi.useFakeTimers();
    const { container } = render(<MetadataUpdaterCard />);
    await act(async () => {});
    expect(container.querySelector('#metadata-progress-label')?.textContent).toBe(
      '7 / 10 artists (70.0%)',
    );

    phase = 'stopping';
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    expect(container.querySelector('#metadata-phase-label')?.textContent).toBe(
      'Current Artist: Stopping...',
    );
    // frozen, not reset
    expect(container.querySelector('#metadata-progress-label')?.textContent).toBe(
      '7 / 10 artists (70.0%)',
    );
    expect((container.querySelector('#metadata-progress-bar') as HTMLElement).style.width).toBe(
      '70%',
    );
    // still locked
    expect(
      (container.querySelector('#metadata-refresh-interval') as HTMLSelectElement).disabled,
    ).toBe(true);
    expect((container.querySelector('#metadata-update-button') as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it('sends the selected interval when starting', async () => {
    routes({ 'active-media-server': server('plex'), '/api/metadata/start': { success: true } });
    const { container } = render(<MetadataUpdaterCard />);
    await flush();
    fireEvent.change(container.querySelector('#metadata-refresh-interval') as Element, {
      target: { value: '90' },
    });
    fireEvent.click(container.querySelector('#metadata-update-button') as Element);
    await flush();
    const call = fetchMock.mock.calls.find(([url]) => url === '/api/metadata/start');
    expect(JSON.parse(call?.[1].body)).toEqual({ refresh_interval_days: 90 });
  });
});

describe('BackupManagerCard', () => {
  const twoBackups = {
    success: true,
    count: 2,
    db_size_mb: 120,
    backups: [
      { filename: 'b2.db', created: '2026-08-03T09:00:00', size_mb: 42, version: '3.1.8' },
      { filename: 'b1.db', created: '2026-08-01T09:00:00', size_mb: 40 },
    ],
  };

  it('summarises the newest backup and the db size', async () => {
    routes({ '/api/database/backups': twoBackups });
    const { container } = render(<BackupManagerCard />);
    await waitFor(() =>
      expect(container.querySelector('#backup-stat-count')?.textContent).toBe('2'),
    );
    expect(container.querySelector('#backup-stat-latest-size')?.textContent).toBe('42 MB');
    expect(container.querySelector('#backup-stat-db-size')?.textContent).toBe('120 MB');
  });

  it('says Never with no backups', async () => {
    routes({ '/api/database/backups': { success: true, count: 0, db_size_mb: 5, backups: [] } });
    const { container } = render(<BackupManagerCard />);
    await waitFor(() =>
      expect(container.querySelector('#backup-stat-last')?.textContent).toBe('Never'),
    );
    expect(container.querySelector('#backup-stat-latest-size')?.textContent).toBe('—');
  });

  it('renders a row per backup, with the version badge only when present', async () => {
    routes({ '/api/database/backups': twoBackups });
    const { container } = render(<BackupManagerCard />);
    await waitFor(() => expect(container.querySelectorAll('.backup-list-item')).toHaveLength(2));
    expect(container.querySelectorAll('.backup-list-version')).toHaveLength(1);
    expect(container.querySelector('.backup-list-version')?.textContent).toBe('v3.1.8');
  });

  it('encodes the download link', async () => {
    routes({
      '/api/database/backups': {
        success: true,
        count: 1,
        db_size_mb: 1,
        backups: [{ filename: 'my backup.db', created: '2026-08-03T09:00:00', size_mb: 1 }],
      },
    });
    const { container } = render(<BackupManagerCard />);
    await waitFor(() => expect(container.querySelector('.backup-dl-btn')).not.toBeNull());
    expect(
      (container.querySelector('.backup-dl-btn') as HTMLAnchorElement).getAttribute('href'),
    ).toBe('/api/database/backups/my%20backup.db/download');
  });

  it('confirms before restoring and does nothing when declined', async () => {
    confirmSpy.mockResolvedValue(false);
    routes({ '/api/database/backups': twoBackups });
    const { container } = render(<BackupManagerCard />);
    await waitFor(() => expect(container.querySelector('.backup-restore-btn')).not.toBeNull());
    fireEvent.click(container.querySelector('.backup-restore-btn') as Element);
    await flush();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/restore'))).toBe(false);
  });

  it('re-confirms and forces on a version mismatch', async () => {
    let restoreCalls = 0;
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (String(url).includes('/restore')) {
        restoreCalls += 1;
        const body = init?.body ? JSON.parse(init.body as string) : undefined;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () =>
            body?.force
              ? { success: true, restored_from: 'b2.db', artist_count: 5, safety_backup: 's.db' }
              : { version_mismatch: true, backup_version: '3.1.0', current_version: '3.1.8' },
        } as never);
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => twoBackups } as never);
    });
    const { container } = render(<BackupManagerCard />);
    await waitFor(() => expect(container.querySelector('.backup-restore-btn')).not.toBeNull());
    fireEvent.click(container.querySelector('.backup-restore-btn') as Element);
    await flush();
    await flush();
    expect(confirmSpy).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Version Mismatch', destructive: true }),
    );
    expect(restoreCalls).toBe(2);
  });

  it('confirms before deleting', async () => {
    confirmSpy.mockResolvedValue(false);
    routes({ '/api/database/backups': twoBackups });
    const { container } = render(<BackupManagerCard />);
    await waitFor(() => expect(container.querySelector('.backup-delete-btn')).not.toBeNull());
    fireEvent.click(container.querySelector('.backup-delete-btn') as Element);
    await flush();
    expect(confirmSpy).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Delete Backup', destructive: true }),
    );
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
  });

  it('shows Backing up… while a backup runs', async () => {
    let resolveBackup: (value: unknown) => void = () => {};
    fetchMock.mockImplementation((url: string) => {
      if (url === '/api/database/backup') {
        return new Promise((resolve) => {
          resolveBackup = resolve;
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => twoBackups } as never);
    });
    const { container } = render(<BackupManagerCard />);
    await flush();
    fireEvent.click(container.querySelector('#backup-now-button') as Element);
    await flush();
    expect((container.querySelector('#backup-now-button') as HTMLElement).textContent).toBe(
      'Backing up...',
    );
    await act(async () => {
      resolveBackup({ ok: true, status: 200, json: async () => ({ success: true, size_mb: 42 }) });
    });
  });
});
