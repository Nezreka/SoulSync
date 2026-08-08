/**
 * Behavioural tests for the sequential-sync runner — core.js 1254-1297 and
 * downloads.js 4059-4099, driven against a fake engine.
 */

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { SequentialSyncEngine } from './-sync.use-sequential';

import {
  resetSequentialSyncStore,
  toggleSequentialSync,
  useSequentialSync,
} from './-sync.use-sequential';

/**
 * A fake download engine. `startPlaylistSync` marks the playlist as syncing;
 * the test releases it with `settle(id)`, which is the moment the vanilla's
 * `activeSyncPollers[id]` disappears.
 */
function fakeEngine(over: Partial<SequentialSyncEngine> = {}) {
  const syncing = new Set<string>();
  const started: string[] = [];
  const engine: SequentialSyncEngine = {
    startPlaylistSync: vi.fn(async (id: string) => {
      started.push(id);
      syncing.add(id);
    }),
    isSyncing: (id: string) => syncing.has(id),
    setSelectionDisabled: vi.fn(),
    refreshButtons: vi.fn(),
    toast: vi.fn(),
    ...over,
  };
  return { engine, syncing, started, settle: (id: string) => syncing.delete(id) };
}

/**
 * FAKE TIMERS, and the hook's REAL wait. An injected wait that resolves
 * immediately turns the completion poll into an unbounded microtask loop
 * whenever a playlist never settles — it starves the event loop and hangs the
 * runner rather than failing it. Driving the real 1s cadence with fake timers
 * controls the loop precisely and exercises the actual poll interval.
 */
