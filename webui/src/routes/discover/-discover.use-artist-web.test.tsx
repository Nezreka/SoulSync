import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FakeGraph } from '@/test/fake-graph';
import { server } from '@/test/msw';

import { artistWeb } from './-discover.artist-web';
import { useArtistWeb } from './-discover.use-artist-web';

// The REAL payload shape (347-395): genre HUB nodes ride along, and artists
// carry their genre as `cluster` — webGenreColorMap counts by cluster.
const libraryPayload = {
  nodes: [
    { key: 'g-idm', label: 'idm', kind: 'genre', genre: 'idm' },
    { key: 'a1', label: 'Aphex Twin', kind: 'artist', cluster: 'idm', popularity: 80, artistId: 1 },
    { key: 'a2', label: 'Autechre', kind: 'artist', cluster: 'idm', popularity: 70, artistId: 2 },
  ],
  edges: [
    { source: 'g-idm', target: 'a1', kind: 'membership' },
    { source: 'g-idm', target: 'a2', kind: 'membership' },
    { source: 'a1', target: 'a2', kind: 'similarity', weight: 2 },
  ],
};

let libraryHits = 0;
let sigmaInstances: { emit: (event: string, payload: unknown) => void }[] = [];
let discoveryHits = 0;

function stub({
  // The REAL discovery payload kinds (558-600): 'owned' vs 'discovery'.
  discovery = {
    nodes: [
      { key: 'a1', label: 'Aphex Twin', kind: 'owned', popularity: 80, artistId: 1 },
      { key: 'c1', label: 'µ-Ziq', kind: 'discovery', popularity: 60, ids: [['spotify', 's9']] },
    ],
    edges: [{ source: 'a1', target: 'c1', kind: 'similarity', weight: 1 }],
  } as Record<string, unknown>,
}: Record<string, unknown> = {}) {
  libraryHits = 0;
  discoveryHits = 0;
  server.use(
    http.get('/api/graph/library', () => {
      libraryHits += 1;
      return HttpResponse.json(libraryPayload);
    }),
    http.get('/api/graph/discovery', () => {
      discoveryHits += 1;
      return HttpResponse.json(discovery);
    }),
  );
}

function installLibs() {
  const w = window as unknown as Record<string, unknown>;
  w.graphology = FakeGraph;
  w.graphologyLibrary = { layout: {} };
  w.Sigma = class {
    /** Handlers by event name, so a test can emit enterNode/clickNode. */
    handlers: Record<string, (payload: unknown) => void> = {};
    constructor() {
      sigmaInstances.push(this);
    }
    kill() {}
    refresh() {}
    on(event: string, fn: (payload: unknown) => void) {
      this.handlers[event] = fn;
    }
    emit(event: string, payload: unknown) {
      this.handlers[event]?.(payload);
    }
    getCamera() {
      return { animate: () => {}, animatedReset: () => {} };
    }
  };
}

function host(): HTMLElement {
  const el = document.createElement('div');
  document.body.appendChild(el);
  return el;
}

function mount() {
  return renderHook(() => useArtistWeb());
}

beforeEach(() => {
  stub();
  sigmaInstances = [];
  installLibs();
  artistWeb.gen = 0;
  artistWeb.data = null;
  artistWeb.discoveryData = null;
  artistWeb.simGraph = null;
  artistWeb.betweenCache = null;
  artistWeb.graph = null;
  artistWeb.sigma = null;
  artistWeb.lens = 'genre';
  vi.stubGlobal('requestAnimationFrame', () => 1);
  vi.stubGlobal('cancelAnimationFrame', () => {});
});

afterEach(() => {
  const w = window as unknown as Record<string, unknown>;
  delete w.graphology;
  delete w.graphologyLibrary;
  delete w.Sigma;
  vi.unstubAllGlobals();
  server.resetHandlers();
});

