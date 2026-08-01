import { useCallback, useEffect, useRef, useState } from 'react';

import type { WebLens } from '../-discover.artist-web';
import type { WebKeyHost } from '../-discover.artist-web.lifecycle';
import type { WebPathRow } from '../-discover.artist-web.panel';

import {
  artWebSetSpread,
  artWebSimGraph,
  artistWeb,
  webEdgeSize,
  webHexToRgba,
  WEB_DISCOVERY_COLOR,
  WEB_OWNED_COLOR,
} from '../-discover.artist-web';
import {
  WEB_CAMERA,
  WEB_EXPAND_PER,
  WEB_FIRST_RUN_KEY,
  WEB_HINT_FADE_MS,
  WEB_HINT_MS,
  WEB_PATH_PROMPT,
  WEB_SEARCH_LIMIT,
  webApplyHover,
  webApplySelection,
  webClearPath,
  webComputePath,
  webExpandPosition,
  webExpandRing,
  webPathClick,
  webPathNoneHint,
  webPathStartHint,
  webPreviewId,
  webResolveGraph,
  webSearchEnterTarget,
  webSearchHits,
  webToggleGenre,
  webTooltip,
} from '../-discover.artist-web.controller';
import { webStartFX } from '../-discover.artist-web.lifecycle';
import {
  WEB_PREVIEW_IDLE,
  WEB_PREVIEW_LOADING,
  WEB_PREVIEW_NONE,
  WEB_PREVIEW_UNAVAILABLE,
  WEB_PREVIEW_VOLUME,
  webPathRows,
  webPreviewPlayingLabel,
} from '../-discover.artist-web.panel';
import { useArtistWeb } from '../-discover.use-artist-web';
import { ArtWebOverlay } from './artist-web-overlay';
import {
  ArtWebArtistCard,
  ArtWebDiscoveryCard,
  ArtWebFirstRunHint,
  ArtWebGenreCard,
  ArtWebHelpModal,
  ArtWebPanel,
  ArtWebPathCard,
  ArtWebPathHint,
} from './artist-web-panel';

/**
 * The Artist Web, assembled: the orchestrator hook plus everything the vanilla
 * did in loose DOM glue — hover/click focus, the search dropdown and tooltip
 * (written into the overlay's placeholder divs, exactly as the vanilla wrote
 * into the static markup's), path mode, the genre filter, camera moves, the
 * preview player, watchlist-add and expand.
 *
 * Transcribed from discover.js 7460-8195. The hook owns open/close/lenses and
 * the selection CARD MODELS; this file owns the interactions around them.
 *
 * The assembly owns the useArtistWeb call — the sigma event handlers close
 * over this file's state and feed the hook's ui seam, and the page talks to
 * the web through `request` (open with a lens) rather than through the hook.
 */

export type ArtWebAssemblyToast = { message: string; level: 'success' | 'error' };

export interface ArtistWebAssemblyProps {
  /** Non-null = open (with an optional lens deep-link). Null = closed. */
  request: { lens?: WebLens } | null;
  onClose: () => void;
  /** Explore-in-Map hand-off: the web must close FIRST (7667-7670). */
  onExploreInMap: (name: string) => void;
  buildDetailPath: (id: unknown, source: string) => string;
  onToast: (toast: ArtWebAssemblyToast) => void;
}

/**
 * The pathfinder, curried for webPathClick. The similarity-only graph builds
 * LAZILY and caches (7684-7700) — pathfinding must run on it, never on the
 * displayed graph, so a path means "sounds like", not "both tagged Rock".
 */
const findPath = (a: string, b: string) => {
  const Graph = webResolveGraph();
  const sim = artWebSimGraph(artistWeb.data ?? null, Graph as never);
  return webComputePath(sim as never, a, b);
};

