import { act, renderHook } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  _tickDiagnostics,
  AUTOMATION_PROGRESS_EVENT,
  isFinished,
  isRunning,
  useAutomationProgress,
  useSecondTick,
} from './-automations.progress';

function emit(detail: unknown) {
  act(() => {
    window.dispatchEvent(new CustomEvent(AUTOMATION_PROGRESS_EVENT, { detail }));
  });
}

describe('useAutomationProgress', () => {
  it('records a frame keyed by automation id', () => {
    const { result } = renderHook(() => useAutomationProgress());
    emit({ '7': { status: 'running', progress: 40, phase: 'Scanning' } });
    expect(result.current[7]).toEqual({ status: 'running', progress: 40, phase: 'Scanning' });
  });

  it('MERGES frames instead of replacing the map', () => {
    // The server reports only the automations it has news about. Replacing
    // would make a still-running automation's panel vanish the moment an
    // unrelated one reported.
    const { result } = renderHook(() => useAutomationProgress());
    emit({ '1': { status: 'running', progress: 10 } });
    emit({ '2': { status: 'running', progress: 90 } });
    expect(result.current[1]?.progress).toBe(10);
    expect(result.current[2]?.progress).toBe(90);
  });

  it('lets a later frame supersede the same id', () => {
    const { result } = renderHook(() => useAutomationProgress());
    emit({ '1': { status: 'running', progress: 10 } });
    emit({ '1': { status: 'finished', progress: 100 } });
    expect(result.current[1]).toEqual({ status: 'finished', progress: 100 });
  });

  it('ignores junk without corrupting the map', () => {
    const { result } = renderHook(() => useAutomationProgress());
    emit({ '1': { status: 'running' } });
    emit(null);
    emit('nonsense');
    emit({ notanumber: { status: 'running' } }); // would become NaN as a key
    expect(Object.keys(result.current)).toEqual(['1']);
  });

  it('seeds from the catch-up response so a page opened mid-run shows it', () => {
    // Without this, a card stays blank until the next socket frame — which in a
    // long quiet phase can be a while. loadAutomations did the same catch-up.
    const { result } = renderHook(() =>
      useAutomationProgress({ '5': { status: 'running', progress: 25 } }),
    );
    expect(result.current[5]?.progress).toBe(25);
  });

  it('ignores the {error} shape of that response', () => {
    const { result } = renderHook(() => useAutomationProgress({ error: 'nope' }));
    expect(result.current).toEqual({});
  });

  it('lets a live frame win over the catch-up seed', () => {
    // A socket frame that landed while the catch-up was in flight is newer.
    const { result } = renderHook(() =>
      useAutomationProgress({ '5': { status: 'running', progress: 10 } }),
    );
    emit({ '5': { status: 'finished', progress: 100 } });
    expect(result.current[5]?.status).toBe('finished');
  });

  it('stops listening once unmounted', () => {
    const { result, unmount } = renderHook(() => useAutomationProgress());
    unmount();
    emit({ '9': { status: 'running' } });
    expect(result.current[9]).toBeUndefined();
  });
});

describe('run state predicates', () => {
  it('separates running from finished and errored', () => {
    expect(isRunning({ status: 'running' })).toBe(true);
    expect(isRunning({ status: 'finished' })).toBe(false);
    expect(isRunning(undefined)).toBe(false);
    expect(isFinished({ status: 'finished' })).toBe(true);
    expect(isFinished({ status: 'error' })).toBe(true);
    expect(isFinished({ status: 'running' })).toBe(false);
  });
});

/**
 * The seam itself. React only ever sees progress if core.js mirrors the socket
 * frame onto the window, and it only owns its cards if the vanilla renderer
 * skips them — both live in plain scripts no bundler or typechecker guards.
 */
