import type { ResponsePromise } from 'ky';

import ky, { HTTPError } from 'ky';

const apiBaseUrl =
  typeof globalThis.location === 'object'
    ? new URL('/api/', globalThis.location.origin).toString()
    : 'http://localhost/api/';

export const apiClient = ky.create({
  baseUrl: apiBaseUrl,
  retry: 0,
});

export async function readJson<T>(promise: ResponsePromise<T>): Promise<T> {
  try {
    return await promise.json<T>();
  } catch (error) {
    if (error instanceof HTTPError) {
      error.message = getHttpErrorMessage(error.data, error.message);
    }

    throw error;
  }
}

type JsonErrorPayload = {
  error?: unknown;
  message?: unknown;
};

function getHttpErrorMessage(data: unknown, fallback: string): string {
  if (typeof data === 'string' && data.trim()) return data;
  if (!data || typeof data !== 'object') return fallback;

  const payload = data as JsonErrorPayload;
  if (typeof payload.error === 'string' && payload.error.trim()) return payload.error;
  if (typeof payload.message === 'string' && payload.message.trim()) return payload.message;

  return fallback;
}
