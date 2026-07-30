/**
 * Keeping basic (Soulseek) search working while React owns the page.
 *
 * Basic search is not part of this port: its markup is `#basic-search-section`
 * in index.html and its logic is `performDownloadsSearch` in downloads.js. But
 * the shell shows a React page by removing `.active` from every `.page`
 * (shell-bridge.js:66-69), which hides `#search-page` and the basic section
 * inside it. So the panel is adopted: the real element is MOVED into the React
 * tree, keeping its ids, its children and any listeners already bound to it.
 * Precedent: the automations page mounts what `_buildAutomationHub()` returns
 * rather than restating it.
 *
 * Moving the markup is only half of it. `initializeSearch()` (search.js:12-36)
 * is what binds the Search button, the Enter key and Cancel, and it runs from
 * `case 'search':` in init.js — which loadPageData only reaches for LEGACY
 * pages. After the manifest flip that call never happens again, so the adopted
 * panel would render perfectly and do nothing at all. It is invoked here, once.
 *
 * `initializeSearchModeToggle` is deliberately NOT invoked: that is the vanilla
 * enhanced-search controller this page replaces, and running it would build a
 * second source row and a second set of listeners over the same DOM.
 */

import { useEffect, useRef } from 'react';

export const BASIC_SECTION_ID = 'basic-search-section';

/**
 * Bound once per page load, not once per mount.
 *
 * initializeSearch uses addEventListener with no guard of its own, so calling it
 * twice makes every basic search fire twice. The listeners live on the adopted
 * node, which outlives this component, so the flag has to as well.
 */
let basicListenersBound = false;

/** Test seam — the module-level flag would otherwise leak between tests. */
export function resetBasicSectionBinding() {
  basicListenersBound = false;
}

/**
 * Move `#basic-search-section` into `host`, and put it back on the way out.
 *
 * Returning it matters: leaving it inside a React container that unmounts would
 * destroy the node, and basic search would stay broken until a full page reload.
 */
export function useAdoptedBasicSection(host: HTMLElement | null, active: boolean) {
  const originRef = useRef<{ parent: Node; next: Node | null } | null>(null);

  useEffect(() => {
    if (!host) return;
    const section = document.getElementById(BASIC_SECTION_ID);
    if (!section) return;

    if (!originRef.current && section.parentNode) {
      originRef.current = { parent: section.parentNode, next: section.nextSibling };
    }
    host.appendChild(section);

    if (!basicListenersBound) {
      basicListenersBound = true;
      window.initializeSearch?.();
      window.initializeFilters?.();
    }

    return () => {
      const origin = originRef.current;
      if (origin?.parent) origin.parent.insertBefore(section, origin.next);
    };
  }, [host]);

  // `.active` is what the stylesheet keys the two panels on, and the vanilla
  // toggles exactly this class when the source picker switches modes.
  useEffect(() => {
    const section = document.getElementById(BASIC_SECTION_ID);
    if (!section) return;
    section.classList.toggle('active', active);
  }, [active]);
}
