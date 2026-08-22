/**
 * Closing an anchored popover, without fighting the button that opened it.
 *
 * Every popover here closes on an outside click, on Escape, and by clicking its
 * own trigger again. Those last two paths collide: clicking the trigger while
 * the menu is open fires BOTH the trigger's onClick and the document-level
 * outside-click listener, in an order that depends on where React attached its
 * delegated handler. Whichever lands last wins, so the menu either closed
 * correctly or was torn down and immediately rebuilt.
 *
 * The rebuild is the damaging half. The card menu renders a quality-profile
 * `<select>` through the vanilla's markup and then fills its options
 * asynchronously; a rebuild replaces that markup while the fill is in flight,
 * and the options never arrive. The menu looked open and was missing data.
 *
 * The fix is to stop the race rather than to sequence it: the outside-click
 * handler IGNORES clicks inside the trigger, so the trigger's own toggle is the
 * only thing that acts on them. Order stops mattering.
 */

import type { RefObject } from 'react';

import { useEffect } from 'react';

export interface PopoverDismissOptions {
  /** The popover itself; clicks inside it never dismiss. */
  ref: RefObject<HTMLElement | null>;
  /**
   * The element that opened it. Clicks here are the trigger's business — it
   * toggles — so this hook leaves them alone. Without it, both handlers fire
   * and the popover is rebuilt instead of closed.
   */
  anchor?: HTMLElement | null;
  onClose: () => void;
}

export function usePopoverDismiss({ ref, anchor, onClose }: PopoverDismissOptions): void {
  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (ref.current?.contains(target)) return;
      if (anchor?.contains(target)) return;
      onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    /**
     * Still deferred a tick. The click that OPENS a popover is still bubbling
     * when the effect runs, so a listener attached synchronously would see that
     * same click and close it immediately. The anchor check above covers the
     * trigger specifically; this covers opening from anywhere else.
     */
    const id = setTimeout(() => document.addEventListener('click', onDocClick), 0);
    document.addEventListener('keydown', onKey);
    return () => {
      clearTimeout(id);
      document.removeEventListener('click', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [ref, anchor, onClose]);
}
