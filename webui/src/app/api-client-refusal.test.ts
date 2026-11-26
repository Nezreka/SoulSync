import ky from 'ky';
import { describe, expect, it } from 'vitest';

import { refusalText } from './api-client';

function refusing(body: unknown, status = 400) {
  const client = ky.create({
    retry: 0,
    fetch: async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'content-type': 'application/json' },
      }),
  });
  return client.put('http://localhost/api/player/setup', { json: {} });
}

describe('refusalText', () => {
  it("shows the server's reason for a refused save", async () => {
    const e = await refusing({ success: false, error: 'two libraries are called Movies' }).catch(
      (x) => x,
    );
    expect(await refusalText(e, 'Could not save.')).toBe('two libraries are called Movies');
  });

  it('prefers plain words over a code', async () => {
    const e = await refusing({ error: 'forbidden', message: 'Admin access required.' }, 403).catch(
      (x) => x,
    );
    expect(await refusalText(e, 'Could not save.')).toBe('Admin access required.');
  });

  it('falls back when there is nothing to say', async () => {
    expect(await refusalText(new Error('offline'), 'Could not save.')).toBe('Could not save.');
  });
});
