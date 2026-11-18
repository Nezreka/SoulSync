/**
 * a fake window.SoulSyncPerms for tests (#2166, piece F): the shell's store
 * answering with this map, the way src/shell/my-permissions.ts would once
 * GET /api/profiles/me/permissions landed.
 */

import {
  type MyPermissions,
  hasAccess,
  isOn,
  modeFor,
  requestLimit,
} from '@/platform/shell/my-permissions-core';

export const PERMS_CHANGED_EVENT = 'ss:perms-changed';

/** an Adult member: everything on, downloads right away, no limit */
export const ADULT: Record<string, unknown> = {
  'music.access': true,
  'music.get': 'download',
  'music.hide_explicit': false,
  'movies.access': true,
  'movies.get': 'download',
  'movies.max_rating': 'any',
  'tv.access': true,
  'tv.get': 'download',
  'tv.max_rating': 'follow',
  'books.access': true,
  'books.get': 'download',
  'podcasts.access': true,
  'podcasts.get': 'download',
  'requests.limit': { n: 0, days: 7 },
  'requests.see_all': false,
  'requests.moderate': false,
  'account.connect': true,
  'chat.use': true,
  'automations.personal': true,
  'music.discover': true,
  'music.import': true,
  'music.sample_studio': true,
};

/** Teen: asks first for music and video, PG-13, 5 asks a week, no books */
export const TEEN: Record<string, unknown> = {
  ...ADULT,
  'music.get': 'ask',
  'movies.get': 'ask',
  'movies.max_rating': 'PG-13',
  'tv.get': 'ask',
  'books.get': 'none',
  'podcasts.get': 'none',
  'requests.limit': { n: 5, days: 7 },
};

/** can't request music at all */
export const LISTEN_ONLY: Record<string, unknown> = { ...ADULT, 'music.get': 'none' };

export const BOOKS_OFF: Record<string, unknown> = {
  ...ADULT,
  'books.access': false,
  'books.get': 'none',
};

export const MODERATOR: Record<string, unknown> = {
  ...TEEN,
  'requests.moderate': true,
  'requests.see_all': true,
};

export const SEE_ALL: Record<string, unknown> = { ...TEEN, 'requests.see_all': true };

export function permsOf(
  effective: Record<string, unknown>,
  opts: { isAdmin?: boolean; profileId?: number; live?: boolean } = {},
): MyPermissions {
  return {
    profileId: opts.profileId ?? 7,
    isAdmin: opts.isAdmin ?? false,
    source: opts.isAdmin ? 'admin' : 'template',
    effective,
    summary: null,
    live: opts.live ?? true,
  };
}

/** install (or swap) the fake store and tell every listener */
export function stubPerms(
  effective: Record<string, unknown>,
  opts: { isAdmin?: boolean; profileId?: number } = {},
): MyPermissions {
  const perms = permsOf(effective, opts);
  window.SoulSyncPerms = {
    get: () => perms,
    load: async () => perms,
    mode: (media) => modeFor(perms, media),
    on: (id) => isOn(perms, id),
    access: (media) => hasAccess(perms, media),
    limit: () => requestLimit(perms),
    live: () => true,
  };
  window.dispatchEvent(new CustomEvent(PERMS_CHANGED_EVENT, { detail: perms }));
  return perms;
}

export function clearPerms(): void {
  Reflect.deleteProperty(window, 'SoulSyncPerms');
}
