import { describe, expect, it } from 'vitest';

import type { DownloadGlobals, DownloadState } from './-discover.download-bar';

import {
  AUTO_REMOVE_MS,
  BUBBLE_FALLBACK_GRADIENT,
  BUBBLE_ICON_ACTIVE,
  BUBBLE_ICON_COMPLETED,
  HYDRATE_ENDPOINT,
  MAX_NOT_FOUND_ATTEMPTS,
  MONITOR_INTERVAL_MS,
  REQUIRED_DOWNLOAD_GLOBALS,
  SNAPSHOT_DEBOUNCE_MS,
  SNAPSHOT_ENDPOINT,
  addDownload,
  bubbleBackground,
  downloadBarView,
  hydrateState,
  markCompleted,
  nextNotFoundCount,
  publishDownloadGlobals,
  removeDownload,
  restPollEnabled,
  restStatusIsTerminal,
  shouldAutoRemove,
  shouldGiveUp,
  snapshotPayload,
  socketStatusIsTerminal,
  syncDownloadState,
} from './-discover.download-bar';

const NOW = new Date('2026-07-31T12:00:00.000Z');

const seeded = (): DownloadState =>
  addDownload(
    {},
    {
      playlistId: 'p1',
      playlistName: 'Release Radar',
      playlistType: 'release_radar',
      imageUrl: '/art.jpg',
      now: NOW,
    },
  );

describe('adding a download', () => {
  it('starts in progress', () => {
    expect(seeded().p1).toEqual({
      name: 'Release Radar',
      type: 'release_radar',
      status: 'in_progress',
      virtualPlaylistId: 'p1',
      imageUrl: '/art.jpg',
      startTime: NOW,
    });
  });

  it('sets virtualPlaylistId to the key, which the snapshot round-trips', () => {
    expect(seeded().p1.virtualPlaylistId).toBe('p1');
  });

  it('normalises a missing image to null, not undefined', () => {
    // undefined disappears through JSON and comes back as a missing key.
    const s = addDownload({}, { playlistId: 'p', playlistName: 'n', playlistType: 't' });
    expect(s.p.imageUrl).toBeNull();
  });

  it('replaces an existing entry under the same id', () => {
    const s = addDownload(seeded(), {
      playlistId: 'p1',
      playlistName: 'Renamed',
      playlistType: 'x',
      now: NOW,
    });
    expect(Object.keys(s)).toHaveLength(1);
    expect(s.p1.name).toBe('Renamed');
  });
});

describe('removing and completing', () => {
  it('removes by id', () => {
    expect(removeDownload(seeded(), 'p1')).toEqual({});
  });

  it('is a no-op for an unknown id, returning the SAME object', () => {
    const before = seeded();
    expect(removeDownload(before, 'nope')).toBe(before);
  });

  it('marks completed without touching anything else', () => {
    const after = markCompleted(seeded(), 'p1');
    expect(after.p1.status).toBe('completed');
    expect(after.p1.name).toBe('Release Radar');
  });

  it('ignores completion of an entry the user already dismissed', () => {
    const before = removeDownload(seeded(), 'p1');
    expect(markCompleted(before, 'p1')).toBe(before);
  });

  it('re-checks before the 30s auto-remove fires', () => {
    // Thirty seconds is long enough for the entry to have been dismissed and a
    // NEW download re-added under the same id; without the check the timer
    // would delete the new one.
    expect(AUTO_REMOVE_MS).toBe(30000);
    expect(shouldAutoRemove(markCompleted(seeded(), 'p1'), 'p1')).toBe(true);
    expect(shouldAutoRemove(seeded(), 'p1')).toBe(false); //   still in progress
    expect(shouldAutoRemove({}, 'p1')).toBe(false); //          already gone
  });
});

