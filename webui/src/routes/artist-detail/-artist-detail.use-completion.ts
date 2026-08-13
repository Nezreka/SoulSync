import { useEffect, useRef, useState } from 'react';

import type { Discography, DiscographyBucket } from './-artist-detail.types';

import {
  applyCompletionEvent,
  type CompletionEvent,
  completionStreamPayload,
  emptyStreamCounts,
  parseSseFrames,
  type StreamCounts,
  tallyEvent,
} from './-artist-detail.completion';

export interface CompletionState {
  /** The discography with every resolved ownership merged in. */
  discography: Discography;
  /** Per-bucket running tallies, or null before the stream starts. */
  counts: StreamCounts | null;
  /** True from the first event until the stream ends. */
  streaming: boolean;
  /**
   * True only once the terminal `complete` frame arrives.
   *
   * This is what triggered recalculateSummaryStats in the vanilla, and it is
   * deliberately NOT the same as "streaming stopped": an aborted or truncated
   * stream leaves the bars showing the running tallies rather than recomputing
   * them from a discography that was never fully checked.
   */
  completed: boolean;
}

/**
 * Runs /api/library/completion-stream and merges results as they arrive.
 *
 * The abort contract is the load-bearing part: the vanilla kept ONE
 * AbortController on artistDetailPageState and aborted it both when a new
 * check started and when the user navigated away
 * (clearArtistDetailPageState). Without that, switching artists quickly leaves
 * two streams writing into the same page and the slower one wins.
 *
 * `enabled` is false for source artists and for a discography with nothing
 * left to check — opening a stream that can never report anything.
 */
export function useCompletionStream(
  artistName: string | undefined,
  initial: Discography,
  enabled: boolean,
): CompletionState {
  const [state, setState] = useState<CompletionState>({
    discography: initial,
    counts: null,
    streaming: false,
    completed: false,
  });

  // Kept in a ref so the merge always sees the latest merged copy rather than
  // the value captured when the effect ran.
  const discographyRef = useRef(initial);
  const countsRef = useRef<StreamCounts | null>(null);
  const completeRef = useRef(false);

  /**
   * Reset DURING render, not in an effect. An effect would leave one committed
   * frame showing the previous artist's merged discography under the new
   * artist's hero; adjusting state here re-renders before anything is shown.
   */
  const [seenInitial, setSeenInitial] = useState(initial);
  if (initial !== seenInitial) {
    setSeenInitial(initial);
    discographyRef.current = initial;
    countsRef.current = null;
    completeRef.current = false;
    setState({ discography: initial, counts: null, streaming: false, completed: false });
  }

  useEffect(() => {
    if (!enabled || !artistName) return;

    const controller = new AbortController();
    countsRef.current = emptyStreamCounts();
    completeRef.current = false;
    let cancelled = false;

    const run = async () => {
      try {
        const response = await fetch('/api/library/completion-stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(completionStreamPayload(artistName, discographyRef.current)),
          signal: controller.signal,
        });
        if (!response.ok || !response.body) return;

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        setState((s) => ({ ...s, streaming: true }));

        for (;;) {
          const { done, value } = await reader.read();
          if (done || cancelled) break;

          buffer += decoder.decode(value, { stream: true });
          const { events, rest } = parseSseFrames(buffer);
          buffer = rest;

          let touched = false;
          for (const event of events as CompletionEvent[]) {
            if (event.type === 'completion') {
              discographyRef.current = applyCompletionEvent(discographyRef.current, event);
              if (countsRef.current) tallyEvent(countsRef.current, event);
              touched = true;
            } else if (event.type === 'complete') {
              completeRef.current = true;
              touched = true;
            }
          }
          if (touched && !cancelled) {
            setState({
              discography: discographyRef.current,
              counts: countsRef.current,
              streaming: true,
              completed: completeRef.current,
            });
          }
        }
      } catch {
        // AbortError on navigation is expected and silent, exactly as the
        // vanilla treated it; any other failure leaves the page as-is rather
        // than tearing it down over a background check.
      } finally {
        if (!cancelled) setState((s) => ({ ...s, streaming: false }));
      }
    };

    void run();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [artistName, enabled, initial]);

  return state;
}

/** Resolved counts for one bucket, for the streaming hero bars. */
export function bucketCounts(
  counts: StreamCounts | null,
  bucket: DiscographyBucket,
): { owned: number; missing: number } | null {
  if (!counts) return null;
  return {
    owned: counts.owned[bucket],
    missing: counts.total[bucket] - counts.owned[bucket],
  };
}
