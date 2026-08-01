import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { ArtMapNode } from '../-discover.artist-map';
import type { ArtMapPoolEntry } from '../-discover.artist-map.entry';
import type { ArtMapContextMenu, ArtMapTooltip } from '../-discover.artist-map.panel';
import type { ArtistMapController } from '../-discover.use-artist-map';
import type { ArtMapSearchState } from './artist-map-chrome';

import { artMap, artMapIsWatched, artMapNodeBest } from '../-discover.artist-map';
import {
  ARTMAP_SEARCH_DEBOUNCE_MS,
  artMapSearchShouldRun,
  artMapSearchUrl,
} from '../-discover.artist-map.entry';
import {
  artMapArtistCard,
  artMapContextMenu,
  artMapPanelModel,
  artMapTooltip,
} from '../-discover.artist-map.panel';
import { artMapEmitRipple, artMapRender } from '../-discover.artist-map.render';
import {
  ArtMapContextMenuView,
  ArtMapSearchResults,
  ArtMapShortcutsModal,
  ArtMapTooltipView,
} from './artist-map-chrome';
import { ArtMapOverlay, artMapHandleResize } from './artist-map-overlay';
import { ArtMapPanel } from './artist-map-panel';

/**
 * The Artist Map, assembled: the orchestrator hook on one side, the overlay,
 * panel and floating chrome on the other. This file owns the state the vanilla
 * kept in loose DOM — which node the tooltip is on, where the context menu
 * is, which artist the panel shows — plus the three flows that never got a
 * module of their own because they are pure glue:
 *
 *   - the toolbar search dropdown (9241-9274)
 *   - the watchlist toggle + lazy membership check (6269-6311)
 *   - the similar-artists filter toggle (9845-9850)
 *
 * It also measures the `--artmap-*` CSS variables. The vanilla wrote these
 * positions inline per render (6165-6166, 6193, 6346); the port's stylesheet
 * rules read them as variables off the container, so the numbers have to be
 * measured here — toolbar height, sidebar width, island-nav bottom — on mount
 * and again whenever the interaction layer reports a resize.
 */

export type ArtMapAssemblyToast = { message: string; level: 'success' | 'info' | 'error' };

export interface ArtistMapAssemblyProps {
  map: ArtistMapController;
  /** Feeds the shared artist info modal (openYourArtistInfoModal_direct). */
  onOpenInfo: (pool: ArtMapPoolEntry) => void;
  buildDetailPath: (id: string, source: string) => string;
  onToast: (toast: ArtMapAssemblyToast) => void;
}

const isMobile = () => (window.innerWidth || document.documentElement.clientWidth || 9999) <= 760;

/** Measure the chrome positions into the container's CSS variables. */
function measureChromeVars() {
  const container = document.getElementById('artist-map-container');
  if (!container) return;
  const toolbar = container.querySelector<HTMLElement>('.artist-map-toolbar');
  const sidebar = document.getElementById('artmap-genre-sidebar');
  const nav = document.getElementById('artmap-island-nav');
  const toolbarH = toolbar ? toolbar.offsetHeight : 56;
  const sidebarW = sidebar && sidebar.style.display !== 'none' ? sidebar.offsetWidth || 0 : 0;
  container.style.setProperty('--artmap-panel-top', `${toolbarH}px`);
  container.style.setProperty('--artmap-chrome-top', `${toolbarH + 10}px`);
  container.style.setProperty('--artmap-chrome-left', `${sidebarW + 16}px`);
  if (nav) {
    container.style.setProperty('--artmap-menu-top', `${nav.offsetTop + nav.offsetHeight + 6}px`);
  }
}

