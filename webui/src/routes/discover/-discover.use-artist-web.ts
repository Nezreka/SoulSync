import { useCallback, useRef, useState } from 'react';

import type { WebBuilt, WebGraph, WebGraphCtor, WebLens, WebSizeBy } from './-discover.artist-web';
import type { WebSigmaHandlers } from './-discover.artist-web.lifecycle';
import type {
  WebArtistCard,
  WebDiscoveryCard,
  WebGenreCard,
  WebLegendItem,
} from './-discover.artist-web.panel';

import {
  artistWeb,
  artWebApplySize,
  artWebBuildCommunity,
  artWebBuildDiscovery,
  artWebBuildGenre,
  artWebEdgeThreshold,
  artWebFinishLayout,
  artWebSimGraph,
} from './-discover.artist-web';
import {
  webBetweenness,
  webCountKinds,
  webDiscoveryStats,
  webLibsReady,
  webResolveGraph,
  webResolveLens,
} from './-discover.artist-web.controller';
import {
  webKillLiveLayout,
  webMountSigma,
  webPreseed,
  webStartLiveLayout,
  webTeardown,
} from './-discover.artist-web.lifecycle';
import {
  webArtistCard,
  webDiscoveryCard,
  webGenreCard,
  webLegendItems,
} from './-discover.artist-web.panel';

/**
 * The Artist Web orchestrator — open/close, the three lenses, mounting, and
 * the selection cards.
 *
 * Transcribed from `openArtistWeb` (6633-6712), `_artWebRenderLens`
 * (6827-6900), `_artWebFetchDiscovery` and `_artWebStateCard` (6714-6760),
 * over the lifecycle/controller/panel modules that own the machinery.
 *
 * The vanilla's generation counter carries over verbatim (`artistWeb.gen`):
 * any fetch resolving after a close or a newer open bails. What dissolves in
 * React: the sibling-section `_prevDisplay` snapshot (rendering the overlay
 * replaces the sections) and the keydown re-wiring dance — `attachWebKeys`
 * is the overlay's mount effect, not this hook's.
 *
 * The empty/error STATE CARD is state here, not innerHTML: `phase: 'error'`
 * with its message, and the vanilla's rule rides along — reaching a state
 * card first TEARS DOWN any live graph so no WebGL context or FA2 worker
 * leaks (6755-6760).
 */

export type WebSelection =
  | { kind: 'artist'; card: WebArtistCard }
  | { kind: 'genre'; card: WebGenreCard }
  | { kind: 'discovery'; card: WebDiscoveryCard }
  | null;

export interface ArtistWebController {
  open: boolean;
  /** Appends "· settling…" to the stats line while the layout runs. */
  settling: boolean;
  lens: WebLens;
  sizeBy: WebSizeBy;
  edgeDeclutter: boolean;
  phase: 'loading' | 'error' | 'ready';
  /** The state card's copy when phase is error (retryable). */
  errorMessage: string | null;
  stats: string;
  legend: WebLegendItem[];
  selection: WebSelection;
  openWeb: (host: HTMLElement, lens?: WebLens) => Promise<void>;
  close: () => void;
  setLens: (host: HTMLElement, lens: WebLens) => Promise<void>;
  setSizeBy: (metric: WebSizeBy) => void;
  toggleEdges: () => void;
  clickNode: (node: string, buildDetailPath: (id: unknown, source: string) => string) => void;
  clearSelection: () => void;
  /** Recompute the owned/discovery tally after an expand grew the graph (8189). */
  recountStats: () => void;
}

/**
 * `ui` is the assembly's window into sigma's events (hover, node/stage clicks,
 * pointer moves for the tooltip). The mount happens deep inside renderLens,
 * long after this hook returned, so the handlers delegate through a ref — the
 * assembly's CURRENT closures always win, and mounting with no ui registered
 * degrades to the base behaviour (track the hovered node) rather than binding
 * stale stubs.
 */
