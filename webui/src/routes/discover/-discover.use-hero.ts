import { useCallback, useEffect, useRef, useState } from 'react';

import type { HeroWatchlistButton, WatchAllPhase } from './-discover.hero';
import type { DiscoverHeroArtist } from './-discover.types';

import {
  HERO_SLIDE_MS,
  heroArtistId,
  heroAutoAdvances,
  heroJumpIndex,
  heroNextIndex,
  heroWatchlistButtonState,
  heroWatchlistCheckBody,
  watchAllPayload,
} from './-discover.hero';
import { watchlistRequest, watchlistToast } from './-discover.your-artists-actions';

/**
 * The hero billboard's controller — rotation, the per-artist watchlist
 * button, and Watch All.
 *
 * Transcribed from `loadDiscoverHero` (413-446), `navigateDiscoverHero` /
 * `jumpToHeroArtist` (1234-1258), `checkAndUpdateDiscoverHeroWatchlistButton`
 * (547-577), `toggleDiscoverHeroWatchlist` (581-597, which delegates to the
 * shared `toggleWatchlist` — the same add/remove endpoints the module's
 * `watchlistRequest` builds) and `watchAllHeroArtists` +
 * `checkAllHeroWatchlistStatus` (599-668, 1200-1232).
 *
 * Rotation auto-advances every 8s ONLY with more than one artist, pauses while
 * the hero is in use, and restarts on a slide change. The watchlist button is
 * NULL until membership is confirmed; a failed check cannot infer membership.
 */

export type HeroToast = { message: string; level: 'success' | 'info' | 'error' };

export interface HeroController {
  artists: DiscoverHeroArtist[];
  index: number;
  artist: DiscoverHeroArtist | null;
  watchlist: HeroWatchlistButton | null;
  watchAllPhase: WatchAllPhase;
  watchlistPending: boolean;
  navigate: (direction: number) => void;
  jump: (index: number) => void;
  toggleWatchlist: () => Promise<void>;
  watchAll: () => Promise<void>;
  /** hold rotation while someone is reading or using the hero. */
  setPaused: (paused: boolean) => void;
}

