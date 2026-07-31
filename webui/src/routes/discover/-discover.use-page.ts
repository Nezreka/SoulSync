import { useQuery } from '@tanstack/react-query';

import type { DiscoverSectionId } from './-discover.layout';

import {
  fetchAdventurousness,
  fetchAvailableDecades,
  fetchDeepCuts,
  fetchDiscoveryShuffle,
  fetchGenreExplorer,
  fetchGenreNewReleases,
  fetchHero,
  fetchHiddenGems,
  fetchLabelExplorer,
  fetchListeningMix,
  fetchListeningRecommendations,
  fetchPopularPicks,
  fetchRecentReleases,
  fetchSeasonalCurrent,
  fetchSimilarArtists,
  fetchUndiscoveredAlbums,
  fetchYourAlbums,
  fetchYourArtists,
} from './-discover.api';
import { discoverLimiter } from './-discover.limiter';

/**
 * The discover page's load orchestration.
 *
 * ── The tiering, and why it is preserved ────────────────────────────────────
 *
 * This page fans out ~20 requests. The vanilla split them into three groups on
 * purpose, and the split is user-visible:
 *
 *   ABOVE THE FOLD  run first and awaited, so the top of the page is usable in
 *                   a couple of seconds instead of after the whole storm
 *   BELOW THE FOLD  stream in afterwards
 *   SLOW EXTERNAL   Last.fm / ListenBrainz / release-radar / weekly. These hit
 *                   third-party services that can take ~39s or hang outright,
 *                   so they start immediately but are never awaited by anything
 *                   — the vanilla's comment records that letting them gate the
 *                   layout caused a visible "reshuffle on load".
 *
 * Tier 2 is gated on tier 1 SETTLING (not succeeding — a failed shelf must
 * still release the tier, or one dead endpoint strands the bottom of the page).
 *
 * The vanilla's 5-at-a-time pool IS reproduced, via `discoverLimiter`. An
 * earlier draft of this hook dropped it on the reasoning that browsers already
 * cap per-origin concurrency; that was wrong, and the vanilla says why:
 *
 *     ~20 heavy DB/consensus queries contend on the backend (Flask + GIL) and
 *     each ends up slow — the page took tens of seconds to become usable
 *
 * The cap protects the SERVER. Browser limits are beside the point, and on
 * HTTP/2 they do nothing at all — every request multiplexes down one connection
 * and lands on Flask together, which is exactly what the pool prevented.
 *
 * Every section fails independently — see `shelf()` in -discover.api.ts. One
 * dead external service leaves one empty shelf, never a broken page.
 */

/** How long a shelf stays fresh. These are cache-backed and change slowly. */
const SHELF_STALE_MS = 5 * 60 * 1000;

interface Section<T> {
  data: T | undefined;
  isPending: boolean;
}

export interface DiscoverPageController {
  hero: ReturnType<typeof useQuery<Awaited<ReturnType<typeof fetchHero>>>>;
  /** True once every above-the-fold query has settled (resolved or failed). */
  aboveFoldSettled: boolean;
  /** Does this section have anything to render? Drives `buildLayoutRows`. */
  hasContent: (id: DiscoverSectionId) => boolean;
  sections: Record<string, Section<unknown>>;
}

/**
 * A query that never retries, and that leaves through the shared limiter.
 *
 * No retry: a dead shelf should fail fast and stay empty rather than spend
 * three attempts holding a slot that another shelf could use.
 */
function shelfQuery<T>(key: string, fn: () => Promise<T>, enabled = true) {
  return {
    queryKey: ['discover', key] as const,
    queryFn: () => discoverLimiter.run(fn),
    staleTime: SHELF_STALE_MS,
    retry: false,
    enabled,
  };
}

