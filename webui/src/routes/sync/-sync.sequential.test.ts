/**
 * Differential tests for the sequential-sync machine — core.js 1268-1418 and
 * downloads.js 4059-4106.
 */

import { describe, expect, it, vi } from 'vitest';

import {
  SEQUENTIAL_CANCELLED,
  SEQUENTIAL_IDLE,
  SEQUENTIAL_NONE_SELECTED,
  type SequentialSyncState,
  sequentialActionsState,
  sequentialAdvance,
  sequentialCompleteToast,
  sequentialDurationSeconds,
  sequentialFailureToast,
  sequentialFinish,
  sequentialIsDone,
  sequentialStart,
  syncOrderedSelection,
  syncSelectionLocked,
  syncToggleSelection,
} from './-sync.sequential';

function running(over: Partial<SequentialSyncState> = {}): SequentialSyncState {
  return { running: true, queue: ['a', 'b', 'c'], currentIndex: 0, startedAt: 1000, ...over };
}

describe('the selection store', () => {
  it('toggles an id in and back out', () => {
    const once = syncToggleSelection(new Set(), 'p1');
    expect([...once]).toEqual(['p1']);
    expect([...syncToggleSelection(once, 'p1')]).toEqual([]);
  });

  it('does not mutate the set it was given', () => {
    const before = new Set(['p1']);
    syncToggleSelection(before, 'p2');
    expect([...before]).toEqual(['p1']);
  });

  it('is locked for the whole run, and only the run', () => {
    // disablePlaylistSelection(true) at start (4098), false in BOTH complete
    // and cancel — so it tracks `running` exactly.
    expect(syncSelectionLocked(SEQUENTIAL_IDLE)).toBe(false);
    expect(syncSelectionLocked(running())).toBe(true);
  });
});

describe('the queue order (4078-4087)', () => {
  const order = ['a', 'b', 'c', 'd'];

  it('keeps the PAGE order, not the click order', () => {
    // The vanilla walks the cards in document order and keeps the selected
    // ones, so selecting c then a still syncs a first.
    expect(syncOrderedSelection(order, new Set(['c', 'a']))).toEqual(['a', 'c']);
  });

  it('drops everything unselected', () => {
    expect(syncOrderedSelection(order, new Set(['b']))).toEqual(['b']);
    expect(syncOrderedSelection(order, new Set())).toEqual([]);
  });

  it('DROPS a selected id with no card, as the vanilla does', () => {
    // It can only queue what it can see a card for — a stale selection from a
    // filtered-away playlist silently does not sync.
    expect(syncOrderedSelection(order, new Set(['b', 'ghost']))).toEqual(['b']);
  });

  it('does not duplicate when the page lists an id twice', () => {
    // Defensive: a duplicated card would queue the same playlist twice and
    // sync it twice. The vanilla would; this is a declared improvement.
    expect(syncOrderedSelection(['a', 'a', 'b'], new Set(['a', 'b']))).toEqual(['a', 'a', 'b']);
  });
});

describe('starting (1237-1252)', () => {
  it('snapshots the queue and stamps the clock', () => {
    const next = sequentialStart(SEQUENTIAL_IDLE, ['a', 'b'], 5000);
    expect(next).toEqual({ running: true, queue: ['a', 'b'], currentIndex: 0, startedAt: 5000 });
  });

  it('COPIES the ids, so the caller cannot mutate the queue afterwards', () => {
    const ids = ['a', 'b'];
    const next = sequentialStart(SEQUENTIAL_IDLE, ids, 0);
    ids.push('c');
    expect(next.queue).toEqual(['a', 'b']);
  });

  it('refuses while one is already running, leaving it untouched', () => {
    // 1238-1241 warns and returns. Returning the SAME object matters: a new
    // one would re-render the sidebar and could restart the effect chain.
    const state = running({ currentIndex: 2 });
    expect(sequentialStart(state, ['x'], 9)).toBe(state);
  });

  it('refuses an EMPTY queue', () => {
    // The vanilla bails earlier (4073), so start() never sees this. Letting it
    // through would set running with nothing to run — "Syncing 1/0", and the
    // only way out is Cancel.
    expect(sequentialStart(SEQUENTIAL_IDLE, [], 5000)).toBe(SEQUENTIAL_IDLE);
  });
});

