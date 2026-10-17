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
  delete window.runMirroredPlaylistPipeline;
  delete window.pollMirroredPipelineStatus;
  delete window.applyMirroredPipelineState;
  delete window.loadMirroredPlaylists;
  delete window.reloadMirroredTab;
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

  it.each([false, true])(
    'delegates legacy runs and preserves skipWishlist: %s',
    async (skipWishlist) => {
      const apply = vi.fn();
      const reload = vi.fn();
      window.applyMirroredPipelineState = apply;
      window.reloadMirroredTab = reload;
      window.loadMirroredPlaylists = vi.fn();
      const fetch = vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith('/pipeline/run')) {
          expect(JSON.parse(init?.body as string)).toEqual({ skip_wishlist: skipWishlist });
          return new Response(JSON.stringify({ state: { status: 'running', progress: 0 } }));
        }
        return new Response(JSON.stringify({ status: 'finished', progress: 100 }));
      });
      vi.stubGlobal('fetch', fetch);
      render(<AutoSyncHost />);
      await act(async () => {
        await window.runMirroredPlaylistPipeline?.(42, 'Evening mix', skipWishlist);
      });
      expect(apply).toHaveBeenCalledWith(42, { status: 'finished', progress: 100 });
      expect(reload).toHaveBeenCalledOnce();
      expect(window.loadMirroredPlaylists).not.toHaveBeenCalled();
      // A closed manager does not refetch its five endpoints after a legacy job.
      expect(fetch).toHaveBeenCalledTimes(2);
    },
  );

  it('hands the globals back when it unmounts', () => {
    const vanilla = vi.fn();
    const run = vi.fn();
    const poll = vi.fn();
    window.openAutoSyncScheduleModal = vanilla;
    window.runMirroredPlaylistPipeline = run;
    window.pollMirroredPipelineStatus = poll;
    const { unmount } = render(<AutoSyncHost />);
    expect(window.runMirroredPlaylistPipeline).not.toBe(run);
    expect(window.pollMirroredPipelineStatus).not.toBe(poll);
    unmount();
    expect(window.openAutoSyncScheduleModal).toBe(vanilla);
    expect(window.runMirroredPlaylistPipeline).toBe(run);
    expect(window.pollMirroredPipelineStatus).toBe(poll);
  });
});
