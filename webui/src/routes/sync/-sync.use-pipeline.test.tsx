/**
 * The Auto-Sync pipeline controller — run, the 2.5s poll, the three stop
 * conditions, and the resume guard (auto-sync.js 2467-2525).
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { MirroredPipelineState } from './-sync.pipeline';

import { useMirroredPipeline } from './-sync.use-pipeline';

let calls: string[] = [];
let runBody: unknown = { state: { status: 'running', progress: 0, phase: 'Refreshing' } };
let statusBody: unknown = { status: 'running', progress: 10 };

function stubFetch(): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url);
      const body = url.endsWith('/pipeline/run') ? runBody : statusBody;
      if (body instanceof Error) throw body;
      return new Response(JSON.stringify(body));
    }),
  );
}

const statusCalls = () => calls.filter((u) => u.endsWith('/pipeline/status')).length;

interface Applied {
  playlistId: number;
  state: MirroredPipelineState;
}
let applied: Applied[] = [];
let reloads = 0;

function Harness({ resumeIds = [] as number[] }) {
  const pipeline = useMirroredPipeline({
    onState: (playlistId, state) => applied.push({ playlistId, state }),
    reload: () => {
      reloads += 1;
    },
  });
  return (
    <div>
      <button type="button" onClick={() => void pipeline.run(3, 'Road Trip')}>
        run
      </button>
      <button type="button" onClick={() => resumeIds.forEach((id) => pipeline.resume(id, 'RT'))}>
        resume
      </button>
    </div>
  );
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  stubFetch();
  applied = [];
  reloads = 0;
  runBody = { state: { status: 'running', progress: 0, phase: 'Refreshing' } };
  statusBody = { status: 'running', progress: 10 };
  window.showToast = vi.fn() as typeof window.showToast;
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as { showToast?: unknown }).showToast;
});

const click = async (label: string) => {
  await act(async () => {
    fireEvent.click(screen.getByText(label));
  });
};

describe('useMirroredPipeline — run', () => {
  it('POSTs, applies the returned state, toasts, and starts polling immediately', async () => {
    render(<Harness />);
    await click('run');
    expect(calls[0]).toBe('/api/mirrored-playlists/3/pipeline/run');
    expect(applied[0]).toEqual({
      playlistId: 3,
      state: { status: 'running', progress: 0, phase: 'Refreshing' },
    });
    expect(window.showToast).toHaveBeenCalledWith('Auto-Sync started for Road Trip', 'success');
    // The first status tick fires without waiting a full interval (2523).
    expect(statusCalls()).toBe(1);
  });

  it('a backend that answers without a state gets the optimistic one (2475)', async () => {
    runBody = {};
    render(<Harness />);
    await click('run');
    expect(applied[0].state).toEqual({
      status: 'running',
      progress: 0,
      phase: 'Starting pipeline...',
    });
  });

  it('a failed start toasts the error and never polls', async () => {
    runBody = new Error('offline');
    render(<Harness />);
    await click('run');
    expect(window.showToast).toHaveBeenCalledWith('Error: offline', 'error');
    expect(statusCalls()).toBe(0);
  });
});

describe('useMirroredPipeline — the poll', () => {
  it('ticks every 2.5s while running', async () => {
    render(<Harness />);
    await click('run');
    expect(statusCalls()).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2499);
    });
    expect(statusCalls()).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(statusCalls()).toBe(2);
    expect(applied.at(-1)?.state).toEqual({ status: 'running', progress: 10 });
  });

  it('finished stops, toasts success, and reloads the list', async () => {
    render(<Harness />);
    await click('run');
    statusBody = { status: 'finished', progress: 100 };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(window.showToast).toHaveBeenCalledWith('Auto-Sync complete for Road Trip', 'success');
    expect(reloads).toBe(1);
    const settled = statusCalls();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(statusCalls()).toBe(settled);
  });

  it('error and skipped stop with the generic message and reload', async () => {
    statusBody = { status: 'skipped', phase: 'Nothing to do' };
    render(<Harness />);
    await click('run');
    expect(window.showToast).toHaveBeenCalledWith('Pipeline stopped for Road Trip', 'error');
    expect(reloads).toBe(1);
    const settled = statusCalls();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(statusCalls()).toBe(settled);
  });

  it('a status body carrying `error` is REJECTED before the tick ever sees it', async () => {
    // The reader throws on any body with an `error` key (2372), so the tick's
    // error arm never runs with a message — the catch reports it instead, and
    // no reload happens. Faithful to the vanilla; see -sync.pipeline.ts.
    statusBody = { status: 'error', error: 'source unreachable' };
    render(<Harness />);
    await click('run');
    expect(window.showToast).toHaveBeenCalledWith(
      'Pipeline status error: source unreachable',
      'error',
    );
    expect(reloads).toBe(0);
    expect(applied.filter((a) => a.state.status === 'error')).toHaveLength(0);
  });

  it('idle stops SILENTLY — no toast, no reload (2515-2518)', async () => {
    statusBody = { status: 'idle' };
    render(<Harness />);
    await click('run');
    const toasts = (window.showToast as unknown as ReturnType<typeof vi.fn>).mock.calls;
    // Only the "started" toast; the idle stop adds none.
    expect(toasts).toHaveLength(1);
    expect(reloads).toBe(0);
    const settled = statusCalls();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(statusCalls()).toBe(settled);
  });

  it('a thrown tick STOPS the poller and toasts — the vanilla never retries', async () => {
    render(<Harness />);
    await click('run');
    statusBody = new Error('boom');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(window.showToast).toHaveBeenCalledWith('Pipeline status error: boom', 'error');
    const settled = statusCalls();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(statusCalls()).toBe(settled);
  });
});

describe('useMirroredPipeline — resume and teardown', () => {
  it('resume polls once, and a second call while live is a no-op (653-655)', async () => {
    render(<Harness resumeIds={[3]} />);
    await click('resume');
    expect(statusCalls()).toBe(1);
    await click('resume');
    await click('resume');
    expect(statusCalls()).toBe(1);
    // ...and the live poller is still ticking.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(statusCalls()).toBe(2);
  });

  it('a run RESTARTS the poll rather than stacking a second one', async () => {
    render(<Harness resumeIds={[3]} />);
    await click('resume');
    await click('run');
    const after = statusCalls();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    // One tick per interval, not two.
    expect(statusCalls()).toBe(after + 1);
  });

  it('resume tracks each playlist separately', async () => {
    render(<Harness resumeIds={[3, 4]} />);
    await click('resume');
    expect(calls.filter((u) => u === '/api/mirrored-playlists/3/pipeline/status')).toHaveLength(1);
    expect(calls.filter((u) => u === '/api/mirrored-playlists/4/pipeline/status')).toHaveLength(1);
  });

  it('unmounting clears every poller', async () => {
    const { unmount } = render(<Harness resumeIds={[3, 4]} />);
    await click('resume');
    const before = statusCalls();
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(statusCalls()).toBe(before);
  });
});
