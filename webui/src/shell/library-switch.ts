/**
 * The header's library switcher (#1199, E-11).
 *
 * An admin on an install where some profile keeps a library of its own picks
 * which library they are working in: what every page lists, which "already in
 * your library" answers the search gives, and where a download started from
 * anywhere lands. The pick lives in the server session; this is only its face.
 *
 * Drawn under the profile indicator, and nowhere at all when there is nothing
 * to switch between -- one library, or a profile that is not an admin.
 */

import { escapeHtml } from './html';

export const LIBRARY_SCOPE_CHANGED_EVENT = 'ss:webui-library-scope-changed';
const PROFILE_CONTEXT_CHANGED_EVENT = 'ss:webui-profile-context-changed';

interface ScopeOption {
  id: string;
  name: string;
  root?: string | null;
  files?: number;
}

interface ScopesPayload {
  switchable?: boolean;
  current?: string;
  current_name?: string;
  target_name?: string;
  options?: ScopeOption[];
}

const ROOT_ID = 'library-switch';
const MENU_ID = 'library-switch-menu';
let _state: ScopesPayload | null = null;
let _open = false;

const _ICON = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 19V5"/><path d="M8 19V5"/><path d="M12.5 19.5 16 5l4 1-3.5 14.5z"/></svg>`;
const _CHEVRON = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>`;
const _CHECK = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l5 5L20 7"/></svg>`;

function _root(): HTMLElement | null {
  let root = document.getElementById(ROOT_ID);
  if (root) return root;
  const anchor = document.getElementById('profile-indicator');
  if (!anchor?.parentElement) return null;
  root = document.createElement('div');
  root.id = ROOT_ID;
  root.className = 'library-switch';
  root.hidden = true;
  anchor.insertAdjacentElement('afterend', root);
  return root;
}

function _files(n: number | undefined): string {
  if (!n) return 'empty';
  return `${n.toLocaleString()} file${n === 1 ? '' : 's'}`;
}

// The sidebar header clips (overflow: hidden) and its backdrop-filter traps
// position: fixed, so the open list lives on <body>, pinned under the trigger.
function _menu(): HTMLElement {
  let menu = document.getElementById(MENU_ID);
  if (!menu) {
    menu = document.createElement('div');
    menu.id = MENU_ID;
    menu.className = 'library-switch-menu';
    menu.setAttribute('role', 'listbox');
    menu.setAttribute('aria-label', 'Choose a library');
    menu.hidden = true;
    document.body.appendChild(menu);
  }
  return menu;
}

function _closeMenu(): void {
  const menu = document.getElementById(MENU_ID);
  if (!menu) return;
  menu.hidden = true;
  menu.innerHTML = '';
}

function _renderMenu(
  root: HTMLElement,
  options: ScopeOption[],
  current: string,
  targetName: string | undefined,
): void {
  if (!_open) {
    _closeMenu();
    return;
  }
  const menu = _menu();
  menu.innerHTML = `${options
    .map(
      (o) => `
      <button type="button" role="option" class="library-switch-option${
        o.id === current ? ' is-current' : ''
      }" data-scope="${escapeHtml(o.id)}" aria-selected="${o.id === current ? 'true' : 'false'}">
        <span class="library-switch-option-main">
          <span class="library-switch-option-name">${escapeHtml(o.name)}</span>
          <span class="library-switch-option-meta">${
            o.root ? `<code>${escapeHtml(o.root)}</code> · ` : ''
          }${_files(o.files)}</span>
        </span>
        <span class="library-switch-option-check">${o.id === current ? _CHECK : ''}</span>
      </button>`,
    )
    .join('')}
    <div class="library-switch-foot">Downloads land in <strong>${escapeHtml(
      targetName || 'Shared library',
    )}</strong></div>`;
  const rect = root.getBoundingClientRect();
  menu.style.top = `${Math.round(rect.bottom + 6)}px`;
  menu.style.left = `${Math.round(rect.left)}px`;
  menu.hidden = false;
  menu.querySelectorAll<HTMLButtonElement>('.library-switch-option').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      void pickLibrary(btn.dataset.scope || 'shared');
    });
  });
}

function _render(): void {
  const root = _root();
  if (!root) return;
  const state = _state;
  if (!state?.switchable || !state.options?.length) {
    root.hidden = true;
    root.innerHTML = '';
    _closeMenu();
    document.documentElement.removeAttribute('data-library-scope');
    return;
  }
  const current = state.current || 'shared';
  const currentOption = state.options.find((o) => o.id === current);
  const label = currentOption?.name || state.current_name || 'Shared library';
  document.documentElement.setAttribute(
    'data-library-scope',
    current === 'shared' ? 'shared' : current === 'all' ? 'all' : 'own',
  );
  root.hidden = false;
  root.innerHTML = `
    <button type="button" class="library-switch-trigger" aria-haspopup="listbox"
            aria-expanded="${_open ? 'true' : 'false'}"
            title="Library you are working in — what pages show and where downloads land">
      <span class="library-switch-icon">${_ICON}</span>
      <span class="library-switch-text">
        <span class="library-switch-caption">Library</span>
        <span class="library-switch-name">${escapeHtml(label)}</span>
      </span>
      <span class="library-switch-chevron">${_CHEVRON}</span>
    </button>`;
  _renderMenu(root, state.options, current, state.target_name);
  root.querySelector('.library-switch-trigger')?.addEventListener('click', (e) => {
    e.stopPropagation();
    _open = !_open;
    _render();
  });
}

/** Re-read the switcher state from the server and redraw it. */
export async function refreshLibrarySwitch(): Promise<void> {
  try {
    const res = await fetch('/api/library/v2/scopes');
    _state = res.ok ? ((await res.json()) as ScopesPayload) : null;
  } catch {
    _state = null;
  }
  _render();
}

/** Work in another library. Everything cached for the old one is stale. */
export async function pickLibrary(scope: string): Promise<boolean> {
  _open = false;
  if (_state && scope === _state.current) {
    _render();
    return true;
  }
  let ok = false;
  try {
    const res = await fetch('/api/library/v2/scope', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope }),
    });
    ok = res.ok;
  } catch {
    ok = false;
  }
  if (!ok) window.showToast?.('Could not switch library', 'error');
  await refreshLibrarySwitch();
  if (ok) {
    window.dispatchEvent(new CustomEvent(LIBRARY_SCOPE_CHANGED_EVENT, { detail: { scope } }));
    const name = _state?.current_name;
    if (name) window.showToast?.(`Working in ${name}`, 'success');
  }
  return ok;
}

function _init(): void {
  void refreshLibrarySwitch();
  window.addEventListener(PROFILE_CONTEXT_CHANGED_EVENT, () => {
    _open = false;
    void refreshLibrarySwitch();
  });
  // the Library page's own control picks the same library; follow it
  window.addEventListener(LIBRARY_SCOPE_CHANGED_EVENT, () => {
    void refreshLibrarySwitch();
  });
  document.addEventListener('click', (e) => {
    if (!_open) return;
    const inside = [ROOT_ID, MENU_ID].some((id) => {
      const el = document.getElementById(id);
      return el && e.target instanceof Node && el.contains(e.target);
    });
    if (inside) return;
    _open = false;
    _render();
  });
  window.addEventListener('resize', () => {
    if (!_open) return;
    _open = false;
    _render();
  });
  document.addEventListener('keydown', (e) => {
    if (_open && e.key === 'Escape') {
      _open = false;
      _render();
    }
  });
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init, { once: true });
  } else {
    _init();
  }
}
