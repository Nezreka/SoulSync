import { useRouter, useRouterState } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';

import { artistNavigationOrigin } from '@/platform/shell/last-navigation';

import {
  backOriginHref,
  backButtonLabel,
  ensurePageOrigin,
  popBackOrigin,
  pushArtistOrigin,
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
export function useArtistBack(artistName: string | undefined): {
  label: string;
  onBack: () => void;
} {
  const router = useRouter();
  const [label, setLabel] = useState(() => backButtonLabel());
  const previousName = useRef<string | null>(null);
  const previousHref = useRef<string | null>(null);
  const currentHref = useRouterState({ select: (state) => state.location.href });

  useEffect(() => {
    // an arrival from outside the artist views starts a fresh chain with the
    // page it came from. no arrival is a cold load, or coming back up from an
    // album, and the chain stays as it was
    const arrival = artistNavigationOrigin();
    if (arrival) ensurePageOrigin(arrival.pathname, arrival.searchStr);
    setLabel(backButtonLabel());
    // only on arrival: re-running as the chain grows would rewrite it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!artistName) return;
    if (previousName.current && previousName.current !== artistName) {
      // A step back down the chain pushes nothing. A forward hop stores the
      // complete prior view, including header, discovery mode, and filters.
      pushArtistOrigin(previousName.current, previousHref.current ?? undefined);
    }
    previousName.current = artistName;
    previousHref.current = currentHref;
    setLabel(backButtonLabel());
    // `currentHref` belongs to the artist whose name just resolved. Do not
    // update it while the next artist is still loading.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artistName]);

  return {
    label,
    onBack: () => {
      void router.navigate({ href: backOriginHref(popBackOrigin()) });
    },
  };
}
