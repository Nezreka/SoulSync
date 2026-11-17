/**
 * Getting a tour (or Show me) to the right page, and finding its anchor
 * once the page is there. Pages load at their own pace (React routes fetch,
 * the video side reveals a section), so everything here waits and checks
 * instead of trusting a fixed delay.
 */

import { goToPage } from '../links';
import { currentPageId, isVideoSide } from '../page';

declare global {
  interface Window {
    /** init.js: what the profile may open (music page ids) */
    isPageAllowed?: (pageId: string) => boolean;
    /** init.js: false for a profile whose asks go to the admin */
    canDownload?: () => boolean;
  }
}

export type Side = 'music' | 'video';

export function pageSide(page: string): Side {
  return page.startsWith('video-') ? 'video' : 'music';
}

export function onPage(page: string): boolean {
  return currentPageId() === page;
}

/**
 * Whether this profile can open a page, asked the way the sidebar asks. The
 * video side keeps its rule to itself, so a video page counts as allowed
 * here and the arrival check (waitForPage) catches a refusal.
 */
export function pageAllowed(page: string): boolean {
  if (pageSide(page) === 'video') return true;
  try {
    const bridge = window.SoulSyncWebShellBridge;
    if (bridge) return bridge.isPageAllowed(page as never);
    if (typeof window.isPageAllowed === 'function') return window.isPageAllowed(page);
  } catch {
    /* no answer: let the arrival check decide */
  }
  return true;
}

/** switches side first when it has to, then goes */
export function goTo(page: string): void {
  const side = pageSide(page);
  const here: Side = isVideoSide() ? 'video' : 'music';
  if (side !== here) window._switchAppSide?.(side);
  goToPage(page);
}

// ── waiting ─────────────────────────────────────────────────────────────

export const POLL_MS = 100;

/** resolves with the first truthy answer, or null after `ms` (or on abort) */
export function waitFor<T>(
  check: () => T | null | undefined | false,
  ms: number,
  signal?: AbortSignal,
): Promise<T | null> {
  return new Promise((resolve) => {
    const first = check();
    if (first) {
      resolve(first);
      return;
    }
    const started = Date.now();
    const timer = setInterval(() => {
      if (signal?.aborted) {
        clearInterval(timer);
        resolve(null);
        return;
      }
      const found = check();
      if (found) {
        clearInterval(timer);
        resolve(found);
      } else if (Date.now() - started >= ms) {
        clearInterval(timer);
        resolve(null);
      }
    }, POLL_MS);
    signal?.addEventListener('abort', () => {
      clearInterval(timer);
      resolve(null);
    });
  });
}

/** a refused page bounces somewhere else: true only when we really got there */
export async function arriveAt(page: string, ms: number, signal?: AbortSignal): Promise<boolean> {
  if (onPage(page)) return true;
  if (!pageAllowed(page)) return false;
  goTo(page);
  // a side switch can land on its own page first: ask once more half way
  const half = await waitFor(() => onPage(page), ms / 2, signal);
  if (half) return true;
  if (signal?.aborted) return false;
  goTo(page);
  return !!(await waitFor(() => onPage(page), ms / 2, signal));
}

// ── anchors ─────────────────────────────────────────────────────────────

/** on screen: connected, has a size, and not hidden by css */
export function isShown(el: Element | null | undefined): el is HTMLElement {
  if (!el || !el.isConnected) return false;
  const rect = el.getBoundingClientRect();
  if (rect.width < 1 || rect.height < 1) return false;
  const check = (el as HTMLElement & { checkVisibility?: (o?: object) => boolean }).checkVisibility;
  if (typeof check === 'function') {
    return check.call(el, { checkOpacity: true, checkVisibilityCSS: true });
  }
  const style = window.getComputedStyle(el);
  return style.visibility !== 'hidden' && style.display !== 'none';
}

/** the first element carrying the anchor that is on screen */
export function findAnchor(id: string): HTMLElement | null {
  const safe = id.replace(/[^\w.-]/g, '');
  const all = document.querySelectorAll<HTMLElement>(`[data-tour="${safe}"]`);
  for (const el of all) if (isShown(el)) return el;
  return null;
}

export function findShown(selector: string): HTMLElement | null {
  for (const el of document.querySelectorAll<HTMLElement>(selector)) if (isShown(el)) return el;
  return null;
}
