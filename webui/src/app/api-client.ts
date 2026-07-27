import type { ResponsePromise } from 'ky';

import ky, { HTTPError } from 'ky';

const apiBaseUrl =
  typeof globalThis.location === 'object'
    ? new URL('/api/', globalThis.location.origin).toString()
    : 'http://localhost/api/';
const shellBaseUrl =
  typeof globalThis.location === 'object'
    ? new URL('/', globalThis.location.origin).toString()
    : 'http://localhost/';

export const apiClient = ky.create({
  baseUrl: apiBaseUrl,
  retry: 0,
});

export const shellClient = ky.create({
  baseUrl: shellBaseUrl,
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

function nonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

function getHttpErrorMessage(data: unknown, fallback: string): string {
  const direct = nonEmptyString(data);
  if (direct) return direct;
  if (!data || typeof data !== 'object') return fallback;

  const payload = data as JsonErrorPayload;
  const error = nonEmptyString(payload.error);
  if (error) return error;
  if (payload.error && typeof payload.error === 'object') {
    const nested = nonEmptyString((payload.error as { message?: unknown }).message);
    if (nested) return nested;
  }
  const message = nonEmptyString(payload.message);
  if (message) return message;

  return fallback;
}
