import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { approveAutoImportResult, rejectAutoImportResult } from './-import.api';

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
});
