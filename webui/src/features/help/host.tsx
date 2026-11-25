/**
 * Mounts the help sheet in its own root, the way Server Activity is
 * (features/server-activity/host.tsx). The ? button stays vanilla
 * (index.html #helper-float-btn); helper.js's toggleHelperMode is a shim
 * that calls window.SoulSyncHelp. This file owns:
 *
 *   - window.SoulSyncHelp: open(view?), close(), toggle(), isOpen(), openSearch()
 *   - the keys: ? and F1 toggle the sheet, Ctrl/Cmd K opens it at the search
 *     field (P3's palette takes Ctrl K over), Esc closes it from anywhere
 *   - the button's state: aria-expanded, and the amber dot for an admin
 *     when Health has an error (never warnings, never for members)
 *   - who is looking, which page, the admin status poll, the version
 */

import { useEffect } from 'react';
import { createRoot } from 'react-dom/client';

import { getHealth, getStatus, getUpdateInfo, mergeStatus, statusFromHealth } from './api';
import { currentPageId, viewerIsAdmin } from './page';
import { installReleaseNotes } from './release-notes';
import { closePalette, isPaletteOpen, openPalette } from './search/palette';
import { BUTTON_ID, HelpSheet, PHONE_QUERY } from './sheet';
import { needsAdmin, useHelpStore } from './store';
import { installTours } from './tours/index';
import { isViewId } from './views';

const HOST_ID = 'soulsync-help-root';
export const READY_EVENT = 'ss:help-ready';
const PROFILE_EVENT = 'ss:webui-profile-context-changed';
/** the admin status refresh; opening the sheet also refreshes it */
export const STATUS_POLL_MS = 120_000;
const PAGE_WATCH_MS = 400;

// ── the button ──────────────────────────────────────────────────────────

export function syncButton(): void {
  const btn = document.getElementById(BUTTON_ID);
  if (!btn) return;
  const state = useHelpStore.getState();
  const alert = needsAdmin(state);
  btn.setAttribute('aria-expanded', String(state.open));
  btn.setAttribute('aria-controls', 'soulsync-help-sheet');
  btn.toggleAttribute('data-help-alert', alert);
  btn.setAttribute('aria-label', alert ? 'Help, something needs you' : 'Help');
}

// ── status, version, page ───────────────────────────────────────────────

let statusInFlight: AbortController | null = null;
let followUp: ReturnType<typeof setTimeout> | null = null;
/** the server's first Health run is still going: ask again shortly */
export const REFRESHING_RETRY_MS = 10_000;

/**
 * The dot: GET /api/help/status, cheap, errors only. full=true (on open)
 * reads /api/help/health instead (cached 30s server side) for the warnings
 * and the titles Home's card names. Admins only; a member never asks.
 */
export async function refreshStatus(full = false): Promise<void> {
  if (!useHelpStore.getState().isAdmin) return;
  statusInFlight?.abort();
  const ctrl = new AbortController();
  statusInFlight = ctrl;
  try {
    if (full) {
      const res = await getHealth(false, ctrl.signal);
      if (!useHelpStore.getState().isAdmin) return;
      if (res.ok) {
        useHelpStore.getState().setStatus(statusFromHealth(res.data));
        return;
      }
    }
    const res = await getStatus(ctrl.signal);
    // a profile switch while it was out: a member keeps nothing
    const { isAdmin, status, setStatus } = useHelpStore.getState();
    if (!isAdmin) return;
    if (!res.ok) {
      setStatus(status, res.error);
      return;
    }
    if (!res.data) return;
    setStatus(mergeStatus(status, res.data));
    if (res.data.refreshing && !followUp) {
      followUp = setTimeout(() => {
        followUp = null;
        void refreshStatus();
      }, REFRESHING_RETRY_MS);
    }
  } catch {
    /* aborted by a newer read */
  } finally {
    if (statusInFlight === ctrl) statusInFlight = null;
  }
}

let updateInFlight = false;

/** once a session: the server caches the github check, the sheet keeps the answer */
async function loadUpdateInfo(): Promise<void> {
  if (updateInFlight || useHelpStore.getState().update) return;
  updateInFlight = true;
  try {
    const res = await getUpdateInfo();
    if (res.ok) useHelpStore.getState().setUpdate(res.data);
  } catch {
    /* no answer: the footer shows the name alone */
  } finally {
    updateInFlight = false;
  }
}

function applyProfile(): void {
  const isAdmin = viewerIsAdmin();
  useHelpStore.getState().setAdmin(isAdmin);
  if (isAdmin) void refreshStatus();
}

function phone(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia(PHONE_QUERY).matches;
}

// ── keys ────────────────────────────────────────────────────────────────