export function useArtistWeb(ui?: Partial<WebSigmaHandlers>): ArtistWebController {
  const [open, setOpen] = useState(false);
  const [lens, setLensState] = useState<WebLens>('genre');
  const [sizeBy, setSizeByState] = useState<WebSizeBy>('popularity');
  const [edgeDeclutter, setEdgeDeclutter] = useState(false);
  const [phase, setPhase] = useState<'loading' | 'error' | 'ready'>('loading');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [stats, setStats] = useState('');
  const [legend, setLegend] = useState<WebLegendItem[]>([]);
  const [selection, setSelection] = useState<WebSelection>(null);
  /** The "· settling…" stats suffix while the live layout runs (6973). */
  const [settling, setSettling] = useState(false);
  const uiRef = useRef(ui);
  uiRef.current = ui;

  /** The vanilla's state card: message + teardown FIRST (6755-6760). */
  const stateCard = useCallback((message: string) => {
    webTeardown();
    setPhase('error');
    setErrorMessage(message);
  }, []);

  const renderLens = useCallback(
    (host: HTMLElement, next: WebLens) => {
      // Per-render UI state reset (6841-6856).
      artistWeb.searchMatch = null;
      artistWeb.focusSet = null;
      artistWeb.focusRoot = null;
      artistWeb.selectedKey = null;
      artistWeb.selectedFocus = null;
      artistWeb.pathSource = null;
      artistWeb.pathTarget = null;
      artistWeb.pathResult = null;
      artistWeb.pathNodes = null;
      artistWeb.pathPairs = null;
      artistWeb.genreFilter = null;
      artistWeb.sizeBy = 'popularity';
      setSizeByState('popularity');
      setSelection(null);

      const Graph = webResolveGraph() as WebGraphCtor | null;
      if (!Graph) {
        stateCard("graphology / sigma didn't load — check the CDN script tags.");
        return;
      }
      const data = artistWeb.data ?? { nodes: [], edges: [] };
      const built: WebBuilt =
        next === 'community'
          ? artWebBuildCommunity(data, Graph)
          : next === 'discovery'
            ? artWebBuildDiscovery(artistWeb.discoveryData ?? { nodes: [], edges: [] }, Graph)
            : artWebBuildGenre(data, Graph);

      artistWeb.genreColor = built.colorOf;
      artistWeb.genreCounts = built.counts;
      setStats(built.stats);

      // Home positions come back AFTER the layout settles; spread stays dormant.
      artistWeb.home = null;
      artistWeb.spreadRoot = null;
      artistWeb.spreadSet = null;
      artistWeb.spreadActive = null;

      artistWeb.edgeThreshold = artWebEdgeThreshold(built.graph);
      setEdgeDeclutter(artistWeb.edgeDeclutter);

      // Empty discovery GUIDES rather than showing a blank canvas (6880-6885).
      if (next === 'discovery' && built.graph.order === 0) {
        setLegend([]);
        stateCard(
          'No discovery candidates yet — add artists to your Watchlist so SoulSync can fetch similar artists to explore.',
        );
        return;
      }

      webPreseed(built.graph, next);
      webMountSigma(host, built.graph, {
        onHover: (node) => {
          artistWeb._hoverNode = node;
          uiRef.current?.onHover?.(node);
        },
        onClickNode: (node) => uiRef.current?.onClickNode?.(node),
        onClickStage: () => uiRef.current?.onClickStage?.(),
        onTooltipMove: (node) => uiRef.current?.onTooltipMove?.(node),
      });
      artWebFinishLayout(built.graph);
      webStartLiveLayout(built.graph, {
        setSettling: (settling) => setSettling(settling),
        onSettled: () => {},
      });
      setLegend(webLegendItems(next, built.counts, built.colorOf));
      setPhase('ready');
      setErrorMessage(null);
    },
    [stateCard],
  );

  const fetchDiscovery = useCallback(
    async (host: HTMLElement, gen: number) => {
      try {
        const payload = await (await fetch('/api/graph/discovery')).json();
        if (artistWeb.gen !== gen) return;
        artistWeb.discoveryData = payload as typeof artistWeb.discoveryData;
        renderLens(host, 'discovery');
      } catch {
        if (artistWeb.gen === gen) stateCard('Failed to load the artist web.');
      }
    },
    [renderLens, stateCard],
  );

  const openWeb = useCallback(
    async (host: HTMLElement, requested?: WebLens) => {
      const gen = ++artistWeb.gen;
      setOpen(true);
      setPhase('loading');
      setErrorMessage(null);
      if (!webLibsReady()) {
        stateCard("graphology / sigma didn't load — check the CDN script tags.");
        return;
      }
      try {
        const payload = await (await fetch('/api/graph/library')).json();
        if (artistWeb.gen !== gen) return; // closed or reopened mid-fetch
        artistWeb.data = payload as typeof artistWeb.data;
        // A fresh library payload invalidates every derived cache (6699-6702).
        artistWeb.simGraph = null;
        artistWeb.betweenCache = null;
        artistWeb.discoveryData = null;
        const next = webResolveLens(requested, artistWeb.lens);
        artistWeb.lens = next;
        setLensState(next);
        if (next === 'discovery') await fetchDiscovery(host, gen);
        else renderLens(host, next);
      } catch {
        if (artistWeb.gen === gen) stateCard('Failed to load the artist web.');
      }
    },
    [renderLens, fetchDiscovery, stateCard],
  );

  const setLens = useCallback(
    async (host: HTMLElement, next: WebLens) => {
      artistWeb.lens = next;
      setLensState(next);
      const gen = artistWeb.gen;
      if (next === 'discovery' && !artistWeb.discoveryData) {
        setPhase('loading');
        await fetchDiscovery(host, gen);
        return;
      }
      renderLens(host, next);
    },
    [renderLens, fetchDiscovery],
  );

  const close = useCallback(() => {
    artistWeb.gen += 1;
    webKillLiveLayout();
    webTeardown();
    setOpen(false);
    setSelection(null);
  }, []);

  const setSizeBy = useCallback((metric: WebSizeBy) => {
    artistWeb.sizeBy = metric;
    setSizeByState(metric);
    const g = artistWeb.graph as Parameters<typeof artWebApplySize>[0] | null;
    if (!g) return;
    // Both derived structures resolve LAZILY and cache (7710-7760): the
    // similarity graph for connections, betweenness for influence.
    const Graph = webResolveGraph() as WebGraphCtor | null;
    const sim = artWebSimGraph(artistWeb.data ?? null, Graph);
    const between = metric === 'influence' ? webBetweenness(sim) : null;
    artWebApplySize(g, metric, sim, between);
  }, []);

  const toggleEdges = useCallback(() => {
    artistWeb.edgeDeclutter = !artistWeb.edgeDeclutter;
    setEdgeDeclutter(artistWeb.edgeDeclutter);
    (artistWeb.sigma as { refresh?: () => void } | null)?.refresh?.();
  }, []);

  const clickNode = useCallback(
    (node: string, buildDetailPath: (id: unknown, source: string) => string) => {
      const g = artistWeb.graph;
      if (!g || !g.hasNode(node)) return;
      const attrs = g.getNodeAttributes(node);
      artistWeb.selectedKey = node;
      // The vanilla routes on the node's KIND alone (7652-7654) — 'genre',
      // 'discovery', else artist. The lens never enters into it: only the
      // discovery builder mints kind:'discovery' nodes anyway.
      if (attrs.kind === 'genre') {
        setSelection({ kind: 'genre', card: webGenreCard(g, node) });
      } else if (attrs.kind === 'discovery') {
        setSelection({ kind: 'discovery', card: webDiscoveryCard(g, node, buildDetailPath) });
      } else {
        setSelection({
          kind: 'artist',
          card: webArtistCard(g, node, artistWeb.lens, buildDetailPath),
        });
      }
    },
    [],
  );

  const recountStats = useCallback(() => {
    const g = artistWeb.graph as WebGraph | null;
    if (!g) return;
    const counts = webCountKinds(g);
    setStats(webDiscoveryStats(counts.owned, counts.discovery));
  }, []);

  const clearSelection = useCallback(() => {
    artistWeb.selectedKey = null;
    artistWeb.selectedFocus = null;
    artistWeb.focusSet = null;
    artistWeb.focusRoot = null;
    (artistWeb.sigma as { refresh?: () => void } | null)?.refresh?.();
    setSelection(null);
  }, []);

  return {
    open,
    settling,
    lens,
    sizeBy,
    edgeDeclutter,
    phase,
    errorMessage,
    stats,
    legend,
    selection,
    openWeb,
    close,
    setLens,
    setSizeBy,
    toggleEdges,
    clickNode,
    clearSelection,
    recountStats,
  };
}
