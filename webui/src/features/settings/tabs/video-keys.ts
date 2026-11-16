/**
 * The video side's enrichment keys and switches (/api/video/enrichment/config),
 * the API Configuration cards on the Connections tab (SETTINGS_PORT.md, video
 * pass V3a). video-settings.js loadKeys read them each time the video settings
 * page was shown and saveKeys posted all of them when one changed (a key when
 * its field was left, a switch at once); so does this. Autoplay and the region
 * share the endpoint and are video-prefs.ts.
 */

import { useSyncExternalStore } from 'react';

import { onVideoSave, videoShown } from './video-shown';

export const CONFIG_URL = '/api/video/enrichment/config';

/** the key fields: the card, the config key */
export const KEY_FIELDS = [
  'tmdb',
  'tvdb',
  'omdb',
  'fanart',
  'opensubtitles',
  'subdl',
  'subsource',
  'trakt',
  'mdblist',
] as const;
/** the keyless switches, ticked in the markup until the config says */
export const SWITCHES = [
  'tvmaze',
  'anilist',
  'wikidata',
  'ryd',
  'sponsorblock',
  'dearrow',
] as const;

export type KeyField = (typeof KEY_FIELDS)[number];
export type Switch = (typeof SWITCHES)[number];

export interface Keys {
  keys: Record<KeyField, string>;
  on: Record<Switch, boolean>;
}

const blank = (): Keys => ({
  keys: Object.fromEntries(KEY_FIELDS.map((k) => [k, ''])) as Record<KeyField, string>,
  on: Object.fromEntries(SWITCHES.map((k) => [k, true])) as Record<Switch, boolean>,
});

/** saveKeys' body */
export function keysBody(s: Keys) {
  return {
    ...Object.fromEntries(KEY_FIELDS.map((k) => [`${k}_api_key`, s.keys[k]])),
    ...Object.fromEntries(SWITCHES.map((k) => [`${k}_enabled`, s.on[k]])),
  };
}

const toast = (message: string, type: string) =>
  (window as Window & { showToast?: (m: string, t: string) => void }).showToast?.(message, type);

function createVideoKeys() {
  let state = blank();
  const listeners = new Set<() => void>();
  const set = (next: Keys) => {
    state = next;
    for (const listener of listeners) listener();
  };

  async function load() {
    try {
      const r = await fetch(CONFIG_URL, { headers: { Accept: 'application/json' } });
      const d = r.ok ? ((await r.json()) as Record<string, unknown> | null) : null;
      if (!d) return;
      // a key the config does not name keeps what the field held
      const keys = { ...state.keys };
      for (const k of KEY_FIELDS) {
        const v = d[`${k}_api_key`];
        if (v != null) keys[k] = typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '';
      }
      const on = { ...state.on };
      for (const k of SWITCHES) {
        const v = d[`${k}_enabled`];
        if (v != null) on[k] = !!v;
      }
      set({ keys, on });
    } catch {
      // ignore
    }
  }

  async function save(silent = false) {
    try {
      await fetch(CONFIG_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(keysBody(state)),
      });
      if (!silent) toast('API keys saved', 'success');
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
    /** a key typed, not saved until the field is left */
    type: (k: KeyField, value: string) => set({ ...state, keys: { ...state.keys, [k]: value } }),
    /** a switch flipped: saved at once */
    flip: (k: Switch, value: boolean) => {
      set({ ...state, on: { ...state.on, [k]: value } });
      return save();
    },
    /** for tests */
    reset: () => {
      state = blank();
    },
  };
}

export const videoKeys = createVideoKeys();
export type VideoKeys = typeof videoKeys;

export function useVideoKeys() {
  return useSyncExternalStore(videoKeys.subscribe, videoKeys.get);
}

// ── Test: save the keys, then each service's test endpoint ──

export const TEST_NAMES: Record<string, string> = {
  tmdb: 'TMDB',
  tvdb: 'TVDB',
  omdb: 'OMDb',
  fanart: 'fanart.tv',
  opensubtitles: 'OpenSubtitles',
  subdl: 'SubDL',
  subsource: 'SubSource',
  trakt: 'Trakt',
  mdblist: 'MDBList',
  tvmaze: 'TVmaze',
  anilist: 'AniList',
  wikidata: 'Wikidata',
  ryd: 'Return YouTube Dislike',
  sponsorblock: 'SponsorBlock',
  dearrow: 'DeArrow',
};

interface Result {
  success?: boolean;
  message?: string;
  error?: string;
}

async function testOne(svc: string): Promise<Result> {
  try {
    const r = await fetch(`/api/video/enrichment/${svc}/test`, {
      method: 'POST',
      headers: { Accept: 'application/json' },
    });
    return (await r.json()) as Result;
  } catch {
    return { success: false, error: 'request failed' };
  }
}

/** video-settings.js testConnection. a keyless card holds several services
 *  behind one button ("tvmaze,anilist,wikidata"): only the ones switched on
 *  are tested, a service you turned off isn't broken */
export async function testVideoServices(spec: string, model: VideoKeys = videoKeys) {
  let svcs = String(spec || '')
    .split(',')
    .filter(Boolean);
  if (svcs.length > 1) {
    const on = model.get().on as Record<string, boolean>;
    svcs = svcs.filter((s) => on[s] !== false);
    if (!svcs.length) {
      toast('Nothing to test, all of these are switched off', 'info');
      return;
    }
  }
  const names = svcs.map((s) => TEST_NAMES[s] || s.toUpperCase());
  toast(`Testing ${names.join(', ')}…`, 'info');
  await model.save(true);
  const results = await Promise.all(svcs.map(testOne));
  if (results.length === 1) {
    const res = results[0];
    if (res && res.success) toast(res.message || `${names[0]} connection successful`, 'success');
    else toast(`${names[0]} connection failed: ${(res && res.error) || 'unknown'}`, 'error');
    return;
  }
  const bad: string[] = [];
  results.forEach((res, i) => {
    if (!(res && res.success)) bad.push(`${names[i]}: ${(res && res.error) || 'unknown'}`);
  });
  if (!bad.length) toast(`${names.join(', ')} all reachable`, 'success');
  else toast(`Failed: ${bad.join(' · ')}`, 'error');
}

if (typeof document !== 'undefined') {
  // each time the video settings page is shown, as loadKeys did
  let seen = videoShown.get();
  videoShown.subscribe(() => {
    if (videoShown.get() === seen) return;
    seen = videoShown.get();
    void videoKeys.load();
  });
  // the video side's save button posts them too, quietly
  onVideoSave(() => videoKeys.save(true));
}