describe('useArtistWeb — open', () => {
  it('opens the genre lens: fetch, build, mount, stats + legend, ready', async () => {
    const { result } = mount();
    await act(async () => result.current.openWeb(host()));
    expect(result.current.open).toBe(true);
    expect(result.current.phase).toBe('ready');
    expect(result.current.lens).toBe('genre');
    expect(result.current.stats).toBeTruthy();
    expect(result.current.legend.length).toBeGreaterThan(0);
    expect(artistWeb.graph).not.toBeNull();
    expect(artistWeb.sigma).not.toBeNull();
    // A fresh payload invalidates every derived cache (6699-6702).
    expect(artistWeb.simGraph).toBeNull();
    expect(artistWeb.betweenCache).toBeNull();
  });

  it('REOPENING invalidates the derived caches a session built up', async () => {
    const { result } = mount();
    await act(async () => result.current.openWeb(host()));
    act(() => result.current.setSizeBy('connections'));
    expect(artistWeb.simGraph).not.toBeNull();
    // Ownership may have changed server-side — a fresh payload must not keep
    // yesterday's derived structures (6699-6702).
    await act(async () => result.current.openWeb(host()));
    expect(artistWeb.simGraph).toBeNull();
    expect(artistWeb.betweenCache).toBeNull();
  });

  it('missing CDN libs become the error card, not a crash', async () => {
    const w = window as unknown as Record<string, unknown>;
    delete w.Sigma;
    const { result } = mount();
    await act(async () => result.current.openWeb(host()));
    expect(result.current.phase).toBe('error');
    expect(result.current.errorMessage).toContain("graphology / sigma didn't load");
    expect(libraryHits).toBe(0);
  });

  it('a payload landing after CLOSE is dropped (the generation counter)', async () => {
    const { result } = mount();
    let p!: Promise<void>;
    act(() => {
      p = result.current.openWeb(host());
    });
    act(() => result.current.close());
    await act(async () => {
      await p;
    });
    expect(result.current.open).toBe(false);
    expect(artistWeb.graph).toBeNull();
  });

  it('the hub deep-links a lens; an invalid one falls back', async () => {
    const { result } = mount();
    await act(async () => result.current.openWeb(host(), 'community'));
    expect(result.current.lens).toBe('community');
    await act(async () => result.current.openWeb(host(), 'nope' as never));
    expect(result.current.lens).toBe('community'); // keeps the current lens
  });
});

describe('useArtistWeb — lenses', () => {
  it('discovery fetches its own payload ONCE, then a switch reuses it', async () => {
    const { result } = mount();
    await act(async () => result.current.openWeb(host()));
    await act(async () => result.current.setLens(host(), 'discovery'));
    expect(discoveryHits).toBe(1);
    expect(result.current.phase).toBe('ready');
    // Discovery legend is the fixed two-entry key.
    expect(result.current.legend.map((l) => l.label)).toEqual(['Your library', 'To discover']);
    await act(async () => result.current.setLens(host(), 'genre'));
    await act(async () => result.current.setLens(host(), 'discovery'));
    expect(discoveryHits).toBe(1);
  });

  it('an EMPTY discovery guides instead of a blank canvas — after teardown', async () => {
    stub({ discovery: { nodes: [], edges: [] } });
    const { result } = mount();
    // Mount the GENRE lens first so a live renderer exists — reaching the
    // state card must kill it (6755-6760), or the WebGL context leaks.
    await act(async () => result.current.openWeb(host()));
    expect(artistWeb.sigma).not.toBeNull();
    await act(async () => result.current.setLens(host(), 'discovery'));
    expect(result.current.phase).toBe('error');
    expect(result.current.errorMessage).toContain('No discovery candidates yet');
    expect(artistWeb.sigma).toBeNull(); // no leaked renderer (6755-6760)
  });
});

