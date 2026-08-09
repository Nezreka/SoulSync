/**
 * The nine source verticals, built from ONE table rather than nine hand-written
 * hook calls.
 *
 * kettui's fourth observation is that similar things get reimplemented per
 * feature instead of sharing an abstraction, and nine near-identical
 * `useSourceVertical(SYNC_SOURCES.x, …)` calls at a page's top level is exactly
 * that shape: nine places to forget an option, nine places to update when the
 * vertical contract changes, and no way to tell that a tenth source was added
 * and missed. The registry below is the abstraction — the table is the source
 * of truth and the page reads a map out of it.
 *
 * NINE, not fifteen: several tabs share a vertical. Last.fm Radio rides
 * ListenBrainz's machinery (same MB-track shape), Deezer-link rides Deezer's,
 * and the three URL-import tabs each have their own. The tab count and the
 * vertical count are different numbers and conflating them is how a tab ends
 * up with a vertical that belongs to something else.
 *
 * HOOK ORDER IS SAFE because `SYNC_VERTICAL_IDS` is a frozen module constant:
 * the same ids in the same order on every render, which is all the rules of
 * hooks require. A test pins it against SYNC_SOURCES so a source added to the
 * table cannot silently skip the registry.
 */

import type { SyncSourceId } from './-sync.sources';
import type { SourceVertical, SourceVerticalOptions } from './-sync.use-vertical';

import { SYNC_SOURCES } from './-sync.sources';
import { useSourceVertical } from './-sync.use-vertical';

/**
 * Fixed order, fixed length — see the hook-order note above. Declared
 * explicitly rather than derived from `Object.keys(SYNC_SOURCES)` so the order
 * is a stated contract instead of an accident of how the table was typed.
 */
export const SYNC_VERTICAL_IDS = [
  'tidal',
  'qobuz',
  'deezer',
  'youtube',
  'beatport',
  'spotify_public',
  'itunes_link',
  'listenbrainz',
  'mirrored',
] as const satisfies readonly SyncSourceId[];

export type SyncVerticals = Record<SyncSourceId, SourceVertical>;

export interface UseSyncVerticalsOptions {
  /**
   * Per-source extras, keyed by source id.
   *
   * THE ONE ENTRY THAT MATTERS is listenbrainz's `onDiscoveryComplete`, which
   * auto-mirrors matched tracks the moment a discovery finishes
   * (`_mirrorListenBrainzAfterDiscovery`). That is what puts ListenBrainz AND
   * Last.fm Radio playlists into the Mirrored tab, and therefore onto the
   * Auto-Sync board. Omit it and two downstream surfaces come up empty with
   * nothing failing — which is precisely why it belongs in a table the
   * registry always reads, not in one call site among nine.
   */
  perSource?: Partial<Record<SyncSourceId, SourceVerticalOptions>>;
}

export function useSyncVerticals({ perSource }: UseSyncVerticalsOptions = {}): SyncVerticals {
  const verticals = {} as SyncVerticals;
  for (const id of SYNC_VERTICAL_IDS) {
    // eslint-disable-next-line react-hooks/rules-of-hooks -- fixed-length
    // frozen constant; the order is identical on every render. See the header.
    verticals[id] = useSourceVertical(SYNC_SOURCES[id], perSource?.[id] ?? {});
  }
  return verticals;
}
