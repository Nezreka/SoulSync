import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';

import { artMap } from './-discover.artist-map';
import {
  ARTMAP_EXPLORE_FAIL_MS,
  ARTMAP_LOADING,
  ARTMAP_SEARCH_EMPTY,
  ARTMAP_SEARCH_FAILED,
  ARTMAP_SEARCH_URL,
  ARTMAP_SEARCHING,
} from './-discover.artist-map.entry';
import {
  ARTMAP_DRAG_SLOP,
  ARTMAP_TAP_SLOP,
  artMapTouchToWorld,
} from './-discover.artist-map.interaction';
import {
  ARTMAP_LIVE_CAP,
  ARTMAP_PERF_URL,
  ARTMAP_REVEAL,
  ARTMAP_REVEAL_CAP,
} from './-discover.artist-map.render';
import {
  type WebGraph,
  WEB_SPREAD_EASE,
  WEB_SPREAD_EPSILON,
  artWebNodeSize,
  artistWeb,
} from './-discover.artist-web';
import {
  WEB_BUILDING,
  WEB_DISCOVERY_FAILED,
  WEB_EXPAND_URL,
  WEB_FIRST_RUN_HINT,
  WEB_FIRST_RUN_KEY,
  WEB_HINT_FADE_MS,
  WEB_HINT_MS,
  WEB_LOAD_FAILED,
  WEB_NO_LIBS,
  WEB_PATH_NOT_ARTISTS,
  WEB_PATH_PROMPT,
  WEB_PREVIEW_URL,
  WEB_REBUILDING,
  WEB_SEARCH_LIMIT,
  WEB_SETTLE_REFRESH_MS,
  WEB_THUMB_URL,
  WEB_TIP_THUMB_MS,
  webBetweenness,
  webComputePath,
  webDiscoveryStats,
  webLouvain,
} from './-discover.artist-web.controller';
import {
  WEB_HELP_SECTIONS,
  WEB_PREVIEW_LOADING,
  WEB_PREVIEW_NONE,
  WEB_PREVIEW_UNAVAILABLE,
  WEB_PREVIEW_VOLUME,
} from './-discover.artist-web.panel';

/**
 * The exports the phase's own suites never named.
 *
 * Found by a mechanical sweep of every export in the visualisation modules
 * against the text of every test — the same check that turned up 40 un-ported
 * functions earlier in this migration. Forty-four came back, and while most are
 * copy and timings, several were FUNCTIONS written and never verified. Untested
 * copy ships typos silently; untested functions ship anything.
 *
 * Constants are pinned against the vanilla text: comparing an independent oracle
 * against the whole constant is a real check, because a mutation makes the
 * lookup fail while discover.js still holds the true string.
 */
const SOURCE = readFileSync(resolve(process.cwd(), 'static/discover.js'), 'utf8');

describe('copy and endpoints still match discover.js', () => {
  it('keeps the map’s loading and search strings', () => {
    expect(SOURCE).toContain(ARTMAP_LOADING);
    expect(SOURCE).toContain(ARTMAP_SEARCH_EMPTY);
    expect(SOURCE).toContain(ARTMAP_SEARCH_FAILED);
    expect(SOURCE).toContain(ARTMAP_SEARCHING);
    expect(SOURCE).toContain(ARTMAP_SEARCH_URL);
    expect(SOURCE).toContain(ARTMAP_PERF_URL);
  });

  it('keeps the web’s state-card copy', () => {
    expect(SOURCE).toContain(WEB_BUILDING);
    expect(SOURCE).toContain(WEB_REBUILDING);
    expect(SOURCE).toContain(WEB_LOAD_FAILED);
    expect(SOURCE).toContain(WEB_DISCOVERY_FAILED);
    expect(SOURCE).toContain(WEB_PATH_PROMPT);
    expect(SOURCE).toContain(WEB_PATH_NOT_ARTISTS);
    expect(SOURCE).toContain(WEB_FIRST_RUN_HINT);
    expect(SOURCE).toContain(WEB_FIRST_RUN_KEY);
  });

  it('keeps the CDN-missing message, entities and all', () => {
    // It is written into innerHTML, so the escaped angle brackets are part of
    // the string rather than an accident.
    expect(WEB_NO_LIBS).toContain('&lt;script&gt;');
    // The vanilla holds it in a SINGLE-quoted literal, so the apostrophe is
    // backslash-escaped in the file and a whole-string lookup misses. Compare
    // the two halves around it instead of weakening the assertion.
    const [before, after] = WEB_NO_LIBS.split("didn't");
    expect(SOURCE).toContain(before + "didn\\'t" + after);
  });

  it('keeps the web’s endpoints', () => {
    expect(SOURCE).toContain(WEB_EXPAND_URL);
    expect(SOURCE).toContain(WEB_PREVIEW_URL);
    expect(SOURCE).toContain(WEB_THUMB_URL);
  });

  it('keeps the preview button’s four labels', () => {
    expect(SOURCE).toContain(WEB_PREVIEW_LOADING);
    expect(SOURCE).toContain(WEB_PREVIEW_NONE);
    expect(SOURCE).toContain(WEB_PREVIEW_UNAVAILABLE);
  });

  it('keeps the help modal’s three section headings', () => {
    expect(WEB_HELP_SECTIONS.map((s) => s.heading)).toEqual(['Three lenses', 'Explore', 'Tools']);
    for (const s of WEB_HELP_SECTIONS) expect(SOURCE).toContain(`<h4>${s.heading}</h4>`);
  });
});

