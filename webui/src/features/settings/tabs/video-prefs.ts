/**
 * The video side's detail page preferences (SETTINGS_PORT.md, video pass
 * V2c): autoplay in the billboard and the where-to-watch region, two keys of
 * the enrichment config (/api/video/enrichment/config) the rest of which the
 * Connections tab's video cards still own. video-settings.js read them with
 * the API keys each time the video settings page was shown, and posted the
 * two of them alone; so does this.
 *
 * The page never read them on the music side, so a change made there posted
 * the defaults over what was saved: they load the first time the Library tab
 * is shown now too.
 */

import { useSyncExternalStore } from 'react';

import { onVideoSave, videoShown } from './video-shown';

export const CONFIG_URL = '/api/video/enrichment/config';

interface State {
  autoplay: boolean;
  region: string;
}

const toast = (message: string, type: string) =>
  (window as Window & { showToast?: (m: string, t: string) => void }).showToast?.(message, type);

function createVideoPrefs() {
  // the markup's own: unticked, the first region
  let state: State = { autoplay: false, region: 'US' };
  const listeners = new Set<() => void>();
  const set = (next: Partial<State>) => {
    state = { ...state, ...next };
    for (const listener of listeners) listener();
  };

  async function load() {
    try {
      const r = await fetch(CONFIG_URL, { headers: { Accept: 'application/json' } });
      const d = r.ok
        ? ((await r.json()) as { billboard_autoplay?: unknown; watch_region?: unknown } | null)
        : null;
      if (!d) return;
      const next: Partial<State> = {};
      if (d.billboard_autoplay != null) next.autoplay = !!d.billboard_autoplay;
      if (typeof d.watch_region === 'string' && d.watch_region) next.region = d.watch_region;
      set(next);
    } catch {
      // ignore
    }
  }

  async function save(silent = false) {
    try {
      await fetch(CONFIG_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ billboard_autoplay: state.autoplay, watch_region: state.region }),
      });
      if (!silent) toast('Preferences saved', 'success');
    } catch {
      // ignore
    }
  }

  return {
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    get: () => state,
    load,
    save,
    change: (next: Partial<State>) => {
      set(next);
      return save();
    },
    /** for tests */
    reset: () => {
      state = { autoplay: false, region: 'US' };
    },
  };
}

export const videoPrefs = createVideoPrefs();

export function useVideoPrefs() {
  return useSyncExternalStore(videoPrefs.subscribe, videoPrefs.get);
}

if (typeof document !== 'undefined') {
  // each time the video settings page is shown, as loadKeys did
  let seen = videoShown.get();
  videoShown.subscribe(() => {
    if (videoShown.get() === seen) return;
    seen = videoShown.get();
    void videoPrefs.load();
  });
  let library = false;
  document.addEventListener('soulsync:library-settings-shown', () => {
    if (library) return;
    library = true;
    void videoPrefs.load();
  });
  // the video side's save button posts them too, quietly
  onVideoSave(() => videoPrefs.save(true));
}
