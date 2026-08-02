/**
 * The two recommendation carousels and their shared card.
 *
 * Transcribed from `_renderRecommendedMini` (965),
 * `_enrichRecommendedCarouselCards` (1009), `loadRecommendedArtistsSection`
 * (1037), `loadListeningRecommendations` (1080), `filterRecommendedArtists`
 * (1124), `toggleRecommendedWatchlist` (1133) and
 * `checkRecommendedWatchlistStatuses` (1173) — read end to end.
 *
 * ── Two sections, one card ──────────────────────────────────────────────────
 *
 * "Recommended Artists" and "Based On Your Listening" (#913) render the SAME
 * card through different reason functions, injected as `reasonFn`/`titleFn`.
 * They are the dial's two targets, which is why they sit directly under it in
 * the layout — the user should see both react.
 */

import {
  listeningRecommendationReason,
  listeningRecommendationReasonTitle,
  recommendationReason,
  recommendationReasonTitle,
  whyIcon,
} from './-discover.helpers';

/** `items.slice(0, 18)` in both sections (1052, 1092). */
export const RECOMMENDED_CARD_LIMIT = 18;

/** `artist.why.slice(0, 2)` (978) — at most two explainability chips. */
export const WHY_CHIP_LIMIT = 2;

export interface RecommendedArtist {
  artist_id?: string;
  artist_name?: string;
  image_url?: string;
  source?: string;
  why?: { type?: string; label?: string }[];
  spotify_artist_id?: string;
  deezer_artist_id?: string;
  itunes_artist_id?: string;
  [key: string]: unknown;
}

export interface RecommendedSectionDef {
  id: string;
  sectionEl: string;
  contentEl: string;
  fetchUrl: string;
  loadingMessage: string;
  emptyMessage: string;
  errorMessage: string;
  /** Both sections vanish entirely when they have nothing (1046, 1089). */
  hideWhenEmpty: true;
}

/**
 * The two section configs, verbatim (1039-1071, 1082-1114).
 *
 * Both hide when empty rather than showing an empty state — a user who has not
 * run a scan should not see a titled box explaining that they have nothing.
 */
export const RECOMMENDED_SECTIONS: Record<'recommended' | 'listening', RecommendedSectionDef> = {
  recommended: {
    id: 'recommended-artists',
    sectionEl: '#recommended-artists-section',
    contentEl: '#recommended-artists-carousel',
    fetchUrl: '/api/discover/similar-artists',
    loadingMessage: 'Finding recommendations...',
    emptyMessage: 'No recommendations yet — let the Similar Artists worker run',
    errorMessage: 'Failed to load recommendations',
    hideWhenEmpty: true,
  },
  listening: {
    id: 'listening-recs',
    sectionEl: '#listening-recs-section',
    contentEl: '#listening-recs-carousel',
    fetchUrl: '/api/discover/listening-recommendations',
    loadingMessage: 'Reading your listening...',
    emptyMessage: 'Play more music and run a watchlist scan to see picks based on your listening',
    errorMessage: 'Failed to load listening recommendations',
    hideWhenEmpty: true,
  },
};

/** The reason pair each section injects (966-967, 1094-1095). */
export const REASON_FNS = {
  recommended: { reason: recommendationReason, title: recommendationReasonTitle },
  listening: { reason: listeningRecommendationReason, title: listeningRecommendationReasonTitle },
};

/** `data.source || 'spotify'` (1048, 1091) — the fallback source. */
export const DEFAULT_REC_SOURCE = 'spotify';

export function recSource(data: { source?: string } | null | undefined): string {
  return data?.source || DEFAULT_REC_SOURCE;
}

export interface WhyChip {
  type: string;
  label: string;
  icon: string;
}

