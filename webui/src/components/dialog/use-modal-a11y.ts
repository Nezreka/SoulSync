import { useEffect, useRef } from 'react';

/**
 * The three things a modal owes a keyboard user, in one place.
 *
 * Eight modals in the Library v2 UI declared `aria-modal="true"` — a promise
 * that focus is confined and that Escape gets you out — and implemented none of
 * it, while two more had no `role="dialog"` at all (frontend-audit FE-04/FE-05).
 * `aria-modal` without a trap is worse than neither: a screen reader hides the
 * rest of the page, and Tab then walks focus into content the user can no
 * longer perceive, with no way back and no way out.
 *
 * What this does:
 *
 *  - **Escape closes.** Attached to the dialog element, not the document, so a
 *    nested popover that handles Escape itself still gets first refusal (it
 *    only has to call `stopPropagation`).
 *  - **Focus enters on mount**, on the first focusable element — or on the
 *    container itself, which is why the caller must render `tabIndex={-1}`.
 *  - **Focus is restored on unmount** to whatever was focused before, so
 *    closing a modal returns you to the button that opened it.
 *  - **Tab cycles inside.** Focusable elements are re-queried on every Tab
 *    rather than cached, because every one of these dialogs changes its
 *    controls as it loads.
 */
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

/** Deliberately does NOT use `offsetParent`, which needs layout: it is always
 *  null under jsdom, so a layout-based filter silently returns an empty list in
 *  tests and the trap would appear to work while testing nothing. These checks
 *  hold in both a browser and a test environment. */
function focusableWithin(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => {
    if (el.hasAttribute('hidden') || el.closest('[hidden]')) return false;
    if (el.getAttribute('aria-hidden') === 'true') return false;
    const style = el.ownerDocument.defaultView?.getComputedStyle(el);
    return !style || (style.display !== 'none' && style.visibility !== 'hidden');
  });
}

export function useModalA11y<T extends HTMLElement>(onClose: () => void) {
  const ref = useRef<T | null>(null);
  // Kept in a ref so a caller that passes an inline arrow (all of them) does
  // not re-run the effect and re-steal focus on every render.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const previous = document.activeElement as HTMLElement | null;

    const first = focusableWithin(node)[0];
    (first ?? node).focus({ preventScroll: true });

    function onKeyDown(event: KeyboardEvent) {
      const root = ref.current;
      if (!root) return;
      if (event.key === 'Escape') {
        event.stopPropagation();
        closeRef.current();
        return;
      }
      if (event.key !== 'Tab') return;
      const items = focusableWithin(root);
      if (items.length === 0) {
        event.preventDefault();
        root.focus({ preventScroll: true });
        return;
      }
      const edge = event.shiftKey ? items[0] : items[items.length - 1];
      if (document.activeElement === edge || !root.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? items[items.length - 1] : items[0]).focus({ preventScroll: true });
      }
    }

    node.addEventListener('keydown', onKeyDown);
    return () => {
      node.removeEventListener('keydown', onKeyDown);
      // Take focus back when the dialog still holds it, and also when focus has
      // fallen to <body> -- which is where it lands once the dialog's DOM is
      // gone, and is the ordinary case, since React may remove the node before
      // this cleanup runs. What must NOT be disturbed is focus that some action
      // deliberately moved to another real element.
      const active = document.activeElement;
      const orphaned = !active || active === document.body;
      if (previous && previous.isConnected && (orphaned || node.contains(active))) {
        previous.focus({ preventScroll: true });
      }
    };
  }, []);

  return ref;
}
