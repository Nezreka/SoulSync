import type { WebGraph, WebLens } from './-discover.artist-web';

import {
  artistWeb,
  artWebEdgeReducer,
  artWebFinishLayout,
  artWebNodeReducer,
  artWebSpreadTick,
  webDrawLabel,
  WEB_CANVAS_BG,
} from './-discover.artist-web';
import {
  webLayoutSettings,
  webPreseedOptions,
  webSettleBudget,
  WEB_SETTLE_REFRESH_MS,
  WEB_SIGMA_SETTINGS,
  WEB_SYNC_ITERATIONS,
} from './-discover.artist-web.controller';

/**
 * The Artist Web's imperative lifecycle: sigma, the ForceAtlas2 supervisor and
 * the spread-effect frame loop.
 *
 * Transcribed from discover.js 6892-6995, 7197-7247, 7270-7272, 7316-7317 and
 * the teardown half of 7319-7351.
 *
 * The controller module holds the parts that are decisions — settings objects,
 * budgets, which lens packs how. This holds the parts that are EFFECTS: things
 * that start a worker, allocate a WebGL context, or schedule a frame. They are
 * separated because effects are what leak, and a leak here is expensive: a live
 * FA2 worker keeps a core busy, and an unkilled sigma holds a WebGL context that
 * the browser will eventually reclaim by killing someone else's.
 *
 * Every function that starts something returns or pairs with the thing that
 * stops it. There is no fire-and-forget in this file.
 */

// ── What we need from the CDN globals ────────────────────────────────────────

/** The members of a sigma instance this port touches (7218-7246, 6982-6987). */
export interface WebSigmaLike {
  kill(): void;
  refresh(): void;
  on(event: string, handler: (payload: { node: string }) => void): void;
  getCamera(): { animatedReset(opts: { duration: number }): void };
}

export type WebSigmaCtor = new (
  graph: WebGraph,
  container: HTMLElement,
  settings: Record<string, unknown>,
) => WebSigmaLike;

interface WebLifecycleGlobals {
  Sigma?: WebSigmaCtor;
  graphologyLibrary?: {
    FA2Layout?: new (
      graph: WebGraph,
      opts: { settings: Record<string, unknown> },
    ) => {
      start(): void;
      kill(): void;
    };
    layoutForceAtlas2?: {
      assign(g: WebGraph, opts: Record<string, unknown>): void;
      inferSettings(g: WebGraph): Record<string, unknown>;
    };
    layout?: { circlepack?: { assign(g: WebGraph, opts: Record<string, unknown>): void } };
  };
}

const globals = () => window as unknown as WebLifecycleGlobals;

// ── The ForceAtlas2 supervisor ───────────────────────────────────────────────

/**
 * Stop the live layout (6992-6995).
 *
 * Both halves matter and they fail differently: a surviving TIMER finalizes a
 * graph that has since been detached, and a surviving WORKER keeps streaming
 * positions into it. The kill is wrapped because a supervisor that already died
 * throws on a second kill, and that must not stop the timer being cleared.
 */
export function webKillLiveLayout(): void {
  if (artistWeb.fa2Timer) {
    clearTimeout(artistWeb.fa2Timer);
    artistWeb.fa2Timer = null;
  }
  if (artistWeb.fa2) {
    try {
      artistWeb.fa2.kill();
    } catch {
      /* already dead — nothing to stop */
    }
    artistWeb.fa2 = null;
  }
}

/** How long the settled-camera reset animates (6982). */
export const WEB_SETTLE_RESET_MS = 500;

/** What the page has to do for the settle to be visible. */
export interface WebSettleHooks {
  /** Toggle the "· settling…" suffix on the stats line (6973, 6980). */
  setSettling: (settling: boolean) => void;
  /** Fit the camera to the settled web, then trail a refresh (6982-6988). */
  onSettled: () => void;
}

/**
 * Run the layout live in a Web Worker, then finalize on a wall-clock budget
 * (6941-6990).
 *
 * The graph is mounted BEFORE this runs, so the pre-seeded islands are on screen
 * immediately and the user watches the blob organize rather than waiting on a
 * blank canvas. When the worker build is unavailable — or its constructor throws
 * — this falls back to the synchronous pass, which blocks briefly but always
 * works. The fallback also runs for an EMPTY graph, where a worker would idle
 * forever and the settle would never finish.
 *
 * Returns whether the live worker actually took over, which is the difference
 * between "settling" chrome being warranted and not.
 */
