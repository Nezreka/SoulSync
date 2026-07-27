import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';

import { LIBRARY_QUERY_KEY } from './-library.api';

/**
 * Fired by library.js when vanilla code changes the artist list under the page.
 *
 * Today that is one caller: closeWatchAllUnwatchedModal, after "Watch All
 * Unwatched" has added artists to the watchlist. The modal is still vanilla
 * (it is invoked, not reimplemented), and it used to refresh by calling
 * loadLibraryArtists() — which now no-ops while React owns the page, so
 * without this the watch badges stay stale until you navigate away and back.
 */
export const LIBRARY_CHANGED_EVENT = 'ss:library-changed';

export function useLibraryChanged(): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    const onChanged = () => {
      // Invalidate rather than patch: Watch All touches an unknown number of
      // artists, so there is nothing local to apply.
      void queryClient.invalidateQueries({ queryKey: LIBRARY_QUERY_KEY });
    };

    window.addEventListener(LIBRARY_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(LIBRARY_CHANGED_EVENT, onChanged);
  }, [queryClient]);
}
