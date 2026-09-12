import { describe, expect, it, vi } from 'vitest';

import { apiClient } from '@/app/api-client';
import { HttpResponse, http, server } from '@/test/msw';

import {
  approveAutoImportResult,
  matchImportAlbum,
  processImportAlbumTrack,
  processImportSingleFile,
  rejectAutoImportResult,
} from './-import.api';

const softFailureMessage = 'Item not found or not pending review';

describe('import api', () => {
  it('surfaces soft failures from auto-import approval endpoints', async () => {
    server.use(
      http.post('/api/auto-import/approve/17', () =>
        HttpResponse.json({
          success: false,
          error: softFailureMessage,
        }),
      ),
      http.post('/api/auto-import/reject/18', () =>
        HttpResponse.json({
          success: false,
          error: softFailureMessage,
        }),
      ),
    );

    await expect(approveAutoImportResult(17)).rejects.toThrow(softFailureMessage);
    await expect(rejectAutoImportResult(18)).rejects.toThrow(softFailureMessage);
  });

  it('#1245: import-process opts into background jobs with an idempotency key', async () => {
    // The acceptance request is short; expensive work is polled by job ID.
    const ok = { json: async () => ({ success: true, processed: 1, total: 1, errors: [] }) };
    const spy = vi.spyOn(apiClient, 'post').mockReturnValue(ok as never);

    await processImportAlbumTrack({ album: {} as never, match: {} as never });
    await processImportSingleFile({});

    expect(spy).toHaveBeenCalledTimes(2);
    for (const call of spy.mock.calls) {
      expect(call[1]).toMatchObject({
        timeout: 300_000,
        headers: { Prefer: 'respond-async', 'Idempotency-Key': expect.any(String) },
      });
    }
    spy.mockRestore();
  });

  it('polls the accepted job and returns the processing result without resubmitting', async () => {
    vi.useFakeTimers();
    const result = { success: true, processed: 1, total: 1, errors: [] };
    const post = vi.spyOn(apiClient, 'post').mockReturnValue({
      json: async () => ({ success: true, job_id: 'job-1245' }),
    } as never);
    const get = vi
      .spyOn(apiClient, 'get')
      .mockReturnValueOnce({ json: async () => ({ state: 'running' }) } as never)
      .mockReturnValueOnce({
        json: async () => ({ state: 'complete', status: 200, result }),
      } as never);
    try {
      const pending = processImportSingleFile({ full_path: '/staging/song.flac' });
      await vi.runAllTimersAsync();
      await expect(pending).resolves.toEqual(result);
      expect(post).toHaveBeenCalledTimes(1);
      expect(get).toHaveBeenCalledTimes(2);
      expect(get).toHaveBeenCalledWith('import/jobs/job-1245');
    } finally {
      post.mockRestore();
      get.mockRestore();
      vi.useRealTimers();
    }
  });

  it('preserves an import failure returned by the background worker', async () => {
    vi.useFakeTimers();
    const result = { success: false, error: 'Provider unavailable' };
    const post = vi.spyOn(apiClient, 'post').mockReturnValue({
      json: async () => ({ success: true, job_id: 'failed-job' }),
    } as never);
    const get = vi.spyOn(apiClient, 'get').mockReturnValue({
      json: async () => ({ state: 'complete', status: 500, result }),
    } as never);
    try {
      const pending = expect(processImportSingleFile({})).rejects.toThrow('Provider unavailable');
      await vi.runAllTimersAsync();
      await pending;
      expect(post).toHaveBeenCalledTimes(1);
    } finally {
      post.mockRestore();
      get.mockRestore();
      vi.useRealTimers();
    }
  });

  it('#957: album-match call uses the long timeout, not ky default 10s', async () => {
    // Building the match payload fetches the tracklist + reads every staging file's tags; on a slow
    // NAS / big album that exceeds the 10s default and aborts with "Request timed out" mid-work.
    const ok = { json: async () => ({ success: true }) };
    const spy = vi.spyOn(apiClient, 'post').mockReturnValue(ok as never);

    await matchImportAlbum({ albumId: 'abc', source: 'deezer' });

    expect(spy).toHaveBeenCalledWith(
      'import/album/match',
      expect.objectContaining({ timeout: 300_000 }),
    );
    spy.mockRestore();
  });
});
