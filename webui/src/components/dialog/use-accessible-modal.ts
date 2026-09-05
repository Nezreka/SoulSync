import { useCallback, useEffect, useRef } from 'react';

/**
 * the modal behaviours every hand-rolled overlay in here was missing: escape,
 * a focus trap, initial focus, focus restore and a scroll lock.
 *
 * base-ui's Dialog gives all of this, but it also brings its own popup/backdrop
 * markup, and the overlays that need fixing (the mix modal first) are styled by
 * legacy class names and read by id from vanilla pollers. this hook bolts the
 * behaviour onto markup that already exists instead of replacing it.
 *
 * WAI-ARIA dialog pattern: focus starts inside, tab stays inside, escape
 * closes, focus goes back to whatever opened it.
 */

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

/** how many modals are currently holding the body scroll lock. */
let lockCount = 0;
let restoreOverflow = '';

function lockScroll() {
  if (lockCount === 0) {
    restoreOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
  }
  lockCount += 1;
}

function unlockScroll() {
  lockCount = Math.max(0, lockCount - 1);
  if (lockCount === 0) document.body.style.overflow = restoreOverflow;
}

/**
 * a control the user can actually reach.
 *
 * NOT offsetParent or getClientRects: those need layout, which means the trap
 * silently finds nothing under jsdom and the tests can't tell a working trap
 * from a broken one. checkVisibility answers from computed style, which both
 * a browser and jsdom have.
 */
function isReachable(el: HTMLElement): boolean {
  if (el.closest('[hidden]')) return false;
  if (typeof el.checkVisibility === 'function') {
    return el.checkVisibility({ checkVisibilityCSS: true, contentVisibilityAuto: true });
  }
  return true;
}

function focusableIn(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(isReachable);
}

export interface AccessibleModalOptions {
  /** which control to focus on open. defaults to the first focusable one. */
  initialFocus?: () => HTMLElement | null;
  /** skip the body scroll lock when the caller already owns it. */
  lockBodyScroll?: boolean;
}

/**
 * returns the ref to put on the dialog element. the element also needs
 * role="dialog", aria-modal="true" and a label; the hook can't add those
 * without owning the markup.
 */
export function useAccessibleModal<T extends HTMLElement = HTMLDivElement>(
  onClose: () => void,
  options: AccessibleModalOptions = {},
) {
  const ref = useRef<T | null>(null);
  const { initialFocus, lockBodyScroll = true } = options;
  // keep the latest onClose without re-running the effect, which would steal
  // focus back to the top of the dialog on every parent render.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const initialFocusRef = useRef(initialFocus);
  initialFocusRef.current = initialFocus;

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const opener = document.activeElement as HTMLElement | null;
    if (lockBodyScroll) lockScroll();

    const target = initialFocusRef.current?.() ?? focusableIn(dialog)[0] ?? dialog;
    if (target === dialog && !dialog.hasAttribute('tabindex')) dialog.tabIndex = -1;
    target.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        closeRef.current();
        return;
      }
      if (e.key !== 'Tab') return;
      const items = focusableIn(dialog);
      if (!items.length) {
        e.preventDefault();
        dialog.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      // wrap at both ends, and pull focus back in if it escaped the dialog
      // some other way (a click on the backdrop, a removed control).
      if (!dialog.contains(active)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      } else if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };

    // capture, so a page-level escape handler underneath doesn't get there
    // first and close something else.
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      if (lockBodyScroll) unlockScroll();
      // back to the card that opened it, if it's still on the page.
      if (opener && document.contains(opener)) opener.focus();
    };
  }, [lockBodyScroll]);

  /** backdrop click, for the overlay element itself. */
  const onBackdropClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose],
  );

  return { ref, onBackdropClick };
}
