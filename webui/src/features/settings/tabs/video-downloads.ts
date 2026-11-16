/**
 * The video side's downloads config (/api/video/downloads/config), the one
 * object video-settings.js loadDownloads filled the page from and
 * saveDownloads posted back whole: the Library tab's video folders (the
 * Folders card's Video panel) and the seeding, usenet and retry blocks in the
 * Sources tab's torrent and usenet panels. Each of them edits it here and the
 * whole of it goes back, as before (SETTINGS_PORT.md, video pass V2a).
 *
 * It loads once a page, the first time the video settings page or the Library
 * tab is shown, as loadSharedLibrary did; a failed load can be retried.
 */

import { useSyncExternalStore } from 'react';

import { onVideoSave, videoShown } from './video-shown';

export const DOWNLOADS_URL = '/api/video/downloads/config';

export type DownloadsConfig = Record<string, unknown>;
export type LoadState = 'idle' | 'loading' | 'loaded' | 'failed';

interface State {
  config: DownloadsConfig | null;
  status: LoadState;
  /** goes up with each load: fields typed in show the loaded value again */
  loads: number;
}

const asText = (v: unknown) => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '');
const num = (v: unknown) => (typeof v === 'number' ? v : parseFloat(asText(v)));

/** settings.js saveDownloads' body, from the config as it is now. The chain
 *  (download_mode, hybrid_order) is not sent: the download chain owns it */
export function downloadsBody(c: DownloadsConfig) {
  const str = (k: string) => asText(c[k]);
  const body: Record<string, unknown> = {
    download_path: str('download_path'),
    movies_path: str('movies_path'),
    tv_path: str('tv_path'),
    anime_path: str('anime_path'),
    movies_additional_paths: c.movies_additional_paths,
    tv_additional_paths: c.tv_additional_paths,
    youtube_path: str('youtube_path'),
    movies_4k_enabled: !!c.movies_4k_enabled,
    movies_4k_path: str('movies_4k_path'),
    tv_4k_enabled: !!c.tv_4k_enabled,
    tv_4k_path: str('tv_4k_path'),
    seed_ratio_goal: num(c.seed_ratio_goal) || 0,
    seed_time_goal_hours: parseInt(asText(c.seed_time_goal_hours), 10) || 0,
    seed_remove_data: c.seed_remove_data !== false,
    seed_mode: str('seed_mode') || 'soulsync',
    seed_overrides: c.seed_overrides || {},
    usenet_remove_completed: str('usenet_remove_completed') || 'off',
    redownload_failed: c.redownload_failed !== false,
  };
  // the extra folders: their list arrived with the load, else they are left alone
  for (const k of ['movies_additional_paths', 'tv_additional_paths'])
    if (!Array.isArray(body[k])) delete body[k];
  return body;
}

function createVideoDownloads() {
  let state: State = { config: null, status: 'idle', loads: 0 };
  let loading: Promise<boolean> | null = null;
  const listeners = new Set<() => void>();
  const set = (next: Partial<State>) => {
    state = { ...state, ...next };
    for (const listener of listeners) listener();
  };

  async function load(): Promise<boolean> {
    if (state.status === 'loaded') return true;
    if (loading) return loading;
    set({ status: 'loading' });
    loading = (async () => {
      try {
        const r = await fetch(DOWNLOADS_URL, { headers: { Accept: 'application/json' } });
        const d = r.ok ? ((await r.json()) as DownloadsConfig | null) : null;
        if (!d) {
          set({ status: 'failed' });
          return false;
        }
        const config = { ...d };
        for (const k of ['movies_additional_paths', 'tv_additional_paths'])
          if (!Array.isArray(config[k])) config[k] = [];
        set({ config, status: 'loaded', loads: state.loads + 1 });
        return true;
      } catch {
        set({ status: 'failed' });
        return false;
      } finally {
        loading = null;
      }
    })();
    return loading;
  }

  async function save(silent = true): Promise<void> {
    if (!state.config) return;
    try {
      await fetch(DOWNLOADS_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(downloadsBody(state.config)),
      });
      if (!silent)
        (window as Window & { showToast?: (m: string, t: string) => void }).showToast?.(
          'Download folders saved',
          'success',
        );
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
    /** some keys changed, without saving (a field still being typed in) */
    edit: (values: DownloadsConfig) => {
      if (state.config) set({ config: { ...state.config, ...values } });
    },
    /** some keys changed, and the whole config saved */
    change: (values: DownloadsConfig) => {
      if (!state.config) return Promise.resolve();
      set({ config: { ...state.config, ...values } });
      return save();
    },
    /** for tests */
    reset: () => {
      state = { config: null, status: 'idle', loads: 0 };
      loading = null;
    },
  };
}

export const videoDownloads = createVideoDownloads();
export type VideoDownloads = typeof videoDownloads;

export function useVideoDownloads(model: VideoDownloads = videoDownloads) {
  return useSyncExternalStore(model.subscribe, model.get);
}

// loads the first time the video settings page or the Library tab is shown
// (loadSharedLibrary's two triggers), and the video side's save button saves
// it too, as it did
if (typeof document !== 'undefined') {
  let seen = videoShown.get();
  videoShown.subscribe(() => {
    if (videoShown.get() === seen) return;
    seen = videoShown.get();
    void videoDownloads.load();
  });
  document.addEventListener('soulsync:library-settings-shown', () => void videoDownloads.load());
  onVideoSave(() => videoDownloads.save());
}