export function webStartLiveLayout(graph: WebGraph, hooks: WebSettleHooks): boolean {
  webKillLiveLayout();
  const lib = globals().graphologyLibrary;
  const FA2Layout = lib?.FA2Layout;
  const fa2 = lib?.layoutForceAtlas2;
  if (!FA2Layout || !fa2 || graph.order === 0) {
    webRunLayoutSync(graph);
    artWebFinishLayout(graph);
    return false;
  }

  let layout: { start(): void; kill(): void } | null = null;
  try {
    layout = new FA2Layout(graph, { settings: webLayoutSettings(fa2.inferSettings(graph)) });
    layout.start();
  } catch (err) {
    console.warn('[Artist Web] worker layout unavailable — falling back to sync', err);
    try {
      layout?.kill();
    } catch {
      /* ignore */
    }
    webRunLayoutSync(graph);
    artWebFinishLayout(graph);
    return false;
  }

  artistWeb.fa2 = layout;
  hooks.setSettling(true);
  artistWeb.fa2Timer = setTimeout(() => {
    webKillLiveLayout();
    // Capture resting positions BEFORE anything else — the spread effect's
    // guards all check `home`, so until this runs a selection does nothing.
    artWebFinishLayout(graph);
    hooks.setSettling(false);
    // Only if this graph is still the mounted one — a lens switch during the
    // settle leaves this timer holding a detached graph.
    if (artistWeb.sigma && artistWeb.graph === graph) hooks.onSettled();
  }, webSettleBudget(graph.order));
  return true;
}

/**
 * The synchronous fallback (7197-7211).
 *
 * Silently leaves the builders' random positions when forceAtlas2 is missing —
 * a scattered web still reads as a web, where an exception would leave a blank
 * canvas with a live sigma on it.
 */
export function webRunLayoutSync(graph: WebGraph): void {
  const fa2 = globals().graphologyLibrary?.layoutForceAtlas2;
  if (!fa2) {
    console.warn('[Artist Web] forceAtlas2 unavailable — nodes stay at random positions');
    return;
  }
  fa2.assign(graph, {
    iterations: WEB_SYNC_ITERATIONS,
    settings: webLayoutSettings(fa2.inferSettings(graph)),
  });
}

/**
 * Fit the settled web, then force one more frame (6982-6988).
 *
 * `hideEdgesOnMove` means the LAST frame of a camera animation is drawn without
 * edges, and nothing re-renders once the camera stops — so the web would sit
 * there edge-less until the user touched it. The trailing refresh is not polish.
 */
export function webResetCamera(): void {
  const sigma = artistWeb.sigma as WebSigmaLike | null;
  const graph = artistWeb.graph;
  if (!sigma) return;
  sigma.getCamera().animatedReset({ duration: WEB_SETTLE_RESET_MS });
  setTimeout(() => {
    const later = artistWeb.sigma as WebSigmaLike | null;
    if (later && artistWeb.graph === graph) later.refresh();
  }, WEB_SETTLE_REFRESH_MS);
}

/**
 * Pack nodes into cluster circles before the force layout runs (6929-6936).
 *
 * FA2 refines structure far faster than it untangles noise, so this is most of
 * why the settle looks calm. A silent no-op when the CDN bundle lacks the
 * helper: the builders already assigned random positions.
 */
export function webPreseed(graph: WebGraph, lens: WebLens): void {
  try {
    const cp = globals().graphologyLibrary?.layout?.circlepack;
    if (!cp || graph.order === 0) return;
    cp.assign(graph, webPreseedOptions(lens));
  } catch {
    /* keep the random positions the builders already set */
  }
}

// ── The spread-effect frame loop ─────────────────────────────────────────────

function spreadFrame(): void {
  artistWeb.fxRAF = null;
  const sigma = artistWeb.sigma as WebSigmaLike | null;
  const graph = artistWeb.graph;
  if (!sigma || !artistWeb.cursorFX || !artistWeb.home || !graph) return;
  if (artWebSpreadTick(graph)) {
    sigma.refresh();
    artistWeb.fxRAF = requestAnimationFrame(spreadFrame);
  }
}

