import { useCallback, useEffect, useRef, useState } from 'react';

import type { LabelRelease, LabelWatchState } from './-label-detail.types';

import { fetchLabelCatalogPage, fetchOwnedKeys } from './-label-detail.api';
import { releaseKey } from './-label-detail.helpers';

export interface LabelCatalogState {
  releases: LabelRelease[];
  owned: ReadonlySet<string>;
  checked: ReadonlySet<string>;
  name: string;
  total: number;
  artistCount: number;
  watch: LabelWatchState;
  hasMore: boolean;
  loading: boolean;
  /** First page failed. Later pages failing leaves what loaded on screen. */
  error: boolean;
  loadMore: () => void;
  setWatch: (next: LabelWatchState) => void;
}

/**
 * The label's catalog, one page at a time.
 *
 * Mirrors the vanilla `_fetchPage` loop, including the parts that are easy to
 * lose in a port:
 *
 *  - the FIRST page carries the page's identity (resolved name, totals, watch
 *    and backlog state); later pages only append releases.
 *  - ownership is checked per BATCH, not per page-load, and only for releases
 *    not already checked — the vanilla kept a `_checked` set for exactly this,
 *    because a re-render must not re-ask.
 *  - the vanilla guarded stale responses with a request token. This aborts
 *    instead, which also stops the work server-side.
 *
 * A failure on page 1 is an error state; a failure on page 5 is not — the
 * vanilla left the already-loaded grid alone, and so does this.
 */
export function useLabelCatalog(labelId: string, initialName: string): LabelCatalogState {
  const [releases, setReleases] = useState<LabelRelease[]>([]);
  const [owned, setOwned] = useState<ReadonlySet<string>>(new Set());
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set());
  const [name, setName] = useState(initialName);
  const [total, setTotal] = useState(0);
  const [artistCount, setArtistCount] = useState(0);
  const [watch, setWatch] = useState<LabelWatchState>({ watching: false, backlog: false });
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  // Refs, not state: the fetch loop reads these to decide whether to run at
  // all, and reading them from state would need them in the deps and restart
  // the very request they are guarding.
  const pageRef = useRef(0);
  const loadingRef = useRef(false);
  const checkedRef = useRef<Set<string>>(new Set());
  const abortRef = useRef<AbortController | null>(null);

  const nameRef = useRef(initialName);
  nameRef.current = name || initialName;

  const load = useCallback(
    async (nextPage: number) => {
      if (loadingRef.current || !labelId) return;
      loadingRef.current = true;
      setLoading(true);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const data = await fetchLabelCatalogPage(
          labelId,
          nameRef.current,
          nextPage,
          controller.signal,
        );
        if (controller.signal.aborted) return;

        if (nextPage === 1) {
          // 'Label' is the vanilla's last-resort heading: the browse call that
          // linked here may not have carried a name at all.
          setName(data.label?.name || nameRef.current || 'Label');
          setTotal(data.total || 0);
          setArtistCount(data.artist_count || 0);
          setWatch({ watching: Boolean(data.is_watching), backlog: Boolean(data.backlog) });
        }

        const batch = data.releases ?? [];
        setReleases((prev) => (nextPage === 1 ? batch : prev.concat(batch)));
        setHasMore(Boolean(data.has_more));
        setError(false);
        pageRef.current = nextPage;

        const fresh = batch.filter((r) => !checkedRef.current.has(releaseKey(r)));
        if (fresh.length) {
          fresh.forEach((r) => checkedRef.current.add(releaseKey(r)));
          const ownedKeys = await fetchOwnedKeys(fresh, controller.signal);
          if (controller.signal.aborted) return;
          // Marked checked only once the answer is IN. Doing it when the
          // request went out would show "Missing" on every card for as long as
          // the check took.
          setChecked(new Set(checkedRef.current));
          if (ownedKeys.size) {
            setOwned((prev) => {
              const next = new Set(prev);
              ownedKeys.forEach((key) => next.add(key));
              return next;
            });
          }
        }
      } catch {
        if (!controller.signal.aborted && nextPage === 1) setError(true);
      } finally {
        if (!controller.signal.aborted) {
          loadingRef.current = false;
          setLoading(false);
        }
      }
    },
    [labelId],
  );

  /**
   * A new label is a new page — and the previous label's releases must never
   * paint under the new label's heading.
   *
   * Done DURING render, not in an effect: an effect runs after the browser has
   * painted, so there is a real frame where sixty releases from the label you
   * just left sit under the name of the one you just opened. This is React's
   * documented "adjust state when a prop changes" pattern; the re-render
   * happens before anything is committed to the screen.
   */
  const renderedLabelRef = useRef(labelId);
  if (renderedLabelRef.current !== labelId) {
    renderedLabelRef.current = labelId;
    abortRef.current?.abort();
    pageRef.current = 0;
    loadingRef.current = false;
    checkedRef.current = new Set();
    nameRef.current = initialName;
    setReleases([]);
    setOwned(new Set());
    setChecked(new Set());
    setName(initialName);
    setTotal(0);
    setArtistCount(0);
    setWatch({ watching: false, backlog: false });
    setHasMore(false);
    setError(false);
    setLoading(true);
  }

  useEffect(() => {
    void load(1);
    return () => abortRef.current?.abort();
    // `load` is keyed to labelId; initialName must not restart the fetch, or
    // resolving the canonical name would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [labelId, load]);

  const loadMore = useCallback(() => {
    if (loadingRef.current || !hasMore) return;
    void load(pageRef.current + 1);
  }, [hasMore, load]);

  return {
    releases,
    owned,
    checked,
    name,
    total,
    artistCount,
    watch,
    hasMore,
    loading,
    error,
    loadMore,
    setWatch,
  };
}