export interface RecommendedCard {
  artistId: string;
  artistName: string;
  /** LOWERCASED — the search filter matches against this, not the display name. */
  filterName: string;
  source: string;
  image: string | null;
  /** Chips REPLACE the reason line when present; they are the reason, clearer. */
  chips: WhyChip[];
  reason: string;
  reasonTitle: string;
  showChips: boolean;
}

/**
 * The shared card (965-1003).
 *
 * `artist.source` wins over the section's source, so a mixed-source response
 * still links each card to the right provider.
 */
export function recommendedCard(
  artist: RecommendedArtist,
  sectionSource: string,
  kind: 'recommended' | 'listening' = 'recommended',
): RecommendedCard {
  const fns = REASON_FNS[kind];
  const chips = (artist.why ?? []).slice(0, WHY_CHIP_LIMIT).map((w) => ({
    type: w.type ?? '',
    label: w.label ?? '',
    icon: whyIcon(w.type ?? ''),
  }));
  return {
    artistId: artist.artist_id ?? '',
    artistName: artist.artist_name ?? '',
    filterName: (artist.artist_name ?? '').toLowerCase(),
    source: artist.source || sectionSource || '',
    image: artist.image_url ?? null,
    chips,
    reason: fns.reason(artist as never),
    reasonTitle: fns.title(artist as never),
    showChips: chips.length > 0,
  };
}

/** `items.slice(0, RECOMMENDED_CARD_LIMIT)`. */
export function recommendedVisible(items: RecommendedArtist[]): RecommendedArtist[] {
  return items.slice(0, RECOMMENDED_CARD_LIMIT);
}

// ── Progressive image enrichment ────────────────────────────────────────────

export const ENRICH_ENDPOINT = '/api/discover/similar-artists/enrich';

/**
 * Which id field carries the artist for a given source (1010-1012).
 *
 * NOTE the fallthrough: anything that is not spotify or deezer is treated as
 * itunes. That is the vanilla's behaviour — there is no separate branch for an
 * unknown source, so it asks the itunes field and simply finds nothing.
 */
export function enrichIdKey(source: string): keyof RecommendedArtist {
  if (source === 'spotify') return 'spotify_artist_id';
  if (source === 'deezer') return 'deezer_artist_id';
  return 'itunes_artist_id';
}

/**
 * Which artists need an image fetched (1013).
 *
 * Only the ones with NO `image_url` — the list endpoint returns cached images
 * only, and re-requesting the ones it already answered would be the bulk of the
 * batch.
 */
export function enrichIds(items: RecommendedArtist[], source: string): string[] {
  const key = enrichIdKey(source);
  return items
    .filter((a) => !a.image_url)
    .map((a) => a[key])
    .filter(Boolean) as string[];
}

/** No ids → no request at all (1014). */
export function shouldEnrich(ids: string[]): boolean {
  return ids.length > 0;
}

/**
 * Which enriched entries actually update a card (1025-1026).
 *
 * An entry without an `image_url` is SKIPPED rather than blanking the card —
 * the fallback glyph already rendered and is better than an empty box.
 */
export function enrichUpdates(
  data: { success?: boolean; artists?: Record<string, { image_url?: string }> } | null | undefined,
): { artistId: string; imageUrl: string }[] {
  if (!data?.success || !data.artists) return [];
  return Object.entries(data.artists)
    .filter(([, info]) => Boolean(info?.image_url))
    .map(([artistId, info]) => ({ artistId, imageUrl: info.image_url as string }));
}

// ── Search filter ───────────────────────────────────────────────────────────

/**
 * The modal's live filter (1124-1131).
 *
 * Case-insensitive SUBSTRING, not prefix — and it matches against the
 * lowercased `data-artist-name`, which is why the card carries a separate
 * filter name. An empty query matches everything, so clearing the box restores
 * the full list without a re-fetch.
 */
export function recommendedMatches(filterName: string, query: string): boolean {
  return filterName.includes(query.toLowerCase());
}

// ── Watchlist toggle ────────────────────────────────────────────────────────

