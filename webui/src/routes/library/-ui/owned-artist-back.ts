import { useRouter } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';

import { takeArtistArrival } from '@/platform/shell/last-navigation';

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
 * back goes to the place the label names, not one step of the browser's
 * history: switching My Library / Discovery or opening an album and coming
 * back up must not turn "Back to Library" into a walk through those steps.
 */
// where each link of the chain leads, beside its label (a module like the
// label chain, so a trip into an album and back keeps it)
const backHrefs: string[] = [];

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
      backHrefs.length = 0;
      const query = arrival.searchStr.replace(/^\?/, '');
      backHrefs.push(query ? `${arrival.pathname}?${query}` : arrival.pathname);
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
        backHrefs.push(`/library?artist=${previousId.current}`);
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
      popBackOrigin();
      const href = backHrefs.pop();
      if (href) {
        void router.navigate({ href });
      } else if (window.history.length > 1) {
        router.history.back();
      } else {
        // a cold load straight onto an artist: going back would leave soulsync
        void router.navigate({ href: '/library' });
      }
    },
  };
}