export function ArtistMapAssembly({
  map,
  onOpenInfo,
  buildDetailPath,
  onToast,
}: ArtistMapAssemblyProps) {
  const [tooltip, setTooltip] = useState<{
    tip: ArtMapTooltip | null;
    node: ArtMapNode | null;
    x: number;
    y: number;
  }>({ tip: null, node: null, x: 0, y: 0 });
  const [menu, setMenu] = useState<{
    menu: ArtMapContextMenu | null;
    node: ArtMapNode | null;
    x: number;
    y: number;
  }>({ menu: null, node: null, x: 0, y: 0 });
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [search, setSearch] = useState<ArtMapSearchState>({ kind: 'hidden' });
  const [panelArtistId, setPanelArtistId] = useState<ArtMapNode['id'] | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  /** Re-render trigger for watch-state changes the singleton absorbs. */
  const [watchVersion, setWatchVersion] = useState(0);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchSeq = useRef(0);

  useEffect(
    () => () => {
      if (searchTimer.current) clearTimeout(searchTimer.current);
    },
    [],
  );

  // A new world (open/switch/focus) invalidates the panel's selection and any
  // floating chrome — the node objects it pointed at were replaced.
  useEffect(() => {
    setPanelArtistId(null);
    setSheetOpen(false);
    setTooltip({ tip: null, node: null, x: 0, y: 0 });
    setMenu((m) => ({ ...m, menu: null, node: null }));
  }, [map.kind]);

  // The chrome variables depend on the toolbar/sidebar/nav having rendered.
  useEffect(() => {
    measureChromeVars();
  });

  /** Lazily confirm watchlist membership, then refresh the button (6296). */
  const checkWatched = useCallback((node: ArtMapNode) => {
    const best = artMapNodeBest(node);
    artMap._watchSet = artMap._watchSet || new Set();
    artMap._watchChecked = artMap._watchChecked || new Set();
    if (!best.id || artMap._watchChecked.has(best.id)) return;
    fetch('/api/watchlist/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artist_id: best.id }),
    })
      .then((r) => r.json())
      .then((d: { success?: boolean; is_watching?: boolean }) => {
        artMap._watchChecked!.add(best.id);
        if (d?.success) {
          if (d.is_watching) artMap._watchSet!.add(best.id);
          else artMap._watchSet!.delete(best.id);
          setWatchVersion((v) => v + 1);
        }
      })
      .catch(() => {});
  }, []);

  /** The map's watchlist toggle (6269-6293). */
  const toggleWatch = useCallback(
    async (node: ArtMapNode) => {
      const best = artMapNodeBest(node);
      if (!best.id) {
        onToast({ message: 'No source id for this artist', level: 'error' });
        return;
      }
      artMap._watchSet = artMap._watchSet || new Set();
      const watched = artMapIsWatched(node);
      try {
        const resp = await fetch(watched ? '/api/watchlist/remove' : '/api/watchlist/add', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(
            watched
              ? { artist_id: best.id }
              : { artist_id: best.id, artist_name: node.name, source: best.source },
          ),
        });
        if (!resp.ok) {
          onToast({ message: 'Failed to update watchlist', level: 'error' });
          return;
        }
        if (watched) {
          artMap._watchSet.delete(best.id);
          // Demoted in place so the bubble recolours (6284).
          if (node.type === 'watchlist') node.type = 'similar';
        } else {
          artMap._watchSet.add(best.id);
        }
        onToast({
          message: watched
            ? `Removed ${node.name} from watchlist`
            : `Added ${node.name} to watchlist`,
          level: watched ? 'info' : 'success',
        });
        setWatchVersion((v) => v + 1);
      } catch {
        onToast({ message: 'Failed to update watchlist', level: 'error' });
      }
    },
    [onToast],
  );

  /** Show a node's card; confirm membership; slide the sheet up on mobile (6512). */
  const showPanelArtist = useCallback(
    (node: ArtMapNode) => {
      setPanelArtistId(node.id);
      checkWatched(node);
      if (isMobile()) setSheetOpen(true);
    },
    [checkWatched],
  );

  const host = useMemo(
    () =>
      map.makeHost({
        showTooltip: (e, node) => {
          if (!e || !node) {
            setTooltip({ tip: null, node: null, x: 0, y: 0 });
            return;
          }
          setTooltip({ tip: artMapTooltip(node), node, x: e.clientX, y: e.clientY });
        },
        showPanelArtist,
        showContextMenu: (e, node) => {
          setMenu({ menu: artMapContextMenu(node), node, x: e.clientX, y: e.clientY });
        },
        hideContextMenu: () => setMenu((m) => ({ ...m, menu: null, node: null })),
        focusSearch: () => {
          const el = document.getElementById('artist-map-search') as HTMLInputElement | null;
          el?.focus();
          return Boolean(el);
        },
        // The similar-artists filter (9845-9850) lives on the singleton; the
        // overlay reads `artMap._hideSimilar` for the button opacity.
        toggleSimilar: () => {
          artMap._hideSimilar = !artMap._hideSimilar;
          artMap.dirty = true;
          artMapRender();
          onToast({
            message: artMap._hideSimilar ? 'Showing watchlist only' : 'Showing all artists',
            level: 'info',
          });
          setWatchVersion((v) => v + 1);
        },
        resized: () => {
          const container = document.getElementById('artist-map-container');
          const canvas = document.getElementById('artist-map-canvas') as HTMLCanvasElement | null;
          if (container && canvas) {
            artMapHandleResize(container, canvas, document.getElementById('artmap-genre-sidebar'));
          }
          measureChromeVars();
          setWatchVersion((v) => v + 1);
        },
      }),
    [map, showPanelArtist, onToast],
  );

  /** The toolbar search: debounce, minimum length, one dropdown (9241-9274). */
  const onSearch = useCallback((query: string) => {
    if (searchTimer.current) clearTimeout(searchTimer.current);
    if (!artMapSearchShouldRun(query)) {
      setSearch({ kind: 'hidden' });
      return;
    }
    setSearch({ kind: 'searching' });
    searchTimer.current = setTimeout(async () => {
      const seq = ++searchSeq.current;
      try {
        const data = (await (await fetch(artMapSearchUrl(query.trim()))).json()) as {
          artists?: { name: string }[];
        };
        if (searchSeq.current !== seq) return;
        const artists = data.artists ?? [];
        setSearch(artists.length ? { kind: 'results', artists } : { kind: 'empty' });
      } catch {
        if (searchSeq.current === seq) setSearch({ kind: 'failed' });
      }
    }, ARTMAP_SEARCH_DEBOUNCE_MS);
  }, []);

  const explore = useCallback(
    (name: string) => {
      setSearch({ kind: 'hidden' });
      void map.openExplorer(name);
    },
    [map],
  );

  const nodeById = useCallback(
    (id: ArtMapNode['id']): ArtMapNode | null =>
      ((artMap._nodeById ?? {}) as Record<string, ArtMapNode>)[String(id)] ?? null,
    [],
  );

  // The panel model and the selected card re-derive from the singleton on any
  // focus or watch change; `watchVersion` is the extra tick the vanilla got
  // for free by re-rendering innerHTML.
  const { model, card } = useMemo(() => {
    void map.focusVersion;
    void watchVersion;
    const node = panelArtistId == null ? null : nodeById(panelArtistId);
    return { model: artMapPanelModel(), card: node ? artMapArtistCard(node) : null };
  }, [map.focusVersion, watchVersion, panelArtistId, nodeById]);

  if (!map.kind) return null;

  return (
    <ArtMapOverlay
      kind={map.kind}
      title={map.title}
      stats={map.stats}
      sidebarGenres={map.kind === 'genre' ? map.sidebarGenres : undefined}
      selectedGenre={map.selectedGenre}
      loading={map.loading}
      host={host}
      onClose={map.close}
      onSearch={onSearch}
      onToggleSimilar={host.toggleSimilar}
      onZoom={map.zoom}
      onFitToView={map.fitToView}
      onShowShortcuts={() => setShortcutsOpen(true)}
      onSwitchGenre={(genre) => void map.switchGenre(genre)}
      onFocusIsland={map.focusIsland}
      onIslandNav={map.islandNav}
    >
      <ArtMapSearchResults state={search} onExplore={explore} />
      <ArtMapPanel
        model={model}
        card={card}
        isMobile={isMobile()}
        open={sheetOpen}
        onSelectArtist={(id) => {
          // Top-list entry also ripples the bubble on the map (6519).
          const node = nodeById(id);
          if (!node) return;
          showPanelArtist(node);
          artMapEmitRipple(node.x, node.y, node._hue);
        }}
        onBackToList={() => setPanelArtistId(null)}
        onCloseSheet={() => setSheetOpen(false)}
        onToggleWatch={(id) => {
          const node = nodeById(id);
          if (node) void toggleWatch(node);
        }}
        onExplore={explore}
        onOpenDetails={(id) => {
          const node = nodeById(id);
          if (node) onOpenInfo(map.poolFor(node));
        }}
        buildArtistDetailPath={buildDetailPath}
      />
      <ArtMapTooltipView
        tip={tooltip.tip}
        node={tooltip.node}
        clientX={tooltip.x}
        clientY={tooltip.y}
      />
      <ArtMapContextMenuView
        menu={menu.menu}
        node={menu.node}
        clientX={menu.x}
        clientY={menu.y}
        onArtistInfo={(node) => onOpenInfo(map.poolFor(node))}
        onToggleWatchlist={(node) => void toggleWatch(node)}
        onClose={() => setMenu((m) => ({ ...m, menu: null, node: null }))}
        buildDetailPath={buildDetailPath}
      />
      {shortcutsOpen && <ArtMapShortcutsModal onClose={() => setShortcutsOpen(false)} />}
    </ArtMapOverlay>
  );
}
