import type { ResponsePromise } from 'ky';

import ky, { HTTPError } from 'ky';

import { appURL } from '@/platform/url-base';

const apiBaseUrl =
  typeof globalThis.location === 'object'
    ? new URL(appURL('/api/'), globalThis.location.origin).toString()
    : 'http://localhost/api/';
const shellBaseUrl =
  typeof globalThis.location === 'object'
    ? new URL(appURL('/'), globalThis.location.origin).toString()
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
  // a permission refusal (#2166) carries a code and plain words: the words
  // are what a person should read ("Audiobooks aren't part of your profile")
  if (payload.error === 'not_allowed') {
    const words = nonEmptyString(payload.message);
    if (words) return words;
  }
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

/** the words a refused request carries (ky reads the body into error.data
 * before our code sees it, so the response can't be read a second time) */
export async function refusalText(e: unknown, fallback: string): Promise<string> {
  if (e instanceof HTTPError) {
    let data: unknown = e.data;
    if (data === undefined) {
      try {
        data = await e.response.clone().json();
      } catch {
        data = undefined;
      }
    }
    if (data && typeof data === 'object') {
      const payload = data as JsonErrorPayload;
      const words = nonEmptyString(payload.message) ?? nonEmptyString(payload.error);
      if (words) return words;
    }
    return nonEmptyString(data) ?? fallback;
  }
  return fallback;
}
