import { useQuery } from '@tanstack/react-query';

import type { DiscoverMix } from './-discover.mixes';
import type { SeasonData } from './-discover.seasonal';
import type { SectionOutcome } from './-discover.section-state';

import {
  fetchAvailableDecades,
  fetchDiscoveryShuffle,
  fetchDiscoveryWeekly,
  fetchHiddenGems,
  fetchListeningMix,
  fetchPopularPicks,
  fetchReleaseRadar,
  fetchSeasonalCurrent,
  fetchSeasonalPlaylist,
} from './-discover.api';
import { decadeMix, type AvailableDecade } from './-discover.decade-shelf';
import { discoverLimiter } from './-discover.limiter';
import { seasonalHasPlaylist, seasonalMixTitles } from './-discover.seasonal';

/**
 * The Your Mixes registry, as a hook.
 *
 * The vanilla has no "mixes loader": seven independent section loaders each
 * call `_upsertMixCard({key, title, subtitle, tracks, syncKey})` (2079, 2108,
 * 4372, 4568, 4595, 4627, 10349) and collapse their own section, so the shelf
 * is whatever arrived. This hook IS that registry — one query per feeder, one
 * mix per feeder that came back with tracks.
 *
 * ── Deliberate divergence: a DETERMINISTIC shelf order ──────────────────────
 *
 * The vanilla renders cards in ARRIVAL order (`_yourMixKeys` grows as loaders
 * finish, 4906-4908), so the shelf shuffles run to run with network timing.
 * The port pins `LIVE_MIX_FEEDERS`' declaration order — the same cards, in
 * the same places, every load. Nothing else about a card changes.
 *
 * The four personalized queries reuse the exact query keys `useDiscoverPage`
 * registers, so React Query serves them from the same cache entries — this
 * hook adds ZERO requests for them. The three it owns are release radar +
 * weekly (slow external — never gate anything, matching the vanilla's
 * "started immediately, slotted in when they arrive") and the seasonal
 * playlist, which is keyed by the CURRENT season and so waits for
 * /seasonal/current — and only fires at all when the season advertises a
 * playlist (`seasonalHasPlaylist`, the vanilla's 4285 guard).
 */

const STALE = Number.POSITIVE_INFINITY;

function mixQuery(key: string, fn: () => Promise<unknown>, enabled = true) {
  return {
    queryKey: ['discover', key] as const,
    queryFn: () => discoverLimiter.run(fn),
    staleTime: STALE,
    gcTime: STALE,
    retry: false,
    enabled,
  };
}

/** The tracks off a section outcome, or [] for pending/error/empty. */
function outcomeTracks(data: unknown): unknown[] {
  const outcome = data as SectionOutcome<Record<string, unknown>> | undefined;
  if (!outcome || outcome.kind !== 'ok') return [];
  const tracks = outcome.data.tracks;
  return Array.isArray(tracks) ? tracks : [];
}

export interface DiscoverMixesController {
  /** The Your Mixes shelf, in feeder-declaration order; empty feeders absent. */
  mixes: DiscoverMix[];
  /** The Time Machine shelf — one card per available decade (2662-2681). */
  decadeMixes: DiscoverMix[];
  /** Every mix the modal can resolve, keyed — the registry itself (4906). */
  registry: Record<string, DiscoverMix>;
}

/**
 * @param belowFoldReady `useDiscoverPage().aboveFoldSettled`. The seasonal
 * and decades queries SHARE their cache keys with useDiscoverPage's tier-2
 * entries, which are gated on tier 1 settling — an ungated observer here
 * would fire those fetches at mount and defeat the tiering the page hook
 * exists to preserve. The slow-external feeders stay ungated on purpose.
 */
