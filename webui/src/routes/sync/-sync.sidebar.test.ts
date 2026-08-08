/**
 * Differential tests for the sidebar's pure core — core.js 1340-1369,
 * sync-spotify.js 1812-1830, api-monitor.js 1129-1148, downloads.js 4041-4057.
 */

import { describe, expect, it } from 'vitest';

import {
  SYNC_LOG_PLACEHOLDER,
  SYNC_PROGRESS_IDLE,
  type SyncActionsState,
  syncLogShouldScrollTop,
  syncLogText,
  syncProgressLabel,
  syncProgressPercent,
  syncSelectionLabel,
  syncSidebarVisible,
  syncStartDisabled,
  syncStartLabel,
} from './-sync.sidebar';

function state(over: Partial<SyncActionsState> = {}): SyncActionsState {
  return { running: false, selectedCount: 0, currentIndex: 0, queueLength: 0, ...over };
}

describe('the selection line (1350-1355, 1362-1367)', () => {
  it('reads the markup default when nothing is selected', () => {
    // Same literal as index.html 3304, so an idle page and a page reset to
    // idle say the same thing.
    expect(syncSelectionLabel(state())).toBe('Select playlists to sync');
  });

  it('is singular at one and plural above', () => {
    expect(syncSelectionLabel(state({ selectedCount: 1 }))).toBe('1 playlist selected');
    expect(syncSelectionLabel(state({ selectedCount: 2 }))).toBe('2 playlists selected');
    expect(syncSelectionLabel(state({ selectedCount: 17 }))).toBe('17 playlists selected');
  });

  it('shows 1-based position, name and total while running', () => {
    expect(
      syncSelectionLabel(
        state({ running: true, currentIndex: 0, queueLength: 3, currentName: 'Road Trip' }),
      ),
    ).toBe('Syncing 1/3: Road Trip');
    expect(
      syncSelectionLabel(
        state({ running: true, currentIndex: 2, queueLength: 3, currentName: 'Last One' }),
      ),
    ).toBe('Syncing 3/3: Last One');
  });

  it('falls back to Unknown when the name did not resolve', () => {
    // 1366's `currentPlaylist?.name || 'Unknown'` — the lookup misses for any
    // queued id that is not a Spotify playlist.
    for (const name of [undefined, null, '']) {
      expect(syncSelectionLabel(state({ running: true, queueLength: 2, currentName: name }))).toBe(
        'Syncing 1/2: Unknown',
      );
    }
  });

  it('ignores the selected count entirely while running', () => {
    // updateSyncActionsUI delegates to updateUI when running (1814-1817), so a
    // selection change mid-sync must not overwrite the progress line.
    expect(
      syncSelectionLabel(
        state({ running: true, selectedCount: 9, queueLength: 2, currentName: 'A' }),
      ),
    ).toBe('Syncing 1/2: A');
  });
});

describe('the Start Sync button (1346-1361)', () => {
  it('is a toggle label', () => {
    expect(syncStartLabel(false)).toBe('Start Sync');
    expect(syncStartLabel(true)).toBe('Cancel Sequential Sync');
  });

  it('is disabled only when idle with nothing selected', () => {
    expect(syncStartDisabled(false, 0)).toBe(true);
    expect(syncStartDisabled(false, 1)).toBe(false);
  });

  it('is ALWAYS live while running, whatever the selection', () => {
    // 1360 sets disabled=false unconditionally — the button is a cancel then,
    // and a cancel must stay reachable even with the selection cleared.
    expect(syncStartDisabled(true, 0)).toBe(false);
    expect(syncStartDisabled(true, 5)).toBe(false);
  });
});

