import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { fetchReconciliationScanStatus } from '../review/-review.api';
import { fetchRepairStatus } from './-tools.api';
import { usePolledStatus } from './-tools.polling';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('keeps idle observation alive after a failed initial request and stops on unmount', async () => {
  vi.useFakeTimers();
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(null)
    .mockResolvedValueOnce({ running: false })
    .mockResolvedValue({ running: true });
  const onState = vi.fn();
  const hook = renderHook(() =>
    usePolledStatus({
      fetcher,
      isRunning: (value: { running: boolean }) => value.running,
      intervalMs: 100,
      pollWhileIdle: true,
      onState,
    }),
  );
  await act(async () => {
    await Promise.resolve();
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(200);
  });
  expect(fetcher).toHaveBeenCalledTimes(3);
  expect(onState).toHaveBeenCalledTimes(2);
  hook.unmount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(200);
  });
  expect(fetcher).toHaveBeenCalledTimes(3);
});

it('preserves Tools polling behavior by stopping after the job finishes', async () => {
  vi.useFakeTimers();
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce({ running: true })
    .mockResolvedValue({ running: false });
  renderHook(() =>
    usePolledStatus({
      fetcher,
      isRunning: (value: { running: boolean }) => value.running,
      intervalMs: 100,
    }),
  );
  await act(async () => {
    await Promise.resolve();
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(300);
  });
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it('shares status transport while retaining strict Review and tolerant Tools errors', async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(
      async () => new Response(JSON.stringify({ error: 'Status unavailable' }), { status: 503 }),
    );
  vi.stubGlobal('fetch', fetcher);
  expect(await fetchRepairStatus()).toBeNull();
  await expect(fetchReconciliationScanStatus()).rejects.toThrow('Status unavailable');
  expect(fetcher.mock.calls.map((call) => call[0])).toEqual([
    '/api/repair/status',
    '/api/repair/status',
  ]);
});
