/**
 * react's view of "what can I do" (#2166, piece F).
 *
 * the live copy is the shell bundle's (src/shell/my-permissions.ts) as
 * window.SoulSyncPerms, because vanilla pages and css read it too. importing
 * that module here would give react its own copy, so this reads through
 * window and re-renders on its change event, the same way ../features.ts
 * reads window.SoulSyncFeatures.
 *
 * no shell (a test, a page opened on its own) means the old columns answer:
 * canDownload() from init.js, else everything.
 */

import { useEffect, useState, useSyncExternalStore } from 'react';

import {
  type MyPermissions,
  type PermMedia,
  type PermMode,
  type RequestLimit,
  fallbackPermissions,
  hasAccess,
  isModerator,
  isOn,
  modeFor,
  requestLimit,
  seesAllRequests,
  loadAdminName,
  asksLeftLine,
} from './my-permissions-core';

export type { MyPermissions, PermMedia, PermMode, RequestLimit } from './my-permissions-core';
export {
  GET_OFF_TITLES,
  OFF_TITLES,
  asksLeftLine,
  offTitle,
  limitSpan,
  refusalMessage,
  stillYours,
  loadAdminName,
  _resetAdminName,
  describePermissions,
} from './my-permissions-core';
export type { CanDo, CanDoRow } from './my-permissions-core';
export { hasAccess as hasAccessTo } from './my-permissions-core';

export const PERMS_CHANGED_EVENT = 'ss:perms-changed';
const PROFILE_CONTEXT_CHANGED_EVENT = 'ss:webui-profile-context-changed';

type Win = Window & {
  canDownload?: () => boolean;
  getCurrentProfileContext?: () => { profileId: number; isAdmin: boolean } | null;
};

// one fallback object per answer, so useSyncExternalStore sees a stable value
let fallbackKey = '';
let fallbackValue: MyPermissions | null = null;

function fallback(): MyPermissions {
  const w = window as Win;
  let ctx: { profileId: number; isAdmin: boolean } | null = null;
  try {
    ctx = w.getCurrentProfileContext?.() ?? null;
  } catch {
    ctx = null;
  }
  const can = typeof w.canDownload === 'function' ? w.canDownload() !== false : true;
  const key = `${ctx?.profileId ?? ''}:${ctx?.isAdmin ? 1 : 0}:${can ? 1 : 0}`;
  if (key !== fallbackKey || !fallbackValue) {
    fallbackKey = key;
    fallbackValue = fallbackPermissions({
      profileId: ctx?.profileId ?? null,
      isAdmin: !!ctx?.isAdmin,
      canDownload: can,
    });
  }
  return fallbackValue;
}

/** the current answer, read at click time */
export function myPermissions(): MyPermissions {
  return window.SoulSyncPerms?.get() ?? fallback();
}

export function permMode(media: PermMedia): PermMode {
  return modeFor(myPermissions(), media);
}

export function permOn(id: string): boolean {
  return isOn(myPermissions(), id);
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(PERMS_CHANGED_EVENT, onChange);
  window.addEventListener(PROFILE_CONTEXT_CHANGED_EVENT, onChange);
  return () => {
    window.removeEventListener(PERMS_CHANGED_EVENT, onChange);
    window.removeEventListener(PROFILE_CONTEXT_CHANGED_EVENT, onChange);
  };
}

/** the whole answer; re-renders when an admin changes it or the profile switches */
export function useMyPermissions(): MyPermissions {
  return useSyncExternalStore(subscribe, myPermissions, myPermissions);
}

/** download, ask or none for one kind of media */
export function usePermMode(media: PermMedia): PermMode {
  const perms = useMyPermissions();
  return modeFor(perms, media);
}

export function usePermOn(id: string): boolean {
  return isOn(useMyPermissions(), id);
}

export function useHasAccess(media: PermMedia): boolean {
  return hasAccess(useMyPermissions(), media);
}

export function useRequestLimit(): RequestLimit | null {
  return requestLimit(useMyPermissions());
}

/** what the Requests page may show and do for this profile */
export function useRequestRights(): { moderate: boolean; seeAll: boolean; isAdmin: boolean } {
  const perms = useMyPermissions();
  return {
    isAdmin: perms.isAdmin,
    moderate: perms.isAdmin || isModerator(perms),
    seeAll: perms.isAdmin || seesAllRequests(perms),
  };
}

/** after a 403 not_allowed: the map is out of date, fetch it now */
export function refreshMyPermissions(): void {
  void window.SoulSyncPerms?.load({ force: true });
}

export function useAdminName(): string {
  const [name, setName] = useState('');
  useEffect(() => {
    let live = true;
    void loadAdminName().then((n) => {
      if (live) setName(n);
    });
    return () => {
      live = false;
    };
  }, []);
  return name;
}

/** "3 of 5 asks left this week" for a profile that asks first for music,
 *  '' otherwise (no limit, downloads right away, or can't ask) */
export function useMusicAsksLeft(): string {
  const mode = usePermMode('music');
  const limited = useRequestLimit() !== null;
  const [line, setLine] = useState('');
  useEffect(() => {
    // before the server's map is in, the columns can't say whether there's
    // a limit, so a profile that asks still checks
    if (mode !== 'ask' || (myPermissions().live && !limited)) {
      setLine('');
      return;
    }
    let live = true;
    fetch('/api/requests/music/quota', { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : null))
      .then(
        (d: { quota?: { limit?: number; remaining?: number; days?: number } | null } | null) => {
          if (!live) return;
          const q = d?.quota;
          setLine(
            q
              ? asksLeftLine({
                  limit: Number(q.limit) || 0,
                  remaining: Number(q.remaining) || 0,
                  days: Number(q.days) || 7,
                })
              : '',
          );
        },
      )
      .catch(() => {
        if (live) setLine('');
      });
    return () => {
      live = false;
    };
  }, [mode, limited]);
  return line;
}
