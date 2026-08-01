import { useCallback, useEffect, useRef, useState } from 'react';

import type { ArtMapNode } from './-discover.artist-map';
import type { ArtMapGenrePayload, ArtMapPoolEntry } from './-discover.artist-map.entry';
import type { ArtMapInteractionHost } from './-discover.artist-map.interaction';
import type { ArtMapKind, ArtMapSidebarGenre } from './-ui/artist-map-overlay';

import {
  artMap,
  artMapGroupByGenre,
  artMapLayoutIslands,
  artMapRemapEdges,
  artMapZoomTarget,
  artMapFitToViewTarget,
} from './-discover.artist-map';
import {
  ARTMAP_EXPLORE_FAIL_MS,
  ARTMAP_EMPTY_WATCHLIST,
  ARTMAP_GENRE_LIST_URL,
  ARTMAP_GENRES_URL,
  ARTMAP_LOADING,
  ARTMAP_TITLES,
  ARTMAP_URL,
  ARTMAP_EXPLORE_URL,
  artMapExploreEmptyMessage,
  artMapExploreNodes,
  artMapExploreStats,
  artMapGenreGroups,
  artMapGenreStats,
  artMapInfoBest,
  artMapPayloadIsEmpty,
  artMapRelatedNodes,
  artMapUsesOneIsland,
  artMapWatchlistNodes,
  artMapWatchlistStats,
} from './-discover.artist-map.entry';
import {
  artMapAnimateConstellation,
  artMapAnimateTo,
  artMapEmitRipple,
  artMapEnsureAmbient,
  artMapFocusIsland,
  artMapIslandNavStep,
  artMapRender,
  artMapStreamImages,
} from './-discover.artist-map.render';

/**
 * The Artist Map orchestrator — the three entry points, close, the genre
 * sidebar, and the interaction host.
 *
 * Transcribed from `openArtistMap` (8273-8422), `_openGenreMapWithSelection`
 * (9493-9624) and `_openArtistMapExplorerWithName` (9640-9760). The three
 * flows share a skeleton the hook owns ONCE: reset the world, show the
 * loading copy, fetch the payload, build islands, set title/stats, focus,
 * stream images. What each varies — payload URL, node extraction, stats
 * line, one-island mode — comes from the entry module per kind.
 *
 * What the hook does NOT do: canvas attach/measure/DPR and the section
 * hide/show. The overlay owns the canvas through the lifecycle module, and
 * "hide every sibling section" is simply React rendering the overlay instead
 * of the sections — the vanilla's `_prevDisplay` bookkeeping (8278-8282) has
 * no equivalent left to need one. The vanilla's `_skipSectionToggle` dance
 * for in-map re-opens (9630) also dissolves: re-opening just replaces state.
 *
 * A LOAD TOKEN guards every async landing (the vanilla gets this by replacing
 * the whole world): a payload resolving after close, or after a newer open,
 * is dropped.
 */

export type ArtMapToast = { message: string; level: 'error' };

export interface ArtistMapController {
  /** null = closed; the page renders sections instead of the overlay. */
  kind: ArtMapKind | null;
  title: string;
  stats: string;
  loading: string | null;
  sidebarGenres: ArtMapSidebarGenre[];
  selectedGenre: string;
  /** Bumped whenever focus/panel-relevant state changed under the hook. */
  focusVersion: number;
  openWatchlist: () => Promise<void>;
  openGenre: (selectedGenre?: string) => Promise<void>;
  openExplorer: (name: string) => Promise<void>;
  switchGenre: (genre: string) => Promise<void>;
  close: () => void;
  islandNav: (dir: number) => void;
  focusIsland: (index: number) => void;
  zoom: (factor: number) => void;
  fitToView: () => void;
  /** The map node → info-modal pool adapter (openYourArtistInfoModal_direct). */
  poolFor: (node: ArtMapNode) => ArtMapPoolEntry;
  /** The host the overlay's interaction layer drives. */
  makeHost: (ui: {
    showTooltip: ArtMapInteractionHost['showTooltip'];
    showPanelArtist: ArtMapInteractionHost['showPanelArtist'];
    showContextMenu: ArtMapInteractionHost['showContextMenu'];
    hideContextMenu: ArtMapInteractionHost['hideContextMenu'];
    focusSearch: () => boolean;
    toggleSimilar: () => void;
    resized: () => void;
  }) => ArtMapInteractionHost;
}

