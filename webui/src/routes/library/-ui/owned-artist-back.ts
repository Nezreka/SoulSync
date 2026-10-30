import { useRouter } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';

import { lastNavigationFrom } from '@/platform/shell/last-navigation';

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
export function useArtistBack(artistName: string | undefined): {
  label: string;
  onBack: () => void;
} {
  const router = useRouter();
  const [label, setLabel] = useState(() => backButtonLabel());
  const previousName = useRef<string | null>(null);

  useEffect(() => {
    // a cold load has no previous location: keep whatever chain vanilla's
    // navigateToArtistDetail left for it
    const cameFrom = lastNavigationFrom();
    const params = new URLSearchParams(cameFrom?.searchStr ?? '');
    const fromInsideArtist =
      cameFrom?.pathname.endsWith('/library') && (params.has('album') || params.has('artist'));
    if (cameFrom && !fromInsideArtist) {
      // cleared in place: vanilla holds the same array
      backLabelStack().length = 0;
      pushPageOrigin(cameFrom?.pathname);
    }
    setLabel(backButtonLabel());
    // only on arrival: re-running as the chain grows would rewrite it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!artistName) return;
    if (previousName.current && previousName.current !== artistName) {
      pushArtistOrigin(previousName.current);
    }
    previousName.current = artistName;
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
