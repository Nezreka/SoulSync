import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { loadAdminName, myPermissions, offTitle, permMode } from '@/platform/shell/my-permissions';

import type {
  AudiobookItem,
  AudiobookNarratorMode,
  AudiobookPlayback,
  AudiobookWishlistCounts,
} from '../-audiobooks.types';

import { addToWishlist, fetchWishlist, removeFromWishlist, requestBook } from '../-audiobooks.api';

/**
 * Sample playback, shared by every /audiobooks/* route.
 *
 * Every Audible title ships a real preview mp3, so a card anywhere on the page
 * can be auditioned without leaving it. The player bar lives in the layout and
 * survives navigation between browse and detail, which is the whole point of
 * hoisting this to a context rather than keeping it per page.
 */

export interface AudiobookContextValue {
  playback: AudiobookPlayback | null;
  /** Start a sample, or toggle it when the same title is already loaded. */
  playSample: (book: AudiobookItem) => void;
  togglePlay: () => void;
  closePlayer: () => void;
  /** True when this ASIN is the one currently loaded and running. */
  isPlaying: (asin: string) => boolean;
  /** True when this ASIN is loaded at all, playing or paused. */
  isLoaded: (asin: string) => boolean;

  /** True when the book is on the wishlist. */
  isWishlisted: (asin: string) => boolean;
  /** True while a toggle for this ASIN is in flight. */
  isWishlistBusy: (asin: string) => boolean;
  /** True when this profile asked an admin for the book and is waiting (#2188). */
  isRequested: (asin: string) => boolean;
  /**
   * Add or remove; returns the state it ended up in. The narrator mode only
   * applies when adding — removing needs no question.
   */
  toggleWishlist: (book: AudiobookItem, mode?: AudiobookNarratorMode) => Promise<boolean>;
  wishlistCounts: AudiobookWishlistCounts;
  refreshWishlist: () => Promise<void>;
}

const AudiobookContext = createContext<AudiobookContextValue | null>(null);

export function useAudiobookContext(): AudiobookContextValue {
  const ctx = useContext(AudiobookContext);
  if (!ctx) throw new Error('useAudiobookContext must be used within AudiobookProvider');
  return ctx;
}

const EMPTY_COUNTS: AudiobookWishlistCounts = {
  wanted: 0,
  searching: 0,
  grabbed: 0,
  done: 0,
  failed: 0,
  total: 0,
};

