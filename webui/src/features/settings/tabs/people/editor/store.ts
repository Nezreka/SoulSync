/**
 * The one open editor, shared by Settings > People (piece D calls
 * openPersonEditor), the vanilla Manage profiles cards (init.js) and the
 * window global. Members never get it: the server refuses the api anyway,
 * this just doesn't draw a sheet that would only say no.
 */

import { useSyncExternalStore } from 'react';

import { getShellProfileContext } from '@/platform/shell/bridge';

import type { CategoryId, ProfilePermissions } from './types';

export interface PersonEditorOptions {
  /** open on this category instead of the first */
  category?: CategoryId;
  /** after every successful save, with the fresh GET payload */
  onSaved?: (payload: ProfilePermissions) => void;
  /** "Change it there" on a policy lock line: open the Global policy pill */
  onOpenPolicy?: () => void;
  onClose?: () => void;
}

export interface EditorRequest {
  key: number;
  profileId: number;
  options: PersonEditorOptions;
}

declare global {
  interface Window {
    /** open the people & permissions sheet for one profile (admins only) */
    openPersonEditor?: (profileId: number, options?: PersonEditorOptions) => boolean;
    closePersonEditor?: () => void;
  }
}

let current: EditorRequest | null = null;
let opened = 0;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

/** may the signed-in profile open the editor? unknown counts as yes: the
 *  server answers for itself, and the sheet shows its refusal calmly. */
export function viewerIsAdmin(): boolean {
  const ctx = getShellProfileContext();
  return ctx ? !!ctx.isAdmin : true;
}

/** open the sheet. false (and nothing drawn) for a member or a bad id. */
export function openPersonEditor(profileId: number, options: PersonEditorOptions = {}): boolean {
  const id = Number(profileId);
  if (!Number.isInteger(id) || id <= 0) return false;
  if (!viewerIsAdmin()) return false;
  opened += 1;
  current = { key: opened, profileId: id, options };
  emit();
  return true;
}

export function closePersonEditor(): void {
  if (!current) return;
  const done = current.options.onClose;
  current = null;
  emit();
  done?.();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useEditorRequest(): EditorRequest | null {
  return useSyncExternalStore(
    subscribe,
    () => current,
    () => null,
  );
}
