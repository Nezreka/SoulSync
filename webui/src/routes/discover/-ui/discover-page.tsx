import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { getShellProfileContext } from '@/platform/shell/bridge';

import type { WebLens } from '../-discover.artist-web';
import type { CacheItem } from '../-discover.cache-sections';
import type { DiscoverSectionId, DiscoverZoneId } from '../-discover.layout';
import type { DiscoverMix, MixAction } from '../-discover.mixes';
import type { RecentAlbum } from '../-discover.recent-releases';
import type { RecommendedArtist } from '../-discover.recommended';
import type { SeasonData, SeasonalAlbum } from '../-discover.seasonal';
import type { DiscoverHeroArtist } from '../-discover.types';
import type { YourAlbum } from '../-discover.your-albums-actions';
import type { GenreDiveData } from './genre-dive-modal';

import {
  fetchBecauseYouListenTo,
  fetchGenreDeepDive,
  fetchFlow,
  fetchInbox,
  fetchLbPlaylist,
  fetchWeekStats,
} from '../-discover.api';
import { useDominantColor } from '../-discover.backdrop';
import { bpMetaStats } from '../-discover.build-playlist';
import {
  byltSections,
  byltRow,
  byltShelfRows,
  byltShelfTitle,
  byltShelfVirtualId,
  byltStatusNote,
  byltTrackToRow,
  byltTracks,
  BYLT_STALE_MS,
  type ByltPayload,
  type ByltSection,
  type ByltTrack,
} from '../-discover.bylt';
import { CACHE_SECTIONS } from '../-discover.cache-sections';
import { decadeClassicsName, decadeTrackToSpotify } from '../-discover.decade-shelf';
import {
  DEEZER_MIRROR_VERB,
  DEEZER_MIX_PREFIX,
  deezerPreviewMix,
  fetchDeezerPreviewTracks,
  type DeezerEditorialPlaylist,
} from '../-discover.deezer-editorial';
import { explanationLine } from '../-discover.explanation';
import { quickTiles } from '../-discover.greeting';
import { normalizeTrack } from '../-discover.helpers';
import { inboxArtistRef } from '../-discover.inbox';
import { discoverLimiter } from '../-discover.limiter';
import {
  lbStatusBase,
  lbSyncFailedId,
  lbSyncMatchedId,
  lbSyncPercentageId,
  lbSyncTotalId,
} from '../-discover.listenbrainz';
import { beginPlayIntent, playMixNow, playTrackNow, type PlayIntent } from '../-discover.playable';
import { syncBubbleImage, toSyncTracks } from '../-discover.playlist-sync';
import { pickConcert, pickPosterAlbum, posterDay } from '../-discover.posters';
import { profileKey, useProfileScope } from '../-discover.profile-scope';
import { pickSpotlight, releaseKind, shortDate, tasteGap, weekSummary } from '../-discover.pulse';
import { keepRecipe, recipeVerb, refreshRecipe, type RecipeMixCard } from '../-discover.recipes';
import { recSource, recommendedVisible } from '../-discover.recommended';
import {
  fetchStations,
  stationSyncKey,
  stationTitle,
  stationVirtualId,
  STATION_NOTHING_SELECTED,
  STATION_NO_BRIDGE,
  type Station,
} from '../-discover.stations';
import { useAlbumOpen } from '../-discover.use-album-open';
import { useArtistMap } from '../-discover.use-artist-map';
import { useBlacklist } from '../-discover.use-blacklist';
import { useBuildPlaylist } from '../-discover.use-build-playlist';
import { useDownloadBar } from '../-discover.use-download-bar';
import { useHero } from '../-discover.use-hero';
import { useLastfmRadio } from '../-discover.use-lastfm-radio';
import { useDiscoverLayout } from '../-discover.use-layout';
import { useListenBrainz } from '../-discover.use-listenbrainz';
import { defaultLazySource, useMixModal } from '../-discover.use-mix-modal';
import { useDiscoverMixes } from '../-discover.use-mixes';
import { useDiscoverPage } from '../-discover.use-page';
import { usePlaylistSync } from '../-discover.use-playlist-sync';
import { usePromoVideo } from '../-discover.use-promo';
import { useAdventurousness, useRecommended } from '../-discover.use-recommended';
import { useStationPreview } from '../-discover.use-station';
import { useYourAlbums } from '../-discover.use-your-albums';
import { useYourArtists } from '../-discover.use-your-artists';
import { useVideoBackdropsEnabled } from '../-discover.video-stage';
import {
  ARTISTS_DEFAULT_SOURCES,
  savedArtistSourcesSubtitle,
} from '../-discover.your-artists-actions';
import { AdventurousnessDial } from './adventurousness-dial';
import { RecentReleasesShelf, SeasonalAlbumsShelf } from './album-shelves';
import { ArtistInfoModal } from './artist-info-modal';
import { ArtistMapAssembly } from './artist-map-assembly';
import { ArtistMapHub, ArtistWebHub } from './artist-map-hub';
import { ArtistWebAssembly } from './artist-web-assembly';
import { ArtMapExplorePrompt } from './artmap-explore-prompt';
import { BlacklistModal } from './blacklist-modal';
import { BuildPlaylistSection } from './build-playlist';
import { ByltSections } from './bylt-sections';
import { CacheShelf, GenreExplorerSection } from './cache-shelves';
import { DeezerEditorialShelf } from './deezer-editorial-shelf';
import { DiscoverHero } from './discover-hero';
import { DiscoverLayoutModal } from './discover-layout-modal';
import { DiscoverNav, type DiscoverNavItem } from './discover-nav';
import { DiscoveryInbox } from './discovery-inbox';
import { DownloadBar } from './download-bar';
import { GenreDiveModal } from './genre-dive-modal';
import { GreetingGrid } from './greeting-grid';
import { MixModal } from './mix-modal';
import { MixShelf } from './mix-shelf';
import { NowPlayingBanner, useNowPlaying } from './now-playing-banner';
import { AlbumPoster, ArtistPoster, ConcertPoster, PosterRow } from './poster-row';
import { PromoBanner } from './promo-banner';
import { TasteGapBanner, useReveal, WeekBanner } from './pulse-banners';
import { LastfmRadioSection, ListenBrainzSection } from './radio-sections';
import { RecipeEditor } from './recipe-editor';
import { RecommendedModal } from './recommended-modal';
import { RecommendedShelf } from './recommended-shelf';
import { YourAlbumsSourcesModal, YourArtistsSourcesModal } from './sources-modals';
import { StationModal } from './station-modal';
import { StationsRow } from './stations-row';
import { VideoRail, type RailArtist } from './video-rail';
import { YourAlbumsBatchModal } from './your-albums-batch-modal';
import { YourAlbumsShelf } from './your-albums-shelf';
import { YourArtistsModal } from './your-artists-modal';
import { YourArtistsShelf } from './your-artists-shelf';

/**
 * The discover page — every controller composed over DISCOVER_LAYOUT.
 *
 * Section ORDER is `buildLayoutRows(hasContent)` over the vanilla's layout
 * array; the hero and the Artist Map hub sit above the rows, exactly where
 * `_reorderDiscoverSections` leaves them (265). Data comes from
 * useDiscoverPage's tiered queries; interactions from one controller per
 * section family; the two viz overlays render INSTEAD of the container when
 * open — the vanilla's `_prevDisplay` sibling bookkeeping has nothing left to
 * do.
 */

/** buildArtistDetailPath (init.js 2964): /artist-detail/<source>/<id>. */
function detailPath(artistId: unknown, source?: string | null, name?: string | null): string {
  const normalized =
    String(source ?? '')
      .trim()
      .toLowerCase() || 'library';
  const path = `/artist-detail/${encodeURIComponent(normalized)}/${encodeURIComponent(String(artistId))}`;
  return name ? `${path}?name=${encodeURIComponent(name)}` : path;
}

/** The source-logo urls core.js holds as globals (915-922) — shared assets. */
const LOGOS = {
  spotify: '/static/img/brands/spotify.png',
  itunes: '/static/img/brands/itunes.png',
  deezer: '/static/img/brands/deezer.png',
  discogs: '/static/img/brands/discogs.svg',
};

const toast = (message: string, level = 'info') => window.showToast?.(message, level);

/**
 * progressFor hands back the REDUCED SyncProgress; SyncStatus re-reduces from
 * the wire shape, so the reduced counts ride back in under the wire keys.
 */
function toRawProgress(p: { total: number; matched: number; failed: number } | undefined) {
  return p
    ? { total_tracks: p.total, matched_tracks: p.matched, failed_tracks: p.failed }
    : undefined;
}

/**
 * The LB sync-status block, transcribed from the statusHtml at 3595-3608.
 *
 * Rendered hidden; sync-services.js flips the container's display and fills
 * the spans by id while its poll runs — the ids are the contract.
 */
function LbSyncStatus({ identifier }: { identifier: string }) {
  return (
    <div
      className="discover-sync-status"
      id={`${lbStatusBase(identifier)}-sync-status`}
      style={{ display: 'none' }}
    >
      <div className="sync-status-content">
        <div className="sync-status-label">
          <span className="sync-icon">⟳</span>
          <span>Syncing to media server...</span>
        </div>
        <div className="sync-status-stats">
          <span className="sync-stat">
            ♪ <span id={lbSyncTotalId(identifier)}>0</span>
          </span>
          <span className="sync-separator">/</span>
          <span className="sync-stat">
            ✓ <span id={lbSyncMatchedId(identifier)}>0</span>
          </span>
          <span className="sync-separator">/</span>
          <span className="sync-stat">
            ✗ <span id={lbSyncFailedId(identifier)}>0</span>
          </span>
          <span className="sync-stat">
            (<span id={lbSyncPercentageId(identifier)}>0</span>%)
          </span>
        </div>
      </div>
    </div>
  );
}