describe('the vanilla side of the progress seam', () => {
  const read = (file: string) => readFileSync(resolve(process.cwd(), 'static', file), 'utf8');

  it('core.js mirrors automation:progress onto the window', () => {
    const src = read('core.js');
    expect(src).toContain("socket.on('automation:progress'");
    expect(src).toContain(AUTOMATION_PROGRESS_EVENT);
  });

  it('still calls the vanilla renderer, for the legacy and video pages', () => {
    expect(read('core.js')).toContain('updateAutomationProgressFromData(data)');
  });

  it('also forwards automation progress to the notification active-card rail', () => {
    expect(read('core.js')).toContain('updateMusicAutomationTask(data)');
    expect(read('downloads.js')).toContain('function updateMusicAutomationTask(data)');
    expect(read('downloads.js')).toContain("_notifActionHTML('Open Automations', 'automations')");
  });

  it('does not keep the wishlist notification pinned on a stale processing flag', () => {
    expect(read('downloads.js')).toContain(
      'const hasActiveBatchSignal = data.active_batches != null',
    );
    expect(read('downloads.js')).toContain(
      'const active = !!data.is_auto_processing && (!hasActiveBatchSignal || activeBatches > 0)',
    );
  });

  it('clears map-style notification tasks when the backend reports no active jobs', () => {
    expect(read('downloads.js')).toContain(
      'for (const aid of Object.keys(_musicAutomationTasks)) delete _musicAutomationTasks[aid]',
    );
    expect(read('downloads.js')).toContain(
      'for (const jobId of Object.keys(_musicRepairTasks)) delete _musicRepairTasks[jobId]',
    );
  });

  it('clamps notification percentages before rendering them', () => {
    expect(read('downloads.js')).toContain('function _taskClampPct(value, fallback = 0)');
    expect(read('downloads.js')).toContain(
      'const safePct = _taskHasPct(pct) ? _taskClampPct(pct) : 0',
    );
  });

  it('renders an indeterminate notification bar when real progress is unavailable', () => {
    expect(read('downloads.js')).toContain('function _taskHasPct(value)');
    expect(read('downloads.js')).toContain("return process?.status === 'starting' ? 0 : null");
    expect(read('downloads.js')).toContain('notif-active-indeterminate');
    expect(read('style.css')).toContain('@keyframes notif-active-slide');
  });

  it('forwards Last.fm listening import progress to the notification rail', () => {
    expect(read('core.js')).toContain("socket.on('lastfm:import-progress'");
    expect(read('downloads.js')).toContain('function updateLastfmListeningImportTask(data)');
    expect(read('downloads.js')).toContain("_notifActionHTML('Open Stats', 'stats')");
  });

  it('loadAutomations refuses to repaint the legacy list behind React', () => {
    // Reachable via the shared builder: saveAutomation calls onSaved ->
    // loadAutomations after the React page has reclaimed the shell. Without
    // this guard the hidden container gets a full duplicate render, and every
    // #auto-section-* id and .automation-card[data-id] exists twice.
    const src = read('stats-automations.js');
    const fn = src.slice(src.indexOf('async function loadAutomations()'));
    expect(fn.slice(0, 1200)).toContain("getElementById('webui-react-root')");
  });

  it('the vanilla renderer refuses to write into React-owned cards', () => {
    // Without this guard the document-wide querySelector finds React's cards
    // and injects panels React clobbers on its next render.
    expect(read('stats-automations.js')).toContain("card.closest('#webui-react-root')");
  });
});

describe('useSecondTick', () => {
  it('runs ONE interval no matter how many countdowns subscribe', () => {
    // The vanilla page ticked every countdown from a single module-level
    // interval. A timer per card means N timers and N re-renders a second for
    // the same output.
    const a = renderHook(() => useSecondTick());
    const b = renderHook(() => useSecondTick());
    const c = renderHook(() => useSecondTick());
    expect(_tickDiagnostics()).toEqual({ running: true, listeners: 3 });
    a.unmount();
    b.unmount();
    c.unmount();
  });

  it('stops the interval once the last subscriber leaves', () => {
    // A page with no scheduled automations must not leave a timer running.
    const a = renderHook(() => useSecondTick());
    const b = renderHook(() => useSecondTick());
    a.unmount();
    expect(_tickDiagnostics().running).toBe(true);
    b.unmount();
    expect(_tickDiagnostics()).toEqual({ running: false, listeners: 0 });
  });

  it('re-renders subscribers on each tick', () => {
    vi.useFakeTimers();
    let renders = 0;
    const h = renderHook(() => {
      renders += 1;
      useSecondTick();
    });
    const before = renders;
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(renders).toBeGreaterThan(before);
    h.unmount();
    vi.useRealTimers();
  });
});
