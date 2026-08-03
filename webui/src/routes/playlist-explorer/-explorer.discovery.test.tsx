import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  DISCOVERY_DONE_PHASES,
  DISCOVERY_POLL_MS,
  DISCOVERY_REFRESH_DELAY_MS,
  EXPLORER_DISCOVERY_EVENT,
  isDiscoveryComplete,
  isDiscoveryDonePhase,
  mirroredPlaylistIdFromEventId,
  useExplorerDiscovery,
  type ExplorerDiscovery,
} from './-explorer.discovery';

/**
 * Discovery wiring. The vanilla read `youtubePlaylistStates` for the finished
 * phase; both that and `socket` are module-scoped in core.js, so core.js
 * re-broadcasts the frame as `ss:discovery-progress` and this tracks the phase
 * from the frames themselves.
 */

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  delete window.discoverMirroredPlaylist;
  delete window.showToast;
  delete window.SoulSyncWebRouter;
});

describe('the frame helpers', () => {
  it('reads the playlist id out of a mirrored_ event id', () => {
    expect(mirroredPlaylistIdFromEventId('mirrored_42')).toBe(42);
    expect(mirroredPlaylistIdFromEventId('mirrored_')).toBeNull();
    // Another page's channel traffic must not touch an explorer card.
    expect(mirroredPlaylistIdFromEventId('spotify_42')).toBeNull();
    expect(mirroredPlaylistIdFromEventId(42)).toBeNull();
    expect(mirroredPlaylistIdFromEventId(undefined)).toBeNull();
  });

  it('treats all three completion signals as done', () => {
    expect(isDiscoveryComplete({ phase: 'discovered' })).toBe(true);
    expect(isDiscoveryComplete({ phase: 'sync_complete' })).toBe(true);
    expect(isDiscoveryComplete({ complete: true })).toBe(true);
    expect(isDiscoveryComplete({ phase: 'searching' })).toBe(false);
    expect(isDiscoveryComplete({})).toBe(false);
  });

  it('names the two phases that stop the poller', () => {
    expect(DISCOVERY_DONE_PHASES).toEqual(['discovered', 'sync_complete']);
    expect(isDiscoveryDonePhase('discovered')).toBe(true);
    expect(isDiscoveryDonePhase('searching')).toBe(false);
    expect(isDiscoveryDonePhase(undefined)).toBe(false);
  });

  it('uses the vanilla delays', () => {
    expect(DISCOVERY_REFRESH_DELAY_MS).toBe(1500);
    expect(DISCOVERY_POLL_MS).toBe(5000);
  });
});

let controls: ExplorerDiscovery;
let live: Record<number, number>;
let states: Record<number, string>;

function Harness({ onRefresh }: { onRefresh: () => void }) {
  controls = useExplorerDiscovery(onRefresh);
  live = controls.liveDiscovery;
  states = controls.discoverStates;
  return null;
}

function emit(detail: unknown) {
  act(() => {
    window.dispatchEvent(new CustomEvent(EXPLORER_DISCOVERY_EVENT, { detail }));
  });
}