describe('monitoring', () => {
  it('polls every two seconds and waits ten for a sync to appear', () => {
    expect(MONITOR_INTERVAL_MS).toBe(2000);
    expect(MAX_NOT_FOUND_ATTEMPTS).toBe(5);
    expect(MONITOR_INTERVAL_MS * MAX_NOT_FOUND_ATTEMPTS).toBe(10000);
  });

  it('accepts "finished" over the SOCKET but not over REST', () => {
    // The socket relays raw sync events, which use 'finished'; /api/sync/status
    // normalises to 'complete'. Rejecting 'finished' on the socket path would
    // strand every socket-delivered completion.
    expect(socketStatusIsTerminal('complete')).toBe(true);
    expect(socketStatusIsTerminal('finished')).toBe(true);
    expect(restStatusIsTerminal('complete')).toBe(true);
    expect(restStatusIsTerminal('finished')).toBe(false);
  });

  it('treats anything else as still running', () => {
    for (const s of ['downloading', 'analyzing', '', undefined]) {
      expect(socketStatusIsTerminal(s)).toBe(false);
      expect(restStatusIsTerminal(s)).toBe(false);
    }
  });

  it('gives up only at the fifth consecutive 404', () => {
    expect(shouldGiveUp(4)).toBe(false);
    expect(shouldGiveUp(5)).toBe(true);
  });

  it('RESETS the counter on any successful response', () => {
    // A sync that flickers 404 then answers must not accumulate toward removal.
    expect(nextNotFoundCount(4, true, 200)).toBe(0);
    expect(nextNotFoundCount(0, false, 404)).toBe(1);
    expect(nextNotFoundCount(3, false, 404)).toBe(4);
  });

  it('leaves the counter alone for a non-404 failure', () => {
    // A 500 is the server being unwell, not the sync being absent.
    expect(nextNotFoundCount(2, false, 500)).toBe(2);
  });

  it('skips the REST poll entirely while the socket is live', () => {
    expect(restPollEnabled(true)).toBe(false);
    expect(restPollEnabled(false)).toBe(true);
  });
});

describe('the bar', () => {
  it('hides at zero downloads', () => {
    const view = downloadBarView({});
    expect(view.hidden).toBe(true);
    expect(view.count).toBe(0);
    expect(view.bubbles).toEqual([]);
  });

  it('shows a bubble per download with the count', () => {
    const view = downloadBarView(seeded());
    expect([view.hidden, view.count]).toEqual([false, 1]);
    expect(view.bubbles[0]).toMatchObject({
      playlistId: 'p1',
      name: 'Release Radar',
      completed: false,
      icon: BUBBLE_ICON_ACTIVE,
      title: 'Release Radar - Click to view',
    });
  });

  it('swaps the icon on completion', () => {
    const view = downloadBarView(markCompleted(seeded(), 'p1'));
    expect(view.bubbles[0].icon).toBe(BUBBLE_ICON_COMPLETED);
    expect(view.bubbles[0].completed).toBe(true);
    expect(BUBBLE_ICON_COMPLETED).not.toBe(BUBBLE_ICON_ACTIVE);
  });

  it('uses the cover art when there is one', () => {
    expect(bubbleBackground('/art.jpg')).toBe("background-image: url('/art.jpg');");
  });

  it('falls back to the green gradient, not to nothing', () => {
    expect(bubbleBackground(null)).toBe(BUBBLE_FALLBACK_GRADIENT);
    expect(bubbleBackground('')).toBe(BUBBLE_FALLBACK_GRADIENT);
    expect(BUBBLE_FALLBACK_GRADIENT).toContain('linear-gradient');
  });
});

describe('the snapshot', () => {
  it('debounces for a second', () => {
    expect(SNAPSHOT_DEBOUNCE_MS).toBe(1000);
    expect(SNAPSHOT_ENDPOINT).toBe('/api/discover_downloads/snapshot');
    expect(HYDRATE_ENDPOINT).toBe('/api/discover_downloads/hydrate');
  });

  it('serialises the start time to ISO, since Dates do not survive JSON', () => {
    const payload = snapshotPayload(seeded());
    expect(payload?.p1.startTime).toBe('2026-07-31T12:00:00.000Z');
    expect(typeof payload?.p1.startTime).toBe('string');
  });

  it('writes exactly the six persisted fields', () => {
    expect(Object.keys(snapshotPayload(seeded())!.p1).sort()).toEqual([
      'imageUrl',
      'name',
      'startTime',
      'status',
      'type',
      'virtualPlaylistId',
    ]);
  });

  it('does NOT write an empty state', () => {
    // The stale snapshot this leaves is absorbed server-side: hydrate deletes
    // the whole snapshot when no process is active (web_server.py:28044).
    expect(snapshotPayload({})).toBeNull();
  });
});