beforeEach(() => {
  // The store is module-scoped so a run survives navigation, which also means
  // it survives the previous test. Every case starts from a clean run.
  resetSequentialSyncStore();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

function mount(over: Parameters<typeof useSequentialSync>[0] | Record<string, unknown> = {}) {
  const fake = fakeEngine();
  const props = {
    engine: fake.engine,
    selectedCount: 2,
    nameFor: (id: string) => ({ a: 'Alpha', b: 'Beta' })[id],
    now: () => 1000,
    ...over,
  } as Parameters<typeof useSequentialSync>[0];
  return { fake, ...renderHook(() => useSequentialSync(props)) };
}

/** Advance the runner: runs due timers and flushes microtasks between them. */
async function tick(ms = 5000) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe('refusing to start', () => {
  it('toasts and does nothing when nothing is selected (4073-4076)', async () => {
    const { fake, result } = mount();
    await act(async () => {
      result.current.toggle(['a', 'b'], new Set());
    });
    expect(fake.engine.toast).toHaveBeenCalledWith('No playlists selected for sync', 'error');
    expect(fake.engine.startPlaylistSync).not.toHaveBeenCalled();
    expect(result.current.state.running).toBe(false);
    // It must not freeze the checkboxes for a run that never began.
    expect(fake.engine.setSelectionDisabled).not.toHaveBeenCalled();
  });

  it('also refuses when the selection names nothing the page lists', async () => {
    const { fake, result } = mount();
    await act(async () => {
      result.current.toggle(['a'], new Set(['ghost']));
    });
    expect(fake.engine.toast).toHaveBeenCalledWith('No playlists selected for sync', 'error');
    expect(result.current.state.running).toBe(false);
  });
});

describe('a full run', () => {
  it('syncs in PAGE order and reports progress as it goes', async () => {
    const { fake, result } = mount();
    act(() => {
      result.current.toggle(['a', 'b'], new Set(['b', 'a']));
    });

    expect(result.current.state.running).toBe(true);
    expect(result.current.state.queue).toEqual(['a', 'b']);
    expect(fake.engine.setSelectionDisabled).toHaveBeenCalledWith(true);
    expect(result.current.locked).toBe(true);
    expect(result.current.actions.currentName).toBe('Alpha');

    fake.settle('a');
    await tick();
    expect(fake.started).toEqual(['a', 'b']);
  });

  it('waits for each sync before starting the next', async () => {
    const { fake, result } = mount();
    act(() => {
      result.current.toggle(['a', 'b'], new Set(['a', 'b']));
    });
    await tick();
    // 'a' never settles, so 'b' must not have been kicked off.
    expect(fake.started).toEqual(['a']);
  });

  it('announces completion, unfreezes the selection and refreshes the buttons', async () => {
    const { fake, result } = mount({
      now: vi.fn().mockReturnValueOnce(1000).mockReturnValue(4500),
    });
    act(() => {
      result.current.toggle(['a'], new Set(['a']));
    });
    fake.settle('a');
    await tick();

    expect(fake.engine.toast).toHaveBeenCalledWith(
      'Sequential sync completed for 1 playlists in 3.5s',
      'success',
    );
    expect(fake.engine.setSelectionDisabled).toHaveBeenLastCalledWith(false);
    expect(fake.engine.refreshButtons).toHaveBeenCalled();
    expect(result.current.state).toEqual({
      running: false,
      queue: [],
      currentIndex: 0,
      startedAt: null,
    });
    expect(result.current.locked).toBe(false);
  });

  it('CONTINUES past a playlist that throws, announcing it (1273-1276)', async () => {
    const fake = fakeEngine();
    const failing = vi.fn(async (id: string) => {
      if (id === 'a') throw new Error('boom');
      fake.syncing.add(id);
      fake.started.push(id);
    });
    const { result } = mount({
      engine: { ...fake.engine, startPlaylistSync: failing },
    });
    act(() => {
      result.current.toggle(['a', 'b'], new Set(['a', 'b']));
    });
    await tick();

    expect(fake.engine.toast).toHaveBeenCalledWith('Failed to sync "Alpha": boom', 'error');
    // ...and 'b' still got its turn.
    expect(failing.mock.calls.map(([id]) => id)).toEqual(['a', 'b']);
  });
});

describe('progress while running', () => {
  it('moves the label on to the next playlist as the queue advances', () => {
    // The sidebar's whole "Syncing 2/3: Beta" line rides on currentIndex. A
    // runner that starts each sync but never advances the index looks
    // identical from the engine's side — nothing else here would notice.
    const { fake, result } = mount();
    act(() => {
      result.current.toggle(['a', 'b'], new Set(['a', 'b']));
    });
    expect(result.current.actions.currentIndex).toBe(0);
    expect(result.current.actions.currentName).toBe('Alpha');

    fake.settle('a');
    return tick().then(() => {
      expect(result.current.actions.currentIndex).toBe(1);
      expect(result.current.actions.currentName).toBe('Beta');
    });
  });
});

describe('cancelling', () => {
  it('is the SAME handler — a second call cancels (the vanilla toggle)', async () => {
    const { fake, result } = mount();
    act(() => {
      result.current.toggle(['a'], new Set(['a']));
    });
    expect(result.current.state.running).toBe(true);

    act(() => {
      result.current.toggle(['a'], new Set(['a']));
    });
    await tick();

    expect(fake.engine.toast).toHaveBeenCalledWith('Sequential sync cancelled', 'info');
    expect(result.current.state.running).toBe(false);
    expect(fake.engine.setSelectionDisabled).toHaveBeenLastCalledWith(false);
  });

  it('does NOT announce a completion, and never the vanilla epoch toast', async () => {
    // The vanilla's cancel leaves a pending syncNext that fires complete(),
    // toasting "completed for 0 playlists in <epoch>s". The runner stops at
    // the cancel flag, so no success toast can be emitted at all.
    const { fake, result } = mount();
    act(() => {
      result.current.toggle(['a', 'b'], new Set(['a', 'b']));
    });
    act(() => {
      result.current.toggle(['a', 'b'], new Set(['a', 'b']));
    });
    await tick();

    const kinds = (fake.engine.toast as ReturnType<typeof vi.fn>).mock.calls.map(
      ([, kind]) => kind,
    );
    expect(kinds).not.toContain('success');
    const messages = (fake.engine.toast as ReturnType<typeof vi.fn>).mock.calls.map(([m]) => m);
    expect(messages.some((m: string) => m.includes('completed for'))).toBe(false);
  });

  it('stops the queue where it stands', async () => {
    const { fake, result } = mount();
    act(() => {
      result.current.toggle(['a', 'b'], new Set(['a', 'b']));
    });
    act(() => {
      result.current.toggle(['a', 'b'], new Set(['a', 'b']));
    });
    fake.settle('a');
    await tick();
    expect(fake.started).toEqual(['a']);
  });

  it('announces exactly ONCE', async () => {
    const { fake, result } = mount();
    act(() => {
      result.current.toggle(['a'], new Set(['a']));
    });
    act(() => {
      result.current.toggle(['a'], new Set(['a']));
      result.current.toggle(['a'], new Set(['a']));
    });
    await tick();
    const cancels = (fake.engine.toast as ReturnType<typeof vi.fn>).mock.calls.filter(
      ([m]) => m === 'Sequential sync cancelled',
    );
    expect(cancels).toHaveLength(1);
  });

  it('is seen during the GAP between syncs, before the next one starts', async () => {
    // The 1s pause at 1280 is a real window: cancel landing inside it must not
    // let the loop roll on to the next playlist. Only the check at the TOP of
    // the loop covers this one.
    const { fake, result } = mount();
    act(() => {
      result.current.toggle(['a', 'b'], new Set(['a', 'b']));
    });
    // Settle before the poll is even reached, so the runner goes straight to
    // the gap. Advancing by 0 flushes the microtasks without firing the 1s
    // gap timer — advancing a full 1000 would fire it and start 'b' before
    // the cancel could land.
    fake.settle('a');
    await tick(0);
    act(() => {
      result.current.toggle(['a', 'b'], new Set(['a', 'b']));
    });
    await tick();

    expect(fake.started).toEqual(['a']);
    expect(fake.engine.toast).toHaveBeenCalledWith('Sequential sync cancelled', 'info');
  });

  it('is seen as soon as the poll notices, not a gap later', async () => {
    // Cancel during a sync. The check after the poll is what makes the
    // announcement land on this tick; without it the runner would sit through
    // another 1s gap first. Distinguished by advancing exactly the poll.
    const { fake, result } = mount();
    act(() => {
      result.current.toggle(['a'], new Set(['a']));
    });
    act(() => {
      result.current.toggle(['a'], new Set(['a']));
    });
    // Advance by 0: flush the microtasks, fire NO timers. With the check the
    // announcement has already landed; without it the runner is parked on the
    // 1s gap and nothing has been said yet. Advancing a full 1000 would let
    // both reach the toast and the assertion would prove nothing.
    await tick(0);

    expect(fake.engine.toast).toHaveBeenCalledWith('Sequential sync cancelled', 'info');
  });

  it('lets a NEW run start afterwards', async () => {
    const { result } = mount();
    act(() => {
      result.current.toggle(['a'], new Set(['a']));
    });
    act(() => {
      result.current.toggle(['a'], new Set(['a']));
    });
    await tick();

    act(() => {
      result.current.toggle(['b'], new Set(['b']));
    });
    expect(result.current.state.running).toBe(true);
    expect(result.current.state.queue).toEqual(['b']);
  });
});

describe('surviving navigation', () => {
  /**
   * `sequentialSyncManager` is a module singleton (core.js 409), so a run
   * outlives the page — leave /sync mid-sync and come back and the vanilla is
   * still going. Component state would come back reading idle while the engine
   * was still working, which is the whole reason the store is module-scoped.
   */
  it('keeps running when the page unmounts', async () => {
    const { fake, result, unmount } = mount();
    act(() => {
      result.current.toggle(['a', 'b'], new Set(['a', 'b']));
    });
    unmount();

    fake.settle('a');
    await tick();
    // The engine kept being driven with nothing mounted.
    expect(fake.started).toEqual(['a', 'b']);
  });

  it('a remount picks the run back up mid-flight', async () => {
    const fake = fakeEngine();
    const props = {
      engine: fake.engine,
      selectedCount: 2,
      nameFor: (id: string) => ({ a: 'Alpha', b: 'Beta' })[id],
      now: () => 1000,
    } as Parameters<typeof useSequentialSync>[0];

    const first = renderHook(() => useSequentialSync(props));
    act(() => {
      first.result.current.toggle(['a', 'b'], new Set(['a', 'b']));
    });
    first.unmount();

    const second = renderHook(() => useSequentialSync(props));
    expect(second.result.current.state.running).toBe(true);
    expect(second.result.current.state.queue).toEqual(['a', 'b']);
    expect(second.result.current.locked).toBe(true);
    expect(second.result.current.actions.currentName).toBe('Alpha');
  });

  it('the new mount can cancel the run the old one started', async () => {
    const fake = fakeEngine();
    const props = {
      engine: fake.engine,
      selectedCount: 2,
      nameFor: () => 'Alpha',
      now: () => 1000,
    } as Parameters<typeof useSequentialSync>[0];

    const first = renderHook(() => useSequentialSync(props));
    act(() => {
      first.result.current.toggle(['a', 'b'], new Set(['a', 'b']));
    });
    first.unmount();

    const second = renderHook(() => useSequentialSync(props));
    act(() => {
      second.result.current.toggle(['a', 'b'], new Set(['a', 'b']));
    });
    await tick();

    expect(fake.engine.toast).toHaveBeenCalledWith('Sequential sync cancelled', 'info');
    expect(second.result.current.state.running).toBe(false);
    expect(fake.started).toEqual(['a']);
  });

  it('the remount SEES later progress, not a frozen snapshot', async () => {
    const fake = fakeEngine();
    const props = {
      engine: fake.engine,
      selectedCount: 2,
      nameFor: (id: string) => ({ a: 'Alpha', b: 'Beta' })[id],
      now: () => 1000,
    } as Parameters<typeof useSequentialSync>[0];

    const first = renderHook(() => useSequentialSync(props));
    act(() => {
      first.result.current.toggle(['a', 'b'], new Set(['a', 'b']));
    });
    first.unmount();

    const second = renderHook(() => useSequentialSync(props));
    expect(second.result.current.actions.currentName).toBe('Alpha');

    fake.settle('a');
    await tick();
    // A snapshot read once at mount would still say Alpha.
    expect(second.result.current.actions.currentName).toBe('Beta');
  });
});

describe('driving it with no component at all', () => {
  /**
   * `toggleSequentialSync` is the module-level entry the hook wraps. It works
   * with nothing mounted, which is the whole point of the store: the vanilla's
   * manager is a singleton, and a run is not owned by any page.
   */
  function deps(fake: ReturnType<typeof fakeEngine>) {
    return {
      engine: fake.engine,
      nameFor: (id: string) => ({ a: 'Alpha', b: 'Beta' })[id],
      now: () => 1000,
      wait: (ms: number) =>
        new Promise<void>((resolve) => {
          setTimeout(resolve, ms);
        }),
    };
  }

  it('runs a queue without React involved', async () => {
    const fake = fakeEngine();
    toggleSequentialSync(deps(fake), ['a', 'b'], new Set(['a', 'b']));
    expect(fake.started).toEqual(['a']);
    expect(fake.engine.setSelectionDisabled).toHaveBeenCalledWith(true);

    fake.settle('a');
    await tick();
    expect(fake.started).toEqual(['a', 'b']);
  });

  it('refuses an empty selection the same way', () => {
    const fake = fakeEngine();
    toggleSequentialSync(deps(fake), ['a'], new Set());
    expect(fake.engine.toast).toHaveBeenCalledWith('No playlists selected for sync', 'error');
    expect(fake.started).toEqual([]);
  });

  it('cancels on a second call, like the button does', async () => {
    const fake = fakeEngine();
    toggleSequentialSync(deps(fake), ['a', 'b'], new Set(['a', 'b']));
    toggleSequentialSync(deps(fake), ['a', 'b'], new Set(['a', 'b']));
    await tick();
    expect(fake.engine.toast).toHaveBeenCalledWith('Sequential sync cancelled', 'info');
    expect(fake.started).toEqual(['a']);
  });

  it('and a mounted hook SEES that run', () => {
    const fake = fakeEngine();
    toggleSequentialSync(deps(fake), ['a', 'b'], new Set(['a', 'b']));

    const { result } = renderHook(() =>
      useSequentialSync({
        engine: fake.engine,
        selectedCount: 2,
        nameFor: (id: string) => ({ a: 'Alpha', b: 'Beta' })[id],
        now: () => 1000,
      }),
    );
    expect(result.current.state.running).toBe(true);
    expect(result.current.actions.currentName).toBe('Alpha');
  });
});

describe('what the sidebar sees', () => {
  it('carries the selection count through while idle', () => {
    const { result } = mount({ selectedCount: 5 });
    expect(result.current.actions).toEqual({
      running: false,
      selectedCount: 5,
      currentIndex: 0,
      queueLength: 0,
      currentName: null,
    });
  });
});
