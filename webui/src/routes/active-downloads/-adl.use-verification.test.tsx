import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { AdlQuarantineEntry } from './-adl.types';

import { groupQuarantine, useAdlVerification } from './-adl.use-verification';

const entry = (over: Partial<AdlQuarantineEntry> = {}): AdlQuarantineEntry =>
  ({ id: 'q1', group_key: 'g1', original_filename: 'a.flac', ...over }) as AdlQuarantineEntry;

/** Stub the config endpoint; returns nothing (the hook reads it once on mount). */
function stubConfig(config: Record<string, unknown>) {
  server.use(http.get('/api/verification/config', () => HttpResponse.json(config)));
}

/** Stub the quarantine list and count how many times it is actually hit. */
function stubQuarantine(entries: AdlQuarantineEntry[] = [], delayMs = 0) {
  const state = { calls: 0 };
  server.use(
    http.get('/api/quarantine/list', async () => {
      state.calls += 1;
      if (delayMs) await new Promise((r) => setTimeout(r, delayMs));
      return HttpResponse.json({ success: true, entries });
    }),
  );
  return state;
}

beforeEach(() => {
  stubConfig({ success: true, acoustid_enabled: true, require_verified: false });
  stubQuarantine([]);
});

afterEach(() => {
  delete window._groupQuarantineEntries;
  vi.useRealTimers();
});

describe('the AcoustID gate', () => {
  it('treats the pre-config moment as enabled', () => {
    // Flashing quarantine-only at everyone on every page load, then correcting
    // a beat later, is worse than briefly showing a pill that turns out to be
    // unavailable.
    const { result } = renderHook(() => useAdlVerification());
    expect(result.current.state.acoustidEnabled).toBeNull();
    expect(result.current.acoustidEnabled).toBe(true);
  });

  it('stays enabled for the normal configuration', async () => {
    const { result } = renderHook(() => useAdlVerification());
    await waitFor(() => expect(result.current.state.acoustidEnabled).toBe(true));
    expect(result.current.state.subView).toBe('unverified');
  });

  it('collapses when AcoustID is off', async () => {
    stubConfig({ success: true, acoustid_enabled: false, require_verified: false });
    const { result } = renderHook(() => useAdlVerification());
    await waitFor(() => expect(result.current.acoustidEnabled).toBe(false));
    expect(result.current.state.subView).toBe('quarantine');
  });

  it('ALSO collapses when require_verified is on', async () => {
    // The easy-to-miss half: AcoustID is running, but unconfirmed tracks get
    // quarantined instead of imported unverified, so the queue is always empty.
    stubConfig({ success: true, acoustid_enabled: true, require_verified: true });
    const { result } = renderHook(() => useAdlVerification());
    await waitFor(() => expect(result.current.acoustidEnabled).toBe(false));
    expect(result.current.state.subView).toBe('quarantine');
  });

  it('loads the quarantine immediately when the queue cannot exist', async () => {
    // Otherwise the only view the user can reach starts empty.
    const quar = stubQuarantine([entry()]);
    stubConfig({ success: true, acoustid_enabled: false });
    const { result } = renderHook(() => useAdlVerification());
    await waitFor(() => expect(result.current.state.quarantineLoaded).toBe(true));
    expect(quar.calls).toBe(1);
    expect(result.current.state.quarantine).toHaveLength(1);
  });

  it('does NOT preload the quarantine when the queue is usable', async () => {
    const quar = stubQuarantine([entry()]);
    const { result } = renderHook(() => useAdlVerification());
    await waitFor(() => expect(result.current.state.acoustidEnabled).toBe(true));
    expect(quar.calls).toBe(0);
  });

  it('assumes enabled when the config read fails', async () => {
    // Hiding the review queue because a config fetch blipped would silently
    // strand files needing attention.
    server.use(http.get('/api/verification/config', () => HttpResponse.error()));
    const { result } = renderHook(() => useAdlVerification());
    await waitFor(() => expect(result.current.state.acoustidEnabled).toBe(true));
  });

  it('does not set state after unmount', async () => {
    // React logs a warning and the update is wasted; the `live` flag exists
    // for exactly this.
    const errors: unknown[] = [];
    const spy = vi.spyOn(console, 'error').mockImplementation((...args) => errors.push(args));
    const { unmount } = renderHook(() => useAdlVerification());
    unmount();
    await new Promise((r) => setTimeout(r, 20));
    expect(errors).toEqual([]);
    spy.mockRestore();
  });
});

