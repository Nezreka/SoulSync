import type { ResponsePromise } from 'ky';

import { HTTPError } from 'ky';
import { describe, expect, it, vi } from 'vite-plus/test';

import { readJson } from './api-client';

function createHttpError(body: unknown, status = 400) {
  const response = new Response(JSON.stringify(body), {
    status,
    statusText: 'Bad Request',
    headers: {
      'Content-Type': 'application/json',
    },
  });
  const request = new Request('https://example.com/api/test');
  const error = new HTTPError(response, request, {} as any);
  error.data = body;
  return error;
}

describe('readJson', () => {
  it('returns parsed JSON', async () => {
    const json = vi.fn().mockResolvedValue({ ok: true });
    const promise = { json } as unknown as ResponsePromise<{ ok: boolean }>;

    await expect(readJson(promise)).resolves.toEqual({ ok: true });
    expect(json).toHaveBeenCalledTimes(1);
  });

  it('keeps HTTPError instances intact and uses the payload message', async () => {
    const error = createHttpError({ error: 'Nope' }, 403);
    const json = vi.fn().mockRejectedValue(error);
    const promise = { json } as unknown as ResponsePromise<unknown>;
    const result = readJson(promise);

    await expect(result).rejects.toBe(error);
    await expect(result).rejects.toHaveProperty('message', 'Nope');
  });

  it('falls back to the HTTPError message when the payload is unhelpful', async () => {
    const error = createHttpError({ detail: 'missing error field' }, 404);
    const json = vi.fn().mockRejectedValue(error);
    const promise = { json } as unknown as ResponsePromise<unknown>;
    const result = readJson(promise);

    await expect(result).rejects.toBe(error);
    await expect(result).rejects.toHaveProperty('message', error.message);
  });
});