export function useDiscoverPage(): DiscoverPageController {
  // ── Tier 1: above the fold ────────────────────────────────────────────
  const hero = useQuery(shelfQuery('hero', fetchHero));
  /**
   * The adventurousness dial's saved value.
   *
   * Above the fold in the vanilla (`loadAdventurousnessDial`) because the dial
   * is visible immediately and would otherwise render at a default before
   * snapping to the user's real setting.
   */
  const adventurousness = useQuery(shelfQuery('adventurousness', fetchAdventurousness));
  const genreExplorer = useQuery(shelfQuery('genre-explorer', fetchGenreExplorer));
  const listeningRecs = useQuery(shelfQuery('listening-recs', fetchListeningRecommendations));
  const recommendedArtists = useQuery(shelfQuery('similar-artists', fetchSimilarArtists));
  const popularPicks = useQuery(shelfQuery('popular-picks', fetchPopularPicks));
  const hiddenGems = useQuery(shelfQuery('hidden-gems', fetchHiddenGems));
  const shuffle = useQuery(shelfQuery('discovery-shuffle', fetchDiscoveryShuffle));
  const listeningMix = useQuery(shelfQuery('listening-mix', fetchListeningMix));
  const recentReleases = useQuery(shelfQuery('recent-releases', fetchRecentReleases));
  const genreNewReleases = useQuery(shelfQuery('genre-new-releases', fetchGenreNewReleases));

  const aboveFold = [
    hero,
    adventurousness,
    genreExplorer,
    listeningRecs,
    recommendedArtists,
    popularPicks,
    hiddenGems,
    shuffle,
    listeningMix,
    recentReleases,
    genreNewReleases,
  ];

  /**
   * Settled, not successful.
   *
   * A failed shelf must still release tier 2 — otherwise one dead endpoint
   * strands the bottom half of the page forever, which is the opposite of the
   * vanilla's fail-soft behaviour.
   */
  const aboveFoldSettled = aboveFold.every((q) => !q.isPending);

  // ── Tier 2: below the fold, gated on tier 1 ───────────────────────────
  const seasonal = useQuery(shelfQuery('seasonal', fetchSeasonalCurrent, aboveFoldSettled));
  const undiscovered = useQuery(
    shelfQuery('undiscovered', fetchUndiscoveredAlbums, aboveFoldSettled),
  );
  const labelExplorer = useQuery(
    shelfQuery('label-explorer', fetchLabelExplorer, aboveFoldSettled),
  );
  const yourAlbums = useQuery(shelfQuery('your-albums', () => fetchYourAlbums(), aboveFoldSettled));
  const yourArtists = useQuery(shelfQuery('your-artists', fetchYourArtists, aboveFoldSettled));
  const deepCuts = useQuery(shelfQuery('deep-cuts', fetchDeepCuts, aboveFoldSettled));
  const decades = useQuery(shelfQuery('decades', fetchAvailableDecades, aboveFoldSettled));

  /**
   * Which sections have something to show.
   *
   * The vanilla expressed this as `style.display !== 'none'` — each loader hid
   * its own section when it came back empty, and the reorder pass then skipped
   * hidden nodes. Here it is a predicate over the data, which is the same rule
   * without the DOM round-trip.
   *
   * `adv-wave` is always present: the dial is a control, not a data shelf, so
   * it renders regardless of what the shelves around it returned.
   */
  const nonEmpty = (v: unknown): boolean =>
    Array.isArray(v) ? v.length > 0 : Boolean(v && typeof v === 'object');

  const hasContent = (id: DiscoverSectionId): boolean => {
    switch (id) {
      case 'adv-wave':
        return true;
      case 'cache-genre-explorer':
        return nonEmpty(genreExplorer.data);
      case 'your-mixes-section':
        return nonEmpty(popularPicks.data) || nonEmpty(hiddenGems.data) || nonEmpty(shuffle.data);
      case 'year-mixes-section':
        return nonEmpty(decades.data);
      case 'listening-recs-section':
        return nonEmpty(listeningRecs.data?.artists);
      case 'recommended-artists-section':
        return nonEmpty(recommendedArtists.data?.artists);
      case 'recent-releases':
        return nonEmpty(recentReleases.data);
      case 'cache-genre-releases':
        return nonEmpty(genreNewReleases.data);
      case 'seasonal-albums-section':
        return nonEmpty(seasonal.data?.albums);
      case 'cache-undiscovered':
        return nonEmpty(undiscovered.data);
      case 'cache-label-explorer':
        return nonEmpty(labelExplorer.data?.albums);
      case 'your-albums-section':
        return nonEmpty(yourAlbums.data?.albums);
      case 'your-artists-section':
        return nonEmpty(yourArtists.data?.artists);
      case 'cache-deep-cuts':
        return nonEmpty(deepCuts.data);
      // The remaining sections belong to phases not yet ported; they render
      // nothing until their controllers land, which keeps them out of the
      // layout rather than showing an empty frame.
      case 'discover-bylt-sections':
      case 'lastfm-radio':
      case 'listenbrainz':
      case 'build-a-playlist':
        return false;
      default:
        return false;
    }
  };

  return {
    hero,
    aboveFoldSettled,
    hasContent,
    sections: {
      adventurousness,
      genreExplorer,
      listeningRecs,
      recommendedArtists,
      popularPicks,
      hiddenGems,
      shuffle,
      listeningMix,
      recentReleases,
      genreNewReleases,
      seasonal,
      undiscovered,
      labelExplorer,
      yourAlbums,
      yourArtists,
      deepCuts,
      decades,
    } as Record<string, Section<unknown>>,
  };
}
