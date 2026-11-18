/**
 * the one copy of "what can I do" on this page (#2166, piece F).
 *
 * loads GET /api/profiles/me/permissions once per profile, then keeps it
 * fresh: on every profile switch, when the tab comes back, and a light poll
 * while it's open, so an admin's change reaches an open page within a couple
 * of minutes without a reload. a server refusal (403 not_allowed) refreshes
 * it right away.
 *
 * everything else reads it, never the profile columns:
 *   - vanilla: window.SoulSyncPerms (mode, on, limit, get, load)
 *   - react: src/platform/shell/my-permissions.ts (hooks over the same object)
 *   - css: body[data-music-get|data-books-get|data-podcasts-get] =
 *     none | ask | download, and the old body.downloads-disabled class (kept
 *     for the code that still hides on it)
 *
 * a change fires PERMS_CHANGED_EVENT on window.
 */

import {
  type MePermissionsResponse,
  type MyPermissions,
  type PermMedia,
  type PermMode,
  type RequestLimit,
  fallbackPermissions,
  fingerprint,
  fromResponse,
  hasAccess,
  isOn,
  modeFor,
  requestLimit,
} from '@/platform/shell/my-permissions-core';

export const PERMS_CHANGED_EVENT = 'ss:perms-changed';
const PROFILE_CONTEXT_CHANGED_EVENT = 'ss:webui-profile-context-changed';
const URL = '/api/profiles/me/permissions';
/** a light poll: an admin's change shows up without a reload */
export const POLL_MS = 120_000;
/** coming back to the tab refreshes, at most this often */
const REFOCUS_MS = 20_000;

type Win = Window & {
  canDownload?: () => boolean;
  getCurrentProfileContext?: () => { profileId: number; isAdmin: boolean } | null;
};

interface State {
  perms: MyPermissions;
  loadedAt: number;
  inflight: Promise<MyPermissions> | null;
  /** bumps on a profile switch, so a slow answer for the old profile is dropped */
  generation: number;
}

function columnsAnswer(): MyPermissions {
  const w = window as Win;
  let ctx: { profileId: number; isAdmin: boolean } | null = null;
  try {
    ctx = w.getCurrentProfileContext?.() ?? null;
  } catch {
    ctx = null;
  }
  let can = true;
  try {
    can = typeof w.canDownload === 'function' ? w.canDownload() !== false : true;
  } catch {
    can = true;
  }
  return fallbackPermissions({
    profileId: ctx?.profileId ?? null,
    isAdmin: !!ctx?.isAdmin,
    canDownload: can,
  });
}

const state: State = {
  perms: fallbackPermissions({ profileId: null, isAdmin: false, canDownload: true }),
  loadedAt: 0,
  inflight: null,
  generation: 0,
};

/** the page-level hooks: body attributes css and old code read */
export function applyBodyState(
  perms: MyPermissions,
  body: HTMLElement | null = document.body,
): void {
  if (!body) return;
  const music = modeFor(perms, 'music');
  body.dataset.musicGet = music;
  body.dataset.booksGet = modeFor(perms, 'books');
  body.dataset.podcastsGet = modeFor(perms, 'podcasts');
  // init.js sets this from the profile columns; once the server's map is in,
  // it follows the map (an admin's change lands without a reload)
  if (perms.live) body.classList.toggle('downloads-disabled', music !== 'download');
}

function set(next: MyPermissions): void {
  const changed = fingerprint(next) !== fingerprint(state.perms);
  state.perms = next;
  applyBodyState(next);
  if (changed) {
    window.dispatchEvent(new CustomEvent(PERMS_CHANGED_EVENT, { detail: next }));
  }
}

export function getMyPermissions(): MyPermissions {
  return state.perms;
}

/** ask the server. keeps the last good answer when it can't (a 503 or a
 *  network blip): the server refuses on its own anyway. */
export function loadMyPermissions(opts: { force?: boolean } = {}): Promise<MyPermissions> {
  if (state.inflight && !opts.force) return state.inflight;
  const generation = state.generation;
  const run = (async () => {
    let body: MePermissionsResponse | null = null;
    try {
      const res = await fetch(URL, { headers: { Accept: 'application/json' }, cache: 'no-store' });
      body = res.ok ? ((await res.json()) as MePermissionsResponse) : null;
    } catch {
      body = null;
    }
    if (generation !== state.generation) return state.perms;
    const live = fromResponse(body);
    state.loadedAt = Date.now();
    if (live) set(live);
    else if (!state.perms.live) set(columnsAnswer());
    return state.perms;
  })();
  state.inflight = run;
  void run.finally(() => {
    if (state.inflight === run) state.inflight = null;
  });
  return run;
}

/** a profile switch: forget the last profile's map at once (the columns
 *  answer the new profile until the server does), then ask */
export function onProfileChanged(): void {
  state.generation += 1;
  state.inflight = null;
  set(columnsAnswer());
  void loadMyPermissions({ force: true });
}

export interface SoulSyncPermsApi {
  get: () => MyPermissions;
  load: (opts?: { force?: boolean }) => Promise<MyPermissions>;
  mode: (media: PermMedia) => PermMode;
  on: (id: string) => boolean;
  access: (media: PermMedia) => boolean;
  limit: () => RequestLimit | null;
  /** the server's map is in for this profile */
  live: () => boolean;
}

declare global {
  interface Window {
    SoulSyncPerms?: SoulSyncPermsApi;
  }
}

let pollTimer: ReturnType<typeof setInterval> | null = null;

export function installMyPermissions(): void {
  window.SoulSyncPerms = {
    get: getMyPermissions,
    load: loadMyPermissions,
    mode: (media) => modeFor(state.perms, media),
    on: (id) => isOn(state.perms, id),
    access: (media) => hasAccess(state.perms, media),
    limit: () => requestLimit(state.perms),
    live: () => state.perms.live,
  };
  window.addEventListener(PROFILE_CONTEXT_CHANGED_EVENT, onProfileChanged);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    if (Date.now() - state.loadedAt < REFOCUS_MS) return;
    void loadMyPermissions();
  });
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    if (document.visibilityState === 'hidden') return;
    void loadMyPermissions();
  }, POLL_MS);
  // init.js announces the profile once it knows it; this covers a shell that
  // loads after that announcement already went out
  const start = () => {
    set(columnsAnswer());
    void loadMyPermissions();
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
}

/** tests only */
export function _resetMyPermissions(): void {
  state.perms = fallbackPermissions({ profileId: null, isAdmin: false, canDownload: true });
  state.loadedAt = 0;
  state.inflight = null;
  state.generation = 0;
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}