describe('useArtistWeb — selection and toggles', () => {
  const detail = (id: unknown, source: string) => `/artist/${String(id)}?source=${source}`;

  it('clicking an artist node builds the ARTIST card', async () => {
    const { result } = mount();
    await act(async () => result.current.openWeb(host()));
    act(() => result.current.clickNode('a1', detail));
    expect(result.current.selection?.kind).toBe('artist');
    expect((result.current.selection?.card as { label: string }).label).toBe('Aphex Twin');
    expect(artistWeb.selectedKey).toBe('a1');
    act(() => result.current.clearSelection());
    expect(result.current.selection).toBeNull();
    expect(artistWeb.selectedKey).toBeNull();
  });

  it('an unowned node on the DISCOVERY lens builds the discovery card', async () => {
    const { result } = mount();
    await act(async () => result.current.openWeb(host(), 'discovery'));
    act(() => result.current.clickNode('c1', detail));
    expect(result.current.selection?.kind).toBe('discovery');
    expect((result.current.selection?.card as { label: string }).label).toBe('µ-Ziq');
    // An OWNED node on the same lens gets the ARTIST card, never the
    // add-to-watchlist pitch.
    act(() => result.current.clickNode('a1', detail));
    expect(result.current.selection?.kind).toBe('artist');
  });

  it('size-by lazily builds the similarity graph and betweenness cache', async () => {
    const { result } = mount();
    await act(async () => result.current.openWeb(host()));
    expect(artistWeb.simGraph).toBeNull();
    act(() => result.current.setSizeBy('connections'));
    expect(result.current.sizeBy).toBe('connections');
    expect(artistWeb.simGraph).not.toBeNull();
    act(() => result.current.setSizeBy('influence'));
    expect(artistWeb.betweenCache).not.toBeNull();
  });

  it('edge declutter flips the singleton flag the reducers read', async () => {
    const { result } = mount();
    await act(async () => result.current.openWeb(host()));
    expect(result.current.edgeDeclutter).toBe(false);
    act(() => result.current.toggleEdges());
    expect(artistWeb.edgeDeclutter).toBe(true);
    expect(result.current.edgeDeclutter).toBe(true);
  });
});

describe('useArtistWeb — the ui seam', () => {
  it("delegates sigma's events to the CURRENT ui handlers, keeping the base hover", async () => {
    const hovered: (string | null)[] = [];
    const clicked: string[] = [];
    const { result } = renderHook(() =>
      useArtistWeb({
        onHover: (n) => hovered.push(n),
        onClickNode: (n) => clicked.push(n),
      }),
    );
    await act(async () => result.current.openWeb(host()));
    const sigma = sigmaInstances[0];
    act(() => sigma.emit('enterNode', { node: 'a1' }));
    act(() => sigma.emit('clickNode', { node: 'a1' }));
    act(() => sigma.emit('leaveNode', {}));
    expect(hovered).toEqual(['a1', null]);
    expect(clicked).toEqual(['a1']);
    // The base behaviour survives delegation: the singleton tracked the hover.
    expect(artistWeb._hoverNode).toBeNull();
  });

  it('mounting with NO ui registered stays inert instead of crashing', async () => {
    const { result } = mount();
    await act(async () => result.current.openWeb(host()));
    const sigma = sigmaInstances[0];
    act(() => sigma.emit('enterNode', { node: 'a1' }));
    expect(artistWeb._hoverNode).toBe('a1');
    act(() => sigma.emit('clickNode', { node: 'a1' }));
    expect(result.current.selection).toBeNull();
  });
});

describe('useArtistWeb — recountStats', () => {
  it('re-tallies owned/discovery from the LIVE graph, the post-expand line (8189)', async () => {
    const { result } = mount();
    await act(async () => result.current.openWeb(host()));
    const g = artistWeb.graph as { addNode: (k: string, a: Record<string, unknown>) => void };
    g.addNode('grown1', { kind: 'discovery', label: 'New' });
    act(() => result.current.recountStats());
    expect(result.current.stats).toMatch(/of your artists · \d+ to discover/);
  });
});
