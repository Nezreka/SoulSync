/**
 * arrow keys drive focus like a TV remote, and back goes back. bound once on
 * the player's root; the geometry is in -lib/spatial.
 */

import { useRouter } from '@tanstack/react-router';
import { useEffect, type RefObject } from 'react';

import { directionForKey, pickNext, type Box } from '../-lib/spatial';

const FOCUSABLE =
  '[data-nav], a[href], button:not([disabled]), input:not([disabled]), select, [tabindex="0"]';

// a remote's back button, and escape. not backspace: browsers dropped it as back
// because people lost pages to it
const BACK_KEYS = new Set(['GoBack', 'BrowserBack', 'Escape']);

function isTyping(el: Element | null): boolean {
  if (!el) return false;
  const tag = el.tagName;
  return (
    tag === 'INPUT' ||
    tag === 'TEXTAREA' ||
    tag === 'SELECT' ||
    (el as HTMLElement).isContentEditable
  );
}

function visible(el: HTMLElement): boolean {
  if (el.closest('[aria-hidden="true"], [inert]')) return false;
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

function toBox(rect: DOMRect): Box {
  return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom };
}

function candidates(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(visible);
}

/** the last input was a key press: show focus and move it on page changes */
let keyboardMode = false;

export function usingKeyboard(): boolean {
  return keyboardMode;
}

export function useSpatialNav(rootRef: RefObject<HTMLElement | null>) {
  const router = useRouter();

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    const onPointer = () => {
      keyboardMode = false;
    };

    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
      keyboardMode = true;
      // the player covers the whole manager: focus left on a manager element
      // underneath counts as nothing focused
      const focused = document.activeElement as HTMLElement | null;
      const active = focused && root.contains(focused) ? focused : null;

      if (BACK_KEYS.has(event.key)) {
        if (isTyping(active)) {
          if (event.key === 'Escape') active?.blur();
          return;
        }
        // a menu or dialog inside the app closes itself first
        if (event.key === 'Escape' && root.querySelector('[role="menu"], [role="dialog"]')) return;
        if (window.location.pathname.replace(/\/+$/, '').endsWith('/player')) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        router.history.back();
        return;
      }

      const dir = directionForKey(event.key);
      if (!dir) return;
      // left/right inside a text field moves the caret
      if (isTyping(active) && (dir === 'left' || dir === 'right')) return;

      const list = candidates(root);
      if (!list.length) return;
      if (!active) {
        event.preventDefault();
        event.stopImmediatePropagation();
        list[0].focus();
        return;
      }
      // right out of the sidebar goes into the page, like a remote expects
      const nav = active.closest('nav');
      if (dir === 'right' && nav && root.contains(nav)) {
        const page = root.querySelector<HTMLElement>('[data-page]');
        const first = page ? candidates(page)[0] : undefined;
        if (first) {
          event.preventDefault();
          event.stopImmediatePropagation();
          first.focus({ preventScroll: true });
          first.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
          return;
        }
      }
      const boxes = list.map((el) => toBox(el.getBoundingClientRect()));
      const from = toBox(active.getBoundingClientRect());
      const index = pickNext(from, boxes, dir);
      if (index < 0) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const next = list[index];
      next.focus({ preventScroll: true });
      next.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
    };

    // on the document, in the capture phase: with nothing focused yet (a
    // fresh page on a TV) the key goes to <body>, outside the player's root,
    // and the manager's own key handlers underneath must not see a key the
    // player moved focus with
    document.addEventListener('keydown', onKey, true);
    root.addEventListener('pointerdown', onPointer);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      root.removeEventListener('pointerdown', onPointer);
    };
  }, [rootRef, router]);
}

/** on a new page, a remote user lands on its first control */
export function focusFirstIn(container: HTMLElement | null) {
  if (!container || !keyboardMode) return;
  const first = candidates(container)[0];
  first?.focus({ preventScroll: true });
}