export function useArtistMap(onToast: (toast: ArtMapToast) => void): ArtistMapController {
  const toastRef = useRef(onToast);
  toastRef.current = onToast;

  const [kind, setKind] = useState<ArtMapKind | null>(null);
  const [title, setTitle] = useState('');
  const [stats, setStats] = useState('');
  const [loading, setLoading] = useState<string | null>(null);
  const [sidebarGenres, setSidebarGenres] = useState<ArtMapSidebarGenre[]>([]);
  const [selectedGenre, setSelectedGenre] = useState('');
  const [focusVersion, setFocusVersion] = useState(0);
  const token = useRef(0);
  const genrePayload = useRef<ArtMapGenrePayload | null>(null);
  const failTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (failTimer.current) clearTimeout(failTimer.current);
    },
    [],
  );

  const bumpFocus = useCallback(() => setFocusVersion((v) => v + 1), []);

  /** The shared skeleton's tail: reveal, focus, stream (8410-8420). */
  const finishBuild = useCallback(
    (focusIndex: number) => {
      // Faithful to 8410 — though behaviourally shadowed: artMapFocusIsland
      // immediately re-decides every opacity per island, in the vanilla too.
      // (A mutant deleting this line is EQUIVALENT on every current path.)
      for (const n of artMap.placed) n.opacity = 1;
      artMap.dirty = true;
      setLoading(null);
      artMapFocusIsland(focusIndex, { bloom: true });
      bumpFocus();
      artMapStreamImages(artMap.placed);
    },
    [bumpFocus],
  );

  const resetWorld = useCallback(() => {
    artMap.placed = [];
    artMap.edges = [];
    artMap.images = {};
    artMap._nodeById = null;
    artMap._islands = [];
    artMap._focusIdx = 0;
    artMap.dirty = true;
  }, []);

  const close = useCallback(() => {
    token.current += 1;
    if (failTimer.current) clearTimeout(failTimer.current);
    setKind(null);
    setLoading(null);
    setSidebarGenres([]);
    setSelectedGenre('');
    resetWorld();
  }, [resetWorld]);

  const openWatchlist = useCallback(async () => {
    token.current += 1;
    const t = token.current;
    setKind('watchlist');
    setTitle(ARTMAP_TITLES.watchlist);
    setStats('');
    setLoading(ARTMAP_LOADING);
    resetWorld();
    try {
      const data = (await (await fetch(ARTMAP_URL)).json()) as Parameters<
        typeof artMapWatchlistNodes
      >[0];
      if (token.current !== t) return;
      if (artMapPayloadIsEmpty(data)) {
        // The vanilla paints this line onto the canvas (8313); the loading
        // overlay is the port's one place for copy.
        setLoading(ARTMAP_EMPTY_WATCHLIST);
        return;
      }
      setStats(artMapWatchlistStats(data));
      const groups = artMapGroupByGenre(artMapWatchlistNodes(data));
      artMapLayoutIslands(groups);
      artMap.edges = artMapRemapEdges(data.edges as never);
      artMap._oneIsland = artMapUsesOneIsland('watchlist');
      finishBuild(0);
    } catch {
      if (token.current === t) setLoading('Failed to load artist map');
    }
  }, [resetWorld, finishBuild]);

  const buildGenreWorld = useCallback(
    (payload: ArtMapGenrePayload, selected: string) => {
      setSelectedGenre(selected);
      const groups = artMapGenreGroups(payload.genres ?? [], payload.nodes ?? {});
      artMapLayoutIslands(groups);
      artMap.edges = [];
      artMap._oneIsland = artMapUsesOneIsland('genre');
      const gs = artMapGenreStats(payload.genres || []);
      setStats(`${gs.count} genres · ${gs.artists} artists`);
      const idx = Math.max(
        0,
        (artMap._islands || []).findIndex((i) => i.name.toLowerCase() === selected.toLowerCase()),
      );
      finishBuild(idx);
    },
    [finishBuild],
  );

  const openGenre = useCallback(
    async (selected = '') => {
      token.current += 1;
      const t = token.current;
      setKind('genre');
      setTitle(ARTMAP_TITLES.genre);
      setStats('');
      setLoading('Loading genre map...');
      resetWorld();
      try {
        // The sidebar's genre list rides along (9457, cached like
        // window._artMapGenreList); mobile hides it in the overlay.
        const [listData, payload] = await Promise.all([
          (await fetch(ARTMAP_GENRE_LIST_URL)).json() as Promise<{
            genres?: { name: string; count: number }[];
          }>,
          genrePayload.current
            ? Promise.resolve(genrePayload.current)
            : ((await fetch(ARTMAP_GENRES_URL)).json() as Promise<ArtMapGenrePayload>),
        ]);
        if (token.current !== t) return;
        genrePayload.current = payload;
        setSidebarGenres(listData.genres ?? []);
        buildGenreWorld(payload, selected);
      } catch {
        if (token.current === t) setLoading('Failed to load genre map');
      }
    },
    [resetWorld, buildGenreWorld],
  );

  const switchGenre = useCallback(
    async (genre: string) => {
      // The cached payload makes a switch a pure rebuild (9560) — no refetch.
      if (genrePayload.current) {
        token.current += 1;
        resetWorld();
        buildGenreWorld(genrePayload.current, genre);
        return;
      }
      await openGenre(genre);
    },
    [resetWorld, buildGenreWorld, openGenre],
  );

  const openExplorer = useCallback(
    async (name: string) => {
      if (!name) return;
      token.current += 1;
      const t = token.current;
      setKind('explorer');
      setTitle(ARTMAP_TITLES.explorer);
      setStats('');
      setLoading(`Exploring ${name}...`);
      resetWorld();
      try {
        const res = await fetch(`${ARTMAP_EXPLORE_URL}?name=${encodeURIComponent(name.trim())}`);
        const data = (await res.json()) as Parameters<typeof artMapExploreNodes>[0];
        if (token.current !== t) return;
        if (!data.success || !(data.nodes ?? []).length) {
          // The failure copy shows, then the map closes itself (9692-9697).
          setLoading(artMapExploreEmptyMessage(res.status, name));
          if (failTimer.current) clearTimeout(failTimer.current);
          failTimer.current = setTimeout(() => {
            if (token.current === t) close();
          }, ARTMAP_EXPLORE_FAIL_MS);
          return;
        }
        setStats(artMapExploreStats(data));
        const groups = artMapGroupByGenre(artMapExploreNodes(data));
        artMapLayoutIslands(groups);
        artMap.edges = artMapRemapEdges(data.edges as never);
        artMap._oneIsland = artMapUsesOneIsland('explorer');
        finishBuild(0);
      } catch {
        if (token.current === t) setLoading('Failed to load artist map');
      }
    },
    [resetWorld, finishBuild, close],
  );

  const islandNav = useCallback(
    (dir: number) => {
      if (artMapIslandNavStep(dir)) bumpFocus();
    },
    [bumpFocus],
  );

  const focusIsland = useCallback(
    (index: number) => {
      if (artMapFocusIsland(index, { bloom: true })) bumpFocus();
    },
    [bumpFocus],
  );

  const zoom = useCallback((factor: number) => {
    const target = artMapZoomTarget(factor);
    artMapAnimateTo(target.zoom, target.offsetX, target.offsetY);
  }, []);

  const fitToView = useCallback(() => {
    const target = artMapFitToViewTarget();
    if (target) artMapAnimateTo(target.zoom, target.offsetX, target.offsetY);
  }, []);

  const poolFor = useCallback((node: ArtMapNode): ArtMapPoolEntry => {
    const best = artMapInfoBest(node, 'spotify');
    return {
      id: node.id,
      artist_name: node.name,
      active_source_id: best.id,
      active_source: best.source,
      image_url: node.image_url || '',
      spotify_artist_id: (node as unknown as Record<string, unknown>).spotify_id as string,
      itunes_artist_id: (node as unknown as Record<string, unknown>).itunes_id as string,
      deezer_artist_id: (node as unknown as Record<string, unknown>).deezer_id as string,
      discogs_artist_id: (node as unknown as Record<string, unknown>).discogs_id as string,
      source_services: [],
      on_watchlist: node.type === 'watchlist' ? 1 : 0,
      _related: artMapRelatedNodes(
        node,
        artMap.edges,
        (artMap._nodeById ?? {}) as Record<string, ArtMapNode>,
      ),
    } as ArtMapPoolEntry;
  }, []);

  const kindRef = useRef(kind);
  kindRef.current = kind;

  const makeHost = useCallback(
    (ui: Parameters<ArtistMapController['makeHost']>[0]): ArtMapInteractionHost => ({
      isVisible: () => kindRef.current !== null,
      render: artMapRender,
      ensureAmbient: artMapEnsureAmbient,
      emitRipple: artMapEmitRipple,
      showTooltip: ui.showTooltip,
      showPanelArtist: ui.showPanelArtist,
      animateConstellation: artMapAnimateConstellation,
      showContextMenu: ui.showContextMenu,
      hideContextMenu: ui.hideContextMenu,
      close,
      zoom,
      fitToView,
      focusSearch: ui.focusSearch,
      toggleSimilar: ui.toggleSimilar,
      islandNav,
      resized: ui.resized,
    }),
    [close, zoom, fitToView, islandNav],
  );

  return {
    kind,
    title,
    stats,
    loading,
    sidebarGenres,
    selectedGenre,
    focusVersion,
    openWatchlist,
    openGenre,
    openExplorer,
    switchGenre,
    close,
    islandNav,
    focusIsland,
    zoom,
    fitToView,
    poolFor,
    makeHost,
  };
}
