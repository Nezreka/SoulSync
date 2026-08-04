/**
 * Dashboard domain types.
 *
 * The provider status payload is what the `enrichment:*` socket channels and
 * `/api/enrichment/status-all` both carry. Field presence varies per provider —
 * the P0 catalogued FIVE distinct gate schemes — so everything is optional and
 * each provider's reducer reads only what its vanilla original read.
 */

export interface EnrichTierProgress {
  matched?: number;
  total?: number;
  percent?: number;
}

export interface EnrichProgress {
  artists?: EnrichTierProgress;
  albums?: EnrichTierProgress;
  tracks?: EnrichTierProgress;
}

export interface EnrichCurrentItem {
  name?: string;
  type?: string;
}

/**
 * `current_item` has TWO SHAPES on the same channel: an object with
 * `.name`/`.type` for most providers, but a plain STRING for Discogs,
 * SimilarArtists and SoulID. The union is the truth; each reducer narrows it
 * the way its vanilla original did.
 */
export type CurrentItem = EnrichCurrentItem | string;

export interface ProviderStatusPayload {
  idle?: boolean;
  running?: boolean;
  paused?: boolean;
  yield_reason?: string | null;
  authenticated?: boolean;
  enabled?: boolean;
  rate_limited?: boolean;
  using_free?: boolean;
  daily_budget?: { exhausted?: boolean; resets_in_seconds?: number } | null;
  rate_limit?: { remaining_seconds?: number } | null;
  current_item?: CurrentItem | null;
  progress?: EnrichProgress | null;
  stats?: Record<string, number | undefined> | null;
}

/** The one state class a pill carries after the vanilla's remove-then-add. */
export type PillStateClass = 'active' | 'paused' | 'complete' | 'no-auth';

/**
 * A provider pill, fully computed. This is the pure half of the vanilla
 * `update<Provider>StatusFromData` functions — everything they decided, none of
 * what they wrote.
 *
 * `current: null` and `progress: null` mean LEAVE THE PREVIOUS TEXT IN PLACE.
 * That is not an accident of modelling: Deezer, JioSaavn, iTunes, LastFM and
 * Tidal/Qobuz have NO final else on their tooltip-current branch, and every
 * provider skips the progress write when `data.progress` is absent — the
 * vanilla tooltips go stale rather than clearing. The UI layer decides whether
 * to reproduce that (hold previous text in state) or clear; the core stays
 * faithful to what the vanilla COMPUTED.
 */
export interface PillView {
  stateClass: PillStateClass | null;
  status: string;
  current: string | null;
  progress: string | null;
}
