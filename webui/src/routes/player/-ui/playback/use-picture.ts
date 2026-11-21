/**
 * the picture on this screen: how bright (dimmed for watching in bed). a
 * viewer's own taste, kept in this browser, followed by every open player.
 */

import { useSyncExternalStore } from 'react';

export const BRIGHTNESS_STEPS = [1, 0.8, 0.6, 0.45, 0.3, 0.15];

const KEY = 'ss-player:brightness';
let current: number | null = null;
const listeners = new Set<() => void>();

function read(): number {
  try {
    const v = Number(localStorage.getItem(KEY));
    return v >= 0.1 && v <= 1 ? v : 1;
  } catch {
    return 1;
  }
}

function snapshot(): number {
  current ??= read();
  return current;
}

export function setBrightness(v: number) {
  current = Math.min(1, Math.max(0.1, v));
  try {
    localStorage.setItem(KEY, String(current));
  } catch {
    /* private window: it lasts the visit */
  }
  for (const l of listeners) l();
}

export function useBrightness(): number {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    snapshot,
    () => 1,
  );
}

/** the css for it: nothing at full brightness */
export function brightnessFilter(v: number): string | undefined {
  return v < 1 ? `brightness(${v})` : undefined;
}
