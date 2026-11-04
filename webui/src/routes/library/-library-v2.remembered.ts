/**
 * The last artists page this browser saw, so a fresh load paints its cards at
 * once instead of a blank page for the 1-2s the list takes to build. It's
 * only ever a first paint: the query treats it as stale and refetches
 * straight away, so the real list replaces it within that same request.
 *
 * One entry, keyed by profile and query: another profile on this browser
 * never sees it, and nothing piles up.
 */

import { getShellProfileContext } from '@/platform/shell/bridge';

const STORAGE_KEY = 'soulsync.library.artistsPage.v1';
const MAX_BYTES = 400_000;

interface Remembered<T> {
  profileId: number;
  key: string;
  data: T;
}

function profileId(): number | null {
  try {
    return getShellProfileContext()?.profileId ?? null;
  } catch {
    return null;
  }
}

export function recallPage<T>(queryKey: readonly unknown[]): T | undefined {
  const pid = profileId();
  if (pid == null) return undefined;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return undefined;
    const saved = JSON.parse(raw) as Remembered<T>;
    return saved.profileId === pid && saved.key === JSON.stringify(queryKey)
      ? saved.data
      : undefined;
  } catch {
    return undefined;
  }
}

export function rememberPage<T>(queryKey: readonly unknown[], data: T): void {
  const pid = profileId();
  if (pid == null) return;
  try {
    const raw = JSON.stringify({ profileId: pid, key: JSON.stringify(queryKey), data });
    if (raw.length > MAX_BYTES) return;
    window.localStorage.setItem(STORAGE_KEY, raw);
  } catch {
    // private mode or a full store: no first paint, nothing else changes
  }
}
