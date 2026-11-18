/**
 * The video side's settings page being shown (video-side.js dispatches
 * soulsync:video-page-shown with the page id). video-settings.js loaded each
 * of its sections on it; the React video sections reload on the count going
 * up. Heard from the moment this module loads, so a section drawn after the
 * page was shown still knows it was.
 */

import { useSyncExternalStore } from 'react';

export const VIDEO_SETTINGS_PAGE = 'video-settings';

let shown = 0;
const listeners = new Set<() => void>();

/**
 * Load the video sections now: the video side's settings page was shown, or
 * the music side's Connections switched its services to Video, which draws
 * the same sections with nothing to fill them.
 */
export function markVideoShown() {
  shown += 1;
  for (const listener of listeners) listener();
}

function heard(event: Event) {
  if ((event as CustomEvent<string>).detail !== VIDEO_SETTINGS_PAGE) return;
  markVideoShown();
}
if (typeof document !== 'undefined') document.addEventListener('soulsync:video-page-shown', heard);

export const videoShown = {
  subscribe: (listener: () => void) => {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  get: () => shown,
};

/** how many times the video settings page has been shown */
export function useVideoShown() {
  return useSyncExternalStore(videoShown.subscribe, videoShown.get);
}

/**
 * The shared save button on the video side: video-settings.js flushed its
 * sections from a capture-phase click listener before the page save ran. Each
 * React video section adds what it saves here.
 */
const savers = new Set<() => Promise<unknown>>();
export function onVideoSave(save: () => Promise<unknown>) {
  savers.add(save);
  return () => {
    savers.delete(save);
  };
}
if (typeof document !== 'undefined')
  document.addEventListener(
    'click',
    (event) => {
      if (document.body.getAttribute('data-side') !== 'video') return;
      if (!(event.target as Element | null)?.closest?.('#save-settings')) return;
      Promise.all([...savers].map((save) => save())).catch(() =>
        (window as Window & { showToast?: (m: string, t: string) => void }).showToast?.(
          'Some video settings could not be saved',
          'error',
        ),
      );
    },
    true,
  );
