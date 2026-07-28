import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useEnhancedData } from './-artist-detail.use-enhanced';

let requested: string[] = [];

function stubEnhanced(body: unknown = { success: true, albums: [] }) {
  requested = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      requested.push(String(input));
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
}

beforeEach(() => stubEnhanced());

afterEach(() => vi.unstubAllGlobals());

describe('useEnhancedData across artists', () => {
  it('refetches when the SAME hook is handed a new artist', async () => {
    // TanStack keeps this component mounted when only the route params change,
    // so the artist swap arrives as a prop — not a remount.
    const { result, rerender } = renderHook(({ id }) => useEnhancedData(id, true), {
      initialProps: { id: 42 as unknown },
    });
    await waitFor(() => expect(result.current.data).not.toBeNull());
    expect(requested).toHaveLength(1);

    rerender({ id: 99 });
    await waitFor(() => expect(requested).toHaveLength(2));
    expect(requested[1]).toContain('/artist/99/');
  });

  it('drops the previous payload the moment the id changes', async () => {
    const { result, rerender } = renderHook(({ id }) => useEnhancedData(id, true), {
      initialProps: { id: 42 as unknown },
    });
    // Wait for a real payload first — otherwise data is null either way and the
    // assertion below proves nothing.
    await waitFor(() => expect(result.current.data).not.toBeNull());

    rerender({ id: 99 });
    // Synchronous: a stale payload must never render under a new artist, not
    // even for the frame before the new request lands.
    expect(result.current.data).toBeNull();
    // Let the remaining in-flight request settle inside act(), so its state
    // update does not land after the test returns (React's act warning).
    await act(async () => {});
  });

  it('retries a FAILED artist once its id changes', async () => {
    stubEnhanced({ success: false, error: 'nope' });
    const { result, rerender } = renderHook(({ id }) => useEnhancedData(id, true), {
      initialProps: { id: 42 as unknown },
    });
    await waitFor(() => expect(result.current.status.error).toBe('nope'));

    stubEnhanced();
    rerender({ id: 99 });
    await waitFor(() => expect(result.current.data).not.toBeNull());
    expect(result.current.status.error).toBe('');
  });

  it('does not retry the SAME artist after a failure', async () => {
    stubEnhanced({ success: false, error: 'nope' });
    const { result, rerender } = renderHook(({ enabled }) => useEnhancedData(42, enabled), {
      initialProps: { enabled: true },
    });
    await waitFor(() => expect(result.current.status.error).toBe('nope'));

    rerender({ enabled: false });
    rerender({ enabled: true });
    await new Promise((r) => setTimeout(r, 20));
    expect(requested).toHaveLength(1);
  });
});
