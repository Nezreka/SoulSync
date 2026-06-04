import { describe, expect, it, vi } from 'vitest';

import { apiClient } from '@/app/api-client';
import { HttpResponse, http, server } from '@/test/msw';

import {
  approveAutoImportResult,
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

  it('#772: import-process calls use a long timeout, not ky default 10s', async () => {
    // Per-track import does heavy server-side enrichment (60-90s+); the default
    // 10s timeout aborted it client-side -> progress bar stuck + "Failed" while
    // files imported. These calls must pass an explicit long timeout.
    const ok = { json: async () => ({ success: true, processed: 1, total: 1, errors: [] }) };
    const spy = vi.spyOn(apiClient, 'post').mockReturnValue(ok as never);

    await processImportAlbumTrack({ album: {} as never, match: {} as never });
    await processImportSingleFile({});

    expect(spy).toHaveBeenCalledTimes(2);
    for (const call of spy.mock.calls) {
      expect(call[1]).toMatchObject({ timeout: 300_000 });
    }
    spy.mockRestore();
  });
});