function typing(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

/** another modal on top owns Esc (a confirm, a download window) */
function otherModalOpen(): boolean {
  const modals = document.querySelectorAll('[aria-modal="true"]');
  return Array.from(modals).some((m) => !m.closest('[data-help-root]'));
}

export function onGlobalKey(event: KeyboardEvent): void {
  if (event.defaultPrevented) return;
  const store = useHelpStore.getState();
  const inSheet = !!(event.target as HTMLElement | null)?.closest?.('[data-help-root]');

  if (event.key === 'F1' && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault();
    store.toggle();
    return;
  }
  if (event.key === '?' && !event.ctrlKey && !event.metaKey && !event.altKey) {
    if (typing(event.target)) return;
    event.preventDefault();
    store.toggle();
    return;
  }
  if (
    (event.key === 'k' || event.key === 'K') &&
    (event.ctrlKey || event.metaKey) &&
    !event.altKey
  ) {
    event.preventDefault();
    // the palette, from anywhere; Ctrl K again closes it. with the sheet open
    // the same search is its own field, and another modal keeps the keys
    if (isPaletteOpen()) closePalette();
    else if (store.open) store.openSheet(undefined, { focusSearch: true });
    else if (!otherModalOpen()) openPalette();
    return;
  }
  // the sheet's own Esc handles focus inside it; this is Esc from the page beside it
  if (event.key === 'Escape' && store.open && !inSheet && !otherModalOpen()) {
    store.close();
    const btn = document.getElementById(BUTTON_ID);
    const active = document.activeElement;
    if (!active || active === document.body) btn?.focus({ preventScroll: true });
  }
}

let keysInstalled = false;

export function installHelpKeys(): void {
  if (keysInstalled) return;
  keysInstalled = true;
  document.addEventListener('keydown', onGlobalKey);
}

/** for tests */
export function uninstallHelpKeys(): void {
  document.removeEventListener('keydown', onGlobalKey);
  keysInstalled = false;
}

// ── the component ───────────────────────────────────────────────────────

export function HelpHost() {
  const open = useHelpStore((st) => st.open);
  const isAdmin = useHelpStore((st) => st.isAdmin);

  // the vanilla button mirrors the store
  useEffect(() => {
    syncButton();
    return useHelpStore.subscribe(syncButton);
  }, []);

  // who is looking, and again after every profile switch
  useEffect(() => {
    applyProfile();
    window.addEventListener(PROFILE_EVENT, applyProfile);
    return () => window.removeEventListener(PROFILE_EVENT, applyProfile);
  }, []);

  // the admin status: the dot needs it with the sheet closed too
  useEffect(() => {
    if (!isAdmin) return;
    const timer = setInterval(() => {
      if (!document.hidden) void refreshStatus();
    }, STATUS_POLL_MS);
    return () => clearInterval(timer);
  }, [isAdmin]);

  // on open: the page it is about, a fresh status, the version
  useEffect(() => {
    if (!open) return;
    useHelpStore.getState().setPage(currentPageId());
    void refreshStatus(true);
    void loadUpdateInfo();
    // the page can change beside a non-modal sheet; on a phone a new page closes it
    let last = currentPageId();
    const timer = setInterval(() => {
      const now = currentPageId();
      if (now === last) return;
      last = now;
      useHelpStore.getState().setPage(now);
      if (phone()) useHelpStore.getState().close();
    }, PAGE_WATCH_MS);
    return () => clearInterval(timer);
  }, [open]);

  return <HelpSheet />;
}

export function installHelpApi(): void {
  window.SoulSyncHelp = {
    open: (view?: string, params?: Record<string, string>) =>
      useHelpStore.getState().openSheet(isViewId(view) ? { view, params } : undefined),
    close: () => useHelpStore.getState().close(),
    toggle: () => useHelpStore.getState().toggle(),
    isOpen: () => useHelpStore.getState().open,
    openSearch: () => useHelpStore.getState().openSheet(undefined, { focusSearch: true }),
  };
}

export function mountHelpHost(): void {
  if (typeof document === 'undefined' || document.getElementById(HOST_ID)) return;
  const el = document.createElement('div');
  el.id = HOST_ID;
  // after the floating buttons, so the shared top layer puts the sheet over them on a phone
  document.body.appendChild(el);
  // the version modal reads the notes, and a page's "Tour: ..." row needs the
  // tours offered, before anyone opens the sheet
  installReleaseNotes();
  installTours();
  installHelpApi();
  installHelpKeys();
  createRoot(el).render(<HelpHost />);
  // helper.js may have been asked to open before this bundle ran
  window.dispatchEvent(new CustomEvent(READY_EVENT));
}