/** A section outcome's payload when it succeeded, else undefined. */
function okData<T>(outcome: unknown): T | undefined {
  const o = outcome as { kind?: string; data?: T } | undefined;
  return o?.kind === 'ok' ? o.data : undefined;
}

interface DiscoveryZoneProps {
  id: string;
  title: string;
  subtitle: string;
  tone: string;
  children: React.ReactNode;
}

/**
 * one zone of the feed. a title and one line, then its rows. the zone used to
 * wear a kicker that repeated its own title ("FOR YOU / For You") and a count
 * of "signals", "leads" and "tools" that meant nothing to anyone reading it.
 */
function DiscoveryZone({ id, title, subtitle, tone, children }: DiscoveryZoneProps) {
  return (
    <section className={`discovery-zone discovery-zone--${tone}`} id={id}>
      <header className="discovery-zone-head">
        <div>
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
      </header>
      <div className="discovery-zone-body">{children}</div>
    </section>
  );
}

export function DiscoverPage() {
  const page = useDiscoverPage();
  const mixes = useDiscoverMixes(page.aboveFoldSettled);
  const queryClient = useQueryClient();
  const sync = usePlaylistSync((t) => toast(t.message, t.level));
  const bar = useDownloadBar();
  const albumOpen = useAlbumOpen((t) => toast(t.message, t.level));
  const heroArtists = useMemo(
    () => (page.hero.data?.artists ?? []) as DiscoverHeroArtist[],
    [page.hero.data],
  );
  const hero = useHero(heroArtists, (t) => toast(t.message, t.level));
  const rec = useRecommended((t) => toast(t.message, t.level));
  const dial = useAdventurousness(
    okData<{ value?: number }>(page.sections.adventurousness?.data)?.value ?? 0.3,
  );
  const artists = useYourArtists((t) => toast(t.message, t.level));
  const albums = useYourAlbums((t) => toast(t.message, t.level));
  const blacklist = useBlacklist((t) => toast(t.message, t.level));
  const lastfm = useLastfmRadio((t) => toast(t.message, t.level));
  const lb = useListenBrainz((t) => toast(t.message, t.level));
  const bp = useBuildPlaylist((t) => toast(t.message, t.level));
  const map = useArtistMap((t) => toast(t.message, t.level));

  const [webRequest, setWebRequest] = useState<{ lens?: WebLens } | null>(null);
  const [dive, setDive] = useState<{
    genre: string;
    data: GenreDiveData | null;
    phase: 'loading' | 'error' | 'ready';
  } | null>(null);
  const [recModalOpen, setRecModalOpen] = useState(false);
  // the recipe editor: a new mix, or the one being edited
  const [recipeEditor, setRecipeEditor] = useState<{ editing: RecipeMixCard | null } | null>(null);
  const [addingAll, setAddingAll] = useState(false);
  const [explorerPromptOpen, setExplorerPromptOpen] = useState(false);
  const [lbCovers, setLbCovers] = useState<Record<string, unknown[]>>({});

  // BYLT never joined use-page's tiers (its loader renders '' when empty), so
  // the page queries it directly with the same limiter/no-retry shape.
  //
  // The key now carries the PROFILE. It used to be ['discover', 'bylt'] with
  // infinite stale and gc times, so switching profiles left the previous
  // profile's shelves on screen until a reload, and an in-flight request begun
  // before the switch could land afterwards. A finite stale time replaces the
  // infinite one: these shelves are regenerated by the scanner, and "never
  // refetch for the life of the tab" is not a freshness policy.
  const profileId = useProfileScope();
  // The profile's page layout: saved sections merged over the defaults by
  // the server. Falls back to the defaults while pending or failed, so the
  // page never renders without sections.
  const pageLayout = useDiscoverLayout(profileId);
  const [layoutOpen, setLayoutOpen] = useState(false);
  const [layoutSaving, setLayoutSaving] = useState(false);
  const bylt = useQuery({
    queryKey: ['discover', 'bylt', profileKey(profileId)] as const,
    queryFn: () => discoverLimiter.run(fetchBecauseYouListenTo),
    staleTime: BYLT_STALE_MS,
    gcTime: BYLT_STALE_MS * 2,
    retry: false,
    enabled: page.aboveFoldSettled,
  });
  const byltData = okData<ByltPayload>(bylt.data) ?? null;
  const byltRows: ByltSection[] = byltSections(byltData);
  const byltNote = byltStatusNote(byltData);

  // Stations: the same profile-scoped key, and a REAL error state. The first
  // version fetched into local state and collapsed every failure to an empty
  // array, which rendered exactly like "you have no stations".
  const stationsQuery = useQuery({
    queryKey: ['discover', 'stations', profileKey(profileId)] as const,
    queryFn: () => discoverLimiter.run(fetchStations),
    staleTime: BYLT_STALE_MS,
    gcTime: BYLT_STALE_MS * 2,
    retry: false,
    enabled: page.aboveFoldSettled,
  });
  // your week: the stats worker's cached summary. instant (a metadata read),
  // so it rides with the first paint instead of waiting for tier 2.
  const weekQuery = useQuery({
    queryKey: ['discover', 'week-stats', profileKey(profileId)] as const,
    queryFn: () => discoverLimiter.run(fetchWeekStats),
    staleTime: BYLT_STALE_MS,
    gcTime: BYLT_STALE_MS * 2,
    retry: false,
  });
  const week = weekSummary(weekQuery.data);
  const gap = tasteGap(weekQuery.data);
  const [playingTop, setPlayingTop] = useState(false);
  const [flowBusy, setFlowBusy] = useState(false);

  // ── the living banners: one music video at a time, only the one on screen ──
  const [videosOn, setVideosOn] = useVideoBackdropsEnabled();
  const heroGlow = useDominantColor(hero.artist?.image_url ?? null);
  const stationPreview = useStationPreview();
  // A profile switch discards an open preview: it belongs to the old profile,
  // and a response already in flight for it must never fill this one in.
  const lastProfile = useRef(profileId);
  useEffect(() => {
    if (lastProfile.current !== profileId) {
      lastProfile.current = profileId;
      stationPreview.reset();
    }
  }, [profileId, stationPreview]);

  // Obligation: resume mid-flight syncs + rehydrate yesterday's download bar.
  useEffect(() => {
    void sync.resumeActiveSyncs();
    void bar.hydrate();
    // The LB discovery states hydrate from the backend at page init
    // (discover.js 244) — without this, a restart forgets every in-flight
    // discovery/download and the resume branches find no state.
    void window.loadListenBrainzPlaylistsFromBackend?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // #1418: a Deezer editorial card opens here to be looked at, and its
  // Mirror button runs the shelf's own mirror (with the card's progress)
  const [deezerMixes, setDeezerMixes] = useState<Record<string, DiscoverMix>>({});
  const deezerMirrors = useRef<Record<string, () => void>>({});
  const [pendingOpen, setPendingOpen] = useState<string | null>(null);
  const previewDeezer = useCallback((playlist: DeezerEditorialPlaylist, mirror: () => void) => {
    const mix = deezerPreviewMix(playlist);
    deezerMirrors.current[playlist.id] = mirror;
    setDeezerMixes((prev) => (prev[mix.key] ? prev : { ...prev, [mix.key]: mix }));
    // the registry only has the new key on the next render
    setPendingOpen(mix.key);
  }, []);

  // ── The mix modal: ONE registry over every section's mixes ──────────────
  const registry = useMemo(() => {
    const all: Record<string, DiscoverMix> = { ...mixes.registry, ...deezerMixes };
    for (const m of [...lb.mixes, ...lastfm.mixes]) all[m.key] = m;
    // Hydrated LB tracks ride into the registry, so opening a card the
    // mosaic pass already fetched is instant — the vanilla gets the same
    // effect by mutating mix.tracks in place (4874).
    for (const [key, tracks] of Object.entries(lbCovers)) {
      if (all[key] && !all[key].tracks?.length && tracks.length) {
        all[key] = { ...all[key], tracks };
      }
    }
    return all;
  }, [mixes.registry, deezerMixes, lb.mixes, lastfm.mixes, lbCovers]);

  // lb-* keys lazy-load from the playlist endpoint — one resolver serves
  // ListenBrainz AND Last.fm radio mixes (both key `lb-<tab>-<identifier>`).
  const lbLazy = useCallback((mix: DiscoverMix) => {
    if (mix.key.startsWith(DEEZER_MIX_PREFIX)) {
      const id = mix.key.slice(DEEZER_MIX_PREFIX.length);
      return () => fetchDeezerPreviewTracks(id);
    }
    if (!mix.key.startsWith('lb-')) return null;
    const identifier = mix.key.split('-').slice(2).join('-');
    return async () => {
      const data = (await fetchLbPlaylist(identifier)) as { tracks?: unknown[] };
      return data.tracks ?? [];
    };
  }, []);
  const modal = useMixModal(registry, lbLazy);
  const openMix = modal.open;
  useEffect(() => {
    if (pendingOpen && registry[pendingOpen]) {
      openMix(pendingOpen);
      setPendingOpen(null);
    }
  }, [pendingOpen, registry, openMix]);

  // which mix key / track row is resolving. two fast taps used to queue the
  // same thing twice, and nothing on screen said anything was happening.
  const pendingPlay = useRef<{ owner: string; intent: PlayIntent } | null>(null);
  const [playingMixKey, setPlayingMixKey] = useState<string | null>(null);
  const [playingTrackIndex, setPlayingTrackIndex] = useState<number | null>(null);

  /** Play a mix straight from its card, fetching a lazy tracklist first. */
  const playMixFromCard = useCallback(
    (key: string) => {
      // Only the SAME mix is blocked while it resolves. Blocking every other
      // card too left them looking live and doing nothing, which on a slow
      // resolve is a dead control for a minute.
      if (pendingPlay.current?.owner === `mix:${key}`) return;
      const intent = beginPlayIntent();
      pendingPlay.current = { owner: `mix:${key}`, intent };
      setPlayingTrackIndex(null);
      setPlayingMixKey(key);
      void (async () => {
        try {
          const tracks = await modal.loadTracks(key);
          if (!intent.isCurrent()) return;
          if (tracks === null) {
            toast('Could not load that mix', 'error');
            return;
          }
          await playMixNow(tracks, registry[key]?.title ?? 'Mix', intent);
        } finally {
          if (pendingPlay.current?.intent === intent) {
            pendingPlay.current = null;
            setPlayingMixKey(null);
          }
        }
      })();
    },
    [playingMixKey, modal, registry],
  );

  /** Play one row of the open mix. Resolved against the rendered list. */
  const playTrackFromModal = useCallback(
    (index: number) => {
      const owner = `track:${modal.mix?.key}:${index}`;
      if (pendingPlay.current?.owner === owner) return;
      const rows = modal.tracks ?? [];
      const track = rows[index];
      if (!track) {
        toast('That track is no longer in this mix', 'error');
        return;
      }
      const intent = beginPlayIntent();
      pendingPlay.current = { owner, intent };
      setPlayingMixKey(null);
      setPlayingTrackIndex(index);
      void (async () => {
        try {
          await playTrackNow(track, normalizeTrack(track as never).name, intent);
        } finally {
          if (pendingPlay.current?.intent === intent) {
            pendingPlay.current = null;
            setPlayingTrackIndex(null);
          }
        }
      })();
    },
    [playingTrackIndex, modal.tracks],
  );

  // The cover-mosaic hydration (_hydrateMixCovers, 4870): LB cards arrive
  // track-less, so each one background-fetches its playlist once and the card
  // re-renders with a real 4-tile mosaic and an honest track count.
  const lbCoversRef = useRef(lbCovers);
  lbCoversRef.current = lbCovers;
  // Deferred: the eager version fetched EVERY LB playlist and EVERY decade
  // playlist at mount just to paint 4-tile mosaics — dozens of requests
  // before the user scrolled anywhere near those shelves. Each shelf now
  // hydrates when it actually enters the viewport. Environments without
  // IntersectionObserver (jsdom) keep the eager behaviour.
  const [mosaicsWanted, setMosaicsWanted] = useState<{ lb: boolean; decades: boolean }>(() => {
    const eager = typeof IntersectionObserver === 'undefined';
    return { lb: eager, decades: eager };
  });
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return;
    if (mosaicsWanted.lb && mosaicsWanted.decades) return;
    const anchors: [keyof typeof mosaicsWanted, HTMLElement | null][] = [
      ['lb', document.getElementById('listenbrainz')],
      ['decades', document.getElementById('year-mixes-grid')],
    ];
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const hit = anchors.find(([, el]) => el === entry.target)?.[0];
          if (hit) {
            setMosaicsWanted((w) => (w[hit] ? w : { ...w, [hit]: true }));
            observer.unobserve(entry.target);
          }
        }
      },
      // Start the fetch a screen early so the mosaics are painted by arrival.
      { rootMargin: '600px' },
    );
    for (const [key, el] of anchors) {
      if (el && !mosaicsWanted[key]) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [mosaicsWanted, lb.mixes.length, mixes.decadeMixes.length]);
  useEffect(() => {
    const due = [
      ...(mosaicsWanted.lb ? lb.mixes : []),
      ...(mosaicsWanted.decades ? mixes.decadeMixes : []),
      // lastfm radios are few (one per generated radio) and were never
      // hydrated at all - every card wore four placeholder tiles forever
      ...lastfm.mixes,
    ];
    for (const mix of due) {
      if (mix.tracks?.length || lbCoversRef.current[mix.key]) continue;
      const lazy = defaultLazySource(mix) ?? lbLazy(mix);
      if (!lazy) continue;
      setLbCovers((prev) => ({ ...prev, [mix.key]: [] })); // in-flight marker
      lazy()
        .then((tracks) => setLbCovers((prev) => ({ ...prev, [mix.key]: tracks })))
        .catch(() => {});
    }
  }, [lb.mixes, mixes.decadeMixes, lastfm.mixes, lbLazy, mosaicsWanted]);
  const decadeMixesHydrated = useMemo(
    () =>
      mixes.decadeMixes.map((mix) =>
        !mix.tracks?.length && lbCovers[mix.key]?.length
          ? { ...mix, tracks: lbCovers[mix.key] }
          : mix,
      ),
    [mixes.decadeMixes, lbCovers],
  );
  const lbMixesHydrated = useMemo(
    () =>
      lb.mixes.map((mix) =>
        !mix.tracks?.length && lbCovers[mix.key]?.length
          ? { ...mix, tracks: lbCovers[mix.key] }
          : mix,
      ),
    [lb.mixes, lbCovers],
  );
  const lastfmMixesHydrated = useMemo(
    () =>
      lastfm.mixes.map((mix) =>
        !mix.tracks?.length && lbCovers[mix.key]?.length
          ? { ...mix, tracks: lbCovers[mix.key] }
          : mix,
      ),
    [lastfm.mixes, lbCovers],
  );

  /** Convert + hand to the SHARED downloads.js modal (11129-11160). */
  const openTracksModal = useCallback((virtualId: string, name: string, rows: unknown[]) => {
    if (!rows.length) {
      toast('No tracks available yet', 'warning');
      return false;
    }
    const spotifyTracks = toSyncTracks(rows as Record<string, unknown>[]);
    void window.openDownloadMissingModalForYouTube?.(virtualId, name, spotifyTracks);
    return true;
  }, []);

  /** A mix's action strings, built-in and lb- alike (4946-4952, 3934, 3223). */
  const runMixAction = useCallback(
    (action: MixAction) => {
      const mix = modal.mix;
      if (!mix) return;
      const recipe = recipeVerb(action.onclick);
      if (recipe) {
        const [rverb, recipeId] = recipe;
        if (rverb === 'recipe-edit') {
          modal.close();
          setRecipeEditor({
            editing: mixes.recipes.find((r) => r.recipe_id === recipeId) ?? null,
          });
        } else if (rverb === 'recipe-refresh') {
          void refreshRecipe(recipeId)
            .then(() => {
              void queryClient.invalidateQueries({ queryKey: ['discover', 'recipes'] });
              toast(`New tracks in ${mix.title}`, 'success');
            })
            .catch(() => toast("Couldn't renew the mix. Try again.", 'error'));
        } else {
          void keepRecipe(recipeId)
            .then((res) => {
              if (res.success === false) throw new Error(res.error);
              toast(`Kept as a playlist: find it on the Sync page`, 'success');
            })
            .catch(() => toast("Couldn't keep this one. Try again.", 'error'));
        }
        return;
      }
      const [verb, ...rest] = action.onclick.split(':');
      if (verb === DEEZER_MIRROR_VERB) {
        modal.close();
        deezerMirrors.current[rest.join(':')]?.();
        return;
      }
      if (verb === 'play') {
        // resolve against the library and play what's owned RIGHT NOW; the
        // rest stays a download away. LB/lastfm cards load tracks lazily -
        // with nothing loaded yet there is nothing resolvable, and
        // playMixNow's empty answer says so honestly.
        //
        // The busy state is not decoration: resolving 50 tracks against the
        // library takes a couple of seconds, and this button said nothing at
        // all for the whole wait.
        // Keep the tracklist open while listening. Completion must never close
        // another dialog opened while the request was pending.
        playMixFromCard(mix.key);
        return;
      }
      if (verb === 'lb-download') {
        // The FULL discovery flow (state check, rehydrate, seed + poll + the
        // sync-services discovery modal), verbatim in the core.js bridge —
        // Boulder's directive: exactly like vanilla, no difference. Serves
        // ListenBrainz AND Last.fm radio playlists (both are LB cards, 3378).
        modal.close();
        void (async () => {
          const identifier = rest.join(':');
          const tracks = modal.tracks?.length
            ? modal.tracks
            : (((await fetchLbPlaylist(identifier)) as { tracks?: unknown[] }).tracks ?? []);
          void window.openLbPlaylistDiscovery?.(identifier, mix.title, tracks);
        })();
        return;
      }
      if (verb === 'lb-sync') {
        // sync-services.js owns the WHOLE LB sync — fetch, virtual playlist,
        // polling — and writes into the -sync-total/-sync-matched spans the
        // modal's override renders (3592-3616). It survives PR2.
        //
        // A playlist whose state never left 'fresh' (a just-generated lastfm
        // radio) has no frontend sync state, and startListenBrainzPlaylistSync
        // console.errors and returns - a silently dead button. Say what to do.
        const lbId = rest.join(':');
        const states = window.listenbrainzPlaylistStates;
        if (states && !states[lbId]) {
          toast('Run Download first — sync needs its discovery results', 'info');
          return;
        }
        void window.startListenBrainzPlaylistSync?.(lbId);
        return;
      }
      const type = mix.syncKey ?? mix.key;
      if (action.isSync) {
        const out = sync.startMixSync(mix, modal.tracks);
        if (out) toast(out.message, out.level);
        // A sync ALSO registers a bar bubble (11344), art from the tracks.
        else {
          bar.add(
            `discover_${type}`,
            mix.title,
            type,
            syncBubbleImage(toSyncTracks((modal.tracks ?? []) as Record<string, unknown>[])),
          );
        }
      } else {
        modal.close();
        const rows = (modal.tracks ?? []) as Record<string, unknown>[];
        const decade = /^decade_(\d+)$/.exec(mix.key);
        if (!rows.length) {
          // The decade path words its refusal differently (2863).
          toast(
            decade ? 'No tracks available for this decade' : 'No tracks available yet',
            'warning',
          );
          return;
        }
        if (decade) {
          // The decade download is its OWN conversion and its OWN ids
          // (2860-2905): decade_<year>, "<year>s Classics", and artists stay
          // OBJECTS (DECADE_DOWNLOAD_KEEPS_ARTIST_OBJECTS) — toSyncTracks here
          // made every artist "Unknown Artist" in the live smoke test.
          const year = Number(decade[1]);
          void window.openDownloadMissingModalForYouTube?.(
            `decade_${year}`,
            decadeClassicsName(year),
            rows.map((t) => decadeTrackToSpotify(t, false)),
          );
        } else {
          void window.openDownloadMissingModalForYouTube?.(
            `discover_${type}`,
            mix.title,
            toSyncTracks(rows),
          );
        }
        // No bubble here: downloads.js registers it when the download actually
        // starts (1806) — a bubble at modal-open outlived a cancelled modal.
      }
    },
    [modal, sync, bar, openTracksModal, playMixFromCard, mixes.recipes, queryClient],
  );

  const downloadSelection = useCallback(() => {
    const result = modal.downloadSelection();
    if (result.kind === 'ok') {
      modal.close();
      // result.tracks are ALREADY spotify-shaped (discoverTrackToSpotifyShape
      // ran inside downloadSelection). Re-converting them read track_name off
      // a shape that has `name` — every artist became "Unknown Artist" in the
      // live smoke test. Straight to the shared modal, no second conversion.
      void window.openDownloadMissingModalForYouTube?.(
        result.virtualId,
        result.name,
        result.tracks,
      );
    } else {
      toast(result.toast, result.level);
    }
  }, [modal]);

  // ── Because You Listen To: named actions on identified tracks ────────────
  //
  // The vanilla tiles were inert and the first port gave the whole card one
  // click that resolved an ALBUM from name strings. Each action below is
  // explicit, and each one carries the track's own identity rather than
  // re-deriving it from a display title.
  const [byltPending, setByltPending] = useState<string | null>(null);
  const [byltErrors, setByltErrors] = useState<Record<string, string>>({});

  const byltKeyOf = useCallback(
    (section: ByltSection, track: ByltTrack, index: number) =>
      `${section.seed_key ?? section.artist_name ?? ''}:${byltRow(track, index).key}`,
    [],
  );

  const playByltTrack = useCallback(
    (track: ByltTrack, section: ByltSection) => {
      const index = byltTracks(section).indexOf(track);
      const key = byltKeyOf(section, track, index < 0 ? 0 : index);
      if (byltPending === key) return;
      const intent = beginPlayIntent();
      setByltPending(key);
      setByltErrors((prev) => {
        if (!prev[key]) return prev;
        const next = { ...prev };
        delete next[key];
        return next;
      });
      void (async () => {
        try {
          await playTrackNow(byltTrackToRow(track), track.name ?? 'Track', intent);
        } catch {
          setByltErrors((prev) => ({ ...prev, [key]: 'Could not play that track' }));
        } finally {
          setByltPending((current) => (current === key ? null : current));
        }
      })();
    },
    [byltKeyOf, byltPending],
  );

  const downloadByltTrack = useCallback((track: ByltTrack, section: ByltSection) => {
    if (!window.openDownloadMissingModalForYouTube) {
      toast('Downloads are not available on this page yet', 'error');
      return;
    }
    // Identity, not a title: the id is in the virtual playlist key so a retry
    // reopens the same operation instead of starting a second one.
    void window.openDownloadMissingModalForYouTube(
      `${byltShelfVirtualId(section)}_${track.id ?? 'track'}`,
      `${track.name ?? 'Track'} — ${track.artist ?? ''}`.trim(),
      toSyncTracks([byltTrackToRow(track)]),
      null,
      null,
      'SoulSync',
    );
  }, []);

  const openByltAlbum = useCallback(
    (track: ByltTrack) => {
      // Explicitly the ALBUM, from a button that says Album — the cache TRACK
      // branch resolves by album name + artist, so the album name goes in the
      // album field. Passing the TRACK name here failed every click.
      void albumOpen.openCacheItem('genre_dive_tracks', {
        name: track.name,
        album_name: track.album,
        artist_name: track.artist,
      });
    },
    [albumOpen],
  );

  const playByltShelf = useCallback((section: ByltSection) => {
    const intent = beginPlayIntent();
    void playMixNow(byltShelfRows(section), byltShelfTitle(section), intent);
  }, []);

  const downloadByltShelf = useCallback((section: ByltSection) => {
    const rows = byltShelfRows(section);
    if (!rows.length) {
      toast('No tracks available yet', 'warning');
      return;
    }
    if (!window.openDownloadMissingModalForYouTube) {
      toast('Downloads are not available on this page yet', 'error');
      return;
    }
    void window.openDownloadMissingModalForYouTube(
      byltShelfVirtualId(section),
      byltShelfTitle(section),
      toSyncTracks(rows),
      null,
      null,
      'SoulSync',
    );
  }, []);

  // ── Stations: preview, then act on exactly what is checked ───────────────
  const [stationPlayingIndex, setStationPlayingIndex] = useState<number | null>(null);

  const playStationRadio = useCallback(async (station: Station) => {
    if (!window.startArtistRadioById) throw new Error(STATION_NO_BRIDGE);
    const started = await window.startArtistRadioById(String(station.artist_id), station.name);
    if (started === false) throw new Error(`Could not start ${station.name} radio`);
  }, []);

  const stationRows = useCallback(
    () => stationPreview.selection() as unknown as Record<string, unknown>[],
    [stationPreview],
  );

  const playStationSelection = useCallback(() => {
    const rows = stationRows();
    if (!rows.length) {
      toast(STATION_NOTHING_SELECTED, 'info');
      return;
    }
    const intent = beginPlayIntent();
    const snap = stationPreview.snapshot;
    void playMixNow(rows, snap ? stationTitle(snap) : 'Station', intent);
  }, [stationPreview, stationRows]);

  const playStationTrack = useCallback(
    (index: number) => {
      const track = stationPreview.snapshot?.tracks?.[index];
      if (!track) return;
      if (stationPlayingIndex === index) return;
      const intent = beginPlayIntent();
      setStationPlayingIndex(index);
      void (async () => {
        try {
          await playTrackNow(
            track as unknown as Record<string, unknown>,
            track.track_name ?? 'Track',
            intent,
          );
        } finally {
          setStationPlayingIndex((current) => (current === index ? null : current));
        }
      })();
    },
    [stationPlayingIndex, stationPreview.snapshot],
  );

  const downloadStationSelection = useCallback(() => {
    const snap = stationPreview.snapshot;
    const rows = stationRows();
    if (!snap || !rows.length) {
      toast(STATION_NOTHING_SELECTED, 'info');
      return;
    }
    if (!window.openDownloadMissingModalForYouTube) {
      toast('Downloads are not available on this page yet', 'error');
      return;
    }
    // The id is station + snapshot revision, so retrying reopens the SAME
    // operation. The label is explicit: this is a SoulSync station, and the
    // modal's prefix sniffing would otherwise call it YouTube.
    void window.openDownloadMissingModalForYouTube(
      stationVirtualId(snap),
      stationTitle(snap),
      toSyncTracks(rows),
      null,
      null,
      'SoulSync',
    );
  }, [stationPreview.snapshot, stationRows]);

  const syncStationSelection = useCallback(() => {
    const snap = stationPreview.snapshot;
    const rows = stationRows();
    if (!snap || !rows.length) {
      toast(STATION_NOTHING_SELECTED, 'info');
      return;
    }
    if (!window.startDiscoverVirtualSync) {
      toast('Sync is not available on this page yet', 'error');
      return;
    }
    const key = stationSyncKey(snap);
    const out = sync.startSync({
      virtualId: stationVirtualId(snap),
      name: stationTitle(snap),
      statusBase: key.replace(/_/g, '-'),
      doneToast: `${stationTitle(snap)} sync complete!`,
      tracks: toSyncTracks(rows),
    });
    if (out) toast(out.message, out.level);
    else
      bar.add(stationVirtualId(snap), stationTitle(snap), key, syncBubbleImage(toSyncTracks(rows)));
  }, [bar, stationPreview.snapshot, stationRows, sync]);

  const openDive = useCallback((genre: string) => {
    setDive({ genre, data: null, phase: 'loading' });
    fetchGenreDeepDive(genre)
      .then((data) => {
        setDive((d) =>
          d?.genre === genre
            ? { genre, data: data as unknown as GenreDiveData, phase: 'ready' }
            : d,
        );
      })
      .catch(() => {
        setDive((d) => (d?.genre === genre ? { genre, data: null, phase: 'error' } : d));
      });
  }, []);

  // ── Section payloads the render below reads more than once ─────────────
  const season = okData<SeasonData>(page.sections.seasonal?.data) ?? null;
  const recPayload = okData<{ artists?: RecommendedArtist[]; source?: string }>(
    page.sections.recommendedArtists?.data,
  );
  const recArtists = recommendedVisible(recPayload?.artists ?? []);

  // Once a shelf has rows: enrich image-less cards AND batch-confirm which
  // are already watched (checkRecommendedWatchlistStatuses, 694/801) — the
  // vanilla does both per shelf load, listening recs included.
  const listeningArtists = recommendedVisible(
    okData<{ artists?: RecommendedArtist[] }>(page.sections.listeningRecs?.data)?.artists ?? [],
  );
  useEffect(() => {
    if (recArtists.length) {
      void rec.enrichImages(recArtists, recSource(recPayload));
      void rec.checkWatching(recArtists);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recArtists.length]);
  useEffect(() => {
    if (listeningArtists.length) void rec.checkWatching(listeningArtists);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listeningArtists.length]);

  const hasContent = useCallback(
    (id: DiscoverSectionId): boolean => {
      if (id === 'lastfm-radio') return lastfm.configured === true;
      if (id === 'listenbrainz') return true; // renders its own load/error states
      if (id === 'deezer-editorial') return true; // fetches and empties itself
      if (id === 'build-a-playlist') return true; // a control, like adv-wave
      // always: 'Build a mix' lives in its header, so it must be reachable
      // before there is a single mix
      if (id === 'your-mixes-section') return true;
      if (id === 'year-mixes-section') return mixes.decadeMixes.length > 0;
      if (id === 'mood-mixes-section') return mixes.moodMixes.length > 0;
      if (id === 'discover-bylt-sections') return byltRows.length > 0;
      return page.hasContent(id);
    },
    [lastfm.configured, mixes, byltRows.length, page],
  );

  const cacheShelf = (id: DiscoverSectionId) => {
    const def = CACHE_SECTIONS.find((d) => d.id === id);
    if (!def) return null;
    const items = page.sectionState(id).items as CacheItem[];
    return (
      <CacheShelf
        def={def}
        items={items}
        onOpenItem={(key, index) => void albumOpen.openCacheItem(key, items[index])}
      />
    );
  };

  const renderSection = (id: DiscoverSectionId): React.ReactNode => {
    switch (id) {
      case 'cache-genre-explorer':
        return (
          <GenreExplorerSection
            genres={page.sectionState(id).items as { genre?: string }[]}
            limit={18}
            onOpenGenre={openDive}
          />
        );
      case 'your-mixes-section':
        return (
          <MixShelf
            id={id}
            title="Made For You"
            subtitle="Fresh mixes built from your listening — open one to see the tracks"
            mixes={mixes.mixes}
            loaded={true}
            gridId="your-mixes-grid"
            actions={
              <button
                type="button"
                className="discover-build-mix-btn"
                onClick={() => setRecipeEditor({ editing: null })}
              >
                + Build a mix
              </button>
            }
            onOpenMix={modal.open}
            onPlayMix={playMixFromCard}
            playingKey={playingMixKey}
          />
        );
      case 'mood-mixes-section':
        return (
          <MixShelf
            id={id}
            title="Moods"
            subtitle="A mix for how you feel, from your own albums. Plays straight away."
            mixes={mixes.moodMixes}
            loaded={true}
            gridId="mood-mixes-grid"
            onOpenMix={modal.open}
            onPlayMix={playMixFromCard}
            playingKey={playingMixKey}
          />
        );
      case 'year-mixes-section':
        return (
          <MixShelf
            id={id}
            title="Decades"
            subtitle="The sound of every era in your collection"
            mixes={decadeMixesHydrated}
            loaded={true}
            gridId="year-mixes-grid"
            onOpenMix={modal.open}
            onPlayMix={playMixFromCard}
            playingKey={playingMixKey}
          />
        );
      case 'adv-wave':
        return (
          <AdventurousnessDial
            value={dial.value}
            onChange={dial.change}
            onCommit={(v) => void dial.commit(v)}
          />
        );
      case 'listening-recs-section':
      case 'recommended-artists-section': {
        const kind = id === 'listening-recs-section' ? 'listening' : 'recommended';
        const q =
          kind === 'listening' ? page.sections.listeningRecs : page.sections.recommendedArtists;
        const payload = okData<{ artists?: RecommendedArtist[]; source?: string }>(q?.data);
        const items = recommendedVisible(payload?.artists ?? []);
        return (
          <RecommendedShelf
            kind={kind}
            artists={items}
            source={recSource(payload)}
            loaded={!q?.isPending}
            watchingIds={rec.watchingIds}
            images={rec.images}
            buildDetailPath={detailPath}
            onAddToWatchlist={(artistId, artistName, source) =>
              void rec.toggleWatchlist(artistId, artistName, source)
            }
            onViewAll={kind === 'recommended' ? () => setRecModalOpen(true) : undefined}
          />
        );
      }
      case 'recent-releases': {
        const items = page.sectionState(id).items as RecentAlbum[];
        return (
          <RecentReleasesShelf
            albums={items}
            loaded={!page.sections.recentReleases?.isPending}
            onOpenAlbum={(i) => void albumOpen.openRecentAlbum(items[i])}
          />
        );
      }
      case 'seasonal-albums-section': {
        const items = page.sectionState(id).items as SeasonalAlbum[];
        return (
          <SeasonalAlbumsShelf
            season={season}
            albums={items}
            loaded={!page.sections.seasonal?.isPending}
            onOpenAlbum={(i) => void albumOpen.openSeasonalAlbum(items[i])}
          />
        );
      }
      case 'cache-genre-releases':
      case 'cache-undiscovered':
      case 'cache-label-explorer':
      case 'cache-deep-cuts':
        return cacheShelf(id);
      case 'your-albums-section':
        return (
          <YourAlbumsShelf
            albums={albums.grid.albums}
            total={albums.grid.total}
            page={albums.grid.state.page}
            loaded={albums.grid.phase !== 'loading'}
            loading={albums.grid.phase === 'loading'}
            subtitle={albums.grid.subtitle}
            query={albums.grid.state.search}
            status={albums.grid.state.status}
            sort={albums.grid.state.sort}
            canDownloadMissing={albums.grid.canDownloadMissing}
            refreshing={albums.refresh.refreshing}
            onRefresh={() => void albums.refresh.start()}
            onConfigureSources={albums.sources.openModal}
            onDownloadMissing={() => void albums.batch.openForMissing()}
            onQueryChange={(search) => albums.grid.filter({ search })}
            onStatusChange={(status) => albums.grid.filter({ status: status as never })}
            onSortChange={(sort) => albums.grid.filter({ sort })}
            onPrevPage={() => albums.grid.page(albums.grid.state.page - 1)}
            onNextPage={() => albums.grid.page(albums.grid.state.page + 1)}
            onOpenAlbum={(i) => void albumOpen.openYourAlbum(albums.grid.albums[i] as YourAlbum, i)}
          />
        );
      case 'your-artists-section':
        return (
          <YourArtistsShelf
            artists={page.sectionState(id).items as never[]}
            loaded={!page.sections.yourArtists?.isPending}
            subtitle={savedArtistSourcesSubtitle(
              artists.sources.savedEnabled ?? ARTISTS_DEFAULT_SOURCES,
            )}
            logos={LOGOS}
            refreshing={artists.refresh.refreshing}
            buildDetailPath={detailPath}
            onRefresh={() => void artists.refresh.start()}
            onConfigureSources={artists.sources.openModal}
            onViewAll={artists.browse.openModal}
            onOpenInfo={(a) => artists.info.open(a as never)}
            onToggleWatchlist={(a) => void artists.info.toggleWatch(a as never)}
          />
        );
      case 'discover-bylt-sections':
        return (
          <ByltSections
            sections={byltRows}
            statusNote={byltNote}
            historyNote={byltData?.history_note ?? null}
            onPlayTrack={playByltTrack}
            onDownloadTrack={downloadByltTrack}
            onOpenAlbum={openByltAlbum}
            onPlayShelf={playByltShelf}
            onDownloadShelf={downloadByltShelf}
            pendingKey={byltPending}
            errors={byltErrors}
          />
        );
      case 'lastfm-radio':
        return (
          <LastfmRadioSection
            query={lastfm.query}
            results={lastfm.results}
            dropdownOpen={lastfm.dropdownOpen}
            searching={lastfm.searching}
            mixes={lastfmMixesHydrated}
            loaded={lastfm.loaded}
            generating={lastfm.generating}
            onQueryChange={lastfm.setQuery}
            onPick={(t) => void lastfm.pick(t)}
            onClear={lastfm.clear}
            onDismiss={lastfm.dismiss}
            onOpenMix={modal.open}
            onPlayMix={playMixFromCard}
            playingKey={playingMixKey}
          />
        );
      case 'listenbrainz':
        return (
          <ListenBrainzSection
            username={lb.username}
            activeTab={lb.activeTab}
            hasData={lb.hasData}
            mixes={lbMixesHydrated}
            loading={!lb.loaded && !lb.error}
            error={lb.error}
            loaded={lb.loaded}
            groups={lb.groups ?? undefined}
            activeGroup={lb.activeGroup}
            onSelectTab={lb.selectTab}
            onSelectGroup={lb.selectGroup}
            onRefresh={() => void lb.refresh()}
            onConnect={() => window.openMyAccountsModal?.()}
            onOpenMix={modal.open}
            onPlayMix={playMixFromCard}
            playingKey={playingMixKey}
          />
        );
      case 'deezer-editorial':
        return (
          <DeezerEditorialShelf onToast={(m) => toast(m, 'error')} onPreview={previewDeezer} />
        );
      case 'build-a-playlist':
        return (
          <BuildPlaylistSection
            query={bp.query}
            results={bp.results}
            resultsMessage={bp.resultsMessage}
            searching={bp.searching}
            selected={bp.selected}
            generating={bp.generating}
            resultSubtitle={bp.resultSubtitle}
            hasResults={bp.tracks !== null}
            syncing={sync.syncingKeys.includes('build-playlist')}
            syncProgress={toRawProgress(sync.progressFor('build-playlist'))}
            metadata={
              bp.metadata ? (
                <div className="build-playlist-metadata">
                  {bpMetaStats(bp.metadata).map((stat) => (
                    <div className="bp-meta-stat" key={stat.label}>
                      <span className="bp-meta-value">{stat.value}</span>
                      <span className="bp-meta-label">{stat.label}</span>
                    </div>
                  ))}
                </div>
              ) : undefined
            }
            onQueryChange={bp.setQuery}
            onAdd={bp.addSeed}
            onRemove={bp.removeSeed}
            onGenerate={() => void bp.generate()}
            onDownload={() => {
              const d = bp.download();
              if (d.kind === 'ok') openTracksModal(d.virtualId, d.name, d.tracks);
              else toast(d.toast, d.level);
            }}
            onSync={() => {
              const out = sync.startMixSync(
                { key: 'build_playlist_custom', title: 'Custom Playlist' },
                bp.tracks ?? undefined,
              );
              if (out) toast(out.message, out.level);
            }}
            infoOpen={bp.infoOpen}
            onToggleInfo={bp.toggleInfo}
            loaded={true}
          />
        );
      default:
        return null;
    }
  };

  const renderZoneSections = (ids: DiscoverSectionId[]) =>
    ids.filter(hasContent).map((id) => (
      <div className={`discovery-zone-section discovery-zone-section--${id}`} key={id}>
        {renderSection(id)}
      </div>
    ));

  /** Enabled sections for a zone from the profile's layout. */
  const zoneSections = (zone: DiscoverZoneId): DiscoverSectionId[] =>
    pageLayout.sectionsByZone[zone].filter((id) => hasContent(id));

  /** The For You zone keeps StationsRow pinned right after Your Mixes. */
  const renderForYouSections = () => {
    const ids = zoneSections('for-you');
    const anchor = ids.indexOf('your-mixes-section');
    const before = anchor < 0 ? [] : ids.slice(0, anchor + 1);
    const after = anchor < 0 ? ids : ids.slice(anchor + 1);
    return (
      <>
        {renderZoneSections(before)}
        <StationsRow
          stations={stationsQuery.data ?? null}
          loading={stationsQuery.isPending}
          error={stationsQuery.isError ? 'Could not load your stations.' : null}
          onRetry={() => void stationsQuery.refetch()}
          onView={stationPreview.open}
          onPlayRadio={playStationRadio}
          pendingId={stationPreview.pendingId}
          cardErrors={stationPreview.cardErrors}
          onPlayLibraryRadio={() => {
            if (window.startLibraryRadio) void window.startLibraryRadio();
            else toast('The player is not ready yet. Try again in a moment.', 'error');
          }}
        />
        {renderZoneSections(after)}
      </>
    );
  };

  const recentForSpotlight = page.sectionState('recent-releases').items as RecentAlbum[];
  const spotlight = pickSpotlight(recentForSpotlight, weekQuery.data?.top_artists ?? []);
  const releasePromo = usePromoVideo(
    'promo-release',
    spotlight?.album.artist_name,
    spotlight?.album.album_name,
    spotlight?.album.album_cover_url,
    videosOn,
  );
  // an artist you should know: the first recommendation with a photo that
  // isn't already rotating through the hero
  const heroNames = new Set(hero.artists.map((a) => a.artist_name.toLowerCase()));
  const artistPick =
    recArtists.find(
      (a) => a.image_url && a.artist_name && !heroNames.has(a.artist_name.toLowerCase()),
    ) ?? null;
  const artistPromo = usePromoVideo(
    'promo-artist',
    artistPick?.artist_name,
    null,
    artistPick?.image_url,
    videosOn,
  );
  // a throwback: the top of repeat rewind, a song you had on repeat
  const rewindMix = mixes.mixes.find((m) => m.key === 'repeat_rewind') ?? null;
  const throwbackRow = (rewindMix?.tracks?.[0] ?? null) as Record<string, unknown> | null;
  const throwback = throwbackRow ? normalizeTrack(throwbackRow as never) : null;
  const throwbackPromo = usePromoVideo(
    'promo-throwback',
    throwback?.artist,
    throwback?.name,
    throwback?.cover,
    videosOn,
  );
  // the poster row: a show coming up (the inbox's concerts, shared with the
  // inbox list's own query), a second release, and your week's number one
  const inboxQuery = useQuery({
    queryKey: ['discover', 'inbox', 'new'] as const,
    queryFn: () => fetchInbox('new'),
    retry: false,
  });
  const concert = pickConcert(inboxQuery.data?.items);
  const concertDay = concert ? posterDay(concert.item.item_date) : null;
  const photoByName = new Map<string, string>();
  for (const a of [
    ...(weekQuery.data?.top_artists ?? []).map((t) => ({ n: t.name, i: t.image_url })),
    ...hero.artists.map((h) => ({ n: h.artist_name, i: h.image_url })),
    ...recArtists.map((r) => ({ n: r.artist_name, i: r.image_url })),
  ]) {
    if (a.n && a.i && !photoByName.has(a.n.toLowerCase())) photoByName.set(a.n.toLowerCase(), a.i);
  }
  const posterAlbum = pickPosterAlbum(recentForSpotlight, spotlight?.index ?? null);
  const topArtist = week?.topArtist ?? null;
  const [bentoRef, bentoIn] = useReveal<HTMLElement>();
  const videoToggle = { on: videosOn, onToggle: () => setVideosOn(!videosOn) };
  // the watch rail: hero picks and recommendations with a photo, one each,
  // never the artist already in the bento
  const railArtists: RailArtist[] = [];
  {
    const seenNames = new Set<string>(
      artistPick?.artist_name ? [artistPick.artist_name.toLowerCase()] : [],
    );
    for (const a of [...hero.artists, ...recArtists] as {
      artist_id?: string | number | null;
      artist_name?: string;
      image_url?: string | null;
      source?: string | null;
      explanation?: unknown;
    }[]) {
      const name = a.artist_name ?? '';
      if (!name || !a.image_url || seenNames.has(name.toLowerCase())) continue;
      seenNames.add(name.toLowerCase());
      railArtists.push({
        key: `${a.artist_id ?? name}`,
        name,
        image: a.image_url,
        reason: explanationLine(a.explanation as never),
        href:
          a.artist_id != null
            ? detailPath(a.artist_id, a.source ?? recSource(recPayload) ?? null, name)
            : '#',
      });
      if (railArtists.length >= 10) break;
    }
  }
  const playTopTracks = () => {
    if (!week || playingTop) return;
    const intent = beginPlayIntent();
    setPlayingTop(true);
    const rows = week.topTracks.map((t) => ({ title: t.name, artist: t.artist, album: t.album }));
    void playMixNow(rows, 'Your top tracks this week', intent).finally(() => setPlayingTop(false));
  };

  /** flow: a fresh queue from the server every press, played straight away. */
  const playFlow = () => {
    if (flowBusy) return;
    const intent = beginPlayIntent();
    setFlowBusy(true);
    void fetchFlow()
      .then((res) => {
        const tracks = res.tracks ?? [];
        if (!intent.isCurrent()) return;
        if (!tracks.length) {
          toast('Flow needs some listening history first', 'info');
          return;
        }
        return playMixNow(tracks, 'Flow', intent);
      })
      .catch(() => toast("Couldn't start Flow. Try again.", 'error'))
      .finally(() => setFlowBusy(false));
  };
  const tiles = quickTiles(mixes.mixes, mixes.moodMixes, new Date().getHours());
  const nowPlaying = useNowPlaying();
  const shellProfile = getShellProfileContext();

  const newMissingIds = zoneSections('new-missing');
  const libraryIds = zoneSections('library');
  const navItems: DiscoverNavItem[] = [
    { id: 'discover-zone-for-you', label: 'For You' },
    { id: 'discover-zone-new-missing', label: 'New & Missing' },
    ...(libraryIds.length ? [{ id: 'discover-zone-library', label: 'From Your Library' }] : []),
    { id: 'discover-zone-tools', label: 'Explore & Build' },
  ];

  const handleSaveLayout = (sections: Parameters<typeof pageLayout.save>[0]) => {
    setLayoutSaving(true);
    void pageLayout.save(sections).then(
      () => {
        setLayoutSaving(false);
        setLayoutOpen(false);
        toast('Layout saved', 'success');
      },
      (err: unknown) => {
        setLayoutSaving(false);
        toast(err instanceof Error ? err.message : 'Could not save layout', 'error');
      },
    );
  };

  const vizOpen = map.kind !== null || webRequest !== null;

  return (
    // NOT the vanilla page class and NOT its page id: .page is display:none
    // until the shell adds .active, which it only does for the react HOST and
    // for legacy pages — the label-detail flip hid its entire page this way.
    // The host (.page.active) already provides the page padding, and nothing
    // in CSS or shared JS targets #discover-page, so the root carries neither.
    <div>
      {/* The overlays render INSTEAD of the container — the vanilla hid every sibling. */}
      <ArtistMapAssembly
        map={map}
        onOpenInfo={(pool) => artists.info.open(pool as never)}
        buildDetailPath={detailPath}
        onToast={(t) => toast(t.message, t.level)}
      />
      <ArtistWebAssembly
        request={webRequest}
        onClose={() => setWebRequest(null)}
        onExploreInMap={(name) => void map.openExplorer(name)}
        buildDetailPath={(id, source) => detailPath(id, source)}
        onToast={(t) => toast(t.message, t.level)}
      />

      {!vizOpen && (
        <div
          className="discover-container"
          style={
            heroGlow ? ({ '--discover-glow-rgb': heroGlow } as React.CSSProperties) : undefined
          }
        >
          {(playingMixKey !== null || playingTrackIndex !== null) && (
            <div className="discover-playback-pending" role="status">
              <span>
                Preparing {playingMixKey ? (registry[playingMixKey]?.title ?? 'mix') : 'track'}…
                Checking your library and preparing audio.
              </span>
              <button
                type="button"
                onClick={() => {
                  beginPlayIntent();
                  pendingPlay.current = null;
                  setPlayingMixKey(null);
                  setPlayingTrackIndex(null);
                }}
              >
                Cancel playback
              </button>
            </div>
          )}
          <GreetingGrid
            name={shellProfile?.name}
            hour={new Date().getHours()}
            tiles={tiles}
            onOpenMix={modal.open}
            onPlayMix={playMixFromCard}
            onPlayFlow={playFlow}
            flowBusy={flowBusy}
            playingKey={playingMixKey}
          />
          <NowPlayingBanner
            state={nowPlaying}
            artistHref={
              nowPlaying.track?.artist_id != null
                ? detailPath(nowPlaying.track.artist_id, nowPlaying.track.artist_source ?? null)
                : null
            }
            onMoreLikeThis={(track) => {
              if (!window.startArtistRadioById || track.artist_id == null) return;
              void Promise.resolve(
                window.startArtistRadioById(String(track.artist_id), track.artist ?? ''),
              ).then((started) => {
                if (started === false)
                  toast(`Could not start ${track.artist ?? 'that'} radio`, 'error');
              });
            }}
          />
          <div className="discover-command-hero">
            <DiscoverHero
              artist={hero.artist}
              loading={page.hero.isPending}
              count={hero.artists.length}
              index={hero.index}
              watchlist={hero.watchlist}
              watchAllPhase={hero.watchAllPhase}
              discographyHref={
                hero.artist?.artist_id != null
                  ? detailPath(hero.artist.artist_id, hero.artist.source ?? null)
                  : '#'
              }
              onNavigate={hero.navigate}
              onJump={hero.jump}
              onToggleWatchlist={() => void hero.toggleWatchlist()}
              onWatchAll={() => void hero.watchAll()}
              onViewRecommended={() => setRecModalOpen(true)}
              onOpenBlacklist={blacklist.openModal}
              artists={hero.artists}
              onPauseChange={hero.setPaused}
              glowRgb={heroGlow}
            />
          </div>
          {week || gap ? (
            <div className={`dsc-pulse-row${week && gap ? '' : ' dsc-pulse-row--single'}`}>
              {week ? (
                <WeekBanner week={week} onPlayTop={playTopTracks} playing={playingTop} />
              ) : null}
              {gap ? (
                <TasteGapBanner gap={gap} onExplore={(g) => openDive(g.toLowerCase())} />
              ) : null}
            </div>
          ) : null}
          <DiscoverNav items={navItems} onOpenLayout={() => setLayoutOpen(true)} />
          <DiscoveryZone
            id="discover-zone-for-you"
            title="For You"
            subtitle="Mixes, stations and artists picked from what you play."
            tone="for-you"
          >
            {renderForYouSections()}
          </DiscoveryZone>

          {spotlight || artistPick || (throwback && throwbackRow) ? (
            <section
              ref={bentoRef}
              className={`dsc-bento dsc-reveal${bentoIn ? ' is-in' : ''}`}
              aria-label="Spotlight"
            >
              {spotlight ? (
                <PromoBanner
                  kind="release"
                  size="feature"
                  onHoverChange={releasePromo.setHover}
                  soundOn={releasePromo.soundOn}
                  onSoundChange={releasePromo.setSound}
                  eyebrow={`${releaseKind(spotlight.album.album_type)}${shortDate(spotlight.album.release_date) ? ` · ${shortDate(spotlight.album.release_date)}` : ''}`}
                  title={spotlight.album.album_name ?? ''}
                  subtitle={
                    <>
                      <strong>{spotlight.album.artist_name}</strong> · {spotlight.reason}
                    </>
                  }
                  art={spotlight.album.album_cover_url ?? null}
                  actions={
                    <button
                      type="button"
                      className="dsc-pulse-btn primary"
                      onClick={() =>
                        void albumOpen.openRecentAlbum(recentForSpotlight[spotlight.index])
                      }
                    >
                      Open {releaseKind(spotlight.album.album_type).replace('New ', '')}
                    </button>
                  }
                  glowRgb={releasePromo.glowRgb}
                  rootRef={releasePromo.ref}
                  videoId={releasePromo.videoId}
                  playing={releasePromo.playing}
                  onUnplayable={releasePromo.onUnplayable}
                  videoToggle={videoToggle}
                />
              ) : null}
              {artistPick ? (
                <PromoBanner
                  kind="artist"
                  size="tile"
                  onHoverChange={artistPromo.setHover}
                  soundOn={artistPromo.soundOn}
                  onSoundChange={artistPromo.setSound}
                  eyebrow="An artist you should know"
                  title={artistPick.artist_name ?? ''}
                  subtitle={
                    explanationLine(artistPick.explanation as never) || 'Picked from your library'
                  }
                  art={artistPick.image_url ?? null}
                  round
                  actions={
                    <>
                      <a
                        className="dsc-pulse-btn primary"
                        href={detailPath(
                          artistPick.artist_id ?? '',
                          recSource(recPayload) || null,
                          artistPick.artist_name,
                        )}
                      >
                        View artist
                      </a>
                      {artistPick.artist_id ? (
                        <button
                          type="button"
                          className="dsc-pulse-btn"
                          disabled={rec.watchingIds.has(String(artistPick.artist_id))}
                          onClick={() =>
                            void rec.toggleWatchlist(
                              String(artistPick.artist_id),
                              artistPick.artist_name ?? '',
                            )
                          }
                        >
                          {rec.watchingIds.has(String(artistPick.artist_id))
                            ? 'On your watchlist'
                            : 'Add to watchlist'}
                        </button>
                      ) : null}
                    </>
                  }
                  glowRgb={artistPromo.glowRgb}
                  rootRef={artistPromo.ref}
                  videoId={artistPromo.videoId}
                  playing={artistPromo.playing}
                  onUnplayable={artistPromo.onUnplayable}
                  videoToggle={videoToggle}
                />
              ) : null}
              {throwback && throwbackRow ? (
                <PromoBanner
                  kind="throwback"
                  size="tile"
                  onHoverChange={throwbackPromo.setHover}
                  soundOn={throwbackPromo.soundOn}
                  onSoundChange={throwbackPromo.setSound}
                  eyebrow="Throwback"
                  title={throwback.name}
                  subtitle={
                    <>
                      <strong>{throwback.artist}</strong> · you had this on repeat, then it went
                      quiet
                    </>
                  }
                  art={throwback.cover || null}
                  actions={
                    <>
                      <button
                        type="button"
                        className="dsc-pulse-btn primary"
                        onClick={() => {
                          const intent = beginPlayIntent();
                          void playTrackNow(throwbackRow, throwback.name, intent);
                        }}
                      >
                        Play it
                      </button>
                      <button
                        type="button"
                        className="dsc-pulse-btn"
                        onClick={() => modal.open('repeat_rewind')}
                      >
                        Repeat Rewind
                      </button>
                    </>
                  }
                  glowRgb={throwbackPromo.glowRgb}
                  rootRef={throwbackPromo.ref}
                  videoId={throwbackPromo.videoId}
                  playing={throwbackPromo.playing}
                  onUnplayable={throwbackPromo.onUnplayable}
                  videoToggle={videoToggle}
                />
              ) : null}
            </section>
          ) : null}

          <DiscoveryZone
            id="discover-zone-new-missing"
            title="New & Missing"
            subtitle="Fresh releases, and the albums your collection is missing."
            tone="new-missing"
          >
            {/* the inbox lives here now: releases and dates worth coming back
                to, which is what this zone is. it used to sit between the hero
                and everything else, pushing the feed a screen down. */}
            <div className="discovery-zone-section discovery-zone-section--inbox">
              <DiscoveryInbox
                onOpenRelease={(album) => void albumOpen.openRecentAlbum(album)}
                buildArtistPath={(item) => {
                  const ref = inboxArtistRef(item);
                  return ref ? detailPath(ref.id, ref.source, item.artist_name) : '';
                }}
              />
            </div>
            {renderZoneSections(newMissingIds)}
          </DiscoveryZone>

          <VideoRail
            title="Watch"
            subtitle="Artists picked for you, every one playing its video."
            artists={railArtists}
            videosOn={videosOn}
          />

          {libraryIds.length > 0 && (
            <DiscoveryZone
              id="discover-zone-library"
              title="From Your Library"
              subtitle="Your artists, your eras, your deep cuts."
              tone="library"
            >
              {renderZoneSections(libraryIds)}
            </DiscoveryZone>
          )}

          <PosterRow>
            {[
              concert && concertDay ? (
                <ConcertPoster
                  key="concert"
                  artist={concert.item.artist_name ?? ''}
                  day={concertDay}
                  venue={(concert.item.payload as { venue?: string } | undefined)?.venue}
                  city={(concert.item.payload as { city?: string } | undefined)?.city}
                  url={concert.item.payload?.url}
                  more={concert.more}
                  photo={photoByName.get((concert.item.artist_name ?? '').toLowerCase()) ?? null}
                />
              ) : null,
              posterAlbum ? (
                <AlbumPoster
                  key="album"
                  title={posterAlbum.album.album_name ?? ''}
                  artist={posterAlbum.album.artist_name ?? ''}
                  tag={releaseKind(posterAlbum.album.album_type as string | undefined)}
                  art={posterAlbum.album.album_cover_url ?? ''}
                  openLabel={`Open ${releaseKind(posterAlbum.album.album_type as string | undefined).replace('New ', '')}`}
                  onOpen={() => void albumOpen.openRecentAlbum(posterAlbum.album)}
                />
              ) : null,
              topArtist && (topArtist.play_count ?? 0) > 0 ? (
                <ArtistPoster
                  key="artist"
                  name={topArtist.name}
                  plays={topArtist.play_count ?? 0}
                  photo={topArtist.image_url ?? null}
                  href={
                    topArtist.id != null
                      ? detailPath(topArtist.id, 'library', topArtist.name)
                      : null
                  }
                />
              ) : null,
            ]}
          </PosterRow>

          <DiscoveryZone
            id="discover-zone-tools"
            title="Explore & Build"
            subtitle="Maps, genres, radio and playlist tools."
            tone="tools"
          >
            <div className="discovery-zone-section discovery-zone-section--map-tools">
              <div className="discover-hub-row discover-hub-row--tools">
                <ArtistMapHub
                  onOpenWatchlist={() => void map.openWatchlist()}
                  onOpenGenre={() => void map.openGenre()}
                  // Explorer asks for an artist FIRST (9633) — the prompt resolves
                  // a real name, then the map explores it.
                  onOpenExplorer={() => setExplorerPromptOpen(true)}
                />
                <ArtistWebHub onOpenLens={(lens) => setWebRequest({ lens })} />
              </div>
            </div>
            {renderZoneSections(zoneSections('tools'))}
          </DiscoveryZone>
        </div>
      )}

      <DownloadBar state={bar.state} onOpen={(id) => void bar.openBubble(id)} />

      {layoutOpen && (
        <DiscoverLayoutModal
          entries={pageLayout.entries}
          saving={layoutSaving}
          onSave={handleSaveLayout}
          onClose={() => setLayoutOpen(false)}
        />
      )}

      {recipeEditor && (
        <RecipeEditor
          editing={recipeEditor.editing}
          onClose={() => setRecipeEditor(null)}
          onSaved={(message) => {
            setRecipeEditor(null);
            toast(message, 'success');
            void queryClient.invalidateQueries({ queryKey: ['discover', 'recipes'] });
          }}
        />
      )}

      {stationPreview.station && (
        <StationModal
          snapshot={stationPreview.snapshot}
          stationName={stationPreview.station.name}
          loading={stationPreview.loading}
          error={stationPreview.error}
          selected={stationPreview.selected}
          syncing={
            stationPreview.snapshot
              ? sync.syncingKeys.includes(
                  stationSyncKey(stationPreview.snapshot).replace(/_/g, '-'),
                )
              : false
          }
          syncStatusBase={
            stationPreview.snapshot
              ? stationSyncKey(stationPreview.snapshot).replace(/_/g, '-')
              : undefined
          }
          syncProgress={
            stationPreview.snapshot
              ? toRawProgress(
                  sync.progressFor(stationSyncKey(stationPreview.snapshot).replace(/_/g, '-')),
                )
              : undefined
          }
          onClose={stationPreview.close}
          onRefresh={stationPreview.refresh}
          onToggleTrack={stationPreview.toggleTrack}
          onPlayTrack={playStationTrack}
          playingIndex={stationPlayingIndex}
          onSelectAll={stationPreview.selectAll}
          onClearSelection={stationPreview.clearSelection}
          onPlaySelected={playStationSelection}
          onDownloadSelected={downloadStationSelection}
          onSyncSelected={syncStationSelection}
        />
      )}

      {modal.mix && (
        <MixModal
          mix={modal.mix}
          tracks={modal.tracks}
          loading={modal.loading}
          error={modal.error}
          selected={modal.selected}
          syncStatusOverride={
            modal.mix.key.startsWith('lb-') ? (
              <LbSyncStatus identifier={modal.mix.key.split('-').slice(2).join('-')} />
            ) : undefined
          }
          syncing={Boolean(modal.mix.statusBase && sync.syncingKeys.includes(modal.mix.statusBase))}
          syncProgress={
            modal.mix.statusBase ? toRawProgress(sync.progressFor(modal.mix.statusBase)) : undefined
          }
          onClose={modal.close}
          onAction={runMixAction}
          onSelectAll={modal.selectAll}
          onClearSelection={modal.clearSelection}
          onToggleTrack={modal.toggleTrack}
          onPlayTrack={playTrackFromModal}
          playingIndex={playingTrackIndex}
          playing={playingMixKey === modal.mix.key}
          onDownloadSelected={downloadSelection}
        />
      )}

      {dive && (
        <GenreDiveModal
          genre={dive.genre}
          data={dive.data}
          phase={dive.phase}
          buildDetailPath={detailPath}
          onOpenGenre={openDive}
          onFollowArtist={() => {}}
          onOpenTrack={(i) => {
            const item = (dive.data?.tracks ?? [])[i] as CacheItem | undefined;
            setDive(null); // the vanilla removes the modal before opening (10486)
            void albumOpen.openCacheItem('genre_dive_tracks', item);
          }}
          onOpenAlbum={(i) => {
            const item = (dive.data?.albums ?? [])[i] as CacheItem | undefined;
            setDive(null);
            void albumOpen.openCacheItem('genre_dive_albums', item);
          }}
          onClose={() => setDive(null)}
        />
      )}

      {recModalOpen && (
        <RecommendedModal
          artists={recArtists}
          source={recSource(recPayload)}
          cachedSource={recPayload?.source ?? null}
          watchingIds={rec.watchingIds}
          images={rec.images}
          addingAll={addingAll}
          buildDetailPath={detailPath}
          onClose={() => setRecModalOpen(false)}
          onAddToWatchlist={(artistId, artistName) =>
            void rec.toggleWatchlist(artistId, artistName)
          }
          onAddAll={() => {
            setAddingAll(true);
            void (async () => {
              for (const a of recArtists) {
                if (a.artist_id && !rec.watchingIds.has(String(a.artist_id))) {
                  await rec.toggleWatchlist(String(a.artist_id), a.artist_name ?? '');
                }
              }
              setAddingAll(false);
            })();
          }}
        />
      )}

      {artists.sources.open && (
        <YourArtistsSourcesModal
          state={artists.sources.state}
          connected={artists.sources.connected}
          onToggle={artists.sources.toggle}
          onSave={() => void artists.sources.save()}
          onClose={artists.sources.closeModal}
        />
      )}
      {artists.browse.open && (
        <YourArtistsModal
          state={artists.browse.state}
          total={artists.browse.total}
          artists={artists.browse.artists}
          phase={artists.browse.phase}
          logos={LOGOS}
          buildDetailPath={detailPath}
          onFilter={artists.browse.filter}
          onPage={artists.browse.page}
          onClose={artists.browse.closeModal}
          onOpenInfo={(a) => artists.info.open(a as never)}
          onToggleWatchlist={(a) => void artists.info.toggleWatch(a as never)}
        />
      )}
      {artists.info.pool && (
        <ArtistInfoModal
          pool={artists.info.pool}
          info={artists.info.data}
          phase={artists.info.phase}
          logos={LOGOS}
          buildDetailPath={detailPath}
          onClose={artists.info.close}
          onToggleWatchlist={() => void artists.info.toggleWatch(artists.info.pool!)}
          onExplore={() => {
            const name = artists.info.pool?.artist_name;
            artists.info.close();
            if (name) {
              setWebRequest(null);
              void map.openExplorer(name);
            }
          }}
          onOpenRelated={(related) =>
            artists.info.open({
              artist_name: related.name,
              image_url: related.image_url ?? '',
            } as never)
          }
          onViewDiscography={artists.info.close}
        />
      )}

      {albums.sources.open && (
        <YourAlbumsSourcesModal
          state={albums.sources.state}
          connected={albums.sources.connected}
          onToggle={albums.sources.toggle}
          onSave={() => void albums.sources.save()}
          onClose={albums.sources.closeModal}
        />
      )}
      {albums.batch.open && (
        <YourAlbumsBatchModal
          rows={albums.batch.rows}
          selected={albums.batch.selected}
          phase={albums.batch.phase}
          progress={albums.batch.progress}
          onToggleRow={albums.batch.toggleRow}
          onSelectAll={albums.batch.selectAll}
          onSubmit={() => void albums.batch.submit()}
          onClose={albums.batch.close}
        />
      )}

      {explorerPromptOpen && (
        <ArtMapExplorePrompt
          onPick={(name) => {
            setExplorerPromptOpen(false);
            void map.openExplorer(name);
          }}
          onClose={() => setExplorerPromptOpen(false)}
        />
      )}

      {blacklist.open && (
        <BlacklistModal
          query={blacklist.query}
          results={blacklist.results}
          entries={blacklist.entries}
          listPhase={blacklist.listPhase}
          onQueryChange={blacklist.setQuery}
          onBlock={(name) => void blacklist.block(name)}
          onUnblock={(entry) => void blacklist.unblock(entry)}
          onClose={blacklist.closeModal}
        />
      )}
    </div>
  );
}
