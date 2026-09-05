import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useRef, useState } from 'react';

import type { RecommendedArtist } from './-discover.recommended';

import { ADV_ENDPOINT } from './-discover.adventurousness';
import { enrichUpdates, watchingIdsFrom, watchlistCheckIds } from './-discover.recommended';
import { watchlistRequest, watchlistToast } from './-discover.your-artists-actions';

/**
 * The recommended-shelf interactions and the adventurousness dial's saves.
 *
 * Recommended, from `toggleRecommendedWatchlist` (1133-1180) and
 * `_enrichRecommendedCarouselCards` (1009-1035): the per-card watchlist
 * toggle through the shared add/remove endpoints, and the image enrichment —
 * ONLY image-less cards are asked about, keyed by the response's source, and
 * the answers land as an id→url map the cards overlay.
 *
 * The dial updates locally during interaction. Its settled commit is serialized
 * with earlier saves; only the latest gesture refreshes recommendation shelves.
 */

export type RecToast = { message: string; level: 'success' | 'info' | 'error' };

export interface RecommendedController {
  watchingIds: Set<string>;
  images: Record<string, string>;
  toggleWatchlist: (artistId: string, artistName: string, source?: string) => Promise<void>;
  /** Ask the enrich endpoint about the image-less cards of one shelf. */
  enrichImages: (items: RecommendedArtist[], source: string) => Promise<void>;
  /** Batch-confirm which cards are already watched (1173-1195). */
  checkWatching: (items: RecommendedArtist[]) => Promise<void>;
}

export function useRecommended(onToast: (toast: RecToast) => void): RecommendedController {
  const toastRef = useRef(onToast);
  toastRef.current = onToast;
  const [watchingIds, setWatchingIds] = useState<Set<string>>(new Set());
  const [images, setImages] = useState<Record<string, string>>({});

  const toggleWatchlist = useCallback(
    async (artistId: string, artistName: string, source = '') => {
      const watching = watchingIds.has(artistId);
      const req = watchlistRequest(watching, { sourceId: artistId, artistName, source });
      try {
        const res = await fetch(req.url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(req.body),
        });
        const data = (await res.json()) as { success?: boolean };
        if (!data.success) return;
        setWatchingIds((prev) => {
          const next = new Set(prev);
          if (watching) next.delete(artistId);
          else next.add(artistId);
          return next;
        });
        const toast = watchlistToast(watching, artistName);
        toastRef.current({ message: toast.message, level: toast.level });
      } catch {
        toastRef.current({ message: 'Failed to update watchlist', level: 'error' });
      }
    },
    [watchingIds],
  );

  const enrichImages = useCallback(async (items: RecommendedArtist[], source: string) => {
    // The response's source picks WHICH id column is asked about (1010-1012).
    const idKey =
      source === 'spotify'
        ? 'spotify_artist_id'
        : source === 'deezer'
          ? 'deezer_artist_id'
          : 'itunes_artist_id';
    const ids = items
      .filter((a) => !a.image_url)
      .map((a) => (a as Record<string, unknown>)[idKey] as string | undefined)
      .filter((id): id is string => Boolean(id));
    if (ids.length === 0) return; // nothing image-less → no request (1014)
    try {
      const res = await fetch('/api/discover/similar-artists/enrich', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artist_ids: ids, source }),
      });
      const data = (await res.json()) as {
        success?: boolean;
        artists?: Record<string, { image_url?: string }>;
      };
      const updates = enrichUpdates(data);
      setImages((prev) => {
        const next = { ...prev };
        for (const update of updates) next[update.artistId] = update.imageUrl;
        return next;
      });
    } catch {
      /* cards keep their placeholders (1034) */
    }
  }, []);

  /**
   * checkRecommendedWatchlistStatuses (1173): one check-batch POST per shelf
   * load, folding every already-watched id into the set the buttons read.
   * A failed probe changes nothing — the optimistic default is "not watched",
   * exactly the vanilla's catch-and-ignore.
   */
  const checkWatching = useCallback(async (items: RecommendedArtist[]) => {
    const artistIds = watchlistCheckIds(items);
    if (!artistIds.length) return;
    try {
      const res = await fetch('/api/watchlist/check-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artist_ids: artistIds }),
      });
      const data = (await res.json()) as { success?: boolean; results?: Record<string, unknown> };
      const watched = watchingIdsFrom(data);
      if (watched.length) {
        setWatchingIds((prev) => {
          const next = new Set(prev);
          for (const id of watched) next.add(id);
          return next;
        });
      }
    } catch {
      /* unknown stays unwatched — the vanilla swallows this too */
    }
  }, []);

  return { watchingIds, images, toggleWatchlist, enrichImages, checkWatching };
}

export interface AdventurousnessController {
  value: number;
  /** Live drag: throttled save so the rec rows re-rank mid-drag (133-139). */
  change: (value: number) => void;
  /** Release: unconditional save (112). */
  commit: (value: number) => Promise<void>;
}

export function useAdventurousness(initial: number): AdventurousnessController {
  const [value, setValue] = useState(initial);
  const saveQueue = useRef(Promise.resolve());
  const revision = useRef(0);
  const savedValue = useRef(initial);
  const queryClient = useQueryClient();

  // Local input is immediate. Persistence is ordered and happens only on commit.
  const change = useCallback((v: number) => {
    revision.current += 1;
    setValue(v);
  }, []);

  const commit = useCallback(
    (v: number) => {
      setValue(v);
      const mine = revision.current;
      saveQueue.current = saveQueue.current.then(async () => {
        if (mine !== revision.current) return;
        try {
          const response = await fetch(ADV_ENDPOINT, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ value: v }),
          });
          if (!response.ok) throw new Error('Preference not saved');
          const data = await response.json();
          if (data.success === false) throw new Error('Preference not saved');
          savedValue.current = v;
          if (mine !== revision.current) return;
          await Promise.all([
            queryClient.refetchQueries({ queryKey: ['discover', 'listening-recs'] }),
            queryClient.refetchQueries({ queryKey: ['discover', 'similar-artists'] }),
          ]);
        } catch {
          if (mine !== revision.current) return;
          setValue(savedValue.current);
          window.showToast?.(
            'Could not save adventurousness. Your previous setting was restored.',
            'error',
          );
        }
      });
      return saveQueue.current;
    },
    [queryClient],
  );

  return { value, change, commit };
}
