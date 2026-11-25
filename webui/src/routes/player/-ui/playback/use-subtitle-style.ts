/**
 * how subtitles look on this screen: their size, and a dark box behind
 * them or none. a viewer's own taste, kept in this browser; every open
 * player follows a change made in any of them.
 */

import { useSyncExternalStore } from 'react';

export type SubtitleSize = 'small' | 'medium' | 'large';

export interface SubtitleStyle {
  size: SubtitleSize;
  box: boolean;
}

const KEY = 'ss-player:subtitle-style';
const DEFAULT: SubtitleStyle = { size: 'medium', box: true };

function read(): SubtitleStyle {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Partial<SubtitleStyle>;
    return {
      size: raw.size === 'small' || raw.size === 'large' ? raw.size : 'medium',
      box: raw.box !== false,
    };
  } catch {
    return DEFAULT;
  }
}

let current: SubtitleStyle | null = null;
const listeners = new Set<() => void>();

function snapshot(): SubtitleStyle {
  current ??= read();
  return current;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setSubtitleStyle(next: Partial<SubtitleStyle>) {
  current = { ...snapshot(), ...next };
  try {
    localStorage.setItem(KEY, JSON.stringify(current));
  } catch {
    /* private window: it lasts the visit */
  }
  for (const l of listeners) l();
}

export function useSubtitleStyle(): SubtitleStyle {
  return useSyncExternalStore(subscribe, snapshot, () => DEFAULT);
}
