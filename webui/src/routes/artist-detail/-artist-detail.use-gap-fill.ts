import { useCallback, useEffect, useRef, useState } from 'react';

import type { Discography } from './-artist-detail.types';

import {
  applyCompletionEvent,
  type CompletionEvent,
  parseSseFrames,
} from './-artist-detail.completion';
import {
  dedupeGaps,
  gapFillEnabled,
  type GapRelease,
  gapFillUrl,
  gapReleasesFromResponse,
  gapStreamPayload,
  setGapFillEnabled,
} from './-artist-detail.gap-fill';

export interface GapFillState {
  enabled: boolean;
  toggle: () => void;
  /** Already deduped against what the page renders. */
  releases: GapRelease[];
}

/**
 * Loads gap-fill releases and streams their ownership (#1067, #1071).
 *
 * Gap cards get REAL ownership checks like every other card — an album bought
 * on another platform must light up OWNED here too — but on their OWN stream,
 * because each gap id is only meaningful on the source that listed it.
 *
 * The vanilla guarded both requests with a request sequence number so a slow
 * response for a previous artist could not land in the current page; here that
 * is an AbortController per load, which also stops the stream itself rather
 * than just ignoring its results.
 */
export function useGapFill(
  artistId: unknown,
  artistName: string | undefined,
  baseSource: string | undefined,
  rendered: Discography,
): GapFillState {
  const [enabled, setEnabled] = useState(gapFillEnabled);
  const [releases, setReleases] = useState<GapRelease[]>([]);
  const releasesRef = useRef<GapRelease[]>([]);

  // Read through a ref so the ownership stream does not have to re-run every
  // time the base discography's ownership settles.
  const renderedRef = useRef(rendered);
  renderedRef.current = rendered;

  const toggle = useCallback(() => {
    setEnabled((on) => {
      setGapFillEnabled(!on);
      return !on;
    });
  }, []);

  useEffect(() => {
    releasesRef.current = [];
    setReleases([]);
    if (!enabled || !artistId) return;

    const controller = new AbortController();

    const run = async () => {
      const response = await fetch(gapFillUrl(artistId, artistName, baseSource), {
        signal: controller.signal,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.success) return;

      const fresh = dedupeGaps(gapReleasesFromResponse(data), renderedRef.current);
      if (fresh.length === 0) return;
      releasesRef.current = fresh;
      setReleases(fresh);

      await streamGapOwnership(artistName ?? '', fresh, controller.signal, (next) => {
        releasesRef.current = next;
        setReleases(next);
      });
    };

    void run().catch(() => {
      // Gap-fill is additive: a failure leaves the base discography alone
      // rather than taking the page down.
    });

    return () => controller.abort();
  }, [enabled, artistId, artistName, baseSource]);

  return { enabled, toggle, releases };
}

/**
 * Run the gap ownership stream, merging each result into the gap list.
 *
 * The merge reuses applyCompletionEvent by wrapping the flat list in a
 * one-bucket discography, so gap cards and base cards resolve ownership through
 * exactly the same code — the vanilla routed both through
 * updateLibraryReleaseCard for the same reason.
 */
async function streamGapOwnership(
  artistName: string,
  gaps: GapRelease[],
  signal: AbortSignal,
  onUpdate: (releases: GapRelease[]) => void,
): Promise<void> {
  const response = await fetch('/api/library/completion-stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(gapStreamPayload(artistName, gaps)),
    signal,
  });
  if (!response.ok || !response.body) return;

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let current = gaps;

  for (;;) {
    const { done, value } = await reader.read();
    if (done || signal.aborted) break;

    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSseFrames(buffer);
    buffer = rest;

    let touched = false;
    for (const event of events as CompletionEvent[]) {
      if (event.type !== 'completion') continue;
      const merged = applyCompletionEvent({ albums: current }, event);
      if (merged.albums && merged.albums !== current) {
        current = merged.albums as GapRelease[];
        touched = true;
      }
    }
    if (touched && !signal.aborted) onUpdate(current);
  }
}
