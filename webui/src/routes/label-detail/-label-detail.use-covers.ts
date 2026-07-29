import { useCallback, useEffect, useRef, useState } from 'react';

import { CoverLoader } from './-label-detail.covers';

/**
 * Wires the cover queue to what is actually on screen.
 *
 * One IntersectionObserver for the whole grid rather than one per card, with
 * the vanilla's 150px margin so a cover starts resolving just before you reach
 * it. Cards register through the returned `observe`, which hands back its own
 * unobserve — so a card that unmounts (a filter change) stops asking.
 *
 * Resolved urls live in state keyed by release, because the grid re-renders on
 * every filter, sort and ownership update and the art has to survive all of
 * them.
 */
export function useLabelCovers(labelId: string) {
  const [resolved, setResolved] = useState<Record<string, string>>({});
  const loaderRef = useRef<CoverLoader | null>(null);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const pendingRef = useRef(new Map<Element, { key: string; url: string }>());

  if (!loaderRef.current) {
    loaderRef.current = new CoverLoader((key, url) => {
      setResolved((prev) => (prev[key] === url ? prev : { ...prev, [key]: url }));
    });
  }

  useEffect(() => {
    const pending = pendingRef.current;
    if (typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const job = pending.get(entry.target);
          if (!job) return;
          loaderRef.current?.request(job.key, job.url);
          // One shot: the queue remembers both hits and misses, so watching it
          // further can only produce requests the loader will discard.
          observer.unobserve(entry.target);
          pending.delete(entry.target);
        });
      },
      { rootMargin: '150px' },
    );
    observerRef.current = observer;
    return () => {
      observer.disconnect();
      observerRef.current = null;
      pending.clear();
    };
  }, []);

  // A new label means new art: drop the queue and the cache, or the previous
  // label's covers keep the two slots and its urls paint under new cards that
  // happen to share a key.
  useEffect(() => {
    loaderRef.current?.reset();
    setResolved({});
  }, [labelId]);

  useEffect(() => () => loaderRef.current?.dispose(), []);

  const observe = useCallback((key: string, url: string, element: Element) => {
    const observer = observerRef.current;
    if (!observer) {
      // No IntersectionObserver (jsdom, ancient browsers): ask immediately
      // rather than never. The concurrency cap still protects the endpoint.
      loaderRef.current?.request(key, url);
      return () => {};
    }
    pendingRef.current.set(element, { key, url });
    observer.observe(element);
    return () => {
      observer.unobserve(element);
      pendingRef.current.delete(element);
    };
  }, []);

  return { resolved, observe };
}