describe('hydration', () => {
  const raw = {
    p1: {
      name: 'Release Radar',
      type: 'release_radar',
      status: 'in_progress' as const,
      virtualPlaylistId: 'p1',
      imageUrl: '/art.jpg',
      startTime: '2026-07-31T12:00:00.000Z',
    },
    p2: {
      name: 'Done One',
      type: 'seasonal',
      status: 'completed' as const,
      virtualPlaylistId: 'p2',
      imageUrl: null,
      startTime: '2026-07-31T11:00:00.000Z',
    },
  };

  it('revives the start time as a Date', () => {
    const { state } = hydrateState(raw);
    expect(state.p1.startTime).toBeInstanceOf(Date);
    expect(state.p1.startTime.toISOString()).toBe('2026-07-31T12:00:00.000Z');
  });

  it('keeps the SERVER’s live status, not the saved one', () => {
    // The handler cross-references running processes, so a download that
    // finished while the page was closed hydrates completed rather than
    // spinning forever.
    expect(hydrateState(raw).state.p2.status).toBe('completed');
  });

  it('restarts monitoring ONLY for in-progress entries', () => {
    expect(hydrateState(raw).toMonitor).toEqual(['p1']);
  });

  it('copes with an empty or absent payload', () => {
    expect(hydrateState({})).toEqual({ state: {}, toMonitor: [] });
    expect(hydrateState(null)).toEqual({ state: {}, toMonitor: [] });
    expect(hydrateState(undefined)).toEqual({ state: {}, toMonitor: [] });
  });

  it('falls back to the key when a row has no virtualPlaylistId', () => {
    const { state } = hydrateState({ px: { name: 'n', startTime: NOW.toISOString() } });
    expect(state.px.virtualPlaylistId).toBe('px');
  });
});

describe('the window contract', () => {
  const api: DownloadGlobals = {
    discoverDownloads: {},
    addDiscoverDownload: () => {},
    removeDiscoverDownload: () => {},
    updateDiscoverDownloadBar: () => {},
    hydrateDiscoverDownloadsFromSnapshot: async () => {},
  };

  it('publishes every name the other files reference', () => {
    // downloads.js, shell-bridge.js, wishlist-tools.js and init.js each read
    // one or more of these by bare name.
    expect([...REQUIRED_DOWNLOAD_GLOBALS]).toEqual([
      'discoverDownloads',
      'addDiscoverDownload',
      'removeDiscoverDownload',
      'updateDiscoverDownloadBar',
      'hydrateDiscoverDownloadsFromSnapshot',
    ]);
  });

  it('puts all of them on the target', () => {
    const target: Record<string, unknown> = {};
    publishDownloadGlobals(target, api);
    for (const key of REQUIRED_DOWNLOAD_GLOBALS) {
      expect(target[key]).toBeDefined();
    }
  });

  it('exposes discoverDownloads as an OBJECT, because two readers are unguarded', () => {
    // wishlist-tools.js:7443 calls Object.keys(discoverDownloads) with no
    // typeof guard — undefined there throws and takes the dashboard's whole
    // download section with it.
    const target: Record<string, unknown> = {};
    publishDownloadGlobals(target, api);
    expect(() => Object.keys(target.discoverDownloads as object)).not.toThrow();
  });

  it('keeps the published state in step with the store', () => {
    const target: Record<string, unknown> = {};
    publishDownloadGlobals(target, api);
    const next = seeded();
    syncDownloadState(target, next);
    expect(target.discoverDownloads).toBe(next);
    expect(Object.keys(target.discoverDownloads as object)).toEqual(['p1']);
  });
});
