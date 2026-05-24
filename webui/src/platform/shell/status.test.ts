import { describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { fetchShellStatus } from './status';

describe('shell status', () => {
  it('fetches the shell status payload', async () => {
    server.use(
      http.get('/status', () =>
        HttpResponse.json({ media_server: { type: 'plex', connected: true } }),
      ),
    );

    await expect(fetchShellStatus()).resolves.toEqual({
      media_server: { type: 'plex', connected: true },
    });
  });
});