export function useDiscoverMixes(belowFoldReady = true): DiscoverMixesController {
  // Same keys as useDiscoverPage → served from cache, no second request.
  const popularPicks = useQuery(mixQuery('popular-picks', fetchPopularPicks));
  const hiddenGems = useQuery(mixQuery('hidden-gems', fetchHiddenGems));
  const shuffle = useQuery(mixQuery('discovery-shuffle', fetchDiscoveryShuffle));
  const listeningMix = useQuery(mixQuery('listening-mix', fetchListeningMix));
  // SHARED cache key with use-page, which stores the seasonal payload as a
  // SectionOutcome — the queryFn and the read must both speak that shape, or
  // whichever hook runs second reads garbage out of the shared entry.
  const seasonal = useQuery({
    queryKey: ['discover', 'seasonal'] as const,
    queryFn: async (): Promise<SectionOutcome<SeasonData>> => {
      try {
        return {
          kind: 'ok',
          data: (await discoverLimiter.run(fetchSeasonalCurrent)) as SeasonData,
        };
      } catch (error) {
        return { kind: 'error', error };
      }
    },
    staleTime: STALE,
    gcTime: STALE,
    retry: false,
    enabled: belowFoldReady,
  });
  const decades = useQuery(mixQuery('decades', fetchAvailableDecades, belowFoldReady));

  // Slow external — enabled from mount, awaited by nothing.
  const releaseRadar = useQuery(mixQuery('release-radar', fetchReleaseRadar));
  const weekly = useQuery(mixQuery('discovery-weekly', fetchDiscoveryWeekly));

  const seasonalOutcome = seasonal.data as SectionOutcome<SeasonData> | undefined;
  const season = seasonalOutcome?.kind === 'ok' ? seasonalOutcome.data : undefined;
  const seasonKey = season?.season ?? '';
  const seasonalPlaylist = useQuery(
    mixQuery(
      `seasonal-playlist-${seasonKey}`,
      () => fetchSeasonalPlaylist(seasonKey),
      seasonKey !== '' && seasonalHasPlaylist(season),
    ),
  );

  const mixes: DiscoverMix[] = [];

  // LIVE_MIX_FEEDERS order: release_radar, discovery_weekly, seasonal_playlist,
  // popular_picks, hidden_gems, listening_mix, discovery_shuffle.
  const radarTracks = outcomeTracks(releaseRadar.data);
  if (radarTracks.length > 0) {
    mixes.push({
      key: 'release_radar',
      title: 'Fresh Tape',
      subtitle: 'New releases from artists you follow',
      tracks: radarTracks,
      syncKey: 'release_radar',
    });
  }
  const weeklyTracks = outcomeTracks(weekly.data);
  if (weeklyTracks.length > 0) {
    mixes.push({
      key: 'discovery_weekly',
      title: 'The Archives',
      subtitle: 'A weekly dig through artists across your library',
      tracks: weeklyTracks,
      syncKey: 'discovery_weekly',
    });
  }
  const seasonalTracks = outcomeTracks(seasonalPlaylist.data);
  if (season && seasonalTracks.length > 0) {
    const titles = seasonalMixTitles(season);
    mixes.push({
      key: 'seasonal_playlist',
      title: titles.title,
      subtitle: titles.subtitle,
      tracks: seasonalTracks,
      syncKey: 'seasonal_playlist',
    });
  }
  for (const feeder of [
    {
      key: 'popular_picks',
      title: 'Popular Picks',
      subtitle: 'Popular tracks from artists you love',
      query: popularPicks,
    },
    {
      key: 'hidden_gems',
      title: 'Hidden Gems',
      subtitle: 'Deeper cuts you might have missed',
      query: hiddenGems,
    },
    {
      key: 'listening_mix',
      title: 'Your Listening Mix',
      subtitle: 'From artists matched to your listening',
      query: listeningMix,
    },
    {
      key: 'discovery_shuffle',
      title: 'Discovery Shuffle',
      subtitle: 'A shuffle across everything we discovered for you',
      query: shuffle,
    },
  ]) {
    const tracks = outcomeTracks(feeder.query.data);
    if (tracks.length > 0) {
      mixes.push({
        key: feeder.key,
        title: feeder.title,
        subtitle: feeder.subtitle,
        tracks,
        syncKey: feeder.key,
      });
    }
  }

  const decadesOutcome = decades.data as SectionOutcome<Record<string, unknown>> | undefined;
  const availableDecades =
    decadesOutcome?.kind === 'ok' && Array.isArray(decadesOutcome.data.decades)
      ? (decadesOutcome.data.decades as AvailableDecade[])
      : [];
  const decadeMixes = availableDecades.map((d) => decadeMix(d));

  const registry: Record<string, DiscoverMix> = {};
  for (const m of [...mixes, ...decadeMixes]) registry[m.key] = m;

  return { mixes, decadeMixes, registry };
}