export function useHero(
  artists: DiscoverHeroArtist[],
  onToast: (toast: HeroToast) => void,
): HeroController {
  const toastRef = useRef(onToast);
  toastRef.current = onToast;

  const [index, setIndex] = useState(0);
  const [watchlist, setWatchlist] = useState<HeroWatchlistButton | null>(null);
  const [watchAllPhase, setWatchAllPhase] = useState<WatchAllPhase>('idle');
  const checkGen = useRef(0);
  const mutationGen = useRef(0);
  const inFlight = useRef(false);
  const [watchlistPending, setWatchlistPending] = useState(false);

  const artist = artists[index] ?? null;
  const artistKey = heroArtistId(artist);

  // A fresh artist list restarts at the first slide (427-428).
  const listKey = JSON.stringify(artists.map((a) => [a.source, heroArtistId(a)]));
  const viewKey = JSON.stringify([listKey, artist?.source, artistKey]);
  const context = useRef({ listKey, viewKey, listGeneration: 0, viewGeneration: 0 });
  if (context.current.listKey !== listKey) {
    context.current.listKey = listKey;
    context.current.listGeneration += 1;
  }
  if (context.current.viewKey !== viewKey) {
    context.current.viewKey = viewKey;
    context.current.viewGeneration += 1;
  }
  useEffect(
    () => () => {
      context.current.viewGeneration += 1;
      context.current.listGeneration += 1;
    },
    [],
  );
  useEffect(() => {
    setIndex(0);
    setWatchAllPhase('idle');
  }, [listKey]);

  // Keep the current hero's pause and restart behavior while changing its
  // watchlist acknowledgement handling.
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (!heroAutoAdvances(artists.length) || paused) return;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    const timer = setTimeout(() => {
      setIndex((i) => heroNextIndex(i, 1, artists.length));
    }, HERO_SLIDE_MS);
    return () => clearTimeout(timer);
  }, [artists.length, index, paused]);

  // Checks only hydrate the current context and cannot supersede a newer
  // mutation. In particular, unknown membership is distinct from not watched.
  useEffect(() => {
    setWatchlist(null);
    const gen = ++checkGen.current;
    const view = context.current.viewGeneration;
    if (!artistKey) return;
    void checkMembership(artistKey)
      .then((watching) => {
        if (checkGen.current !== gen || context.current.viewGeneration !== view) return;
        setWatchlist(heroWatchlistButtonState({ success: true, is_watching: watching }));
      })
      .catch(() => {
        /* unknown until a successful check or mutation */
      });
    return () => {
      checkGen.current += 1;
    };
  }, [artistKey, viewKey]);

  // Probe completion belongs to this list and precedes any later mutation.
  useEffect(() => {
    if (artists.length === 0) return;
    let cancelled = false;
    const list = context.current.listGeneration;
    const mutation = mutationGen.current;
    void (async () => {
      for (const a of artists) {
        const id = heroArtistId(a);
        if (!id) return;
        try {
          if (!(await checkMembership(id))) return;
        } catch {
          return;
        }
        if (
          cancelled ||
          context.current.listGeneration !== list ||
          mutationGen.current !== mutation
        )
          return;
      }
      if (
        !cancelled &&
        context.current.listGeneration === list &&
        mutationGen.current === mutation
      ) {
        setWatchAllPhase('done');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [listKey, artists]);

  const navigate = useCallback(
    (direction: number) => {
      if (artists.length === 0) return;
      setIndex((i) => heroNextIndex(i, direction, artists.length));
    },
    [artists.length],
  );

  const jump = useCallback(
    (target: number) => {
      setIndex((i) => heroJumpIndex(i, target, artists.length));
    },
    [artists.length],
  );

  const toggleWatchlist = useCallback(async () => {
    if (!artist || !artistKey || inFlight.current) return;
    inFlight.current = true;
    setWatchlistPending(true);
    const view = context.current.viewGeneration;
    const list = context.current.listGeneration;
    const acknowledgedView = context.current.viewKey;
    const isCurrent = () =>
      context.current.viewGeneration === view && context.current.listGeneration === list;
    ++checkGen.current;
    ++mutationGen.current;
    try {
      // The visible Add control also recovers a failed/unfinished membership
      // check. Refresh first; the next activation has a confirmed intent.
      if (!watchlist) {
        const watching = await checkMembership(artistKey);
        if (!isCurrent()) return;
        setWatchlist(heroWatchlistButtonState({ success: true, is_watching: watching }));
        toastRef.current({
          message: 'Watchlist status refreshed. Try the action again.',
          level: 'info',
        });
        return;
      }
      const watching = watchlist.watching;
      const req = watchlistRequest(watching, {
        sourceId: artistKey,
        artistName: artist.artist_name ?? '',
        source: artist.source ?? '',
      });
      const res = await fetch(req.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(req.body),
      });
      const data: unknown = await res.json();
      if (!res.ok || !acknowledged(data)) throw new Error('Watchlist update refused');
      if (context.current.listGeneration === list) {
        ++mutationGen.current;
        if (watching) setWatchAllPhase('idle');
      }
      // A navigation round trip can return to this artist before its commit.
      // Reconcile that same identity while still excluding other views, list
      // replacements and unmounts. Older checks cannot undo this acknowledgement.
      if (context.current.listGeneration !== list || context.current.viewKey !== acknowledgedView)
        return;
      ++checkGen.current;
      const toast = watchlistToast(watching, artist.artist_name ?? '');
      toastRef.current(toast);
      setWatchlist(heroWatchlistButtonState({ success: true, is_watching: !watching }));
    } catch {
      if (isCurrent()) {
        toastRef.current({
          message: watchlist
            ? 'Failed to update watchlist. Please try again.'
            : 'Could not check watchlist status. Please try again.',
          level: 'error',
        });
      }
    } finally {
      inFlight.current = false;
      setWatchlistPending(false);
    }
  }, [artist, artistKey, watchlist]);

  const watchAll = useCallback(async () => {
    if (watchAllPhase === 'done' || artists.length === 0 || inFlight.current) return;
    inFlight.current = true;
    setWatchlistPending(true);
    setWatchAllPhase('busy');
    const list = context.current.listGeneration;
    ++checkGen.current;
    ++mutationGen.current;
    try {
      const payload = watchAllPayload(artists);
      const res = await fetch('/api/watchlist/add-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artists: payload }),
      });
      const data: unknown = await res.json();
      if (!res.ok || !batchAcknowledged(data, payload.length))
        throw new Error('Incomplete watchlist update');
      if (context.current.listGeneration !== list) return;
      ++checkGen.current;
      ++mutationGen.current;
      setWatchAllPhase('done');
      setWatchlist(heroWatchlistButtonState({ success: true, is_watching: true }));
    } catch {
      if (context.current.listGeneration === list) {
        setWatchAllPhase('idle');
        toastRef.current({
          message: 'Could not add every artist to watchlist. Please try again.',
          level: 'error',
        });
      }
    } finally {
      inFlight.current = false;
      setWatchlistPending(false);
    }
  }, [watchAllPhase, artists]);

  return {
    artists,
    index,
    artist,
    watchlist,
    watchAllPhase,
    watchlistPending,
    navigate,
    jump,
    toggleWatchlist,
    watchAll,
    setPaused,
  };
}

function acknowledged(data: unknown): data is Record<string, unknown> {
  return typeof data === 'object' && data !== null && 'success' in data && data.success === true;
}

async function checkMembership(artistKey: string): Promise<boolean> {
  const res = await fetch('/api/watchlist/check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(heroWatchlistCheckBody(artistKey)),
  });
  const data: unknown = await res.json();
  if (!res.ok || !acknowledged(data) || typeof data.is_watching !== 'boolean') {
    throw new Error('Could not check watchlist status');
  }
  return data.is_watching;
}

function batchAcknowledged(data: unknown, count: number): boolean {
  // This endpoint returns success:true for partial failures and silently omits
  // invalid input rows. Only complete counts with no rejections confirm all.
  if (!acknowledged(data)) return false;
  const { added, skipped, rejected } = data;
  return (
    typeof added === 'number' &&
    Number.isSafeInteger(added) &&
    added >= 0 &&
    typeof skipped === 'number' &&
    Number.isSafeInteger(skipped) &&
    skipped >= 0 &&
    added + skipped === count &&
    Array.isArray(rejected) &&
    rejected.length === 0
  );
}
