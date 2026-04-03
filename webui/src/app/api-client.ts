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

export async function parseJsonResponse<T>(promise: ResponsePromise<T>): Promise<T> {
  try {
    return (await promise.json()) as T;
  } catch (error) {
    if (error instanceof HTTPError) {
      const payload = (await error.response.json().catch(() => null)) as { error?: string } | null;

      throw new Error(payload?.error || `Request failed with status ${error.response.status}`);
    }

    throw error;
  }
}
