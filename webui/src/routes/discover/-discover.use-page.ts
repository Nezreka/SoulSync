import { useQuery } from '@tanstack/react-query';

import type { DiscoverSectionId } from './-discover.layout';
import type { ResolvedSection, SectionOutcome } from './-discover.section-state';

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
import { resolveSection } from './-discover.section-state';

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
 *
 * ── NOT YET PORTED: resuming an in-flight sync ──────────────────────────────
 *
 * `loadDiscoverPage` ends by calling `checkForActiveDiscoverSyncs()`, which
 * polls three endpoints and, if a sync is mid-flight, re-shows its status
 * display, disables its sync button and restarts polling:
 *
 *     /api/sync/status/discover_release_radar      (Fresh Tape)
 *     /api/sync/status/discover_discovery_weekly   (The Archives)
 *     /api/sync/status/discover_seasonal_playlist
 *
 * Without it, reloading mid-sync makes a running sync look like it stopped.
 * All three belong to sections in later phases (release radar, weekly,
 * seasonal playlist), so there is nothing to attach it to yet — it lands with
 * the FIRST of those phases, and must not be dropped on the floor when it does.
 */

/**
 * The discover page loads ONCE per session, and that is the vanilla's rule,
 * not a performance opinion of mine:
 *
 *     case 'discover':
 *         if (!discoverPageInitialized) {
 *             await loadDiscoverPage();
 *             discoverPageInitialized = true;
 *         }
 *         // Already initialized — DOM content persists, no reload needed
 *
 * Navigating away and back re-shows the existing DOM without a single request.
 * `Infinity` on both is the faithful equivalent: data never goes stale and is
 * never garbage-collected while the session lives, so a revisit is instant and
 * silent. An earlier draft used a 5-minute staleTime, which was invented — and
 * wrong in the direction of refetching MORE than the vanilla ever does.
 *
 * A real refresh still comes from a page reload, exactly as before.
 */
const SHELF_STALE_MS = Number.POSITIVE_INFINITY;
const SHELF_GC_MS = Number.POSITIVE_INFINITY;

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
  /** Full render state for a section — phase, copy, poll/toast flags. */
  sectionState: (id: DiscoverSectionId) => ResolvedSection<unknown>;
  sections: Record<string, Section<unknown>>;
}

/**
 * A query that never retries, and that leaves through the shared limiter.
 *
 * No retry: a dead shelf should fail fast and stay empty rather than spend
 * three attempts holding a slot that another shelf could use.
 */
/**
 * Wrap a RAW fetcher into the SectionOutcome shape resolveSection expects.
 *
 * Seven of the api module's fetchers return the payload directly (their other
 * consumers want it that way); feeding one to resolveSection made
 * `outcome.data` undefined, which resolved the section to 'empty' + hidden.
 * That single mismatch took out both recommendation shelves, seasonal,
 * your-artists, the label explorer and the dial's saved value in the first
 * live smoke test — and, because every hidden section's PAIR PARTNER falls
 * back to full width, the whole two-column layout with them.
 */
function asOutcome<T>(fn: () => Promise<T>): () => Promise<SectionOutcome<T>> {
  return async () => {
    try {
      return { kind: 'ok', data: await fn() };
    } catch (error) {
      return { kind: 'error', error };
    }
  };
}

function shelfQuery<T>(key: string, fn: () => Promise<T>, enabled = true) {
  return {
    queryKey: ['discover', key] as const,
    queryFn: () => discoverLimiter.run(fn),
    staleTime: SHELF_STALE_MS,
    gcTime: SHELF_GC_MS,
    retry: false,
    enabled,
  };
}

