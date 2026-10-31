/**
 * The video header's action menu: Clear failed takes only failed and
 * cancelled rows, and sits beside Retry failed and Clear finished.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { VdlHeaderProps } from './vdl-header';

import { VdlHeader } from './vdl-header';

afterEach(cleanup);

const counts = { active: 0, completed: 0, failed: 0, removable: 0, total: 0 };

const props = (over: Partial<VdlHeaderProps> = {}): VdlHeaderProps => ({
  view: 'downloads',
  onView: vi.fn(),
  filter: 'all',
  onFilter: vi.fn(),
  downloads: [],
  counts,
  liveSpeedBps: 0,
  hasRunningWork: false,
  retryableCount: 0,
  reviewCount: null,
  historyCount: null,
  onOpenHistory: vi.fn(),
  onCancelAll: vi.fn(),
  onRetryAll: vi.fn(),
  onClearFinished: vi.fn(),
  onClearFailed: vi.fn(),
  retryAllPending: false,
  ...over,
});

const openMenu = (container: HTMLElement) =>
  fireEvent.click(container.querySelector('.dlc-more') as HTMLElement);

describe('video header actions', () => {
  it('offers Clear failed with the failed + cancelled count, and it never retries', () => {
    const p = props({
      counts: { ...counts, completed: 54, failed: 45, removable: 44, total: 99 },
      retryableCount: 43,
    });
    const { container } = render(<VdlHeader {...p} />);
    openMenu(container);
    const items = screen.getAllByRole('menuitem').map((el) => el.textContent);
    expect(items.indexOf('Retry failed (43)')).toBeLessThan(items.indexOf('Clear failed (44)'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Clear failed (44)' }));
    expect(p.onClearFailed).toHaveBeenCalledTimes(1);
    expect(p.onRetryAll).not.toHaveBeenCalled();
    expect(p.onClearFinished).not.toHaveBeenCalled();
  });

  it('hides Clear failed when only import failures are left', () => {
    const p = props({ counts: { ...counts, completed: 2, failed: 1, removable: 0, total: 3 } });
    const { container } = render(<VdlHeader {...p} />);
    openMenu(container);
    expect(screen.queryByRole('menuitem', { name: /^Clear failed/ })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: /^Clear finished/ })).not.toBeNull();
  });
});
