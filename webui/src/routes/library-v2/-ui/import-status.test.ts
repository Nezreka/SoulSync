import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { waitForLibraryV2Import } from './library-v2-page';

describe('library v2 import polling', () => {
  it('rejects with a finite timeout instead of leaving an Importing status behind', async () => {
    let polls = 0;
    server.use(
      http.get('/api/library/v2/import/status', () => {
        polls += 1;
        return HttpResponse.json({ running: true, error: null });
      }),
    );

    await expect(waitForLibraryV2Import(2, 0)).rejects.toThrow(
      'Timed out waiting for the library import',
    );
    expect(polls).toBe(2);
  });

  it('returns the first terminal import state', async () => {
    let polls = 0;
    server.use(
      http.get('/api/library/v2/import/status', () => {
        polls += 1;
        return HttpResponse.json(
          polls === 1
            ? { running: true, error: null }
            : { running: false, error: 'Importer failed safely' },
        );
      }),
    );

    await expect(waitForLibraryV2Import(3, 0)).resolves.toMatchObject({
      running: false,
      error: 'Importer failed safely',
    });
    expect(polls).toBe(2);
  });
});
