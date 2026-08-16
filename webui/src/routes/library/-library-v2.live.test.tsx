import type { ReactNode } from 'react';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { LIBRARY_V2_QUERY_KEY } from './-library-v2.api';
import { useMaintenanceChanged } from './-library-v2.live';

/**
 * "The AcoustID tool finished and the Check column still says Not scanned —
 * even after a refresh." The stale data had its own cause (the scanner was not
 * writing `acoustid_status`), but the second half of the report stands on its
 * own: a job that changes the library while you are looking at it must make
 * the page catch up, without a manual refresh.
 *
 * The signal already exists app-wide. core.js re-broadcasts the worker's
 * socket frames as `ss:repair-progress` on every page, not just Tools, so the
 * library only has to notice a job leaving the running state.
 */

function wrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

function frame(detail: Record<string, { status: string }>) {
  window.dispatchEvent(new CustomEvent('ss:repair-progress', { detail }));
}

describe('useMaintenanceChanged', () => {
  it('refetches the library when a maintenance job finishes', () => {
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    renderHook(() => useMaintenanceChanged(), { wrapper: wrapper(client) });

    act(() => frame({ acoustid_scanner: { status: 'running' } }));
    expect(invalidate).not.toHaveBeenCalled();

    act(() => frame({ acoustid_scanner: { status: 'finished' } }));

    expect(invalidate).toHaveBeenCalledWith({ queryKey: LIBRARY_V2_QUERY_KEY });
  });

  it('does not refetch on every progress frame of a running job', () => {
    // The worker pushes these once a second. Invalidating on each one would
    // re-fetch the whole artist view for the length of the scan.
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    renderHook(() => useMaintenanceChanged(), { wrapper: wrapper(client) });

    act(() => frame({ acoustid_scanner: { status: 'running' } }));
    act(() => frame({ acoustid_scanner: { status: 'running' } }));
    act(() => frame({ acoustid_scanner: { status: 'running' } }));

    expect(invalidate).not.toHaveBeenCalled();
  });

  it('refetches once for a job that ends in an error, too', () => {
    // A failed run still changed whatever it got through before failing.
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    renderHook(() => useMaintenanceChanged(), { wrapper: wrapper(client) });

    act(() => frame({ lossy_converter: { status: 'running' } }));
    act(() => frame({ lossy_converter: { status: 'error' } }));

    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it('ignores a job it never saw running', () => {
    // Arriving on the page after a scan ended should not trigger a refetch
    // from a trailing frame about it.
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    renderHook(() => useMaintenanceChanged(), { wrapper: wrapper(client) });

    act(() => frame({ acoustid_scanner: { status: 'finished' } }));

    expect(invalidate).not.toHaveBeenCalled();
  });

  it('stops listening once the page is gone', () => {
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const { unmount } = renderHook(() => useMaintenanceChanged(), {
      wrapper: wrapper(client),
    });

    act(() => frame({ acoustid_scanner: { status: 'running' } }));
    unmount();
    act(() => frame({ acoustid_scanner: { status: 'finished' } }));

    expect(invalidate).not.toHaveBeenCalled();
  });
});
