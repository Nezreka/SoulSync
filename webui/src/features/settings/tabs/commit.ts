/**
 * The browser's change event on a field (a text field left, a slider let
 * go, a select picked), which React's onChange is not: the video side's
 * sections saved on it (video-settings.js), and still do.
 */

import { useRef, useState } from 'react';

/** the browser's change event on an input (a text field left, a slider let
 *  go), which React's onChange is not */
export function useCommit<T extends HTMLElement>(commit: (el: T) => void) {
  const latest = useRef(commit);
  latest.current = commit;
  const attached = useRef<{ el: T; listener: () => void } | null>(null);
  const [ref] = useState(() => (el: T | null) => {
    if (attached.current) {
      attached.current.el.removeEventListener('change', attached.current.listener);
      attached.current = null;
    }
    if (!el) return;
    const listener = () => latest.current(el);
    el.addEventListener('change', listener);
    attached.current = { el, listener };
  });
  return ref;
}
