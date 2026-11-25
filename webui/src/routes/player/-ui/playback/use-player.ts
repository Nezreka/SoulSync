/**
 * the react side of the player core: one engine for the page, its state as
 * a hook, and the one bit of UI state that isn't the engine's (is the video
 * full screen, or tucked into the bar).
 */

import { useSyncExternalStore } from 'react';

import type { PlayOptions, PlayerState } from '../../-engine/engine';
import type { PlayItem } from '../../-engine/types';

import { browserDeps } from '../../-engine/browser';
import { PlayerEngine } from '../../-engine/engine';
import { MEDIA_KIND } from '../../-engine/types';

let engine: PlayerEngine | null = null;

/** the page's one engine, made the first time anything plays or asks */
export function getEngine(): PlayerEngine {
  if (!engine) {
    engine = new PlayerEngine(browserDeps());
    // the page going away or out of sight: save where we are now
    const save = () => engine?.saveNow();
    document.addEventListener('visibilitychange', () => document.hidden && save());
    window.addEventListener('pagehide', save);
  }
  return engine;
}

export function usePlayer(): PlayerState {
  const e = getEngine();
  return useSyncExternalStore(e.subscribe, e.getState, e.getState);
}

// ── the video view: full screen, or in the bar ─────────────────────────

let videoOpen = false;
const viewListeners = new Set<() => void>();
const setVideoOpen = (open: boolean) => {
  if (videoOpen === open) return;
  videoOpen = open;
  for (const l of viewListeners) l();
};

export function useVideoOpen(): [boolean, (open: boolean) => void] {
  const open = useSyncExternalStore(
    (l) => {
      viewListeners.add(l);
      return () => viewListeners.delete(l);
    },
    () => videoOpen,
    () => false,
  );
  return [open, setVideoOpen];
}

/** play these from ``start``; a video opens full screen */
export function playItems(items: PlayItem[], start = 0, options?: PlayOptions) {
  if (!items.length) return;
  getEngine().playItems(items, start, options);
  const first = items[Math.min(start, items.length - 1)];
  setVideoOpen(MEDIA_KIND[first.type] === 'video');
}
