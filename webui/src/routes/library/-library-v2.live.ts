import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';

import { REPAIR_PROGRESS_EVENT } from '../tools/-tools.events';
import { LIBRARY_V2_QUERY_KEY } from './-library-v2.api';

/**
 * Fired by library.js when vanilla code changes the artist list under the page.
 *
 * Today that is one caller: closeWatchAllUnwatchedModal, after "Watch All
 * Unwatched" has added artists to the watchlist. The modal is still vanilla (it
 * is invoked, not reimplemented), and it used to refresh by calling
 * loadLibraryArtists() — a function that no longer exists. Library v2 mirrors
 * monitoring through the watchlist, so a watchlist write it did not make is
 * exactly the case where its own cache is stale and nothing tells it.
 */
export const LIBRARY_CHANGED_EVENT = 'ss:library-changed';

export function useLibraryChanged(): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    const onChanged = () => {
      // Invalidate rather than patch: Watch All touches an unknown number of
      // artists, so there is nothing local to apply.
      void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
    };

    window.addEventListener(LIBRARY_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(LIBRARY_CHANGED_EVENT, onChanged);
  }, [queryClient]);
}

/**
 * Catch the library up when a maintenance job finishes.
 *
 * Reported after the AcoustID checker ran: the Check column still read "Not
 * scanned", "und eigentlich sollte es ja automatisch updaten". The stale value
 * had its own cause in the scanner, but the refresh gap is real on its own —
 * a job that rewrites verification state, deletes a file or retags a track
 * changes exactly what this page is showing, and nothing told the page.
 *
 * The signal is already app-wide: core.js re-broadcasts the worker's
 * `repair:progress` socket frames as `ss:repair-progress` on every page. This
 * only has to notice a job LEAVING the running state — invalidating on every
 * frame would refetch the artist view once a second for the whole scan.
 */
export function useMaintenanceChanged(): void {
  const queryClient = useQueryClient();
  const running = useRef<Set<string>>(new Set());

  useEffect(() => {
    const onFrame = (event: Event) => {
      const frames = (event as CustomEvent<Record<string, { status?: string }>>).detail;
      if (!frames || typeof frames !== 'object') return;
      let finished = false;
      for (const [jobId, frame] of Object.entries(frames)) {
        if (String(frame?.status ?? '') === 'running') {
          running.current.add(jobId);
        } else if (running.current.delete(jobId)) {
          // Only for a job this page actually watched start: a trailing frame
          // about a run that ended before you arrived is not news.
          finished = true;
        }
      }
      if (finished) {
        void queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
      }
    };

    window.addEventListener(REPAIR_PROGRESS_EVENT, onFrame);
    return () => window.removeEventListener(REPAIR_PROGRESS_EVENT, onFrame);
  }, [queryClient]);
}