describe('useExplorerDiscovery', () => {
  beforeEach(() => vi.useFakeTimers());

  it('paints a live percentage on the matching card only', () => {
    render(<Harness onRefresh={vi.fn()} />);
    emit({ id: 'mirrored_7', progress: 42 });
    expect(live).toEqual({ 7: 42 });

    emit({ id: 'spotify_7', progress: 99 });
    expect(live).toEqual({ 7: 42 });

    // 0 is a real reading; only null/undefined is "no progress in this frame".
    emit({ id: 'mirrored_7', progress: 0 });
    expect(live).toEqual({ 7: 0 });
    emit({ id: 'mirrored_7', phase: 'searching' });
    expect(live).toEqual({ 7: 0 });
  });

  it('re-reads the cards 1.5s after a completion frame', () => {
    const onRefresh = vi.fn();
    render(<Harness onRefresh={onRefresh} />);

    emit({ id: 'mirrored_7', phase: 'discovered' });
    act(() => {
      vi.advanceTimersByTime(DISCOVERY_REFRESH_DELAY_MS - 1);
    });
    expect(onRefresh).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('never fires a queued refresh after unmount', () => {
    const onRefresh = vi.fn();
    const view = render(<Harness onRefresh={onRefresh} />);
    emit({ id: 'mirrored_7', complete: true });
    view.unmount();
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it('stops listening once unmounted', () => {
    const onRefresh = vi.fn();
    const view = render(<Harness onRefresh={onRefresh} />);
    view.unmount();
    window.dispatchEvent(new CustomEvent(EXPLORER_DISCOVERY_EVENT, { detail: { complete: true } }));
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it('opens the discovery modal, then polls until the phase says done', async () => {
    const onRefresh = vi.fn();
    const discover = vi.fn(async () => {});
    window.discoverMirroredPlaylist = discover;
    render(<Harness onRefresh={onRefresh} />);

    await act(async () => {
      await controls.startDiscovery(7);
    });
    expect(discover).toHaveBeenCalledWith(7);
    expect(states[7]).toBe('open');

    act(() => {
      vi.advanceTimersByTime(DISCOVERY_POLL_MS);
    });
    expect(onRefresh).toHaveBeenCalledTimes(1);
    act(() => {
      vi.advanceTimersByTime(DISCOVERY_POLL_MS);
    });
    expect(onRefresh).toHaveBeenCalledTimes(2);

    // The frame that ends it.
    emit({ id: 'mirrored_7', phase: 'sync_complete' });
    act(() => {
      vi.advanceTimersByTime(DISCOVERY_POLL_MS * 4);
    });
    // One more tick fires (it is the tick that notices), then it stops. The
    // 1.5s completion refresh also lands, so this is 3 + 1.
    expect(onRefresh).toHaveBeenCalledTimes(4);
  });

  it('keeps only ONE poller, as the vanilla did', async () => {
    const onRefresh = vi.fn();
    window.discoverMirroredPlaylist = vi.fn(async () => {});
    render(<Harness onRefresh={onRefresh} />);

    await act(async () => {
      await controls.startDiscovery(1);
    });
    await act(async () => {
      await controls.startDiscovery(2);
    });
    act(() => {
      vi.advanceTimersByTime(DISCOVERY_POLL_MS);
    });
    // Two pollers would refresh twice per tick.
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('falls back to the Sync page when the discovery modal is absent', async () => {
    const navigateToPage = vi.fn(async () => true);
    window.SoulSyncWebRouter = { navigateToPage } as unknown as Window['SoulSyncWebRouter'];
    window.showToast = vi.fn();
    const tab = document.createElement('button');
    tab.className = 'sync-tab-button';
    tab.dataset.tab = 'mirrored';
    const click = vi.fn();
    tab.addEventListener('click', click);
    document.body.appendChild(tab);

    render(<Harness onRefresh={vi.fn()} />);
    await act(async () => {
      await controls.startDiscovery(7);
    });

    expect(window.showToast).toHaveBeenCalledWith(
      'This playlist needs more tracks discovered before exploring. Redirecting to Sync...',
      'info',
    );
    expect(navigateToPage).toHaveBeenCalledWith('sync');
    expect(states[7]).toBe('idle');

    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(click).toHaveBeenCalled();
    tab.remove();
  });

  it('puts the button back on a failure and says why', async () => {
    window.showToast = vi.fn();
    window.discoverMirroredPlaylist = vi.fn(async () => {
      throw new Error('slskd down');
    });
    render(<Harness onRefresh={vi.fn()} />);

    await act(async () => {
      await controls.startDiscovery(7);
    });
    expect(states[7]).toBe('idle');
    expect(window.showToast).toHaveBeenCalledWith('Discovery failed: slskd down', 'error');
  });
});