describe('the progress bar and text (port-added; the vanilla never wrote them)', () => {
  it('is 0% and the markup default when idle', () => {
    expect(syncProgressPercent(state())).toBe(0);
    expect(syncProgressLabel(state())).toBe(SYNC_PROGRESS_IDLE);
    expect(SYNC_PROGRESS_IDLE).toBe('Ready to sync...');
  });

  it('counts COMPLETED playlists, so the first one starts at 0%', () => {
    expect(syncProgressPercent(state({ running: true, currentIndex: 0, queueLength: 4 }))).toBe(0);
    expect(syncProgressPercent(state({ running: true, currentIndex: 1, queueLength: 4 }))).toBe(25);
    expect(syncProgressPercent(state({ running: true, currentIndex: 3, queueLength: 4 }))).toBe(75);
  });

  it('reaches 100% only once the last has finished', () => {
    // complete() advances currentIndex past the end before clearing isRunning.
    expect(syncProgressPercent(state({ running: true, currentIndex: 4, queueLength: 4 }))).toBe(
      100,
    );
  });

  it('rounds rather than truncating', () => {
    expect(syncProgressPercent(state({ running: true, currentIndex: 1, queueLength: 3 }))).toBe(33);
    expect(syncProgressPercent(state({ running: true, currentIndex: 2, queueLength: 3 }))).toBe(67);
  });

  it('never divides by zero or escapes 0-100', () => {
    expect(syncProgressPercent(state({ running: true, queueLength: 0 }))).toBe(0);
    // currentIndex is clamped both ways, so a stale index cannot render a bar
    // wider than its container or a negative width.
    expect(syncProgressPercent(state({ running: true, currentIndex: 99, queueLength: 4 }))).toBe(
      100,
    );
    expect(syncProgressPercent(state({ running: true, currentIndex: -3, queueLength: 4 }))).toBe(0);
  });

  it('labels the position 1-based, clamped to the queue', () => {
    expect(syncProgressLabel(state({ running: true, currentIndex: 0, queueLength: 4 }))).toBe(
      '1 of 4 playlists',
    );
    expect(syncProgressLabel(state({ running: true, currentIndex: 4, queueLength: 4 }))).toBe(
      '4 of 4 playlists',
    );
  });

  it('returns to the idle text when the run ends', () => {
    expect(syncProgressLabel(state({ running: false, currentIndex: 4, queueLength: 4 }))).toBe(
      SYNC_PROGRESS_IDLE,
    );
    expect(syncProgressPercent(state({ running: false, currentIndex: 4, queueLength: 4 }))).toBe(0);
  });
});

describe('the log text (1129-1134)', () => {
  it('joins the lines with newlines', () => {
    expect(syncLogText({ logs: ['one', 'two', 'three'] })).toBe('one\ntwo\nthree');
  });

  it('renders a real empty feed as empty', () => {
    expect(syncLogText({ logs: [] })).toBe('');
  });

  it('DROPS a frame with no usable logs array, rather than blanking', () => {
    // 1130 returns before touching the textarea. null is the caller's signal
    // to keep the last good text — a malformed push must not wipe the feed.
    for (const frame of [null, undefined, {}, { logs: null }, { logs: 'nope' }, { logs: 42 }]) {
      expect(syncLogText(frame)).toBeNull();
    }
  });

  it('keeps the markup placeholder as the pre-data text', () => {
    expect(SYNC_LOG_PLACEHOLDER).toBe('Waiting for sync to start...');
  });
});

describe('the log scroll rule (1137-1147)', () => {
  // scrollHeight 1000, clientHeight 100 → bottom is scrollTop 900.
  it('jumps to the top when the reader was already at the top', () => {
    expect(syncLogShouldScrollTop(0, 1000, 100)).toBe(true);
    expect(syncLogShouldScrollTop(10, 1000, 100)).toBe(true);
  });

  it('ALSO jumps when the reader was pinned to the bottom', () => {
    // Transcribed, not corrected: `wasUserScrolled` is false at the bottom, so
    // `wasAtTop || !wasUserScrolled` fires there too. Newest entries are at
    // the top, so the bottom is not a position anyone holds deliberately.
    expect(syncLogShouldScrollTop(900, 1000, 100)).toBe(true);
    expect(syncLogShouldScrollTop(895, 1000, 100)).toBe(true);
  });

  it('leaves a reader parked in the middle alone', () => {
    expect(syncLogShouldScrollTop(400, 1000, 100)).toBe(false);
    expect(syncLogShouldScrollTop(11, 1000, 100)).toBe(false);
  });

  it('does nothing surprising when the content does not overflow', () => {
    // scrollHeight === clientHeight: there is nowhere to scroll, and the
    // vanilla still resets to 0, which is a no-op.
    expect(syncLogShouldScrollTop(0, 100, 100)).toBe(true);
  });
});

describe('sidebar visibility (downloads.js 4041-4057, sync-services.js 3747-3753)', () => {
  it('is hidden at rest', () => {
    expect(syncSidebarVisible(false, false)).toBe(false);
  });

  it('is shown while a sequential sync runs', () => {
    expect(syncSidebarVisible(true, false)).toBe(true);
  });

  it('is hidden again by a tab switch, even mid-sync', () => {
    // The vanilla tab handler hides it unconditionally (3751), without asking
    // whether a sync is running. Transcribed: it is visible behaviour.
    expect(syncSidebarVisible(true, true)).toBe(false);
  });

  it('stays hidden when a tab switch happens with no sync running', () => {
    expect(syncSidebarVisible(false, true)).toBe(false);
  });
});