export function AudiobookProvider({ children }: { children: ReactNode }) {
  const [playback, setPlayback] = useState<AudiobookPlayback | null>(null);

  /**
   * The wishlist is held as a set of ASINs rather than asked about per card.
   * A browse page renders well over a hundred covers, and a "is this
   * wishlisted" request per card would be a hundred round trips to answer a
   * question one request already answers.
   */
  const [wishlisted, setWishlisted] = useState<Set<string>>(() => new Set());
  const [wishlistCounts, setWishlistCounts] = useState<AudiobookWishlistCounts>(EMPTY_COUNTS);
  const [requested, setRequested] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState<Set<string>>(() => new Set());

  const refreshWishlist = useCallback(async () => {
    const view = await fetchWishlist();
    setWishlisted(new Set(view.items.map((item) => item.asin)));
    setWishlistCounts(view.counts);
    setRequested(new Set(view.requested));
  }, []);

  useEffect(() => {
    void refreshWishlist();
  }, [refreshWishlist]);

  const isWishlisted = useCallback((asin: string) => wishlisted.has(asin), [wishlisted]);
  const isWishlistBusy = useCallback((asin: string) => busy.has(asin), [busy]);
  const isRequested = useCallback((asin: string) => requested.has(asin), [requested]);

  /** Ask first (#2188): the heart files a request for an admin instead */
  const askForBook = useCallback(
    async (book: AudiobookItem, mode: AudiobookNarratorMode): Promise<boolean> => {
      const asin = book.asin;
      if (requested.has(asin)) return false;
      setBusy((current) => new Set(current).add(asin));
      const res = await requestBook(asin, mode);
      setBusy((current) => {
        const next = new Set(current);
        next.delete(asin);
        return next;
      });
      if (res.ok) {
        setRequested((current) => new Set(current).add(asin));
        const admin = await loadAdminName();
        window.showToast?.(
          res.already
            ? `You already asked for "${book.title}"`
            : `Asked ${admin || 'your admin'} for "${book.title}". You'll hear when it's approved`,
          'success',
        );
      } else {
        window.showToast?.(res.error || 'Could not send that request', 'error');
      }
      return false;
    },
    [requested],
  );

  const toggleWishlist = useCallback(
    async (book: AudiobookItem, mode: AudiobookNarratorMode = 'exact'): Promise<boolean> => {
      const asin = book.asin;
      if (!asin || busy.has(asin)) return wishlisted.has(asin);

      const wanted = !wishlisted.has(asin);
      if (wanted && permMode('books') === 'ask') return askForBook(book, mode);
      // the add button is hidden for a profile that can't get books (#2166);
      // a stray click (a stale page after an admin's change) says why calmly
      // instead of 403ing, and the refusal isn't kept as a notification
      if (wanted && permMode('books') !== 'download') {
        window.showToast?.(
          `${offTitle(myPermissions(), 'books')}. Ask your admin if you'd like it.`,
          'info',
          null,
          { history: false },
        );
        return false;
      }
      setBusy((current) => new Set(current).add(asin));
      // Optimistic: the button answers immediately and rolls back if the
      // server disagrees, because a wishlist toggle that waits on an indexer
      // round trip feels broken.
      setWishlisted((current) => {
        const next = new Set(current);
        if (wanted) next.add(asin);
        else next.delete(asin);
        return next;
      });

      const ok = wanted ? await addToWishlist(asin, mode) : await removeFromWishlist(asin);
      if (!ok) {
        setWishlisted((current) => {
          const next = new Set(current);
          if (wanted) next.delete(asin);
          else next.add(asin);
          return next;
        });
      } else {
        void refreshWishlist();
      }

      setBusy((current) => {
        const next = new Set(current);
        next.delete(asin);
        return next;
      });
      return ok ? wanted : !wanted;
    },
    [busy, wishlisted, refreshWishlist, askForBook],
  );

  const playSample = useCallback((book: AudiobookItem) => {
    if (!book.sample_url) return;
    setPlayback((current) => {
      // Same title already loaded: this is a toggle, not a restart. Restarting
      // would drop the listener back to zero mid-preview.
      if (current && current.asin === book.asin) {
        return { ...current, isPlaying: !current.isPlaying };
      }
      return {
        asin: book.asin,
        title: book.title,
        author: book.author_names[0] || '',
        narrator: book.narrator_names[0] || '',
        coverUrl: book.cover_url || book.cover_url_large,
        sampleUrl: book.sample_url as string,
        isPlaying: true,
      };
    });
  }, []);

  const togglePlay = useCallback(() => {
    setPlayback((current) => (current ? { ...current, isPlaying: !current.isPlaying } : current));
  }, []);

  const closePlayer = useCallback(() => setPlayback(null), []);

  const isPlaying = useCallback(
    (asin: string) => Boolean(playback && playback.asin === asin && playback.isPlaying),
    [playback],
  );

  const isLoaded = useCallback(
    (asin: string) => Boolean(playback && playback.asin === asin),
    [playback],
  );

  const value = useMemo(
    () => ({
      playback,
      playSample,
      togglePlay,
      closePlayer,
      isPlaying,
      isLoaded,
      isWishlisted,
      isWishlistBusy,
      isRequested,
      toggleWishlist,
      wishlistCounts,
      refreshWishlist,
    }),
    [
      playback,
      playSample,
      togglePlay,
      closePlayer,
      isPlaying,
      isLoaded,
      isWishlisted,
      isWishlistBusy,
      isRequested,
      toggleWishlist,
      wishlistCounts,
      refreshWishlist,
    ],
  );

  return <AudiobookContext.Provider value={value}>{children}</AudiobookContext.Provider>;
}
