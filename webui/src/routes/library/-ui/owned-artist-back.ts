import { useRouter } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';

import { takeArtistArrival } from '@/platform/shell/last-navigation';
import { getShellRouteByPageId, type ShellPageId } from '@/platform/shell/route-manifest';

import {
  backButtonLabel,
  backLabelStack,
  popBackOrigin,
  pushArtistOrigin,
  pushPageOrigin,
} from '../../artist-detail/-artist-detail.back-label';

/**
 * dev's back button on the library artist page: labelled with where you came
 * from ("← Back to Search", "← Back to Library", "← Back to Aphex Twin") and
 * going back there.
 *
 * arriving from a page starts a fresh chain with that page; hopping artist to
 * artist pushes the one you left; back walks down the chain. coming back up
 * from one of the artist's albums keeps the chain as it was.
 *
 * back goes to the place the label names, never one step of the browser's
 * history: switching My Library / Discovery, or an album and back up, must
 * not turn "Back to Library" into a walk through those steps (or a loop
 * between the album and its artist). each entry carries where it leads; one
 * the vanilla pushed goes to its page, an empty chain to the library.
 */
export function useArtistBack(
  artistName: string | undefined,
  artistId?: number,
): {
  label: string;
  onBack: () => void;
} {
  const router = useRouter();
  const [label, setLabel] = useState(() => backButtonLabel());
  const previousName = useRef<string | null>(null);
  const previousId = useRef<number | undefined>(undefined);

  useEffect(() => {
    // an arrival from outside the artist views starts a fresh chain with the
    // page it came from. no arrival is a cold load, or coming back up from an
    // album, and the chain stays as it was
    const arrival = takeArtistArrival();
    if (arrival) {
      // cleared in place: vanilla holds the same array
      backLabelStack().length = 0;
      pushPageOrigin(arrival.pathname);
      const query = arrival.searchStr.replace(/^\?/, '');
      const top = backLabelStack().at(-1);
      if (top) top.href = query ? `${arrival.pathname}?${query}` : arrival.pathname;
    }
    setLabel(backButtonLabel());
    // only on arrival: re-running as the chain grows would rewrite it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!artistName) return;
    if (previousName.current && previousName.current !== artistName) {
      // a step back down the chain pushes nothing, and neither does its link
      const links = backLabelStack().length;
      pushArtistOrigin(previousName.current);
      if (backLabelStack().length > links && previousId.current) {
        backLabelStack().at(-1)!.href = `/library?artist=${previousId.current}`;
      }
    }
    previousName.current = artistName;
    previousId.current = artistId;
    setLabel(backButtonLabel());
    // the name arrives with the artist's data, the id with the url: the pair
    // is taken when the name changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artistName]);

  return {
    label,
    onBack: () => {
      const entry = popBackOrigin();
      const page =
        entry?.type === 'page' && entry.pageId
          ? getShellRouteByPageId(entry.pageId as ShellPageId)?.path
          : undefined;
      void router.navigate({ href: entry?.href ?? page ?? '/library' });
    },
  };
}
