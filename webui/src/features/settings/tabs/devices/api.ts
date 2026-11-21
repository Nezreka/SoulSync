/**
 * Every route Settings > Devices reads and writes (api/devices.py).
 * Errors come back as values, never throws, so each part can say what
 * happened in words.
 */

import { appURL } from '@/platform/url-base';

export type Result<T> = { ok: true; data: T } | { ok: false; status: number; message: string };

const FALLBACK: Record<number, string> = {
  0: "Couldn't reach SoulSync. Check the connection and try again.",
  403: 'Devices are for admins.',
};

export async function call<T>(path: string, init?: RequestInit): Promise<Result<T>> {
  let response: Response;
  try {
    response = await fetch(appURL(path), {
      credentials: 'same-origin',
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      },
    });
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') throw error;
    return { ok: false, status: 0, message: FALLBACK[0] };
  }
  let body: Record<string, unknown> = {};
  try {
    body = ((await response.json()) ?? {}) as Record<string, unknown>;
  } catch {
    body = {};
  }
  if (!response.ok || body.success === false) {
    const message =
      (typeof body.error === 'string' && body.error) ||
      FALLBACK[response.status] ||
      "That didn't work.";
    return { ok: false, status: response.status, message };
  }
  return { ok: true, data: body as T };
}

export interface ServerIdentity {
  lookup_id: string;
  fingerprint: string;
  public_key: string;
}

export const getIdentity = (signal?: AbortSignal) =>
  call<ServerIdentity>('/api/devices/identity', { signal });
