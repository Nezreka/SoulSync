import { useCallback, useEffect, useRef } from 'react';

import { ROUTER_ROOT_ID } from '@/platform/shell/route-controllers';

import type { AutomationsSide } from './-automations.types';

/** The legacy page container that physically holds the music builder markup. */
const LEGACY_PAGE_ID = 'automations-page';

/** The video shell's page host, and the subpage inside it that holds the video builder. */
const VIDEO_HOST_ID = 'video-page-host';
const VIDEO_SUBPAGE = 'video-automations';
/**
 * On the body while the video builder is open. video-side.css reveals the
 * React host for the video automations page only while this is absent, so
 * setting it hands the screen to the video shell's own host.
 */
export const VIDEO_BUILDING_CLASS = 'vauto-building';

type ShowBuilder = (automationId?: number) => unknown;

function showKey(side: AutomationsSide): 'showAutomationBuilder' | 'showVideoAutomationBuilder' {
  return side === 'video' ? 'showVideoAutomationBuilder' : 'showAutomationBuilder';
}

/** Give the screen to the vanilla container that holds this side's builder. */
function showVanilla(side: AutomationsSide) {
  if (side === 'video') {
    const host = document.getElementById(VIDEO_HOST_ID);
    // the host still holds whichever video page was open last: show ours alone
    host?.querySelectorAll<HTMLElement>('.video-subpage').forEach((page) => {
      page.hidden = page.getAttribute('data-video-subpage') !== VIDEO_SUBPAGE;
    });
    const slot = document.getElementById('video-placeholder-slot');
    if (slot) slot.hidden = true;
    document.body.classList.add(VIDEO_BUILDING_CLASS);
    return;
  }
  document.getElementById(ROUTER_ROOT_ID)?.classList.remove('active');
  document.getElementById(LEGACY_PAGE_ID)?.classList.add('active');
}

/** Take the screen back for the React page. */
function showReact(side: AutomationsSide) {
  if (side === 'video') {
    document.body.classList.remove(VIDEO_BUILDING_CLASS);
    const page = document
      .getElementById(VIDEO_HOST_ID)
      ?.querySelector<HTMLElement>(`.video-subpage[data-video-subpage="${VIDEO_SUBPAGE}"]`);
    if (page) page.hidden = true;
    return;
  }
  document.getElementById(LEGACY_PAGE_ID)?.classList.remove('active');
  document.getElementById(ROUTER_ROOT_ID)?.classList.add('active');
}

/**
 * Hand off to the vanilla automation builder, then take the page back.
 *
 * The builder is NOT ported to React on purpose. It is ~645 lines and it is
 * SHARED: showAutomationBuilder (music) and showVideoAutomationBuilder (video)
 * both set a context and call the same _openAutomationBuilder. A React copy
 * would be a second implementation of one builder, and the two would drift.
 *
 * The problem it creates: the builder's markup lives in a vanilla container
 * (#automations-page for music, the video shell's automations subpage for
 * video), and both are hidden while React owns the route. Opening the builder
 * there would render it inside a hidden ancestor. A dead button.
 *
 * So this briefly gives the screen back to the vanilla side for the duration
 * of the edit, and reclaims it on close. Every exit (Back, Cancel and Save)
 * routes through hideAutomationBuilder, so wrapping that one function is a
 * complete interception; saveAutomation calls it before its onSaved hook.
 *
 * The side's show function is wrapped too, not only called: the Guides tab is
 * vanilla markup whose "Use This" buttons call it directly, and those have to
 * reveal the container the same way the page's own buttons do.
 */
export function useVanillaBuilder(
  onClosed: () => void,
  side: AutomationsSide = 'music',
): (automationId?: number) => void {
  // Only reclaim the page if WE handed it over. Both sides share the close
  // function, so an unrelated close must not yank the shell around.
  const openedByUs = useRef(false);
  const closed = useRef(onClosed);
  closed.current = onClosed;

  useEffect(() => {
    const key = showKey(side);
    const originalHide = window.hideAutomationBuilder;
    const originalShow = window[key] as ShowBuilder | undefined;

    if (originalHide) {
      window.hideAutomationBuilder = function patchedHideAutomationBuilder(
        this: unknown,
        ...args: unknown[]
      ) {
        const result = (originalHide as (...a: unknown[]) => unknown).apply(this, args);
        if (openedByUs.current) {
          openedByUs.current = false;
          showReact(side);
          // The builder may have just created or edited a row; the vanilla
          // onSaved hook does not know about our list.
          closed.current();
        }
        return result;
      } as typeof window.hideAutomationBuilder;
    }

    if (originalShow) {
      window[key] = function patchedShowAutomationBuilder(this: unknown, ...args: unknown[]) {
        openedByUs.current = true;
        // Reveal the vanilla container FIRST, so the builder is measured and
        // painted in a visible tree rather than a display:none one.
        showVanilla(side);
        return (originalShow as (...a: unknown[]) => unknown).apply(this, args);
      } as (typeof window)[typeof key];
    }

    return () => {
      // Restore on unmount, and if the user navigated away mid-edit make sure
      // the shell is not left showing the vanilla container.
      if (originalHide) window.hideAutomationBuilder = originalHide;
      if (originalShow) window[key] = originalShow as (typeof window)[typeof key];
      if (openedByUs.current) {
        openedByUs.current = false;
        showReact(side);
      }
    };
  }, [side]);

  return useCallback(
    (automationId?: number) => {
      const show = window[showKey(side)] as ShowBuilder | undefined;
      if (typeof show !== 'function') return;
      show(automationId);
    },
    [side],
  );
}
