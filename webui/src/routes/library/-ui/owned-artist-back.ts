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
 */
// the artist last shown, across views: an owned artist (the library row) and
// one the library has never heard of (read from the provider) are two views,
// and hopping between them must still name the artist you came from
let lastShownArtist: string | null = null;

export function useArtistBack(artistName: string | undefined): {
  label: string;
  onBack: () => void;
} {
  const router = useRouter();
  const [label, setLabel] = useState(() => backButtonLabel());
  const previousName = useRef<string | null>(null);

  useEffect(() => {
    // an arrival from outside the artist views starts a fresh chain with the
    // page it came from. no arrival is a cold load, or coming back up from an
    // album, and the chain stays as it was
    const arrival = takeArtistArrival();
    if (arrival) {
      // cleared in place: vanilla holds the same array
      backLabelStack().length = 0;
      pushPageOrigin(arrival.pathname);
      lastShownArtist = null;
    }
    setLabel(backButtonLabel());
    // only on arrival: re-running as the chain grows would rewrite it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!artistName) return;
    const previous = previousName.current ?? lastShownArtist;
    if (previous && previous !== artistName) {
      pushArtistOrigin(previous);
    }
    previousName.current = artistName;
    lastShownArtist = artistName;
    setLabel(backButtonLabel());
  }, [artistName]);

  return {
    label,
    onBack: () => {
      if (window.history.length > 1) {
        popBackOrigin();
        router.history.back();
        return;
      }
      // a cold load straight onto an artist: going back would leave soulsync
      popBackOrigin();
      void router.navigate({ href: '/library' });
    },
  };
}