export const REC_WATCH_ADD_LABEL = 'Add to Watchlist';
export const REC_WATCH_ON_LABEL = 'Watching';

/**
 * Add sends id + name but NO source (1157).
 *
 * The Your Artists toggle DOES send a source. Different callers, different
 * bodies — transcribed rather than unified, because the endpoint treats a
 * missing source as "resolve it yourself" and adding one here would change
 * which provider the watchlist row points at.
 */
export function recWatchlistRequest(
  wasWatching: boolean,
  artistId: string,
  artistName: string,
): { url: string; body: Record<string, unknown> } {
  return wasWatching
    ? { url: '/api/watchlist/remove', body: { artist_id: artistId } }
    : { url: '/api/watchlist/add', body: { artist_id: artistId, artist_name: artistName } };
}

/** Both id AND name are required; otherwise the click is ignored (1136). */
export function recWatchlistClickable(artistId: string, artistName: string): boolean {
  return Boolean(artistId && artistName);
}

/**
 * The UI only flips on `data.success` (1149, 1160).
 *
 * A failed request leaves the button as it was — no optimistic flip to undo,
 * and no silent lie about what the watchlist contains.
 */
export function recWatchlistNextState(
  wasWatching: boolean,
  data: { success?: boolean } | null | undefined,
): { watching: boolean; label: string } | null {
  if (!data?.success) return null;
  const watching = !wasWatching;
  return { watching, label: watching ? REC_WATCH_ON_LABEL : REC_WATCH_ADD_LABEL };
}

/**
 * The batch status check only ever marks buttons AS watching (1186).
 *
 * It never un-marks. A card rendered fresh starts as "Add to Watchlist", so
 * there is nothing to clear — and clearing on a partial response would drop the
 * state of an artist the batch did not cover.
 */
export function watchingIdsFrom(
  data: { success?: boolean; results?: Record<string, unknown> } | null | undefined,
): string[] {
  if (!data?.success || !data.results) return [];
  return Object.entries(data.results)
    .filter(([, isWatching]) => Boolean(isWatching))
    .map(([artistId]) => artistId);
}

export function watchlistCheckIds(artists: RecommendedArtist[]): string[] {
  return artists.map((a) => a.artist_id).filter(Boolean) as string[];
}

// ── The "View All" modal (811) ─────────────────────────────────────────────

/**
 * `renderRecommendedArtistsModal` (811), found missing by the coverage audit.
 *
 * The grid inside reuses the same `.recommended-artist-card` shape as the
 * carousel — same data hooks, same watchlist button — so `recommendedCard`
 * covers a row. What is modal-specific is the header count, the search box and
 * the Add All action.
 */
export const REC_MODAL_TITLE = 'Recommended Artists';
export const REC_MODAL_SEARCH_PLACEHOLDER = 'Search recommended artists...';
export const REC_MODAL_ADD_ALL = 'Add All to Watchlist';

/** `${n} artist${n !== 1 ? 's' : ''}` (818) — plural at zero, like the batch footer. */
export function recModalCountLabel(count: number): string {
  return `${count} artist${count !== 1 ? 's' : ''}`;
}

/** At most three genre tags per card (840). */
export const REC_MODAL_MAX_GENRES = 3;

export function recModalGenres(artist: RecommendedArtist): string[] {
  const genres = (artist as { genres?: unknown }).genres;
  return Array.isArray(genres) ? (genres as string[]).slice(0, REC_MODAL_MAX_GENRES) : [];
}

/**
 * The modal's source fallback is THREE deep (844), one more than the carousel's.
 *
 * `artist.source || source || _recommendedArtistsSource` — the module-level
 * cache is the last resort, because the modal can be opened from the primed
 * cache without a fresh response to read a source off.
 */
export function recModalSource(
  artist: RecommendedArtist,
  modalSource: string | null,
  cachedSource: string | null,
): string {
  return artist.source || modalSource || cachedSource || '';
}
