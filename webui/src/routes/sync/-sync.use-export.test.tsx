/**
 * The export job controller — start, poll cadence, auto-hide, teardown
 * (stats-automations.js 731-819).
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ExportMode } from './-sync.export';

import { useExportJobs } from './-sync.use-export';

interface Call {
  url: string;
  method: string;
  body: unknown;
}
let calls: Call[] = [];
let responder: (url: string, method: string) => unknown = () => ({});

function stubFetch(): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
      const data = responder(url, method);
      if (data instanceof Error) throw data;
      return new Response(JSON.stringify(data));
    }),
  );
}

function Harness({ mode = 'push' as ExportMode, backfill = false }) {
  const jobs = useExportJobs();
  const status = jobs.statuses[3];
  return (
    <div>
      <button type="button" onClick={() => void jobs.start(3, mode, backfill)}>
        go
      </button>
      <span data-testid="status">{status ? status.text : 'none'}</span>
      <span data-testid="link">{status?.link ? status.link.url : 'none'}</span>
    </div>
  );
}

const text = () => screen.getByTestId('status').textContent;

beforeEach(() => {
  /**
   * NOT `shouldAdvanceTime: true`, which this used and which made
   * "drops the status after its autoHideMs" flaky — it failed in roughly half
   * of full-suite runs and never once in isolation.
   *
   * That option makes the fake clock advance with REAL time as well as with
   * `advanceTimersByTime`. The auto-hide test asserts an exact boundary (still
   * up at 9999ms, gone at 10000), so any real milliseconds that elapse between
   * setting the status and advancing the clock are added on top — and under a
   * loaded 317-file run on WSL, that is easily the one millisecond needed to
   * fire the timer a tick early. In isolation the machine is fast enough that
   * it never accumulates.
   *
   * Nothing here needs it: every wait in this file goes through
   * `advanceTimersByTimeAsync`, which pumps microtasks itself, and the fetch
   * stub resolves as a promise rather than on a timer.
   */
  vi.useFakeTimers();
  stubFetch();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as { showToast?: unknown }).showToast;
});

describe('useExportJobs — starting a job', () => {
  it('paints "Starting export…" and POSTs the LB endpoint with {mode}', async () => {
    responder = () => ({});
    render(<Harness mode="download" />);
    await act(async () => {
      fireEvent.click(screen.getByText('go'));
    });
    expect(calls[0].url).toBe('/api/playlists/3/export/listenbrainz');
    expect(calls[0].method).toBe('POST');
    expect(calls[0].body).toEqual({ mode: 'download' });
    // No job_id came back, so the failure line replaces the starting one.
    expect(text()).toBe('Export failed to start');
  });

  it('a service mode POSTs the service endpoint with {backfill}', async () => {
    responder = () => ({});
    render(<Harness mode="deezer" backfill />);
    await act(async () => {
      fireEvent.click(screen.getByText('go'));
    });
    expect(calls[0].url).toBe('/api/playlists/3/export/service/deezer');
    expect(calls[0].body).toEqual({ backfill: true });
  });

  it('a thrown POST paints "Export error"', async () => {
    responder = () => new Error('offline');
    render(<Harness />);
    await act(async () => {
      fireEvent.click(screen.getByText('go'));
    });
    expect(text()).toBe('Export error');
  });

  it('needs_auth surfaces the authorize link and never polls', async () => {
    responder = () => ({ needs_auth: true, auth_url: 'https://accounts/x' });
    render(<Harness mode="spotify" />);
    await act(async () => {
      fireEvent.click(screen.getByText('go'));
    });
    expect(text()).toBe('Spotify needs permission to create playlists —');
    expect(screen.getByTestId('link').textContent).toBe('https://accounts/x');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(calls.filter((c) => c.url.includes('/export/status/'))).toHaveLength(0);
  });
});

describe('useExportJobs — polling', () => {
  it('polls the job at 1s until a terminal phase, then stops and toasts', async () => {
    const toast = vi.fn();
    (window as { showToast?: unknown }).showToast = toast;
    let phase = 'resolving';
    responder = (url) =>
      url.includes('/export/status/')
        ? { job: { phase, done: 1, total: 4, summary: { included: 4, total: 4 } } }
        : { success: true, job_id: 'j9' };
    render(<Harness />);
    await act(async () => {
      fireEvent.click(screen.getByText('go'));
    });
    // The first tick runs immediately (802).
    expect(text()).toBe('Matching 1/4 (25%)');
    const afterFirst = calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(calls.length).toBe(afterFirst + 1);

    phase = 'done';
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(text()).toBe('Synced to ListenBrainz · 4/4 matched');
    expect(toast).toHaveBeenCalledWith('Playlist synced to ListenBrainz (4/4 matched)', 'success');

    // Terminal: no further requests, ever.
    const settled = calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(calls.length).toBe(settled);
  });

  it('a failed tick paints nothing and retries at 2s instead of 1s', async () => {
    let fail = true;
    responder = (url) => {
      if (!url.includes('/export/status/')) return { success: true, job_id: 'j9' };
      if (fail) return new Error('boom');
      return { job: { phase: 'pushing' } };
    };
    render(<Harness />);
    await act(async () => {
      fireEvent.click(screen.getByText('go'));
    });
    // The failed tick left the pre-poll status in place.
    expect(text()).toBe('Starting export…');
    const afterFirst = calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(calls.length).toBe(afterFirst);

    fail = false;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(calls.length).toBe(afterFirst + 1);
    expect(text()).toBe('Pushing to ListenBrainz…');
  });

  it('the .jspf arm navigates to the download url', async () => {
    vi.stubGlobal('location', { href: '' });
    responder = (url) =>
      url.includes('/export/status/')
        ? { job: { phase: 'done', summary: { included: 2, total: 2 } } }
        : { success: true, job_id: 'j9' };
    render(<Harness mode="download" />);
    await act(async () => {
      fireEvent.click(screen.getByText('go'));
    });
    expect(window.location.href).toBe('/api/playlists/export/download/j9');
    expect(text()).toBe('Downloaded · 2/2 matched');
  });
});

describe('useExportJobs — the auto-hide timer and teardown', () => {
  it('drops the status after its autoHideMs', async () => {
    responder = (url) =>
      url.includes('/export/status/')
        ? { job: { phase: 'error', error: 'MB timeout' } }
        : { success: true, job_id: 'j9' };
    render(<Harness />);
    await act(async () => {
      fireEvent.click(screen.getByText('go'));
    });
    expect(text()).toBe('MB timeout');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9999);
    });
    expect(text()).toBe('MB timeout');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(text()).toBe('none');
  });

  it('a status with no autoHideMs stays up', async () => {
    responder = (url) =>
      url.includes('/export/status/')
        ? { job: { phase: 'pushing' } }
        : { success: true, job_id: 'j9' };
    render(<Harness />);
    await act(async () => {
      fireEvent.click(screen.getByText('go'));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(text()).toBe('Pushing to ListenBrainz…');
  });

  it('unmounting stops the poll — the vanilla has no teardown at all', async () => {
    responder = (url) =>
      url.includes('/export/status/')
        ? { job: { phase: 'resolving' } }
        : { success: true, job_id: 'j9' };
    const { unmount } = render(<Harness />);
    await act(async () => {
      fireEvent.click(screen.getByText('go'));
    });
    const before = calls.length;
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(calls.length).toBe(before);
  });
});
