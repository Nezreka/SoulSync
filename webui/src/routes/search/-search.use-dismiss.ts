/**
 * Closing the results dropdown when the user clicks away.
 *
 * Ported from the document listener at search.js:371-390. The value is entirely
 * in the exemptions — things that sit visually over or beside the dropdown and
 * must NOT dismiss it:
 *
 *   - the dropdown itself: clicking a RESULT must never throw the results away.
 *     The vanilla listener never named it because the vanilla DOM nested
 *     #enhanced-dropdown INSIDE .enhanced-search-input-wrapper, so the first
 *     exemption covered it implicitly. The React markup makes them siblings —
 *     dropping this containment silently made every result click a dismissal
 *     (naked on video cards, which open no modal; masked on albums/tracks by
 *     the download modal opening over the already-dismissed results)
 *   - the input wrapper itself (you are typing in it)
 *   - the source row, which lives ABOVE the input and outside the dropdown, and
 *     whose whole job is switching which results are shown
 *   - the download-missing modal, which opens on top; closing it must not also
 *     throw away the results you opened it from
 *   - the media player, mini bar and expanded now-playing modal both (#732 —
 *     clicking the mini player to expand it dismissed the search)
 */

import { useEffect } from 'react';

/** Selectors a click may land in without closing the dropdown. */
export const DISMISS_EXEMPT_SELECTORS = [
  '#enhanced-dropdown',
  '.enhanced-search-input-wrapper',
  '#enh-source-row',
  '.download-missing-modal',
  // Reachable FROM the download modal but appended to <body> as siblings, so
  // the modal exemption doesn't cover them: the candidates picker
  // (downloads.js showCandidatesModal) and the track-detail overlay
  // (track-detail.js) — clicking either must not eat the results underneath.
  '#candidates-modal-overlay',
  '#track-detail-overlay',
  '#media-player',
  '#np-modal-overlay',
] as const;

/** Should a click on this element dismiss the dropdown? */
export function shouldDismiss(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return true;
  return !DISMISS_EXEMPT_SELECTORS.some((selector) => target.closest(selector));
}

/** Dismiss the dropdown on an outside click, while it is open. */
export function useDismissOnOutsideClick(open: boolean, onDismiss: () => void) {
  useEffect(() => {
    if (!open) return;
    const onDocumentClick = (event: MouseEvent) => {
      // React handlers run before this document listener and may re-render the
      // clicked subtree, detaching event.target from the dropdown — closest()
      // on a detached node then misses every exemption and dismisses anyway
      // (the same detach the vanilla source row worked around with
      // stopPropagation). composedPath() is captured at dispatch, so it still
      // holds the true ancestry.
      const path = typeof event.composedPath === 'function' ? event.composedPath() : [];
      const inExempt = path.some(
        (node) =>
          node instanceof Element &&
          DISMISS_EXEMPT_SELECTORS.some((selector) => node.matches(selector)),
      );
      if (!inExempt && shouldDismiss(event.target)) onDismiss();
    };
    document.addEventListener('click', onDocumentClick);
    return () => document.removeEventListener('click', onDocumentClick);
  }, [open, onDismiss]);
}
