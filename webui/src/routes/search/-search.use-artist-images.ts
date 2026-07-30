/**
 * Resolving artist photos the source did not send.
 *
 * Ported from lazyLoadEnhancedSearchArtistImages (search.js:715-771). Two
 * properties of that loop are deliberate and preserved:
 *
 * 1. **Sequential, one request at a time.** The resolver falls back to hitting
 *    iTunes/Deezer server-side for sources that store no artist art (MusicBrainz
 *    has MBIDs and nothing else), so ten parallel requests means ten parallel
 *    third-party lookups. Slow and polite beats fast and rate-limited.
 * 2. **In document order**, library artists first, so the images fill in from the
 *    top of the list the way the user is reading it.
 *
 * The one thing that had to change: the vanilla replaced the placeholder node
 * in-place and glowed the card from here. In React the resolved url goes into
 * state, the card re-renders with it, and CompactItem's own glow effect fires on
 * the new url — so the glow is not this file's business. That is also the more
 * faithful arrangement: renderCompactSection glowed EVERY card with artwork, not
 * only the ones whose photo had to be resolved.
 */

import { useEffect, useRef, useState } from 'react';

import type { SearchArtist } from './-search.types';

import { fetchArtistImage } from './-search.api';

/** Which artists still need a photo, in the order they are rendered. */
export function artistsNeedingImages(
  dbArtists: SearchArtist[],
  artists: SearchArtist[],
  resolved: Record<string, string>,
): SearchArtist[] {
  return [...dbArtists, ...artists].filter((artist) => {
    const id = String(artist.id ?? '');
    if (!id) return false;
    if (resolved[id]) return false;
    return !artist.image_url && !artist.images?.[0]?.url;
  });
}

/**
 * Resolved artist images, keyed by artist id.
 *
 * Restarts whenever the artist lists change — a new query means a new set of
 * cards, and the run in flight is resolving photos nobody is looking at.
 */
export function useArtistImages(
  dbArtists: SearchArtist[],
  artists: SearchArtist[],
  activeSource: string,
): Record<string, string> {
  const [resolved, setResolved] = useState<Record<string, string>>({});

  /**
   * Everything the loop reads goes through a ref, and the effect keys on the
   * artist IDS instead.
   *
   * Neither half is incidental. Depending on the arrays themselves re-runs the
   * loop whenever a caller passes a fresh literal — which is every render, since
   * an absent source's results are rebuilt by emptySourceResults() — and each
   * re-run re-requests whatever was in flight. Depending on `resolved` is worse:
   * the loop writes to it, so it would restart after every single image and
   * duplicate the rest of the queue.
   */
  const listKey = [...dbArtists, ...artists].map((artist) => String(artist.id ?? '')).join('|');
  const inputsRef = useRef({ dbArtists, artists, resolved });
  inputsRef.current = { dbArtists, artists, resolved };

  useEffect(() => {
    const { dbArtists: db, artists: source, resolved: done } = inputsRef.current;
    const pending = artistsNeedingImages(db, source, done);
    if (!pending.length) return;

    let live = true;
    void (async () => {
      for (const artist of pending) {
        if (!live) return;
        const id = String(artist.id ?? '');
        const url = await fetchArtistImage(id, activeSource, artist.name ?? '');
        if (!live) return;
        if (!url) continue;
        setResolved((prev) => ({ ...prev, [id]: url }));
      }
    })();

    return () => {
      live = false;
    };
  }, [listKey, activeSource]);

  return resolved;
}