describe('timings and caps still match discover.js', () => {
  it('keeps the map’s tuning', () => {
    expect(ARTMAP_EXPLORE_FAIL_MS).toBe(2500);
    expect(ARTMAP_DRAG_SLOP).toBe(5);
    expect(ARTMAP_TAP_SLOP).toBe(8);
    expect(ARTMAP_LIVE_CAP).toBe(600);
    expect(ARTMAP_REVEAL_CAP).toBe(2200);
    expect(SOURCE).toContain('}, 2500);');
    expect(SOURCE).toContain('const CAP = revealing ? 2200 : 600;');
  });

  it('keeps the reveal’s three stagger values', () => {
    expect(ARTMAP_REVEAL).toEqual({ ISL_STAGGER: 145, RADIAL_MS: 430, NODE_DUR: 470 });
    expect(SOURCE).toContain('ISL_STAGGER = 145, RADIAL_MS = 430, NODE_DUR = 470');
  });

  it('keeps the web’s tuning', () => {
    expect(WEB_HINT_MS).toBe(8000);
    expect(WEB_HINT_FADE_MS).toBe(400);
    expect(WEB_TIP_THUMB_MS).toBe(140);
    expect(WEB_SETTLE_REFRESH_MS).toBe(650);
    expect(WEB_SEARCH_LIMIT).toBe(8);
    expect(WEB_PREVIEW_VOLUME).toBe(0.9);
    expect(WEB_SPREAD_EASE).toBe(0.18);
    expect(WEB_SPREAD_EPSILON).toBe(0.0005);
    expect(SOURCE).toContain('}, 8000);');
    expect(SOURCE).toContain('}, 140);');
    expect(SOURCE).toContain('}, 650);');
    expect(SOURCE).toContain('audio.volume = 0.9;');
    expect(SOURCE).toContain('EASE = 0.18');
  });
});

describe('artMapTouchToWorld', () => {
  beforeEach(() => {
    artMap.offsetX = 700;
    artMap.offsetY = 400;
    artMap.zoom = 1;
  });

  it('inverts the pan/zoom transform, offset by the canvas rect', () => {
    expect(artMapTouchToWorld({ clientX: 700, clientY: 400 }, { left: 0, top: 0 })).toEqual({
      wx: 0,
      wy: 0,
    });
    expect(artMapTouchToWorld({ clientX: 720, clientY: 400 }, { left: 0, top: 0 })).toEqual({
      wx: 20,
      wy: 0,
    });
  });

  it('subtracts the rect before the transform, not after', () => {
    // A canvas inset by the toolbar: the same client point is a different world
    // point, and getting the order wrong shifts every tap by the inset.
    expect(artMapTouchToWorld({ clientX: 700, clientY: 456 }, { left: 0, top: 56 })).toEqual({
      wx: 0,
      wy: 0,
    });
  });

  it('divides by zoom', () => {
    artMap.zoom = 0.5;
    expect(artMapTouchToWorld({ clientX: 800, clientY: 400 }, { left: 0, top: 0 })).toEqual({
      wx: 200,
      wy: 0,
    });
  });
});

