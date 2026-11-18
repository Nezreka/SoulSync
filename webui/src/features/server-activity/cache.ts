/**
 * What the drawer has already read, kept while the page is open. A tab opened
 * again, a range picked again or the drawer reopened paints from here at once
 * and asks the server again behind it (stale while revalidate). A load in
 * flight is shared, so the prefetch on open and the tab that mounts a moment
 * later make one request between them.
 */

import type { Result } from './api';

interface Entry {
  at: number;
  data: unknown;
}

const MAX_ENTRIES = 40;
const entries = new Map<string, Entry>();
const inflight = new Map<string, Promise<Result<unknown>>>();

export function peek<T>(key: string): { at: number; data: T } | undefined {
  const hit = entries.get(key);
  return hit ? { at: hit.at, data: hit.data as T } : undefined;
}

export function put<T>(key: string, data: T): void {
  entries.delete(key);
  entries.set(key, { at: Date.now(), data });
  while (entries.size > MAX_ENTRIES) {
    const oldest = entries.keys().next().value;
    if (oldest === undefined) break;
    entries.delete(oldest);
  }
}

/** one request per key at a time; a good answer is kept */
export function load<T>(key: string, loader: () => Promise<Result<T>>): Promise<Result<T>> {
  const running = inflight.get(key);
  if (running) return running as Promise<Result<T>>;
  const promise = loader()
    .then((result) => {
      if (result.ok && (result.data as { ok?: boolean } | null)?.ok !== false)
        put(key, result.data);
      return result;
    })
    .finally(() => inflight.delete(key));
  inflight.set(key, promise as Promise<Result<unknown>>);
  return promise;
}

/** tests start from nothing */
export function clearCache(): void {
  entries.clear();
  inflight.clear();
}
