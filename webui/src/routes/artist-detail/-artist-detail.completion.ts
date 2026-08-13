import type { Discography, DiscographyBucket, DiscographyRelease } from './-artist-detail.types';

/**
 * The library completion stream, ported from checkLibraryCompletion and
 * updateLibraryReleaseCard (library.js:1918-2088).
 *
 * The vanilla mutated `card._releaseData` in place and rewrote the overlay by
 * hand. Here each event produces a NEW release object and React re-renders,
 * which is why the merge is a pure function.
 */

export interface CompletionEvent {
  type?: string;
  id?: string | number;
  category?: DiscographyBucket;
  status?: string;
  owned_tracks?: number;
  expected_tracks?: number;
  completion_percentage?: number;
  formats?: string[];
  processed_count?: number;
}

/** Anything but 'missing' or 'error' counts as owned — including partials. */
export function isEventOwned(event: CompletionEvent): boolean {
  return event.status !== 'missing' && event.status !== 'error';
}

/**
 * The track_completion an event implies.
 *
 * Three shapes, matching the vanilla exactly:
 *   - owned with a known expectation -> the object form. When the library has
 *     at least as many tracks as expected, `total_tracks` is clamped to the
 *     OWNED count and the percentage forced to 100, so an album with bonus
 *     tracks does not render as "13/12" or 108%.
 *   - owned with no expectation -> owned/owned at 100%
 *   - not owned -> the NUMBER 0, not an object. Downstream code branches on
 *     typeof, so this distinction matters.
 */
export function completionFromEvent(
  event: CompletionEvent,
): { owned_tracks: number; total_tracks: number; percentage: number; missing_tracks: number } | 0 {
  const owned = event.owned_tracks ?? 0;
  const expected = event.expected_tracks ?? 0;

  if (!isEventOwned(event)) return 0;

  if (expected > 0) {
    // `owned > 0` is redundant inside this branch — expected is already > 0,
    // so owned >= expected implies owned > 0. Kept verbatim because it is what
    // the vanilla wrote; it survives mutation for that reason, not for lack of
    // a test.
    const complete = owned >= expected && owned > 0;
    return {
      owned_tracks: owned,
      total_tracks: complete ? owned : expected,
      percentage: complete ? 100 : (event.completion_percentage ?? 0),
      missing_tracks: expected - owned,
    };
  }

  return { owned_tracks: owned, total_tracks: owned, percentage: 100, missing_tracks: 0 };
}

/**
 * Apply one event to a discography, returning a new object.
 *
 * Matching is by release id as a STRING: the vanilla looked the card up with
 * a `[data-release-id="..."]` selector, so a numeric id and its string form
 * were the same card.
 */
export function applyCompletionEvent(
  discography: Discography,
  event: CompletionEvent,
): Discography {
  const targetId = String(event.id ?? '');
  if (!targetId) return discography;

  let changed = false;
  const next: Discography = { ...discography };

  for (const bucket of ['albums', 'eps', 'singles'] as const) {
    const releases = discography[bucket];
    if (!releases) continue;
    const updated = releases.map((release) => {
      if (String(release.id ?? '') !== targetId) return release;
      changed = true;
      return {
        ...release,
        owned: isEventOwned(event),
        track_completion: completionFromEvent(event),
      } as DiscographyRelease;
    });
    if (updated !== releases) next[bucket] = updated;
  }

  return changed ? next : discography;
}

export interface StreamCounts {
  owned: Record<DiscographyBucket, number>;
  total: Record<DiscographyBucket, number>;
  formats: Set<string>;
}

export function emptyStreamCounts(): StreamCounts {
  return {
    owned: { albums: 0, eps: 0, singles: 0 },
    total: { albums: 0, eps: 0, singles: 0 },
    formats: new Set(),
  };
}

/**
 * Running tallies, mutated in place as the vanilla did.
 *
 * `total` counts EVERY completion event; `owned` only the owned ones — so
 * "missing" for a category is total minus owned, never a separate counter.
 * Formats accumulate from owned releases only.
 */
export function tallyEvent(counts: StreamCounts, event: CompletionEvent): StreamCounts {
  const category = event.category;
  if (!category || !(category in counts.total)) return counts;

  counts.total[category] += 1;
  if (isEventOwned(event)) {
    counts.owned[category] += 1;
    for (const format of event.formats ?? []) counts.formats.add(format);
  }
  return counts;
}

/** Parse the `data: ` frames out of an SSE chunk buffer. */
export function parseSseFrames(buffer: string): { events: CompletionEvent[]; rest: string } {
  const lines = buffer.split('\n');
  // The last element is an INCOMPLETE line and must be carried forward, or a
  // frame split across two chunks is silently dropped.
  const rest = lines.pop() ?? '';
  const events: CompletionEvent[] = [];

  for (const line of lines) {
    if (!line.startsWith('data: ')) continue;
    try {
      events.push(JSON.parse(line.slice(6)) as CompletionEvent);
    } catch {
      // A malformed frame is skipped, not fatal — the vanilla warned and
      // carried on so one bad event could not stall the whole stream.
    }
  }
  return { events, rest };
}

/** The request body the stream endpoint expects. */
export function completionStreamPayload(artistName: string, discography: Discography) {
  return {
    artist_name: artistName,
    albums: discography.albums ?? [],
    eps: discography.eps ?? [],
    singles: discography.singles ?? [],
    source: discography.source ?? null,
  };
}