describe('loading the quarantine', () => {
  it('loads once and then skips repeat calls', async () => {
    // It is a filesystem scan server-side.
    const quar = stubQuarantine([entry()]);
    const { result } = renderHook(() => useAdlVerification());
    await waitFor(() => expect(result.current.state.acoustidEnabled).toBe(true));

    await act(async () => {
      await result.current.loadQuarantine();
    });
    expect(quar.calls).toBe(1);

    await act(async () => {
      await result.current.loadQuarantine();
    });
    expect(quar.calls).toBe(1);
  });

  it('reloads when forced', async () => {
    const quar = stubQuarantine([entry()]);
    const { result } = renderHook(() => useAdlVerification());
    await waitFor(() => expect(result.current.state.acoustidEnabled).toBe(true));

    await act(async () => {
      await result.current.loadQuarantine();
    });
    await act(async () => {
      await result.current.loadQuarantine(true);
    });
    expect(quar.calls).toBe(2);
  });

  it('allows only one request in flight', async () => {
    const quar = stubQuarantine([entry()], 30);
    const { result } = renderHook(() => useAdlVerification());
    await waitFor(() => expect(result.current.state.acoustidEnabled).toBe(true));

    await act(async () => {
      await Promise.all([
        result.current.loadQuarantine(true),
        result.current.loadQuarantine(true),
        result.current.loadQuarantine(true),
      ]);
    });
    expect(quar.calls).toBe(1);
  });

  it('marks itself loaded even when the list comes back empty', async () => {
    // quarantineLoaded is what switches the UI from "Loading…" to the empty
    // state; leaving it false spins forever on a clean install.
    const { result } = renderHook(() => useAdlVerification());
    await act(async () => {
      await result.current.loadQuarantine(true);
    });
    expect(result.current.state.quarantineLoaded).toBe(true);
    expect(result.current.state.quarantine).toEqual([]);
  });
});

describe('the sub-view', () => {
  it('fetches fresh entries every time quarantine is opened', async () => {
    const quar = stubQuarantine([entry()]);
    const { result } = renderHook(() => useAdlVerification());
    await waitFor(() => expect(result.current.state.acoustidEnabled).toBe(true));

    await act(async () => {
      result.current.setSubView('quarantine');
    });
    await waitFor(() => expect(quar.calls).toBe(1));

    await act(async () => {
      result.current.setSubView('unverified');
    });
    await act(async () => {
      result.current.setSubView('quarantine');
    });
    await waitFor(() => expect(quar.calls).toBe(2));
  });

  it('switches back to unverified without refetching', async () => {
    const quar = stubQuarantine([entry()]);
    const { result } = renderHook(() => useAdlVerification());
    await waitFor(() => expect(result.current.state.acoustidEnabled).toBe(true));

    await act(async () => {
      result.current.setSubView('unverified');
    });
    expect(result.current.state.subView).toBe('unverified');
    expect(quar.calls).toBe(0);
  });

  it('refuses to leave quarantine when no unverified queue can exist', async () => {
    // The pill is hidden in that mode, but the setter must not be the one
    // thing that can strand the user on a permanently empty view.
    stubConfig({ success: true, acoustid_enabled: false });
    const { result } = renderHook(() => useAdlVerification());
    await waitFor(() => expect(result.current.acoustidEnabled).toBe(false));

    await act(async () => {
      result.current.setSubView('unverified');
    });
    expect(result.current.state.subView).toBe('quarantine');
  });
});

describe('expanded-row state', () => {
  it('toggles each set independently', async () => {
    const { result } = renderHook(() => useAdlVerification());
    await waitFor(() => expect(result.current.state.acoustidEnabled).toBe(true));

    act(() => result.current.toggleUnverified('u1'));
    act(() => result.current.toggleQuarantine('q1'));
    act(() => result.current.toggleGroup('g1'));

    expect([...result.current.state.openUnverified]).toEqual(['u1']);
    expect([...result.current.state.openQuarantine]).toEqual(['q1']);
    expect([...result.current.state.openGroups]).toEqual(['g1']);

    act(() => result.current.toggleUnverified('u1'));
    expect(result.current.state.openUnverified.size).toBe(0);
    // Untouched by the collapse above.
    expect(result.current.state.openQuarantine.size).toBe(1);
  });

  it('keeps several rows open at once', async () => {
    const { result } = renderHook(() => useAdlVerification());
    act(() => result.current.toggleUnverified('a'));
    act(() => result.current.toggleUnverified('b'));
    expect([...result.current.state.openUnverified].sort()).toEqual(['a', 'b']);
  });

  it('replaces the Set rather than mutating it', async () => {
    // A mutated Set is referentially equal, so React skips the re-render and
    // the row never visibly opens.
    const { result } = renderHook(() => useAdlVerification());
    const before = result.current.state.openUnverified;
    act(() => result.current.toggleUnverified('a'));
    expect(result.current.state.openUnverified).not.toBe(before);
    expect(before.size).toBe(0);
  });

  it('survives the 2s poll re-render, because the keys are ids not DOM nodes', async () => {
    const { result, rerender } = renderHook(() => useAdlVerification());
    act(() => result.current.toggleQuarantine('q1'));
    rerender();
    expect(result.current.state.openQuarantine.has('q1')).toBe(true);
  });
});

describe('grouping alternative candidates', () => {
  it('defers to the shared grouper so the two tabs cannot drift', () => {
    const grouped = [{ key: 'g1', members: [entry(), entry({ id: 'q2' })] }];
    window._groupQuarantineEntries = vi.fn(() => grouped);
    const entries = [entry(), entry({ id: 'q2' })];

    expect(groupQuarantine(entries)).toBe(grouped);
    expect(window._groupQuarantineEntries).toHaveBeenCalledWith(entries);
  });

  it('falls back to one group per entry when the grouper is absent', () => {
    // Honest rather than clever: every candidate stands alone.
    const a = entry();
    const b = entry({ id: 'q2' });
    expect(groupQuarantine([a, b])).toEqual([
      { key: null, members: [a] },
      { key: null, members: [b] },
    ]);
  });

  it('returns nothing for no entries', () => {
    expect(groupQuarantine([])).toEqual([]);
  });
});
