import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { WebGraph } from './-discover.artist-web';

import { loadVanilla } from '../../test/vanilla-extract';
import { artistWeb } from './-discover.artist-web';
import {
  webKillFX,
  webKillLiveLayout,
  webKillSigma,
  webMountSigma,
  webPreseed,
  webResetCamera,
  webRunLayoutSync,
  webStartFX,
  webStartLiveLayout,
  webTeardown,
  WEB_SETTLE_RESET_MS,
} from './-discover.artist-web.lifecycle';

/**
 * The Artist Web's lifecycle.
 *
 * Everything here starts a worker, allocates a WebGL context or schedules a
 * frame, and every one of those fails SILENTLY when it is wrong: a surviving FA2
 * worker just keeps a core busy, an unkilled sigma just holds a context, a
 * missed `finishLayout` just means selections stop fanning out. None of it
 * throws, so none of it shows up without a test that watches the ORDER of
 * effects — which is why the settle sequence is run against the real vanilla
 * body rather than against expectations I wrote from reading it.
 */

// ── A recorder standing in for every collaborator ────────────────────────────

let log: string[] = [];

/** A graph with just the members the lifecycle touches. */
function fakeGraph(order = 12): WebGraph {
  const nodes: Record<string, { x: number; y: number }> = {};
  for (let i = 0; i < order; i++) nodes[`n${i}`] = { x: i, y: -i };
  return {
    order,
    size: order,
    forEachNode(cb: (k: string, a: Record<string, unknown>) => void) {
      for (const [k, v] of Object.entries(nodes)) cb(k, v as unknown as Record<string, unknown>);
    },
    getNodeAttribute: (k: string, n: string) => (nodes[k] as Record<string, number>)[n],
    setNodeAttribute: (k: string, n: string, v: unknown) => {
      (nodes[k] as Record<string, unknown>)[n] = v;
    },
  } as unknown as WebGraph;
}

interface FakeLibs {
  fa2Throws?: boolean;
  noWorker?: boolean;
  noForceAtlas?: boolean;
  noCirclepack?: boolean;
}

function installGlobals(opts: FakeLibs = {}) {
  class FA2Layout {
    constructor(_g: unknown, cfg: { settings: Record<string, unknown> }) {
      log.push(`fa2.new linLog=${String(cfg.settings.linLogMode)}`);
      if (opts.fa2Throws) throw new Error('no worker in this build');
    }
    start() {
      log.push('fa2.start');
    }
    kill() {
      log.push('fa2.kill');
    }
  }
  const forceAtlas2 = {
    assign: (_g: WebGraph, o: Record<string, unknown>) =>
      log.push(`sync.assign iterations=${String(o.iterations)}`),
    inferSettings: () => ({ inferred: true }),
  };
  const w = window as unknown as Record<string, unknown>;
  w.graphologyLibrary = {
    FA2Layout: opts.noWorker ? undefined : FA2Layout,
    layoutForceAtlas2: opts.noForceAtlas ? undefined : forceAtlas2,
    layout: opts.noCirclepack
      ? {}
      : {
          circlepack: {
            assign: (_g: WebGraph, o: Record<string, unknown>) =>
              log.push(`circlepack ${JSON.stringify(o)}`),
          },
        },
  };
  w.Sigma = class {
    constructor(
      _g: unknown,
      _host: unknown,
      readonly settings: Record<string, unknown>,
    ) {
      log.push('sigma.new');
    }
    kill() {
      log.push('sigma.kill');
    }
    refresh() {
      log.push('sigma.refresh');
    }
    on(event: string, handler: (p: { node: string }) => void) {
      handlers[event] = handler;
    }
    getCamera() {
      return {
        animatedReset: (o: { duration: number }) => log.push(`camera.reset ${o.duration}`),
      };
    }
  };
}

let handlers: Record<string, (p: { node: string }) => void> = {};

