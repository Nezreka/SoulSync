import { useCallback, useEffect, useRef, useState } from 'react';

import type { HeroWatchlistButton, WatchAllPhase } from './-discover.hero';
import type { DiscoverHeroArtist } from './-discover.types';

import {
  allWatched,
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
 * Rotation auto-advances every 8s ONLY with more than one artist; the vanilla
 * never pauses it for manual navigation, and neither does this. The button is
 * NULL until its check answers — a failed check says nothing about
 * membership. The all-watched probe short-circuits on the first miss, exactly
 * as the vanilla's early break.
 */

export type HeroToast = { message: string; level: 'success' | 'info' | 'error' };

export interface HeroController {
  artists: DiscoverHeroArtist[];
  index: number;
  artist: DiscoverHeroArtist | null;
  watchlist: HeroWatchlistButton | null;
  watchAllPhase: WatchAllPhase;
  navigate: (direction: number) => void;
  jump: (index: number) => void;
  toggleWatchlist: () => Promise<void>;
  watchAll: () => Promise<void>;
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

  const artist = artists[index] ?? null;
  const artistKey = heroArtistId(artist);

  // A fresh artist list restarts at the first slide (427-428).
  const listKey = artists.map((a) => heroArtistId(a)).join('|');
  useEffect(() => {
    setIndex(0);
  }, [listKey]);

  // Auto-advance every 8s, only with more than one artist (432-437).
  useEffect(() => {
    // Faithful to 432 — though with ONE artist the interval would only step
    // 0→0 (mod 1), so a mutant removing this guard is EQUIVALENT; the guard
    // saves a pointless timer, not a visible behaviour.
    if (!heroAutoAdvances(artists.length)) return;
    const timer = setInterval(() => {
      setIndex((i) => heroNextIndex(i, 1, artists.length));
    }, HERO_SLIDE_MS);
    return () => clearInterval(timer);
  }, [artists.length]);

  // The per-artist button check (547-577), re-run per slide; null until
  // answered, and a stale answer never lands on a newer slide.
  useEffect(() => {
    setWatchlist(null);
    if (!artistKey) return;
    checkGen.current += 1;
    const gen = checkGen.current;
    void (async () => {
      try {
        const res = await fetch('/api/watchlist/check', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(heroWatchlistCheckBody(artistKey)),
        });
        const data = (await res.json()) as { success?: boolean; is_watching?: boolean };
        if (checkGen.current !== gen) return;
        setWatchlist(heroWatchlistButtonState(data));
      } catch {
        /* the button stays as it was (573-575) */
      }
    })();
  }, [artistKey]);

  // The all-watched probe on load, short-circuiting on the first miss
  // (1200-1232).
  useEffect(() => {
    if (artists.length === 0) return;
    let cancelled = false;
    void (async () => {
      const results: { success?: boolean; is_watching?: boolean }[] = [];
      for (const a of artists) {
        const id = heroArtistId(a);
        if (!id) return;
        try {
          const res = await fetch('/api/watchlist/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(heroWatchlistCheckBody(id)),
          });
          const data = (await res.json()) as { success?: boolean; is_watching?: boolean };
          results.push(data);
          if (!data.success || !data.is_watching) break; // early break (1215)
        } catch {
          return; // probe failure leaves the button idle
        }
      }
      // The length check is defense-in-depth and EQUIVALENT in practice:
      // every early-broken result set already contains a false entry, and the
      // catch path returns before evaluating at all.
      if (!cancelled && results.length === artists.length && allWatched(results)) {
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
    if (!artist || !artistKey) return;
    const watching = Boolean(watchlist?.watching);
    const req = watchlistRequest(watching, {
      sourceId: artistKey,
      artistName: artist.artist_name ?? '',
      source: artist.source ?? '',
    });
    try {
      const res = await fetch(req.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(req.body),
      });
      if (!res.ok) return;
      const toast = watchlistToast(watching, artist.artist_name ?? '');
      toastRef.current({ message: toast.message, level: toast.level });
      setWatchlist(heroWatchlistButtonState({ success: true, is_watching: !watching }));
    } catch {
      toastRef.current({ message: 'Failed to update watchlist', level: 'error' });
    }
  }, [artist, artistKey, watchlist]);

  const watchAll = useCallback(async () => {
    // An all-watched button is inert (601-602).
    if (watchAllPhase === 'done' || artists.length === 0) return;
    setWatchAllPhase('busy');
    try {
      const res = await fetch('/api/watchlist/add-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // The endpoint reads an { artists } envelope (617) — the module's
        // payload is the array inside it.
        body: JSON.stringify({ artists: watchAllPayload(artists) }),
      });
      const data = (await res.json()) as { success?: boolean };
      if (data.success) {
        setWatchAllPhase('done');
        // The current slide's own button follows (628-632).
        setWatchlist(heroWatchlistButtonState({ success: true, is_watching: true }));
      } else {
        setWatchAllPhase('idle');
      }
    } catch {
      setWatchAllPhase('idle');
    }
  }, [watchAllPhase, artists]);

  return {
    artists,
    index,
    artist,
    watchlist,
    watchAllPhase,
    navigate,
    jump,
    toggleWatchlist,
    watchAll,
  };
}