describe('artWebNodeSize', () => {
  it('runs 2..5.5 across the metric’s range', () => {
    expect(artWebNodeSize(0, 100, false)).toBe(2);
    expect(artWebNodeSize(100, 100, false)).toBe(5.5);
    // Square-rooted, so the middle of the range sits above the midpoint.
    expect(artWebNodeSize(25, 100, false)).toBe(3.75);
  });

  it('never shrinks a star below 6', () => {
    expect(artWebNodeSize(0, 100, true)).toBe(6);
    expect(artWebNodeSize(100, 100, true)).toBe(6);
  });

  it('guards a zero maximum rather than dividing by it', () => {
    expect(Number.isFinite(artWebNodeSize(0, 0, false))).toBe(true);
    expect(artWebNodeSize(0, 0, false)).toBe(2);
  });
});

describe('webDiscoveryStats', () => {
  it('reads as the toolbar shows it', () => {
    expect(webDiscoveryStats(12, 340)).toBe('12 of your artists · 340 to discover');
    expect(webDiscoveryStats(0, 0)).toBe('0 of your artists · 0 to discover');
  });
});

describe('the CDN-backed helpers', () => {
  beforeEach(() => {
    artistWeb.betweenCache = null;
    artistWeb.simGraph = null;
  });

  const graph = {
    hasNode: (k: string) => k === 'a' || k === 'b',
  } as unknown as WebGraph;

  it('finds louvain under its own name, or reports none', () => {
    expect(webLouvain({ graphologyLibrary: { communitiesLouvain: () => ({}) } })).toBeTypeOf(
      'function',
    );
    expect(webLouvain({ graphologyLibrary: {} })).toBeNull();
    expect(webLouvain({})).toBeNull();
  });

  describe('webBetweenness', () => {
    it('accepts either of the two names the bundle uses', () => {
      const scores = { a: 0.5 };
      expect(
        webBetweenness(graph, {
          graphologyLibrary: { metrics: { centrality: { betweenness: () => scores } } },
        }),
      ).toEqual(scores);
      artistWeb.betweenCache = null;
      expect(
        webBetweenness(graph, {
          graphologyLibrary: { metrics: { centrality: { betweennessCentrality: () => scores } } },
        }),
      ).toEqual(scores);
    });

    it('returns an empty map rather than throwing when the metric is missing', () => {
      expect(webBetweenness(graph, {})).toEqual({});
    });

    it('swallows a throw from the metric', () => {
      expect(
        webBetweenness(graph, {
          graphologyLibrary: {
            metrics: {
              centrality: {
                betweenness: () => {
                  throw new Error('too big');
                },
              },
            },
          },
        }),
      ).toEqual({});
    });

    it('caches, so the expensive pass runs once', () => {
      let calls = 0;
      const globals = {
        graphologyLibrary: {
          metrics: {
            centrality: {
              betweenness: () => {
                calls++;
                return { a: 1 };
              },
            },
          },
        },
      };
      webBetweenness(graph, globals);
      webBetweenness(graph, globals);
      expect(calls).toBe(1);
    });

    it('caches an EMPTY result too, so a missing metric is not retried per frame', () => {
      expect(webBetweenness(graph, {})).toEqual({});
      expect(artistWeb.betweenCache).toEqual({});
    });
  });

  describe('webComputePath', () => {
    const sp = { bidirectional: (_g: WebGraph, a: string, b: string) => [a, 'mid', b] };

    it('returns the bidirectional path', () => {
      expect(webComputePath(graph, 'a', 'b', { graphologyLibrary: { shortestPath: sp } })).toEqual([
        'a',
        'mid',
        'b',
      ]);
    });

    it('returns null without the library', () => {
      expect(webComputePath(graph, 'a', 'b', {})).toBeNull();
      expect(
        webComputePath(graph, 'a', 'b', { graphologyLibrary: { shortestPath: {} } }),
      ).toBeNull();
    });

    it('returns null without a graph', () => {
      expect(
        webComputePath(null, 'a', 'b', { graphologyLibrary: { shortestPath: sp } }),
      ).toBeNull();
    });

    it('returns null when either endpoint is not in the similarity graph', () => {
      // An artist with no similarity links at all is absent from that graph, and
      // asking for a path to it must not throw.
      const globals = { graphologyLibrary: { shortestPath: sp } };
      expect(webComputePath(graph, 'a', 'ghost', globals)).toBeNull();
      expect(webComputePath(graph, 'ghost', 'b', globals)).toBeNull();
    });

    it('swallows a throw from the pathfinder', () => {
      expect(
        webComputePath(graph, 'a', 'b', {
          graphologyLibrary: {
            shortestPath: {
              bidirectional: () => {
                throw new Error('nope');
              },
            },
          },
        }),
      ).toBeNull();
    });
  });
});