/**
 * Start the spread loop if it is not already running (7270-7272).
 *
 * The guard is what makes this cheap to call from anywhere: every selection,
 * clear and expand pokes it, and only the first poke schedules a frame. The loop
 * stops itself as soon as nothing is moving, so it is not a continuous render.
 */
export function webStartFX(): void {
  if (!artistWeb.fxRAF) artistWeb.fxRAF = requestAnimationFrame(spreadFrame);
}

/** Cancel any pending spread frame (7217, 7330). */
export function webKillFX(): void {
  if (artistWeb.fxRAF) {
    cancelAnimationFrame(artistWeb.fxRAF);
    artistWeb.fxRAF = null;
  }
}

// ── Mounting sigma ───────────────────────────────────────────────────────────

/** The four things the graph reports back to the page (7240-7246). */
export interface WebSigmaHandlers {
  /** enterNode/leaveNode, collapsed — leaving passes null (7240-7241). */
  onHover: (node: string | null) => void;
  onClickNode: (node: string) => void;
  onClickStage: () => void;
  /**
   * The pointer moved over the graph while a node was hovered (7234-7237).
   *
   * sigma's own node events don't carry client coordinates reliably across
   * versions, so the tooltip's position comes from this instead.
   */
  onTooltipMove: (node: string) => void;
}

/**
 * Mount sigma onto a host element and wire its events (7214-7247).
 *
 * Returns a dispose function. The vanilla instead guards the pointer listener
 * with a `_mouseBound` flag on the singleton, because its host element is a
 * static piece of index.html that is bound once and lives forever. React
 * recreates the host on every mount, so that flag would bind the FIRST host and
 * then skip every later one — the tooltip would stop following the pointer after
 * one close and reopen, with nothing in the console. Disposing is the port's
 * answer, and the flag is deliberately not carried over.
 */
export function webMountSigma(
  host: HTMLElement,
  graph: WebGraph,
  handlers: WebSigmaHandlers,
): () => void {
  host.innerHTML = '';
  host.style.background = WEB_CANVAS_BG; // dark charcoal, so the cluster colours glow
  webKillFX();
  webKillSigma();

  const Sigma = globals().Sigma;
  if (!Sigma) return () => {};

  artistWeb.graph = graph;
  const sigma = new Sigma(graph, host, {
    ...WEB_SIGMA_SETTINGS,
    labelRenderer: webDrawLabel,
    nodeReducer: (node: string, data: Record<string, unknown>) => artWebNodeReducer(node, data),
    edgeReducer: (edge: string, data: Record<string, unknown>) =>
      artWebEdgeReducer(edge, data, artistWeb.graph),
  });
  artistWeb.sigma = sigma;

  const onMouseMove = (e: MouseEvent) => {
    artistWeb._mouse = { x: e.clientX, y: e.clientY };
    if (artistWeb._hoverNode) handlers.onTooltipMove(artistWeb._hoverNode);
  };
  host.addEventListener('mousemove', onMouseMove);

  sigma.on('enterNode', ({ node }) => handlers.onHover(node));
  sigma.on('leaveNode', () => handlers.onHover(null));
  sigma.on('clickNode', ({ node }) => handlers.onClickNode(node));
  sigma.on('clickStage', () => handlers.onClickStage());

  return () => {
    host.removeEventListener('mousemove', onMouseMove);
  };
}

/** Kill the live renderer and drop the reference (7218, 7332). */
export function webKillSigma(): void {
  const sigma = artistWeb.sigma as WebSigmaLike | null;
  if (sigma) {
    try {
      sigma.kill();
    } catch {
      /* ignore — a dead renderer is what we wanted anyway */
    }
    artistWeb.sigma = null;
  }
}

// ── Keyboard ─────────────────────────────────────────────────────────────────

export type WebKeyAction =
  | 'none'
  | 'blur-input'
  | 'exit-path'
  | 'clear-selection'
  | 'close'
  | 'focus-search'
  | 'fit'
  | 'zoom-in'
  | 'zoom-out'
  | 'help';

