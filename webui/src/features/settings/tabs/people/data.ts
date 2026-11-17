/**
 * What the People tab loads, once on the first open and again each time it
 * is opened, a profile modal closes, or a policy or template is saved. The
 * per-person summaries come from GET /api/profiles/<id>/permissions, one per
 * member, read side by side.
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';

import { SHELL_PROFILE_CONTEXT_CHANGED_EVENT } from '@/platform/shell/bridge';

import type { Catalogue, PermMap, Person, QualityOptions, Template } from './types';

import {
  getCatalogue,
  getInvites,
  getPersonPerms,
  getPolicy,
  getProfiles,
  getQualityOptions,
  getTemplates,
  getWaiting,
} from './api';
import { type PersonRow } from './model';

export interface PeopleData {
  catalogue: Catalogue;
  quality: QualityOptions | null;
  policy: PermMap;
  templates: Template[];
  signupTemplateId: number | null;
  people: PersonRow[];
  waiting: number | null;
  /** open invite links, for the pill; null when they couldn't be read */
  openInvites: number | null;
}

export type LoadState =
  | { status: 'idle' | 'loading' }
  | { status: 'error'; message: string; forbidden: boolean }
  | { status: 'ready'; data: PeopleData };

export async function loadPeople(signal?: AbortSignal): Promise<LoadState> {
  const [catalogue, quality, policy, templates, profiles, waiting, invites] = await Promise.all([
    getCatalogue(signal),
    getQualityOptions(signal),
    getPolicy(signal),
    getTemplates(signal),
    getProfiles(signal),
    getWaiting(signal).catch(() => null),
    getInvites(signal),
  ]);
  for (const r of [catalogue, policy, templates, profiles]) {
    if (!r.ok) return { status: 'error', message: r.message, forbidden: r.status === 403 };
  }
  if (!catalogue.ok || !policy.ok || !templates.ok || !profiles.ok) {
    return { status: 'error', message: "Couldn't load people.", forbidden: false };
  }
  const list: Person[] = profiles.data.profiles || [];
  const perms = await Promise.all(
    list.map((p) =>
      p.is_admin || p.id === 1 ? Promise.resolve(null) : getPersonPerms(p.id, signal),
    ),
  );
  const people: PersonRow[] = list.map((person, i) => {
    const r = perms[i];
    return { person, perms: r && r.ok ? r.data : null, failed: !!r && !r.ok };
  });
  return {
    status: 'ready',
    data: {
      catalogue: catalogue.data,
      quality: quality.ok ? quality.data : null,
      policy: policy.data.policy,
      templates: templates.data.templates,
      signupTemplateId: templates.data.signup_template_id,
      people,
      waiting,
      openInvites: invites.ok
        ? (invites.data.invites || []).filter((i) => i.state === 'open').length
        : null,
    },
  };
}

/** the data, plus reload(); a reload keeps the old data on screen while it runs */
export function usePeopleData(enabled: boolean, opened: number) {
  const [state, setState] = useState<LoadState>({ status: 'idle' });
  const ctrl = useRef<AbortController | null>(null);
  const reload = useCallback(async () => {
    ctrl.current?.abort();
    const mine = new AbortController();
    ctrl.current = mine;
    setState((s) => (s.status === 'ready' ? s : { status: 'loading' }));
    try {
      const next = await loadPeople(mine.signal);
      if (!mine.signal.aborted) setState(next);
    } catch {
      // left the tab first
    }
  }, []);
  useEffect(() => {
    if (enabled && opened > 0) void reload();
  }, [enabled, opened, reload]);
  useEffect(() => () => ctrl.current?.abort(), []);
  return { state, reload, setState };
}

// ── who is looking ──

type ContextWindow = Window & {
  getCurrentProfileContext?: () => { profileId: number; isAdmin: boolean } | null;
};

function viewer(): { id: number | null; admin: boolean } {
  if (typeof window === 'undefined') return { id: null, admin: false };
  try {
    const ctx = (window as ContextWindow).getCurrentProfileContext?.();
    if (!ctx) return { id: null, admin: false };
    return {
      id: Number(ctx.profileId),
      admin: ctx.isAdmin === true || Number(ctx.profileId) === 1,
    };
  } catch {
    return { id: null, admin: false };
  }
}

let snapshot = viewer();
const subscribe = (listener: () => void) => {
  const on = () => {
    snapshot = viewer();
    listener();
  };
  window.addEventListener(SHELL_PROFILE_CONTEXT_CHANGED_EVENT, on);
  return () => window.removeEventListener(SHELL_PROFILE_CONTEXT_CHANGED_EVENT, on);
};
const get = () => {
  const now = viewer();
  if (now.id !== snapshot.id || now.admin !== snapshot.admin) snapshot = now;
  return snapshot;
};
const NOBODY = { id: null, admin: false };

/** the signed-in profile: only an admin sees the People tab at all */
export function useViewer() {
  return useSyncExternalStore(subscribe, get, () => NOBODY);
}