export function ArtistWebAssembly({
  request,
  onClose,
  onExploreInMap,
  buildDetailPath,
  onToast,
}: ArtistWebAssemblyProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [pathMode, setPathMode] = useState(false);
  const [pathHint, setPathHint] = useState<string | null>(null);
  const [pathRowsState, setPathRows] = useState<WebPathRow[] | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [firstRun, setFirstRun] = useState<'hidden' | 'shown' | 'fading'>('hidden');
  const [genreVersion, setGenreVersion] = useState(0);
  const [preview, setPreview] = useState<{ key: string | null; label: string; busy: boolean }>({
    key: null,
    label: WEB_PREVIEW_IDLE,
    busy: false,
  });
  const [artistThumb, setArtistThumb] = useState<{ key: string; url: string } | null>(null);
  const previewAudio = useRef<HTMLAudioElement | null>(null);
  const thumbCache = useRef<Record<string, string | null | undefined>>({});
  const pathModeRef = useRef(pathMode);
  pathModeRef.current = pathMode;

  const stopPreview = useCallback(() => {
    if (previewAudio.current) {
      try {
        previewAudio.current.pause();
      } catch {
        /* already dead */
      }
      previewAudio.current = null;
    }
    setPreview({ key: null, label: WEB_PREVIEW_IDLE, busy: false });
  }, []);

  /** DOM writes into the overlay's placeholder divs — the vanilla's own shape. */
  const renderSearchDropdown = useCallback((html: string | null) => {
    const el = document.getElementById('artist-web-search-results');
    if (!el) return;
    if (html === null) {
      el.style.display = 'none';
      el.innerHTML = '';
    } else {
      el.style.display = 'block';
      el.innerHTML = html;
    }
  }, []);

  const hideTooltip = useCallback(() => {
    const tip = document.getElementById('artist-web-tooltip');
    if (tip) tip.style.display = 'none';
  }, []);

  const refreshAfter = useCallback((ms: number) => {
    setTimeout(() => {
      (artistWeb.sigma as { refresh?: () => void } | null)?.refresh?.();
    }, ms);
  }, []);

  const cameraTo = useCallback(
    (key: string, ratio = WEB_CAMERA.cameraToRatio) => {
      const sigma = artistWeb.sigma as {
        getNodeDisplayData?: (k: string) => { x: number; y: number } | undefined;
        getCamera?: () => {
          animate: (pos: Record<string, number>, opts: Record<string, number>) => void;
        };
      } | null;
      const d = sigma?.getNodeDisplayData?.(key);
      if (d && sigma?.getCamera) {
        sigma.getCamera().animate({ x: d.x, y: d.y, ratio }, { duration: WEB_CAMERA.focusMs });
        refreshAfter(WEB_CAMERA.refreshAfterFocus);
      }
    },
    [refreshAfter],
  );

  /** The panel closes → its preview stops: the vanilla's single choke point. */
  const web = useArtistWeb({
    onHover: (node) => {
      const g = artistWeb.graph;
      // The tooltip shows even in path mode; nothing dims there (7627).
      if (node && g) {
        renderTooltip(node);
      } else {
        hideTooltip();
      }
      webApplyHover(g as never, node);
      (artistWeb.sigma as { refresh?: () => void } | null)?.refresh?.();
    },
    onClickNode: (node) => {
      const g = artistWeb.graph;
      if (!g || !g.hasNode(node)) return;
      if (pathModeRef.current) {
        pathClick(node);
        return;
      }
      const set = webApplySelection(g as never, node);
      artWebSetSpread(node, set);
      webStartFX();
      (artistWeb.sigma as { refresh?: () => void } | null)?.refresh?.();
      setArtistThumb(null);
      web.clickNode(node, buildDetailPath);
      resolveCardThumb(node);
    },
    onClickStage: () => {
      if (pathModeRef.current) return;
      clearSelection();
    },
    onTooltipMove: () => positionTooltip(),
  });
  const webRef = useRef(web);
  webRef.current = web;

  /** The hover tooltip, written into the overlay's placeholder (7571-7622). */
  const renderTooltip = useCallback(
    (nodeKey: string) => {
      const tip = document.getElementById('artist-web-tooltip');
      const g = artistWeb.graph;
      if (!tip || !g || !g.hasNode(nodeKey)) return;
      const model = webTooltip(g as never, nodeKey, thumbCache.current);
      const img = model.imageUrl
        ? `<img class="artmap-tip-img" src="${model.imageUrl.replace(/"/g, '&quot;')}" alt="">`
        : model.badge === 'Genre'
          ? ''
          : '<div class="artmap-tip-img artmap-tip-img-fallback">&#9835;</div>';
      const badge = model.badge ? `<span class="artmap-tip-badge">${model.badge}</span>` : '';
      const conn = model.connectionText
        ? `<div class="artmap-tip-conn">${model.connectionText}</div>`
        : '';
      const genre = model.genre
        ? `<div class="artmap-tip-genres"><span>${model.genre
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')}</span></div>`
        : '';
      tip.innerHTML =
        `<div class="artmap-tip-row">${img}<div class="artmap-tip-info">` +
        `<div class="artmap-tip-name">${model.label
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')}</div>${badge}${conn}${genre}</div></div>`;
      tip.style.display = 'block';
      positionTooltip();
      // Lazily resolve an owned artist's thumb; a fast sweep must not fetch
      // for every node grazed past (7605-7612, the 140ms debounce).
      if (model.needsThumb && model.artistId != null) {
        const aid = String(model.artistId);
        if (thumbCache.current[aid] === undefined) {
          thumbCache.current[aid] = null;
          fetch(`/api/library/artist/${encodeURIComponent(aid)}/thumb`)
            .then((r) => r.json())
            .then((d: { success?: boolean; image_url?: string }) => {
              thumbCache.current[aid] = d?.success && d.image_url ? d.image_url : '';
            })
            .catch(() => {
              thumbCache.current[aid] = '';
            });
        }
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const positionTooltip = useCallback(() => {
    const tip = document.getElementById('artist-web-tooltip');
    const m = artistWeb._mouse;
    if (!tip || !m || tip.style.display === 'none') return;
    tip.style.left = `${Math.min(m.x + 16, window.innerWidth - tip.offsetWidth - 10)}px`;
    tip.style.top = `${Math.min(m.y - 10, window.innerHeight - tip.offsetHeight - 10)}px`;
  }, []);

  /** The artist card's own avatar, guarded by the current selection (8016-8030). */
  const resolveCardThumb = useCallback((node: string) => {
    const g = artistWeb.graph;
    if (!g || !g.hasNode(node)) return;
    const artistId = g.getNodeAttribute(node, 'artistId');
    if (artistId == null) return;
    fetch(`/api/library/artist/${encodeURIComponent(String(artistId))}/thumb`)
      .then((r) => r.json())
      .then((d: { success?: boolean; image_url?: string }) => {
        if (!d?.success || !d.image_url) return;
        if (artistWeb.selectedKey !== node) return; // selection moved on
        setArtistThumb({ key: node, url: d.image_url });
      })
      .catch(() => {});
  }, []);

  const clearSelection = useCallback(() => {
    // The spread clears by nulling its three fields (7331), letting the fanned
    // neighbours settle back home on the next FX frames.
    artistWeb.spreadRoot = null;
    artistWeb.spreadSet = null;
    artistWeb.spreadActive = null;
    stopPreview();
    setArtistThumb(null);
    webRef.current.clearSelection();
  }, [stopPreview]);

  /** One click in path mode (7713-7746), rendered as hint or panel. */
  const pathClick = useCallback(
    (node: string) => {
      const g = artistWeb.graph;
      if (!g) return;
      const step = webPathClick(g as never, node, findPath);
      if (step.kind === 'reject-hub') {
        setPathHint('Pick artists, not genre hubs.');
        return;
      }
      if (step.kind === 'start') {
        setPathRows(null);
        stopPreview();
        setArtistThumb(null);
        webRef.current.clearSelection();
        (artistWeb.sigma as { refresh?: () => void } | null)?.refresh?.();
        setPathHint(webPathStartHint(step.label));
        return;
      }
      if (step.kind === 'same-node') return;
      if (step.kind === 'no-path') {
        setPathHint(webPathNoneHint(step.from, step.to));
        return;
      }
      (artistWeb.sigma as { refresh?: () => void } | null)?.refresh?.();
      setPathRows(webPathRows(g as never, step.path));
      setPathHint(null);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    },
    [stopPreview],
  );

  const exitPath = useCallback(() => {
    setPathMode(false);
    setPathHint(null);
    setPathRows(null);
    stopPreview();
    webClearPath();
    (artistWeb.sigma as { refresh?: () => void } | null)?.refresh?.();
  }, [stopPreview]);

  const togglePath = useCallback(() => {
    if (pathModeRef.current) {
      exitPath();
      return;
    }
    setPathMode(true);
    artistWeb.pathMode = true;
    clearSelection();
    webClearPath();
    setPathHint(WEB_PATH_PROMPT);
  }, [exitPath, clearSelection]);

  // pathMode mirrors into the singleton the reducers read.
  useEffect(() => {
    artistWeb.pathMode = pathMode;
  }, [pathMode]);

  /** The toolbar search — instant, client-side (7460-7516). */
  const onSearch = useCallback(
    (query: string) => {
      const hits = webSearchHits(query);
      if (!query.trim() || query.trim().length < 2) {
        renderSearchDropdown(null);
        artistWeb.searchMatch = null;
        (artistWeb.sigma as { refresh?: () => void } | null)?.refresh?.();
        return;
      }
      artistWeb.searchMatch = new Set(hits.map((n) => n.key));
      (artistWeb.sigma as { refresh?: () => void } | null)?.refresh?.();
      if (!hits.length) {
        renderSearchDropdown(
          '<div class="artist-map-search-item artist-map-search-empty">No artists found</div>',
        );
        return;
      }
      const esc = (t: string) => t.replace(/&/g, '&amp;').replace(/</g, '&lt;');
      renderSearchDropdown(
        hits
          .slice(0, WEB_SEARCH_LIMIT)
          .map(
            (n) =>
              `<div class="artist-map-search-item" data-web-focus="${esc(n.key)}">` +
              `<span class="artist-map-search-type similar">○</span>${esc(n.label)}` +
              `<span class="artist-map-search-go">Focus &rarr;</span></div>`,
          )
          .join(''),
      );
    },
    [renderSearchDropdown],
  );

  /** Centre the camera on a node and isolate it as the single match (7500-7516). */
  const focusNode = useCallback(
    (key: string) => {
      const g = artistWeb.graph;
      if (!g || !g.hasNode(key)) return;
      renderSearchDropdown(null);
      artistWeb.searchMatch = new Set([key]);
      (artistWeb.sigma as { refresh?: () => void } | null)?.refresh?.();
      cameraTo(key, WEB_CAMERA.focusRatio);
    },
    [renderSearchDropdown, cameraTo],
  );

  // The dropdown is innerHTML (vanilla-shaped), so its clicks arrive by
  // delegation rather than through React handlers.
  useEffect(() => {
    const el = document.getElementById('artist-web-search-results');
    if (!el) return;
    const onClick = (e: Event) => {
      const item = (e.target as HTMLElement).closest?.('[data-web-focus]');
      const key = item?.getAttribute('data-web-focus');
      if (key) focusNode(key);
    };
    el.addEventListener('click', onClick);
    return () => el.removeEventListener('click', onClick);
  }, [focusNode, request]);

  const onSearchEnter = useCallback(() => {
    const target = webSearchEnterTarget();
    if (target) focusNode(target);
  }, [focusNode]);

  const zoom = useCallback(
    (factor: number) => {
      const sigma = artistWeb.sigma as {
        getCamera?: () => {
          ratio: number;
          animate: (pos: Record<string, number>, opts: Record<string, number>) => void;
        };
      } | null;
      const cam = sigma?.getCamera?.();
      if (!cam) return;
      cam.animate({ ratio: cam.ratio * factor }, { duration: WEB_CAMERA.zoomMs });
      refreshAfter(WEB_CAMERA.refreshAfterZoom);
    },
    [refreshAfter],
  );

  const fitToView = useCallback(() => {
    const sigma = artistWeb.sigma as {
      refresh?: () => void;
      getCamera?: () => { animatedReset: (opts: Record<string, number>) => void };
    } | null;
    sigma?.getCamera?.()?.animatedReset({ duration: WEB_CAMERA.fitMs });
    refreshAfter(WEB_CAMERA.refreshAfterFit);
    // Clear any search focus so the whole web is visible again (7537-7541).
    const input = document.getElementById('artist-web-search') as HTMLInputElement | null;
    if (input) input.value = '';
    artistWeb.searchMatch = null;
    sigma?.refresh?.();
  }, [refreshAfter]);

  /** 30s Deezer preview for discovery candidates (7925-7955). */
  const togglePreview = useCallback(
    async (key: string) => {
      const g = artistWeb.graph;
      if (!g || !g.hasNode(key)) return;
      if (preview.key === key) {
        stopPreview();
        return;
      }
      stopPreview();
      const ids = g.getNodeAttribute(key, 'ids') as [string, string][] | undefined;
      const dz = webPreviewId(ids);
      if (!dz) {
        setPreview({ key: null, label: WEB_PREVIEW_NONE, busy: false });
        return;
      }
      setPreview({ key: null, label: WEB_PREVIEW_LOADING, busy: true });
      const myGen = artistWeb.gen;
      try {
        const r = await fetch(`/api/graph/discovery/preview/${encodeURIComponent(dz)}`);
        const d = (await r.json()) as { success?: boolean; preview_url?: string; track?: string };
        if (artistWeb.gen !== myGen) return;
        if (!d.success || !d.preview_url) throw new Error('no preview');
        const audio = new Audio(d.preview_url);
        audio.volume = WEB_PREVIEW_VOLUME;
        audio.onended = () => stopPreview();
        await audio.play();
        if (artistWeb.gen !== myGen) {
          try {
            audio.pause();
          } catch {
            /* ignore */
          }
          return;
        }
        previewAudio.current = audio;
        setPreview({ key, label: webPreviewPlayingLabel(d.track), busy: false });
      } catch {
        stopPreview();
        setPreview({ key: null, label: WEB_PREVIEW_UNAVAILABLE, busy: false });
      }
    },
    [preview.key, stopPreview],
  );

  /** Add a discovery candidate to the watchlist, id WITH its source (8095-8120). */
  const addToWatchlist = useCallback(
    async (key: string) => {
      const g = artistWeb.graph;
      if (!g || !g.hasNode(key)) return;
      const a = g.getNodeAttributes(key) as { label?: string; ids?: [string, string][] };
      const pairs = a.ids ?? [];
      const pair = pairs.find((p) => p && p[0] === 'spotify') || pairs[0];
      if (!pair) {
        onToast({ message: 'No id available', level: 'error' });
        return;
      }
      try {
        const r = await fetch('/api/watchlist/add', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            artist_id: String(pair[1]),
            artist_name: a.label,
            source: pair[0],
          }),
        });
        const d = (await r.json()) as { success?: boolean; error?: string };
        if (!d.success) throw new Error(d.error || 'failed');
        onToast({ message: `Added ${a.label} to watchlist`, level: 'success' });
        window.updateWatchlistCount?.();
      } catch (e) {
        onToast({
          message: `Couldn't add: ${e instanceof Error ? e.message : String(e)}`,
          level: 'error',
        });
      }
    },
    [onToast],
  );

  /** Expand-on-click: grow the discovery graph around an owned node (8125-8195). */
  const expandNode = useCallback(
    async (key: string) => {
      const g = artistWeb.graph as {
        hasNode: (k: string) => boolean;
        hasEdge: (a: string, b: string) => boolean;
        addNode: (k: string, attrs: Record<string, unknown>) => void;
        addEdge: (a: string, b: string, attrs: Record<string, unknown>) => void;
        setNodeAttribute: (k: string, name: string, value: unknown) => void;
        getNodeAttributes: (k: string) => Record<string, unknown>;
        forEachNode: (fn: (k: string) => void) => void;
      } | null;
      if (!g || !g.hasNode(key) || artistWeb.lens !== 'discovery') return;
      const a = g.getNodeAttributes(key);
      if (a.expanded) return;
      try {
        const exclude: string[] = [];
        g.forEachNode((k) => exclude.push(k));
        const r = await fetch('/api/graph/discovery/expand', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            key,
            // ids holds [source, id] pairs; the matcher wants the flat ids.
            ids: ((a.ids as [string, string][]) || []).map((p) => p[1]),
            exclude,
            per: WEB_EXPAND_PER,
          }),
        });
        const d = (await r.json()) as {
          error?: string;
          nodes?: Record<string, unknown>[];
          edges?: { source: string; target: string; weight?: number }[];
        };
        if (d.error) throw new Error(d.error);
        const newNodes = (d.nodes ?? []).filter((n) => n.key && !g.hasNode(n.key as string));
        if (!newNodes.length) {
          g.setNodeAttribute(key, 'expanded', true);
          webRef.current.clickNode(key, buildDetailPath); // re-render: "Expanded ✓"
          return;
        }
        // Ring placement around the parent's HOME position (8150-8155).
        const home = artistWeb.home || (artistWeb.home = {});
        const parent = home[key] || { x: a.x as number, y: a.y as number };
        const radius = webExpandRing(artistWeb.spreadPush || 0);
        newNodes.forEach((n, i) => {
          const k = n.key as string;
          const pos = webExpandPosition(parent, i, newNodes.length, radius, Math.random() * 0.4);
          if (n.kind === 'owned') {
            g.addNode(k, {
              label: n.label,
              x: pos.x,
              y: pos.y,
              size: 7,
              color: WEB_OWNED_COLOR,
              baseColor: WEB_OWNED_COLOR,
              forceLabel: true,
              kind: 'owned',
              genre: 'Your library',
              artistId: n.id ?? null,
              thumb: n.thumb ?? null,
            });
          } else {
            g.addNode(k, {
              label: n.label,
              x: pos.x,
              y: pos.y,
              size: 3.5,
              color: WEB_DISCOVERY_COLOR,
              baseColor: WEB_DISCOVERY_COLOR,
              kind: 'discovery',
              genre: 'Discovery',
              image_url: n.image_url ?? null,
              genresList: n.genres ?? null,
              ids: n.ids ?? [],
              popularity: n.popularity ?? 0,
            });
          }
          home[k] = pos;
          artistWeb.index.push({ key: k, label: n.label as string }); // searchable immediately
        });
        (d.edges ?? []).forEach((e) => {
          if (g.hasNode(e.source) && g.hasNode(e.target) && !g.hasEdge(e.source, e.target)) {
            g.addEdge(e.source, e.target, {
              weight: e.weight,
              size: webEdgeSize(e.weight),
              color: webHexToRgba(WEB_DISCOVERY_COLOR, 0.28),
              baseColor: WEB_DISCOVERY_COLOR,
              kind: 'discovery',
            });
          }
        });
        g.setNodeAttribute(key, 'expanded', true);
        // Re-select: the focus set now includes the new neighbours (8186).
        const set = webApplySelection(g as never, key);
        artWebSetSpread(key, set);
        webStartFX();
        (artistWeb.sigma as { refresh?: () => void } | null)?.refresh?.();
        webRef.current.clickNode(key, buildDetailPath);
        webRef.current.recountStats();
      } catch (e) {
        onToast({
          message: `Expand failed: ${e instanceof Error ? e.message : String(e)}`,
          level: 'error',
        });
      }
    },
    [buildDetailPath, onToast],
  );

  const keyHost: WebKeyHost = {
    pathMode: () => pathModeRef.current,
    panelOpen: () => webRef.current.selection !== null || pathRowsState !== null,
    exitPath,
    clearSelection,
    close: onClose,
    focusSearch: () => {
      const el = document.getElementById('artist-web-search') as HTMLInputElement | null;
      el?.focus();
      return Boolean(el);
    },
    fitToView,
    zoom,
    showHelp: () => setHelpOpen(true),
  };

  // Open when requested; the host div exists once the overlay rendered.
  useEffect(() => {
    if (!request) return;
    const host = hostRef.current;
    if (!host) return;
    void web.openWeb(host, request.lens);
    // One-time first-run hint, a self-dismissing pill (7826-7845).
    try {
      if (!localStorage.getItem(WEB_FIRST_RUN_KEY)) {
        localStorage.setItem(WEB_FIRST_RUN_KEY, '1');
        setFirstRun('shown');
      }
    } catch {
      /* private mode: no hint */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request]);

  useEffect(() => {
    if (firstRun !== 'shown') return;
    const fade = setTimeout(() => setFirstRun('fading'), WEB_HINT_MS);
    const gone = setTimeout(() => setFirstRun('hidden'), WEB_HINT_MS + WEB_HINT_FADE_MS);
    return () => {
      clearTimeout(fade);
      clearTimeout(gone);
    };
  }, [firstRun]);

  // Closing (unmount) stops any preview left playing.
  useEffect(
    () => () => {
      stopPreview();
    },
    [stopPreview],
  );

  if (!request) return null;

  const sel = web.selection;
  const panelOpen = sel !== null || pathRowsState !== null;

  return (
    <ArtWebOverlay
      stats={web.settling ? `${web.stats} · settling…` : web.stats}
      lens={web.lens}
      sizeBy={web.sizeBy}
      pathMode={pathMode}
      edgeDeclutter={web.edgeDeclutter}
      hostRef={hostRef}
      sidebar={{
        open: sidebarOpen,
        counts: (artistWeb.genreCounts as Record<string, number>) ?? {},
        colorOf: (artistWeb.genreColor as (g: string) => string) ?? (() => '#888'),
      }}
      legend={web.legend}
      keyHost={keyHost}
      onClose={() => {
        web.close();
        onClose();
      }}
      onSearch={onSearch}
      onSearchEnter={onSearchEnter}
      onSetLens={(lens) => {
        if (hostRef.current) void web.setLens(hostRef.current, lens);
        setSidebarOpen(false);
        exitPath();
      }}
      onSetSize={web.setSizeBy}
      onTogglePath={togglePath}
      onToggleEdges={web.toggleEdges}
      onToggleFilter={() => setSidebarOpen((open) => !open)}
      onZoom={zoom}
      onFitToView={fitToView}
      onShowHelp={() => setHelpOpen(true)}
      onToggleGenre={(genre) => {
        webToggleGenre(genre);
        (artistWeb.sigma as { refresh?: () => void } | null)?.refresh?.();
        setGenreVersion((v) => v + 1);
      }}
      onClearGenreFilter={() => {
        void genreVersion;
        artistWeb.genreFilter = null;
        (artistWeb.sigma as { refresh?: () => void } | null)?.refresh?.();
        setGenreVersion((v) => v + 1);
      }}
    >
      {web.phase === 'error' && web.errorMessage && (
        <div className="artweb-state-card">
          <div className="artweb-state-msg">{web.errorMessage}</div>
          <button
            type="button"
            className="artweb-state-btn"
            onClick={() => {
              if (hostRef.current) void web.openWeb(hostRef.current, web.lens);
            }}
          >
            Retry
          </button>
        </div>
      )}

      {panelOpen && (
        <ArtWebPanel>
          {pathRowsState ? (
            <ArtWebPathCard rows={pathRowsState} onDone={exitPath} onCameraTo={cameraTo} />
          ) : sel?.kind === 'artist' ? (
            <ArtWebArtistCard
              card={sel.card}
              imageUrl={artistThumb?.key === sel.card.key ? artistThumb.url : null}
              onClose={clearSelection}
              onPlayRadio={(key) => {
                const g = artistWeb.graph;
                const artistId = g?.hasNode(key) ? g.getNodeAttribute(key, 'artistId') : null;
                if (artistId == null) return;
                stopPreview();
                const label = (g?.getNodeAttribute(key, 'label') as string) || '';
                if (window.startArtistRadioById) {
                  void window.startArtistRadioById(artistId as string | number, label);
                } else {
                  onToast({ message: 'Player not available', level: 'error' });
                }
              }}
              onExpand={(key) => void expandNode(key)}
              onExploreInMap={(label) => {
                // Close FIRST: the web overlay sits on top of the map (7667).
                web.close();
                onClose();
                onExploreInMap(label);
              }}
            />
          ) : sel?.kind === 'genre' ? (
            <ArtWebGenreCard
              card={sel.card}
              onClose={clearSelection}
              onGoToArtist={(key) => {
                const g = artistWeb.graph;
                if (!g || !g.hasNode(key)) return;
                const set = webApplySelection(g as never, key);
                artWebSetSpread(key, set);
                webStartFX();
                setArtistThumb(null);
                web.clickNode(key, buildDetailPath);
                resolveCardThumb(key);
                cameraTo(key, WEB_CAMERA.focusRatio);
              }}
            />
          ) : sel?.kind === 'discovery' ? (
            <ArtWebDiscoveryCard
              card={sel.card}
              previewLabel={preview.label}
              previewBusy={preview.busy}
              onClose={clearSelection}
              onTogglePreview={(key) => void togglePreview(key)}
              onAddToWatchlist={(key) => void addToWatchlist(key)}
            />
          ) : null}
        </ArtWebPanel>
      )}

      {pathHint && <ArtWebPathHint html={pathHint} />}
      {firstRun !== 'hidden' && <ArtWebFirstRunHint fading={firstRun === 'fading'} />}
      {helpOpen && <ArtWebHelpModal onClose={() => setHelpOpen(false)} />}
    </ArtWebOverlay>
  );
}