export function useDiscoverPage(): DiscoverPageController {
  // ── Tier 1: above the fold ────────────────────────────────────────────
  //
  // TRIMMED to what the viewport actually shows before scrolling: the hero,
  // the dial (visible immediately — would otherwise snap from a default),
  // and the first shelves. The original port put ELEVEN queries here, which
  // meant eleven heavy Flask queries had to drain through the 5-slot limiter
  // before the rest of the page was even ALLOWED to start. Five above-fold
  // queries fill the pool exactly once; everything else streams in behind
  // them — same data, same fail-soft, first paint several seconds sooner.
  const hero = useQuery(shelfQuery('hero', fetchHero));
  const adventurousness = useQuery(shelfQuery('adventurousness', asOutcome(fetchAdventurousness)));
  const listeningRecs = useQuery(
    shelfQuery('listening-recs', asOutcome(fetchListeningRecommendations)),
  );
  const recommendedArtists = useQuery(
    shelfQuery('similar-artists', asOutcome(fetchSimilarArtists)),
  );
  const recentReleases = useQuery(shelfQuery('recent-releases', fetchRecentReleases));

  const aboveFold = [hero, adventurousness, listeningRecs, recommendedArtists, recentReleases];

  /**
   * Settled, not successful.
   *
   * A failed shelf must still release tier 2 — otherwise one dead endpoint
   * strands the bottom half of the page forever, which is the opposite of the
   * vanilla's fail-soft behaviour.
   */
  const aboveFoldSettled = aboveFold.every((q) => !q.isPending);

  // ── Tier 2: below the fold, gated on tier 1 ───────────────────────────
  // The four mix-registry feeders, the genre shelves and the caches all live
  // here now — they render well below the first viewport.
  const genreExplorer = useQuery(shelfQuery('genre-explorer', fetchGenreExplorer, aboveFoldSettled));
  const popularPicks = useQuery(shelfQuery('popular-picks', fetchPopularPicks, aboveFoldSettled));
  const hiddenGems = useQuery(shelfQuery('hidden-gems', fetchHiddenGems, aboveFoldSettled));
  const shuffle = useQuery(shelfQuery('discovery-shuffle', fetchDiscoveryShuffle, aboveFoldSettled));
  const listeningMix = useQuery(shelfQuery('listening-mix', fetchListeningMix, aboveFoldSettled));
  const genreNewReleases = useQuery(
    shelfQuery('genre-new-releases', fetchGenreNewReleases, aboveFoldSettled),
  );
  const seasonal = useQuery(
    shelfQuery('seasonal', asOutcome(fetchSeasonalCurrent), aboveFoldSettled),
  );
  const undiscovered = useQuery(
    shelfQuery('undiscovered', fetchUndiscoveredAlbums, aboveFoldSettled),
  );
  const labelExplorer = useQuery(
    shelfQuery('label-explorer', asOutcome(fetchLabelExplorer), aboveFoldSettled),
  );
  const yourAlbums = useQuery(
    shelfQuery(
      'your-albums',
      asOutcome(() => fetchYourAlbums()),
      aboveFoldSettled,
    ),
  );
  const yourArtists = useQuery(
    shelfQuery('your-artists', asOutcome(fetchYourArtists), aboveFoldSettled),
  );
  const deepCuts = useQuery(shelfQuery('deep-cuts', fetchDeepCuts, aboveFoldSettled));
  const decades = useQuery(shelfQuery('decades', fetchAvailableDecades, aboveFoldSettled));

  /**
   * Which sections belong in the layout.
   *
   * The vanilla expressed this as `style.display !== 'none'`, and it is NOT a
   * single rule: `createDiscoverSectionController` defaults to
   * `hideWhenEmpty: false`, so an empty shelf normally STAYS and renders an
   * explanatory message. Only four sections opt into vanishing.
   *
   * So this asks two things and hands both to `isSectionVisible`:
   *   hasItems  did the shelf return rows
   *   loaded    did its query finish (an 'empty-state' section still has to
   *             have loaded — the vanilla's loader bails before showing the
   *             section when there is no current season, say)
   *
   * `adv-wave` is always visible: the dial is a control, not a shelf.
   */
  /** Pull `key` off a payload as an array, tolerating anything else. */
  const arr = (d: unknown, key: string): unknown[] => {
    const v = (d as Record<string, unknown> | null | undefined)?.[key];
    return Array.isArray(v) ? v : [];
  };

  /**
   * How to pull the item list out of each section's payload.
   *
   * One per section, because the key genuinely differs — the vanilla made
   * `extractItems` REQUIRED for exactly this reason: "the controller refusing
   * to guess prevents silent wrong-key bugs".
   */
  const EXTRACT: Partial<Record<DiscoverSectionId, (d: unknown) => unknown[]>> = {
    'cache-genre-explorer': (d) => arr(d, 'genres'),
    'year-mixes-section': (d) => arr(d, 'decades'),
    'listening-recs-section': (d) => arr(d, 'artists'),
    'recommended-artists-section': (d) => arr(d, 'artists'),
    'recent-releases': (d) => arr(d, 'albums'),
    'cache-genre-releases': (d) => arr(d, 'albums'),
    'seasonal-albums-section': (d) => arr(d, 'albums'),
    'cache-undiscovered': (d) => arr(d, 'albums'),
    'cache-label-explorer': (d) => arr(d, 'albums'),
    'your-albums-section': (d) => arr(d, 'albums'),
    'your-artists-section': (d) => arr(d, 'artists'),
    'cache-deep-cuts': (d) => arr(d, 'tracks'),
  };

  /** The query behind each section. */
  const QUERY: Partial<Record<DiscoverSectionId, { data: unknown; isPending: boolean }>> = {
    'cache-genre-explorer': genreExplorer,
    'year-mixes-section': decades,
    'listening-recs-section': listeningRecs,
    'recommended-artists-section': recommendedArtists,
    'recent-releases': recentReleases,
    'cache-genre-releases': genreNewReleases,
    'seasonal-albums-section': seasonal,
    'cache-undiscovered': undiscovered,
    'cache-label-explorer': labelExplorer,
    'your-albums-section': yourAlbums,
    'your-artists-section': yourArtists,
    'cache-deep-cuts': deepCuts,
  };

  /**
   * Resolve one section to its full render state — loading / rendered / empty /
   * stale / error — with the vanilla's per-section copy, hide rule and toast
   * policy. See -discover.section-state.ts.
   */
  const sectionState = (id: DiscoverSectionId): ResolvedSection<unknown> => {
    const q = QUERY[id];
    const extract = EXTRACT[id] ?? (() => []);
    return resolveSection(
      id,
      q?.data as SectionOutcome<unknown> | undefined,
      extract,
      Boolean(q?.isPending),
    );
  };

  const hasContent = (id: DiscoverSectionId): boolean => {
    if (id === 'adv-wave') return true;

    if (id === 'your-mixes-section') {
      /**
       * Not a shelf — a REGISTRY. Seven live loaders each call
       * `_upsertMixCard({key, ...})`; the section shows if ANY produced a mix.
       *
       * Ported feeders: popular_picks, hidden_gems, discovery_shuffle,
       * listening_mix. STILL TO COME (add here with their phases, or the shelf
       * wrongly hides for a user who only has that one): release_radar,
       * discovery_weekly, seasonal_playlist. `daily_mixes` is dead code.
       */
      return [popularPicks, hiddenGems, shuffle, listeningMix].some(
        (q) =>
          arr(
            (q.data as SectionOutcome<unknown> | undefined)?.kind === 'ok'
              ? (q.data as { data: unknown }).data
              : undefined,
            'tracks',
          ).length > 0,
      );
    }

    // Sections whose phases have not landed yet stay out of the layout —
    // better absent than an empty frame.
    if (!QUERY[id]) return false;

    const s = sectionState(id);
    // `hidden` is the vanilla's hideWhenEmpty flip; loading/stale/error all
    // still occupy their slot, exactly as the controller leaves them.
    if (s.phase === 'empty' && s.hidden) return false;
    return true;
  };

  return {
    hero,
    aboveFoldSettled,
    hasContent,
    sectionState,
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