/**
 * What a keypress means (6659-6681).
 *
 * Escape UNWINDS rather than closing: it leaves path mode if you are in it, then
 * clears a selection if one is showing, and only closes the web when there is
 * nothing left to back out of. Getting that order wrong does not look like a
 * bug — Escape just feels like it skips a step.
 *
 * While typing, Escape blurs the field and every other key is left alone. The
 * shortcuts are single letters, so without that you could not type "safe" into
 * the search box without firing four of them.
 */
export function webKeyAction(
  key: string,
  tagName: string | undefined,
  state: { pathMode: boolean; panelOpen: boolean },
): WebKeyAction {
  if (tagName === 'INPUT' || tagName === 'TEXTAREA') {
    return key === 'Escape' ? 'blur-input' : 'none';
  }
  if (key === 'Escape') {
    if (state.pathMode) return 'exit-path';
    return state.panelOpen ? 'clear-selection' : 'close';
  }
  if (key === 's' || key === 'S') return 'focus-search';
  if (key === 'f' || key === 'F' || key === '0') return 'fit';
  if (key === '+' || key === '=') return 'zoom-in';
  if (key === '-' || key === '_') return 'zoom-out';
  if (key === '?') return 'help';
  return 'none';
}

/**
 * The KEYBOARD's zoom steps (6676-6678).
 *
 * Deliberately gentler than the toolbar buttons' WEB_ZOOM_IN/OUT, because a key
 * repeats when held. Note that a ratio BELOW one is zooming in.
 */
export const WEB_KEY_ZOOM_IN = 0.77;
export const WEB_KEY_ZOOM_OUT = 1.3;

/** What the shortcuts need from the page. */
export interface WebKeyHost {
  pathMode: () => boolean;
  panelOpen: () => boolean;
  exitPath: () => void;
  clearSelection: () => void;
  close: () => void;
  /** Focus the search box; returns whether there was one to focus. */
  focusSearch: () => boolean;
  fitToView: () => void;
  zoom: (ratio: number) => void;
  showHelp: () => void;
}

/**
 * Bind the shortcuts, and return the unbind (6659-6683, 7334).
 *
 * The vanilla removes a stale listener at the top of every open, because its
 * handler lives on the singleton and an open can happen twice. A disposer is the
 * same guarantee in the shape React already enforces.
 *
 * preventDefault is called ONLY for 's', and only when there was a search box to
 * focus (6672) — with no search box on the page, 's' still types an 's'. The
 * host's boolean return is what carries that.
 */
export function attachWebKeys(host: WebKeyHost): () => void {
  const onKey = (e: KeyboardEvent) => {
    const target = e.target as HTMLElement | null;
    const action = webKeyAction(e.key, target?.tagName, {
      pathMode: host.pathMode(),
      panelOpen: host.panelOpen(),
    });
    switch (action) {
      case 'blur-input':
        target?.blur();
        break;
      case 'exit-path':
        host.exitPath();
        break;
      case 'clear-selection':
        host.clearSelection();
        break;
      case 'close':
        host.close();
        break;
      case 'focus-search':
        if (host.focusSearch()) e.preventDefault();
        break;
      case 'fit':
        host.fitToView();
        break;
      case 'zoom-in':
        host.zoom(WEB_KEY_ZOOM_IN);
        break;
      case 'zoom-out':
        host.zoom(WEB_KEY_ZOOM_OUT);
        break;
      case 'help':
        host.showHelp();
        break;
      default:
        break;
    }
  };
  document.addEventListener('keydown', onKey);
  return () => document.removeEventListener('keydown', onKey);
}

/**
 * Everything the canvas owns, released (7329-7333, and 6717-6721 for the state
 * card, which replaces the canvas with a message and so must free it too).
 *
 * `graph` is nulled last and on purpose: a late async re-select — an expand that
 * resolves after a close — would otherwise refresh a dead graph.
 */
export function webTeardown(): void {
  webKillLiveLayout();
  webKillFX();
  artistWeb.spreadRoot = null;
  artistWeb.spreadSet = null;
  artistWeb.spreadActive = null;
  webKillSigma();
  artistWeb.graph = null;
}
