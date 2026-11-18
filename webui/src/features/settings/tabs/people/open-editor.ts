/**
 * Opening one person's editor, and the old profile flows this tab reuses.
 *
 * The per-person sheet is piece E's (people/editor/index.ts exports
 * openPersonEditor(profileId)). It is built in parallel, so it is found
 * through a glob: when the file is there this calls it, and until then a
 * click opens the existing profile editor from init.js. Nothing here needs
 * changing when E's file lands.
 */

import type { Person } from './types';

/** piece E's options (people/editor/store.ts PersonEditorOptions) */
export interface EditorHooks {
  onSaved?: () => void;
  onOpenPolicy?: () => void;
}
type EditorModule = {
  openPersonEditor?: (profileId: number, options?: EditorHooks) => unknown;
};

/** E's sheet says so on window after every save */
export const PERSON_SAVED_EVENT = 'ss:person-permissions-saved';

const editors = import.meta.glob<EditorModule>('./editor/index.ts');

type ProfileWindow = Window & {
  openProfileEditor?: (options: { mode: 'create' | 'edit' | 'self'; profile?: unknown }) => unknown;
  openInviteCreator?: () => unknown;
};
const w = () => window as ProfileWindow;

/** E's sheet when it exists, else the old editor with the profile row */
export async function openPersonEditor(
  person: Person,
  meId: number | null = null,
  hooks: EditorHooks = {},
): Promise<boolean> {
  const load = editors['./editor/index.ts'];
  if (load) {
    try {
      const mod = await load();
      if (typeof mod.openPersonEditor === 'function') {
        if (mod.openPersonEditor(person.id, hooks) !== false) return true;
      }
    } catch (error) {
      console.error('people: the editor failed to load', error);
    }
  }
  if (w().openProfileEditor) {
    // your own row opens the editor's self mode, as Manage profiles did
    w().openProfileEditor?.({ mode: person.id === meId ? 'self' : 'edit', profile: person });
    return true;
  }
  return false;
}

/** the existing "add a profile" flow (init.js openProfileEditor create) */
export function openAddPerson(): boolean {
  if (!w().openProfileEditor) return false;
  w().openProfileEditor?.({ mode: 'create' });
  return true;
}

/** the existing invite link maker (init.js openInviteCreator) */
export function openInviteMaker(): boolean {
  if (!w().openInviteCreator) return false;
  w().openInviteCreator?.();
  return true;
}

/**
 * Runs `refresh` each time the profile modals (the pf- layer family the old
 * editor and the invite maker open in) all close, and each time E's sheet
 * saves, so the table shows what was just saved.
 */
export function onProfileLayersClosed(refresh: () => void): () => void {
  if (typeof document === 'undefined') return () => {};
  let open = document.body.classList.contains('pf-modal-open');
  const observer = new MutationObserver(() => {
    const now = document.body.classList.contains('pf-modal-open');
    if (open && !now) refresh();
    open = now;
  });
  observer.observe(document.body, { attributes: true, attributeFilter: ['class'] });
  // E's sheet mounts its own root: it says when it saved
  window.addEventListener(PERSON_SAVED_EVENT, refresh);
  return () => {
    observer.disconnect();
    window.removeEventListener(PERSON_SAVED_EVENT, refresh);
  };
}