describe('advancing and finishing', () => {
  it('moves to the next playlist', () => {
    expect(sequentialAdvance(running()).currentIndex).toBe(1);
  });

  it('ignores an advance when nothing is running', () => {
    expect(sequentialAdvance(SEQUENTIAL_IDLE)).toBe(SEQUENTIAL_IDLE);
  });

  it('is done once the index runs off the end (1255)', () => {
    expect(sequentialIsDone(running({ currentIndex: 2 }))).toBe(false);
    expect(sequentialIsDone(running({ currentIndex: 3 }))).toBe(true);
  });

  it('is never done while idle', () => {
    expect(sequentialIsDone({ ...SEQUENTIAL_IDLE, currentIndex: 9 })).toBe(false);
  });

  it('resets everything, the same way for complete and cancel', () => {
    // 1304-1307 and 1324-1327 are identical resets; only the toast differs.
    expect(sequentialFinish(running({ currentIndex: 2 }))).toEqual(SEQUENTIAL_IDLE);
  });

  it('ignores a cancel when nothing is running (1321)', () => {
    expect(sequentialFinish(SEQUENTIAL_IDLE)).toBe(SEQUENTIAL_IDLE);
  });

  it('leaves a non-running state ALONE rather than blanking it', () => {
    // Passing SEQUENTIAL_IDLE cannot prove the guard works: with or without
    // it the answer is that same constant, so the assertion above holds
    // either way. This input is the one where the two disagree — a stopped
    // state that still carries residue. Caught by mutation.
    const stopped: SequentialSyncState = {
      running: false,
      queue: ['a', 'b'],
      currentIndex: 1,
      startedAt: 42,
    };
    expect(sequentialFinish(stopped)).toBe(stopped);
  });
});

describe('the duration (1300)', () => {
  it('is seconds to one decimal place', () => {
    expect(sequentialDurationSeconds(running({ startedAt: 1000 }), 8500)).toBe('7.5');
    expect(sequentialDurationSeconds(running({ startedAt: 0 }), 1000)).toBe('1.0');
  });

  it('rounds rather than truncating, as toFixed does', () => {
    expect(sequentialDurationSeconds(running({ startedAt: 0 }), 1250)).toBe('1.3');
  });

  it('is 0.0 when the clock was never stamped', () => {
    expect(sequentialDurationSeconds(SEQUENTIAL_IDLE, 9999)).toBe('0.0');
  });
});

describe('the announcements', () => {
  it('are the vanilla strings', () => {
    expect(SEQUENTIAL_NONE_SELECTED).toBe('No playlists selected for sync');
    expect(SEQUENTIAL_CANCELLED).toBe('Sequential sync cancelled');
    expect(sequentialCompleteToast(3, '12.4')).toBe(
      'Sequential sync completed for 3 playlists in 12.4s',
    );
  });

  it('names the playlist that failed, falling back to its id', () => {
    expect(sequentialFailureToast('Road Trip', 'p1', 'boom')).toBe(
      'Failed to sync "Road Trip": boom',
    );
    for (const name of [null, undefined, '']) {
      expect(sequentialFailureToast(name, 'p1', 'boom')).toBe('Failed to sync "p1": boom');
    }
  });
});

describe('the bridge to the sidebar', () => {
  const nameFor = (id: string) => ({ a: 'Alpha', b: 'Beta', c: 'Gamma' })[id];

  it('reports the run so the sidebar can label it', () => {
    expect(sequentialActionsState(running({ currentIndex: 1 }), 0, nameFor)).toEqual({
      running: true,
      selectedCount: 0,
      currentIndex: 1,
      queueLength: 3,
      currentName: 'Beta',
    });
  });

  it('carries the selection through while idle', () => {
    expect(sequentialActionsState(SEQUENTIAL_IDLE, 4, nameFor)).toEqual({
      running: false,
      selectedCount: 4,
      currentIndex: 0,
      queueLength: 0,
      currentName: null,
    });
  });

  it('does not call the lookup at all while idle', () => {
    // It resolves against the SOURCE's playlists, which need not be loaded
    // when nothing is running.
    const spy = vi.fn(nameFor);
    sequentialActionsState(SEQUENTIAL_IDLE, 0, spy);
    expect(spy).not.toHaveBeenCalled();
  });

  it('survives an index past the end of the queue', () => {
    // complete() advances past the end before the reset lands, so this state
    // is reachable for one tick. `queue[3]` is undefined, and indexing it
    // must not throw before the label falls back.
    const state = running({ currentIndex: 3 });
    expect(() => sequentialActionsState(state, 0, nameFor)).not.toThrow();
    expect(sequentialActionsState(state, 0, nameFor).currentName).toBeUndefined();
  });

  it('passes an unresolved name straight through for the label to handle', () => {
    // syncSelectionLabel owns the 'Unknown' fallback (1366); duplicating it
    // here would put the same decision in two places.
    expect(sequentialActionsState(running(), 0, () => null).currentName).toBeNull();
  });
});
