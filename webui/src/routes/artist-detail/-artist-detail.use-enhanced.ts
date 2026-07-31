import { useEffect, useRef, useState } from 'react';

import type { EnhancedData } from './-artist-detail.enhanced';

export interface EnhancedState {
  data: EnhancedData | null;
  status: { loading: boolean; error: string };
}

const IDLE: EnhancedState = { data: null, status: { loading: false, error: '' } };

/**
 * Loads /api/library/artist/<id>/enhanced (loadEnhancedViewData, library.js:2857).
 *
 * Fetched lazily — only once the user actually switches to Enhanced — and kept
 * afterwards, because the vanilla only re-fetched when it had no data.
 * Switching back to Standard and returning must not re-request a payload that
 * carries every track of every album.
 *
 * "Already attempted" is a REF, not state. Putting it in the effect's deps made
 * the effect re-run the moment it set `loading`, and the re-run's cleanup
 * aborted the request it had just started — leaving the view on "Loading..."
 * forever, or resolving first and passing by luck.
 */
export function useEnhancedData(artistId: unknown, enabled: boolean): EnhancedState {
  const [state, setState] = useState<EnhancedState>(IDLE);
  const attemptedRef = useRef<unknown>(undefined);

  /**
   * A new artist invalidates the payload; kept across a Standard/Enhanced
   * toggle, never across an artist change.
   *
   * Not test-observable: when Enhanced is on, the effect below re-runs on the
   * id change and clears the state itself, and when it is off nothing renders
   * the payload anyway. Kept so the invariant holds independently of effect
   * ordering rather than as a consequence of it.
   */
  const [seenArtist, setSeenArtist] = useState(artistId);
  if (artistId !== seenArtist) {
    setSeenArtist(artistId);
    // Belt and braces: the guard below compares the ref against the CURRENT
    // artist, so a stale id would not block the new fetch anyway. Clearing it
    // survives mutation for that reason.
    attemptedRef.current = undefined;
    setState(IDLE);
  }

  useEffect(() => {
    if (!enabled || !artistId) return;
    // One attempt per artist — the vanilla did not retry a failure either.
    if (attemptedRef.current === artistId) return;
    attemptedRef.current = artistId;

    const controller = new AbortController();
    setState({ data: null, status: { loading: true, error: '' } });

    void (async () => {
      try {
        const response = await fetch(`/api/library/artist/${artistId}/enhanced`, {
          signal: controller.signal,
        });
        const data = await response.json();
        if (!data.success) throw new Error(data.error || 'Failed to load enhanced data');
        setState({ data, status: { loading: false, error: '' } });
      } catch (error) {
        if (controller.signal.aborted) return;
        setState({
          data: null,
          status: { loading: false, error: (error as Error).message || String(error) },
        });
      }
    })();

    return () => controller.abort();
  }, [artistId, enabled]);

  return state;
}
