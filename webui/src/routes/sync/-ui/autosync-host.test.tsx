/**
 * the dashboard opened a different auto-sync manager than the playlists page
 * (boulder, sept 30: dragging on the dashboard's copy showed no glow, old
 * cards, two refresh buttons). the host takes window.openAutoSyncScheduleModal
 * over so every entry point opens the react one.
 */

import { act, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AutoSyncHost } from './autosync-host';

beforeEach(() => {
  // every load endpoint answers empty, the modal only needs to exist
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify([]), { status: 200 })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.openAutoSyncScheduleModal;
});

describe('AutoSyncHost', () => {
  it('takes over the vanilla opener and opens the react manager', () => {
    const vanilla = vi.fn();
    window.openAutoSyncScheduleModal = vanilla;
    const { container } = render(<AutoSyncHost />);
    expect(window.openAutoSyncScheduleModal).not.toBe(vanilla);
    expect(container.querySelector('#auto-sync-schedule-modal')).toBeNull();

    act(() => {
      void window.openAutoSyncScheduleModal?.();
    });
    expect(vanilla).not.toHaveBeenCalled();
    // the react modal, the one with the drag glow, not auto-sync.js's
    expect(container.querySelector('#auto-sync-schedule-modal .auto-sync-modal')).not.toBeNull();
    expect(container.querySelector('.auto-sync-eyebrow')).toBeNull();

    fireEvent.click(container.querySelector('.auto-sync-close') as HTMLElement);
    // the dashboard's sync band watches for this id to vanish before reloading
    expect(container.querySelector('#auto-sync-schedule-modal')).toBeNull();
  });

  it('hands the global back when it unmounts', () => {
    const vanilla = vi.fn();
    window.openAutoSyncScheduleModal = vanilla;
    const { unmount } = render(<AutoSyncHost />);
    unmount();
    expect(window.openAutoSyncScheduleModal).toBe(vanilla);
  });
});
