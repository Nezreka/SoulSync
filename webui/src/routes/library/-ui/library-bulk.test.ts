import { afterEach, describe, expect, it, vi } from 'vitest';

import { rangeBetween, runBulk, stopBulkRun, waitForJob } from './library-bulk';

const api = vi.hoisted(() => ({ status: vi.fn() }));
vi.mock('../-library-v2.api', async (original) => ({
  ...(await original<typeof import('../-library-v2.api')>()),
  fetchLibraryV2JobStatus: api.status,
}));

afterEach(() => vi.clearAllMocks());

describe('#2109 selecting artists', () => {
  it('a shift-click range follows the page order, either way', () => {
    const order = [5, 9, 2, 7, 4];
    expect(rangeBetween(order, 9, 7)).toEqual([9, 2, 7]);
    expect(rangeBetween(order, 4, 9)).toEqual([9, 2, 7, 4]);
    // an anchor that left the page: just the row
    expect(rangeBetween(order, 99, 7)).toEqual([7]);
  });
});

describe('#2109 a bulk run', () => {
  it('counts what it did, skipped and failed, and names the failures', async () => {
    const result = await runBulk('Test', [1, 2, 3, 4], async (id) => {
      if (id === 2) return 'skipped';
      if (id === 3) throw new Error('artist 3 went away');
      return 'done';
    });
    expect(result).toMatchObject({
      total: 4,
      done: 2,
      skipped: 1,
      failed: 1,
      running: false,
      errors: ['artist 3 went away'],
    });
  });

  it('stop lets the one in flight finish and starts nothing new', async () => {
    const seen: number[] = [];
    const result = await runBulk(
      'Test',
      [1, 2, 3],
      async (id) => {
        seen.push(id);
        stopBulkRun();
        return 'done';
      },
      1,
    );
    expect(seen).toEqual([1]);
    expect(result).toMatchObject({ done: 1, stopped: true, running: false });
  });

  it('waits for a background job before the next artist starts', async () => {
    api.status
      .mockResolvedValueOnce({ running: true, error: null })
      .mockResolvedValueOnce({ running: false, error: null });
    await waitForJob('job-1', 1);
    expect(api.status).toHaveBeenCalledTimes(2);
    api.status.mockResolvedValueOnce({ running: false, error: 'provider down' });
    await expect(waitForJob('job-2', 1)).rejects.toThrow('provider down');
  });
});
