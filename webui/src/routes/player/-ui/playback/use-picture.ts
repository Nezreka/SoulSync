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

/** the dimming layer is black at 90%: this strength of it leaves the picture at ``v`` */
const LAYER = 0.9;

/** how strong the layer over the picture is (0: none, at full brightness) */
export function dimOpacity(v: number): number {
  return v < 1 ? Math.min(1, Math.round(((1 - v) / LAYER) * 100) / 100) : 0;
}
