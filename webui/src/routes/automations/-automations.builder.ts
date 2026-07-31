import { useCallback, useEffect, useRef } from 'react';

import { ROUTER_ROOT_ID } from '@/platform/shell/route-controllers';

/** The legacy page container that physically holds the builder markup. */
const LEGACY_PAGE_ID = 'automations-page';

/**
 * Hand off to the vanilla automation builder, then take the page back.
 *
 * The builder is NOT ported to React on purpose. It is ~645 lines and it is
 * SHARED: showAutomationBuilder (music) and showVideoAutomationBuilder (video)
 * both set a context and call the same _openAutomationBuilder. A React copy
 * would be a second implementation of a builder the video page still needs,
 * and the two would drift.
 *
 * The problem it creates: the builder's markup lives inside #automations-page,
 * and `.page { display: none }` — only `.page.active` shows. When React owns
 * the route the shell strips `.active` from every legacy page, so opening the
 * builder would render it inside a hidden ancestor. A dead button.
 *
 * So this briefly gives the page back to the vanilla side for the duration of
 * the edit, and reclaims it on close. Every exit — Back, Cancel and Save —
 * routes through hideAutomationBuilder, so wrapping that one function is a
 * complete interception; saveAutomation calls it before its onSaved hook.
 */
export function useVanillaBuilder(onClosed: () => void): (automationId?: number) => void {
  // Only reclaim the page if WE handed it over. The video page shares this
  // function, so an unrelated close must not yank the shell around.
  const openedByUs = useRef(false);
  const closed = useRef(onClosed);
  closed.current = onClosed;

  const showReact = useCallback(() => {
    document.getElementById(LEGACY_PAGE_ID)?.classList.remove('active');
    document.getElementById(ROUTER_ROOT_ID)?.classList.add('active');
  }, []);

  useEffect(() => {
    const original = window.hideAutomationBuilder;
    if (!original) return;

    const wrapped = function patchedHideAutomationBuilder(this: unknown, ...args: unknown[]) {
      const result = (original as (...a: unknown[]) => unknown).apply(this, args);
      if (openedByUs.current) {
        openedByUs.current = false;
        showReact();
        // The builder may have just created or edited a row; the vanilla
        // onSaved hook repaints the hidden legacy list, not ours.
        closed.current();
      }
      return result;
    } as typeof window.hideAutomationBuilder;

    window.hideAutomationBuilder = wrapped;
    return () => {
      // Restore on unmount, and if the user navigated away mid-edit make sure
      // the shell is not left showing the legacy container.
      window.hideAutomationBuilder = original;
      if (openedByUs.current) {
        openedByUs.current = false;
        showReact();
      }
    };
  }, [showReact]);

  return useCallback((automationId?: number) => {
    if (typeof window.showAutomationBuilder !== 'function') return;
    openedByUs.current = true;
    // Reveal the legacy container FIRST, so the builder is measured and
    // painted in a visible tree rather than a display:none one.
    document.getElementById(ROUTER_ROOT_ID)?.classList.remove('active');
    document.getElementById(LEGACY_PAGE_ID)?.classList.add('active');
    window.showAutomationBuilder(automationId);
  }, []);
}
