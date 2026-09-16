import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Horizontal scroller state for a strip that can overflow.
 *
 * Two things a plain `overflow-x: auto` strip gets wrong on a desktop with a
 * mouse: the scrollbar is hidden so nothing says there is more to the right, and
 * a mouse wheel scrolls the page rather than the strip. So the caller gets
 * arrows that only light up when there is somewhere to go, and a wheel handler
 * that steers a vertical wheel sideways while the pointer is over the strip.
 *
 * The edge flags are recomputed on scroll, on resize of the track, and whenever
 * its contents change, because a strip that is filled asynchronously starts out
 * measuring as "nothing to scroll".
 */
export function useEdgeScroll<T extends HTMLElement>(deps: unknown = null) {
  const ref = useRef<T | null>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const sync = useCallback(() => {
    const node = ref.current;
    if (!node) return;
    // A couple of pixels of slack: sub-pixel layout means scrollLeft rarely
    // reaches scrollWidth - clientWidth exactly, which would leave the right
    // arrow enabled forever at the end of the strip.
    setCanScrollLeft(node.scrollLeft > 4);
    setCanScrollRight(node.scrollLeft + node.clientWidth < node.scrollWidth - 4);
  }, []);

  useEffect(() => {
    sync();
    const node = ref.current;
    if (!node) return;
    const observer = new ResizeObserver(sync);
    observer.observe(node);
    for (const child of Array.from(node.children)) observer.observe(child);
    return () => observer.disconnect();
  }, [sync, deps]);

  const scrollByPage = useCallback((direction: -1 | 1, fraction = 0.85) => {
    const node = ref.current;
    if (!node) return;
    node.scrollBy({
      left: direction * Math.round(node.clientWidth * fraction),
      behavior: 'smooth',
    });
  }, []);

  const onWheel = useCallback((event: React.WheelEvent<T>) => {
    const node = ref.current;
    if (!node) return;
    // Trackpads send horizontal deltas of their own; leave those alone and only
    // translate a dominantly vertical wheel.
    if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
    const atStart = node.scrollLeft <= 0;
    const atEnd = node.scrollLeft + node.clientWidth >= node.scrollWidth - 1;
    // At either end, let the page scroll instead of swallowing the gesture.
    if ((event.deltaY < 0 && atStart) || (event.deltaY > 0 && atEnd)) return;
    event.preventDefault();
    node.scrollLeft += event.deltaY;
  }, []);

  return { ref, canScrollLeft, canScrollRight, sync, scrollByPage, onWheel };
}