beforeEach(() => {
  log = [];
  handlers = {};
  vi.useFakeTimers();
  installGlobals();
  Object.assign(artistWeb, {
    sigma: null,
    graph: null,
    fa2: null,
    fa2Timer: null,
    fxRAF: null,
    home: null,
    cursorFX: true,
    spreadRoot: null,
    spreadSet: null,
    spreadActive: null,
    spreadPush: 0,
    _hoverNode: null,
    _mouse: null,
    _mouseBound: false,
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

// ── The settle sequence, against the real vanilla ────────────────────────────

interface VanillaSettle {
  _artWebStartLiveLayout: (g: unknown) => void;
  _artistWeb: Record<string, unknown>;
}

/**
 * Run the vanilla's settle and return its effects in order.
 *
 * The vanilla writes "· settling…" onto a stats ELEMENT where the port calls a
 * hook; both are normalised to settling:on/off so what is compared is the
 * sequence, not the mechanism.
 */
function vanillaSettle(graph: WebGraph, opts: { detachDuring?: boolean } = {}): string[] {
  const stats = document.createElement('div');
  stats.id = 'artist-web-stats';
  stats.textContent = '900 artists';
  document.body.append(stats);

  const v = loadVanilla<VanillaSettle>(
    ['_artWebStartLiveLayout', '_artWebKillLiveLayout', '_artWebRunLayout', '_artWebFinishLayout'],
    `const _artistWeb = { sigma: null, graph: null, fa2: null, fa2Timer: null, home: null,
        spreadRoot: null, spreadSet: null, spreadPush: 0 };`,
    ['_artistWeb'],
  );
  // The vanilla's finishLayout writes home positions onto its own singleton; the
  // recorder watches the write rather than the function, so no stub is needed.
  v._artistWeb.sigma = {
    refresh: () => log.push('sigma.refresh'),
    getCamera: () => ({
      animatedReset: (o: { duration: number }) => log.push(`camera.reset ${o.duration}`),
    }),
  };
  v._artistWeb.graph = graph;

  const before = log.length;
  const origPush = log.push.bind(log);
  let seenHome = false;
  let settling = false;
  // Neither `finishLayout` nor the stats suffix calls anything we can spy on —
  // they are a field write and a textContent write. So they are detected by
  // WATCHING, and the watch runs BEFORE each recorded effect rather than after:
  // a silent step that happened between two calls belongs in front of the second
  // one, not behind it. Checking afterwards reports it one slot late, which
  // reads exactly like a real ordering difference.
  const watch = () => {
    if (v._artistWeb.home && !seenHome) {
      seenHome = true;
      origPush('finishLayout');
    }
    if (stats.textContent!.includes('settling') !== settling) {
      settling = !settling;
      origPush(settling ? 'settling:on' : 'settling:off');
    }
  };
  log.push = ((...args: string[]) => {
    watch();
    return origPush(...args);
  }) as typeof log.push;

  v._artWebStartLiveLayout(graph);
  watch();
  if (opts.detachDuring) v._artistWeb.graph = fakeGraph(3);
  vi.advanceTimersByTime(20000);
  watch();
  log.push = origPush;
  stats.remove();
  return log.slice(before);
}

/** The same run through the port. */
function portSettle(graph: WebGraph, opts: { detachDuring?: boolean } = {}): string[] {
  artistWeb.sigma = {
    kill: () => log.push('sigma.kill'),
    refresh: () => log.push('sigma.refresh'),
    on: () => {},
    getCamera: () => ({
      animatedReset: (o: { duration: number }) => log.push(`camera.reset ${o.duration}`),
    }),
  };
  artistWeb.graph = graph;
  const before = log.length;
  let seenHome = false;
  const watch = () => {
    if (artistWeb.home && !seenHome) {
      seenHome = true;
      log.push('finishLayout');
    }
  };
  const origPush = log.push.bind(log);
  webStartLiveLayout(graph, {
    setSettling: (on) => {
      watch();
      origPush(on ? 'settling:on' : 'settling:off');
    },
    onSettled: () => {
      watch();
      webResetCamera();
    },
  });
  watch();
  if (opts.detachDuring) artistWeb.graph = fakeGraph(3);
  vi.advanceTimersByTime(20000);
  watch();
  return log.slice(before);
}

describe('the settle sequence matches the vanilla', () => {
  it('with a worker: start, settle, finalize, fit, trailing refresh', () => {
    const mine = portSettle(fakeGraph(40));
    log = [];
    const theirs = vanillaSettle(fakeGraph(40));
    expect(mine).toEqual(theirs);
    // Named, so a shared drift in both sides cannot pass as agreement.
    expect(mine).toEqual([
      'fa2.new linLog=true',
      'fa2.start',
      'settling:on',
      'fa2.kill',
      'finishLayout',
      'settling:off',
      'camera.reset 500',
      'sigma.refresh',
    ]);
  });

  it('finalizes even when the worker build is missing', () => {
    installGlobals({ noWorker: true });
    const mine = portSettle(fakeGraph(40));
    log = [];
    installGlobals({ noWorker: true });
    const theirs = vanillaSettle(fakeGraph(40));
    expect(mine).toEqual(theirs);
    // The point: the sync fallback STILL captures home positions. Skipping that
    // leaves selections silently inert — nothing throws, nothing logs.
    expect(mine).toEqual(['sync.assign iterations=800', 'finishLayout']);
  });

  it('falls back when the supervisor constructor throws', () => {
    installGlobals({ fa2Throws: true });
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const mine = portSettle(fakeGraph(40));
    log = [];
    installGlobals({ fa2Throws: true });
    const theirs = vanillaSettle(fakeGraph(40));
    expect(mine).toEqual(theirs);
    expect(mine).toEqual(['fa2.new linLog=true', 'sync.assign iterations=800', 'finishLayout']);
  });

  it('takes the sync path for an empty graph, where a worker would never settle', () => {
    const mine = portSettle(fakeGraph(0));
    log = [];
    const theirs = vanillaSettle(fakeGraph(0));
    expect(mine).toEqual(theirs);
    expect(mine).toEqual(['sync.assign iterations=800', 'finishLayout']);
  });

  it('does not move the camera when the lens changed mid-settle', () => {
    const mine = portSettle(fakeGraph(40), { detachDuring: true });
    log = [];
    const theirs = vanillaSettle(fakeGraph(40), { detachDuring: true });
    expect(mine).toEqual(theirs);
    // Finalizing still happens — the graph object is passed in and is still the
    // right one to record. Framing does not: the camera belongs to what is now
    // on screen, which is a different graph.
    expect(mine).toEqual([
      'fa2.new linLog=true',
      'fa2.start',
      'settling:on',
      'fa2.kill',
      'finishLayout',
      'settling:off',
    ]);
  });

  it('replaces a settle already in flight, rather than running two', () => {
    // A lens switch. The previous supervisor keeps streaming positions into the
    // graph it was given, and its timer would finalize that now-detached graph —
    // so starting a new settle has to stop the old one first.
    artistWeb.sigma = { kill: () => {}, refresh: () => {} }; //  finalizing needs a live renderer
    const first = fakeGraph(40);
    artistWeb.graph = first;
    webStartLiveLayout(first, {
      setSettling: () => {},
      onSettled: () => log.push('settled:first'),
    });
    log = [];
    const second = fakeGraph(60);
    artistWeb.graph = second;
    webStartLiveLayout(second, {
      setSettling: () => {},
      onSettled: () => log.push('settled:second'),
    });
    expect(log).toEqual(['fa2.kill', 'fa2.new linLog=true', 'fa2.start']);

    log = [];
    vi.advanceTimersByTime(20000);
    // Exactly ONE finalization, and it is the SECOND graph's. The first timer
    // must be gone, not merely outlived — two would fight over the camera, and
    // the loser would frame a graph nothing is rendering.
    expect(log.filter((e) => e === 'settled:second')).toHaveLength(1);
    expect(log).not.toContain('settled:first');
    expect(log.filter((e) => e === 'fa2.kill')).toHaveLength(1);
  });

  it('scales the budget with graph size', () => {
    webStartLiveLayout(fakeGraph(1000), { setSettling: () => {}, onSettled: () => {} });
    log = [];
    vi.advanceTimersByTime(3199); //  1600 + 1000 * 1.6 = 3200
    expect(log).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(log).toContain('fa2.kill');
  });
});

// ── Stopping things ──────────────────────────────────────────────────────────

describe('webKillLiveLayout', () => {
  it('clears the timer AND kills the worker', () => {
    webStartLiveLayout(fakeGraph(40), { setSettling: () => {}, onSettled: () => {} });
    log = [];
    webKillLiveLayout();
    expect(log).toEqual(['fa2.kill']);
    expect(artistWeb.fa2).toBeNull();
    expect(artistWeb.fa2Timer).toBeNull();
    // The timer must be gone, not merely orphaned: it would finalize a graph the
    // page has since replaced.
    vi.advanceTimersByTime(20000);
    expect(log).toEqual(['fa2.kill']);
  });

  it('still clears the timer when the worker throws on kill', () => {
    artistWeb.fa2Timer = setTimeout(() => log.push('timer fired'), 50);
    artistWeb.fa2 = {
      kill: () => {
        throw new Error('already dead');
      },
    };
    expect(() => webKillLiveLayout()).not.toThrow();
    expect(artistWeb.fa2Timer).toBeNull();
    vi.advanceTimersByTime(500);
    expect(log).toEqual([]);
  });
});

describe('webKillSigma', () => {
  it('kills the renderer and drops the reference', () => {
    artistWeb.sigma = { kill: () => log.push('sigma.kill') };
    webKillSigma();
    expect(log).toEqual(['sigma.kill']);
    expect(artistWeb.sigma).toBeNull();
  });

  it('drops the reference even when kill throws', () => {
    artistWeb.sigma = {
      kill: () => {
        throw new Error('context lost');
      },
    };
    expect(() => webKillSigma()).not.toThrow();
    expect(artistWeb.sigma).toBeNull();
  });

  it('is a no-op with nothing mounted', () => {
    webKillSigma();
    expect(log).toEqual([]);
  });
});

describe('webTeardown', () => {
  it('releases the worker, the frame loop and the renderer', () => {
    const raf = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    webStartLiveLayout(fakeGraph(40), { setSettling: () => {}, onSettled: () => {} });
    artistWeb.sigma = { kill: () => log.push('sigma.kill') };
    artistWeb.graph = fakeGraph(40); //  a graph really is mounted, so nulling it means something
    artistWeb.fxRAF = 77;
    artistWeb.spreadRoot = 'n1';
    artistWeb.spreadSet = new Set(['n2']);
    artistWeb.spreadActive = new Set(['n2']);
    log = [];

    webTeardown();

    expect(log).toEqual(['fa2.kill', 'sigma.kill']);
    expect(raf).toHaveBeenCalledWith(77);
    expect(artistWeb.fxRAF).toBeNull();
    expect(artistWeb.sigma).toBeNull();
    // Nulled last and on purpose: a late expand resolving after a close would
    // otherwise refresh a graph nothing is rendering.
    expect(artistWeb.graph).toBeNull();
    expect([artistWeb.spreadRoot, artistWeb.spreadSet, artistWeb.spreadActive]).toEqual([
      null,
      null,
      null,
    ]);
  });
});

// ── The spread frame loop ────────────────────────────────────────────────────

describe('the spread loop', () => {
  function drive(frames: number) {
    const queue: FrameRequestCallback[] = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      queue.push(cb);
      return queue.length;
    });
    webStartFX();
    for (let i = 0; i < frames && queue.length; i++) queue.shift()!(i * 16);
    return queue;
  }

  beforeEach(() => {
    const graph = fakeGraph(3);
    artistWeb.graph = graph;
    artistWeb.sigma = { refresh: () => log.push('sigma.refresh') };
    artistWeb.home = { n0: { x: 0, y: 0 }, n1: { x: 10, y: 0 }, n2: { x: 0, y: 10 } };
    artistWeb.spreadPush = 5;
  });

  it('runs while nodes are moving and stops once they arrive', () => {
    artistWeb.spreadRoot = 'n0';
    artistWeb.spreadSet = new Set(['n1']);
    const queue = drive(1);
    expect(log).toEqual(['sigma.refresh']);
    expect(queue.length).toBe(1); //  it asked for another frame
  });

  it('does not schedule a second frame when nothing moved', () => {
    // No spread and every node already home: the tick reports no movement, so
    // the loop must stop rather than burn a frame forever.
    artistWeb.spreadRoot = null;
    artistWeb.spreadSet = null;
    artistWeb.spreadActive = new Set();
    const queue = drive(1);
    expect(log).toEqual([]);
    expect(queue.length).toBe(0);
  });

  it('will not stack loops — a second start while one is pending is a no-op', () => {
    const raf = vi.spyOn(window, 'requestAnimationFrame').mockReturnValue(9);
    webStartFX();
    webStartFX();
    webStartFX();
    expect(raf).toHaveBeenCalledTimes(1);
  });

  it('bails without a renderer, so a frame in flight past a close does nothing', () => {
    artistWeb.spreadRoot = 'n0';
    artistWeb.spreadSet = new Set(['n1']);
    artistWeb.sigma = null;
    const queue = drive(1);
    expect(log).toEqual([]);
    expect(queue.length).toBe(0);
  });

  it('respects the cursorFX switch', () => {
    artistWeb.spreadRoot = 'n0';
    artistWeb.spreadSet = new Set(['n1']);
    artistWeb.cursorFX = false;
    drive(1);
    expect(log).toEqual([]);
  });

  it('webKillFX cancels a pending frame', () => {
    const cancel = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    artistWeb.fxRAF = 42;
    webKillFX();
    expect(cancel).toHaveBeenCalledWith(42);
    expect(artistWeb.fxRAF).toBeNull();
  });
});

// ── Mounting ─────────────────────────────────────────────────────────────────

describe('webMountSigma', () => {
  function host() {
    const el = document.createElement('div');
    el.innerHTML = '<div>Building your artist web…</div>';
    document.body.append(el);
    return el;
  }

  const noopHandlers = () => ({
    onHover: vi.fn(),
    onClickNode: vi.fn(),
    onClickStage: vi.fn(),
    onTooltipMove: vi.fn(),
  });

  it('clears the placeholder and paints the canvas background', () => {
    const el = host();
    webMountSigma(el, fakeGraph(), noopHandlers());
    expect(el.querySelector('div')).toBeNull();
    expect(el.style.background).toBe('rgb(17, 16, 22)'); //  #111016, as jsdom reports it
  });

  it('kills the previous renderer before allocating another', () => {
    const el = host();
    artistWeb.sigma = { kill: () => log.push('sigma.kill') };
    log = [];
    webMountSigma(el, fakeGraph(), noopHandlers());
    // Order matters: two live WebGL contexts on one page is how the browser
    // starts dropping other pages' contexts.
    expect(log).toEqual(['sigma.kill', 'sigma.new']);
  });

  it('relays sigma events to the page', () => {
    const h = noopHandlers();
    webMountSigma(host(), fakeGraph(), h);
    handlers.enterNode({ node: 'n1' });
    expect(h.onHover).toHaveBeenCalledWith('n1');
    handlers.leaveNode({ node: 'n1' });
    expect(h.onHover).toHaveBeenLastCalledWith(null); //  leaving collapses to null
    handlers.clickNode({ node: 'n2' });
    expect(h.onClickNode).toHaveBeenCalledWith('n2');
    handlers.clickStage({ node: '' });
    expect(h.onClickStage).toHaveBeenCalled();
  });

  it('tracks the pointer, but only reports it while a node is hovered', () => {
    const el = host();
    const h = noopHandlers();
    webMountSigma(el, fakeGraph(), h);

    el.dispatchEvent(new MouseEvent('mousemove', { clientX: 40, clientY: 90 }));
    expect(artistWeb._mouse).toEqual({ x: 40, y: 90 });
    expect(h.onTooltipMove).not.toHaveBeenCalled();

    artistWeb._hoverNode = 'n3';
    el.dispatchEvent(new MouseEvent('mousemove', { clientX: 41, clientY: 91 }));
    expect(h.onTooltipMove).toHaveBeenCalledWith('n3');
    expect(artistWeb._mouse).toEqual({ x: 41, y: 91 });
  });

  it('disposes the pointer listener, and does not stack across a remount', () => {
    // The vanilla guards this with a flag on the singleton because ITS host is a
    // static element bound once. React builds a new host per mount, so that flag
    // would bind the first host and skip every later one — the tooltip would
    // silently stop following the pointer after one close and reopen.
    const h = noopHandlers();
    const first = host();
    const dispose = webMountSigma(first, fakeGraph(), h);
    dispose();
    artistWeb._hoverNode = 'n3';
    first.dispatchEvent(new MouseEvent('mousemove', { clientX: 1, clientY: 1 }));
    expect(h.onTooltipMove).not.toHaveBeenCalled();

    const second = host();
    webMountSigma(second, fakeGraph(), h);
    second.dispatchEvent(new MouseEvent('mousemove', { clientX: 2, clientY: 2 }));
    expect(h.onTooltipMove).toHaveBeenCalledTimes(1);
  });

  it('records the graph so the reducers and teardown can find it', () => {
    const graph = fakeGraph();
    webMountSigma(host(), graph, noopHandlers());
    expect(artistWeb.graph).toBe(graph);
    expect(artistWeb.sigma).not.toBeNull();
  });

  it('degrades to nothing when sigma never loaded', () => {
    (window as unknown as Record<string, unknown>).Sigma = undefined;
    const el = host();
    expect(() => webMountSigma(el, fakeGraph(), noopHandlers())()).not.toThrow();
    expect(artistWeb.sigma).toBeNull();
  });
});

// ── The pre-seed ─────────────────────────────────────────────────────────────

describe('webPreseed', () => {
  it('packs by genre for the clustered lenses', () => {
    webPreseed(fakeGraph(), 'genre');
    expect(log).toEqual(['circlepack {"hierarchyAttributes":["genre"]}']);
  });

  it('packs flat for discovery, which has no grouping worth packing by', () => {
    webPreseed(fakeGraph(), 'discovery');
    expect(log).toEqual(['circlepack {}']);
  });

  it('is a silent no-op without the helper — the builders set random positions', () => {
    installGlobals({ noCirclepack: true });
    expect(() => webPreseed(fakeGraph(), 'genre')).not.toThrow();
    expect(log).toEqual([]);
  });

  it('skips an empty graph', () => {
    webPreseed(fakeGraph(0), 'genre');
    expect(log).toEqual([]);
  });

  it('swallows a throwing pack rather than losing the render', () => {
    (window as unknown as Record<string, unknown>).graphologyLibrary = {
      layout: {
        circlepack: {
          assign: () => {
            throw new Error('bad hierarchy attribute');
          },
        },
      },
    };
    expect(() => webPreseed(fakeGraph(), 'genre')).not.toThrow();
  });
});

// ── Framing ──────────────────────────────────────────────────────────────────

describe('webResetCamera', () => {
  it('fits the web, then forces the frame hideEdgesOnMove skipped', () => {
    const graph = fakeGraph();
    artistWeb.graph = graph;
    artistWeb.sigma = {
      refresh: () => log.push('sigma.refresh'),
      getCamera: () => ({
        animatedReset: (o: { duration: number }) => log.push(`camera.reset ${o.duration}`),
      }),
    };
    webResetCamera();
    expect(log).toEqual(['camera.reset 500']);
    vi.advanceTimersByTime(649);
    expect(log).toEqual(['camera.reset 500']);
    vi.advanceTimersByTime(1);
    // Without this the settled web sits there edge-less until the user touches
    // it: nothing re-renders after a camera animation stops.
    expect(log).toEqual(['camera.reset 500', 'sigma.refresh']);
  });

  it('does not refresh a renderer that was replaced while the camera moved', () => {
    artistWeb.graph = fakeGraph();
    artistWeb.sigma = {
      refresh: () => log.push('sigma.refresh'),
      getCamera: () => ({ animatedReset: () => log.push('camera.reset') }),
    };
    webResetCamera();
    artistWeb.graph = fakeGraph(3); //  a lens switch landed mid-animation
    vi.advanceTimersByTime(1000);
    expect(log).toEqual(['camera.reset']);
  });

  it('is a no-op with nothing mounted', () => {
    artistWeb.sigma = null;
    expect(() => webResetCamera()).not.toThrow();
    expect(log).toEqual([]);
  });

  it('animates for the duration the vanilla used', () => {
    expect(WEB_SETTLE_RESET_MS).toBe(500);
  });
});

// ── The synchronous fallback ─────────────────────────────────────────────────

describe('webRunLayoutSync', () => {
  it('runs the fixed iteration count with the shared settings', () => {
    webRunLayoutSync(fakeGraph());
    expect(log).toEqual(['sync.assign iterations=800']);
  });

  it('warns and leaves the random positions when forceAtlas2 is missing', () => {
    installGlobals({ noForceAtlas: true });
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(() => webRunLayoutSync(fakeGraph())).not.toThrow();
    expect(warn).toHaveBeenCalled();
    expect(log).toEqual([]);
  });
});
