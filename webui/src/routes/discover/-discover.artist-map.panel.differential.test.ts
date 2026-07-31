import { beforeEach, describe, expect, it } from 'vitest';

import { loadVanilla } from '../../test/vanilla-extract';
import {
  type ArtMapIsland,
  type ArtMapNode,
  type ArtMapState,
  artMap,
} from './-discover.artist-map';
import {
  ARTMAP_SHORTCUTS,
  ARTMAP_TOP_ARTISTS,
  artMapArtistCard,
  artMapClampIsland,
  artMapContextMenu,
  artMapCoverageGradient,
  artMapIslandMenu,
  artMapIslandNav,
  artMapIslandNavStep,
  artMapPanelModel,
  artMapSheetTransform,
  artMapTooltip,
  artMapTooltipPosition,
  artMapWatchButton,
  miniStat,
} from './-discover.artist-map.panel';

/**
 * Differential parity for the Artist Map's chrome.
 *
 * These vanilla functions do not return anything — they write innerHTML into
 * elements they look up by id. So rather than compare return values, each case
 * builds the REAL container in jsdom, lets the REAL vanilla paint into it, then
 * reads the produced DOM back and checks my model against what it actually
 * rendered. If the vanilla's numbers, ordering, labels or colours change, these
 * fail; a model that merely looks plausible does not pass.
 */
const PREAMBLE = `
const _artMap = {
  placed: [], edges: [], images: {},
  canvas: null, ctx: null, offscreen: null, offCtx: null,
  width: 0, height: 0, offsetX: 0, offsetY: 0, zoom: 0.15,
  hoveredNode: null, animFrame: null, dirty: true,
  WATCHLIST_R: 320, BUFFER: 8, MAX_BUFFER_PX: 4096, LIVE_PX: 12,
  _anim: { running: false, raf: null, last: 0 },
  _fieldAlpha: 1, _revealT0: 0, _panelW: 320,
};
const escapeForInlineJs = (s) => String(s).replace(/'/g, "\\\\'");
function buildArtistDetailPath(id, source) { return '/artist-detail/' + source + '/' + id; }
function showToast() {}
function _artMapCheckWatched() {}
function _artMapEmitRipple() {}
function _artMapFocusIsland() {}
function _artMapRender() {}
function openYourArtistInfoModal_direct() {}
function artMapExploreArtist() {}
`;

interface Vanilla {
  _artMap: ArtMapState;
  _miniStat: (label: string, value: unknown, hue?: number | null) => string;
  _artMapRefreshPanel: () => void;
  _artMapPanelArtist: (node: unknown) => void;
  _artMapWatchBtnHtml: (n: unknown) => string;
  _artMapIsWatched: (n: unknown) => boolean;
  _artMapNodeBest: (n: unknown) => { id: string; source: string };
  _artMapConnCount: (n: unknown) => number;
  _artMapEnsurePanel: () => HTMLElement | null;
  _artMapEnsureStatsFab: () => void;
  _artMapIsMobile: () => boolean;
  _artMapTogglePanelSheet: (open: boolean) => void;
  _artMapClosePanel: () => void;
  _artMapUpdateIslandNav: () => void;
  _artMapToggleIslandMenu: (ev: unknown) => void;
  _artMapIslandNav: (dir: number) => void;
  _artMapShowTooltip: (e: unknown, node: unknown) => void;
  artMapShowShortcuts: () => void;
}

const V = loadVanilla<Vanilla>(
  [
    '_miniStat',
    '_artMapRefreshPanel',
    '_artMapPanelArtist',
    '_artMapWatchBtnHtml',
    '_artMapIsWatched',
    '_artMapNodeBest',
    '_artMapConnCount',
    '_artMapEnsurePanel',
    '_artMapEnsureStatsFab',
    '_artMapIsMobile',
    '_artMapTogglePanelSheet',
    '_artMapClosePanel',
    '_artMapUpdateIslandNav',
    '_artMapToggleIslandMenu',
    '_artMapIslandNav',
    '_artMapShowTooltip',
    'artMapShowShortcuts',
  ],
  PREAMBLE,
  ['_artMap'],
);

/** The markup the map's chrome writes into, as index.html declares it (4365-4421). */
function mountContainer() {
  document.body.innerHTML = `
    <div class="artist-map-container" id="artist-map-container">
      <div class="artist-map-toolbar"></div>
      <div class="artmap-content-row">
        <div class="artmap-genre-sidebar" id="artmap-genre-sidebar" style="display:none;"></div>
        <canvas id="artist-map-canvas"></canvas>
      </div>
      <div class="artist-map-tooltip" id="artist-map-tooltip"></div>
      <div class="artist-map-search-results" id="artist-map-search-results"></div>
    </div>`;
}

function sync(state: Partial<ArtMapState>) {
  const base: Partial<ArtMapState> = {
    placed: [],
    edges: [],
    images: {},
    _islands: undefined,
    _focusIdx: undefined,
    _oneIsland: undefined,
    _mapTitle: undefined,
    _watchSet: undefined,
    _panelArtistId: undefined,
    _panelOpen: undefined,
    _panelW: 320,
    // The tooltip only rebuilds when the hovered node CHANGES (9311), so a stale
    // id from a previous case silently skips the render under test.
    _tipNodeId: undefined,
    ...state,
  };
  for (const k of Object.keys(base)) {
    const v = (base as Record<string, unknown>)[k];
    (artMap as Record<string, unknown>)[k] = v;
    (V._artMap as Record<string, unknown>)[k] = v instanceof Set ? new Set(v) : v;
  }
}

beforeEach(() => {
  mountContainer();
  sync({});
});

const node = (over: Partial<ArtMapNode> = {}): ArtMapNode =>
  ({
    id: 1,
    name: 'Aphex Twin',
    x: 0,
    y: 0,
    radius: 70.4,
    opacity: 1,
    type: 'similar',
    image_url: '',
    popularity: 50,
    genres: [],
    _hue: 200,
    _island: 'Rock',
    ...over,
  }) as ArtMapNode;

// ── Stat tiles ───────────────────────────────────────────────────────────────

describe('miniStat', () => {
  const cases: [string, string | number, number | null | undefined][] = [
    ['Artists', 12, 200],
    ['Watchlist', 0, undefined],
    ['Genres', '1', null],
    ['Popularity', 100, 0], //  hue 0 is RED, not "no hue" — it must not fall back to #fff
    ['Connections', 7, 359],
  ];
  for (const [label, value, hue] of cases) {
    it(`matches the vanilla for ${label}/${value}/hue=${hue}`, () => {
      const html = V._miniStat(label, value, hue);
      const host = document.createElement('div');
      host.innerHTML = html;
      const tile = host.firstElementChild as HTMLElement;
      const valueEl = tile.children[0] as HTMLElement;
      const labelEl = tile.children[1] as HTMLElement;

      const mine = miniStat(label, value, hue);
      expect(valueEl.textContent).toBe(String(mine.value));
      expect(labelEl.textContent).toBe(mine.label);
      expect(tile.innerHTML).toContain(`color:${mine.color}`);
    });
  }

  it('keeps a hue of 0 rather than falling back to white', () => {
    expect(miniStat('x', 1, 0).color).toBe('hsl(0,80%,80%)');
    expect(V._miniStat('x', 1, 0)).toContain('color:hsl(0,80%,80%)');
  });
});

// ── The panel dashboard ──────────────────────────────────────────────────────

describe('artMapPanelModel', () => {
  const placed = (): ArtMapNode[] => [
    node({ id: 'label_Rock', name: 'Rock', _isLabel: true, type: 'genre_label' }),
    node({ id: 1, name: 'Watched One', type: 'watchlist', popularity: 90, _island: 'Rock' }),
    node({ id: 2, name: 'Centre', type: 'center', popularity: 80, _island: 'Rock' }),
    node({ id: 3, name: 'Similar A', type: 'similar', popularity: 70, _island: 'Rock' }),
    node({ id: 4, name: 'Jazz Cat', type: 'similar', popularity: 95, _island: 'Jazz' }),
  ];
  const islands: ArtMapIsland[] = [
    { name: 'Rock', cx: 0, cy: 0, r: 500, hue: 42, count: 900 },
    { name: 'Jazz', cx: 900, cy: 0, r: 200, hue: 180, count: 1 },
  ];

  const cases: [string, Partial<ArtMapState>][] = [
    ['whole-map overview', { placed: placed(), _islands: islands }],
    [
      'one-island mode, first island',
      { placed: placed(), _islands: islands, _oneIsland: true, _focusIdx: 0 },
    ],
    [
      'one-island mode, second island',
      { placed: placed(), _islands: islands, _oneIsland: true, _focusIdx: 1 },
    ],
    [
      'one-island mode with NO islands falls back to the overview',
      { placed: placed(), _oneIsland: true },
    ],
    ['a custom map title', { placed: placed(), _islands: islands, _mapTitle: 'Genre Map' }],
    ['nothing placed at all', {}],
    [
      'an island whose count is 0 — the coverage guard',
      {
        placed: placed(),
        _islands: [{ name: 'Rock', cx: 0, cy: 0, r: 1, hue: 5, count: 0 }],
        _oneIsland: true,
      },
    ],
    // 2 of 3 is 66.67% — the only shape where rounding and truncation disagree.
    // Every other case here lands on a whole percent and hides a floor/round swap.
    [
      'a fractional coverage percentage',
      {
        placed: placed(),
        _islands: [{ name: 'Rock', cx: 0, cy: 0, r: 1, hue: 5, count: 3 }],
        _oneIsland: true,
      },
    ],
    [
      'more than 14 artists in scope',
      {
        placed: Array.from({ length: 30 }, (_, i) =>
          node({ id: i, name: `A${i}`, popularity: i, _island: 'Rock' }),
        ),
        _islands: islands,
      },
    ],
  ];

  for (const [label, state] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      sync(state);
      V._artMapRefreshPanel();
      const head = document.getElementById('artmap-panel-head') as HTMLElement;
      const body = document.getElementById('artmap-panel-body') as HTMLElement;
      const mine = artMapPanelModel();

      // Title + heading
      expect(head.children[0].textContent).toBe(mine.title);
      expect(head.children[1].textContent).toBe(mine.island ? mine.island.name : 'Overview');

      // The three stat tiles, in order
      const tiles = Array.from(head.querySelectorAll<HTMLElement>('[style*="flex:1"]'));
      expect(tiles).toHaveLength(3);
      tiles.forEach((tile, i) => {
        expect(tile.children[0].textContent).toBe(String(mine.stats[i].value));
        expect(tile.children[1].textContent).toBe(mine.stats[i].label);
      });

      // The coverage line + bar
      const bar = head.querySelector<HTMLElement>(
        '[style*="linear-gradient(90deg"]',
      ) as HTMLElement;
      expect(bar.style.width).toBe(`${mine.coveragePct}%`);
      const [from, to] = artMapCoverageGradient(mine.hue);
      expect(bar.getAttribute('style')).toContain(from);
      expect(bar.getAttribute('style')).toContain(to);
      expect(head.textContent).toContain(`${mine.scopeWatch}/${mine.scopeTotal}`);

      // The top-artists list, in order
      const rows = Array.from(
        body.querySelectorAll<HTMLElement>('[onclick^="_artMapPanelArtistById"]'),
      );
      expect(rows.map((r) => r.querySelectorAll('span')[2].textContent)).toEqual(
        mine.topArtists.map((n) => n.name),
      );
    });
  }

  it('lists at most 14 artists, as the vanilla does', () => {
    sync({
      placed: Array.from({ length: 30 }, (_, i) => node({ id: i, name: `A${i}`, popularity: i })),
    });
    V._artMapRefreshPanel();
    const body = document.getElementById('artmap-panel-body') as HTMLElement;
    const rows = body.querySelectorAll('[onclick^="_artMapPanelArtistById"]');
    // LITERAL 14, then the constant pinned separately — asserting against the
    // constant would move with it and let the cap change unnoticed.
    expect(rows).toHaveLength(14);
    expect(artMapPanelModel().topArtists).toHaveLength(14);
    expect(ARTMAP_TOP_ARTISTS).toBe(14);
  });

  it('reports island coverage against the GENRE size, not the placed bubbles', () => {
    // The Rock island holds 900 artists but only 3 non-label bubbles are placed;
    // the bar must read 2/900, not 2/3.
    sync({ placed: placed(), _islands: islands, _oneIsland: true, _focusIdx: 0 });
    const mine = artMapPanelModel();
    expect([mine.scopeWatch, mine.scopeTotal]).toEqual([2, 900]);
    V._artMapRefreshPanel();
    expect((document.getElementById('artmap-panel-head') as HTMLElement).textContent).toContain(
      '2/900',
    );
  });

  it('shows an empty-state row when the scope has no artists', () => {
    sync({});
    V._artMapRefreshPanel();
    const body = document.getElementById('artmap-panel-body') as HTMLElement;
    expect(body.textContent).toContain('No artists');
    expect(artMapPanelModel().topArtists).toEqual([]);
  });
});

// ── The watchlist button ─────────────────────────────────────────────────────

describe('artMapWatchButton', () => {
  const cases: [string, Partial<ArtMapNode>, string[] | undefined][] = [
    ['a watchlist-typed node', { type: 'watchlist' }, undefined],
    ['a similar node not in the set', { type: 'similar', spotify_id: 'sp' }, []],
    ['a similar node in the set', { type: 'similar', spotify_id: 'sp' }, ['sp']],
    ['a node with no ids at all', { type: 'similar' }, []],
    ['a centre node is NOT auto-watched', { type: 'center', spotify_id: 'sp' }, []],
  ];
  for (const [label, over, set] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      sync({ _watchSet: set ? new Set(set) : undefined });
      const n = node(over);
      const html = V._artMapWatchBtnHtml(n);
      const mine = artMapWatchButton(n);
      expect(mine.watched).toBe(V._artMapIsWatched(n));
      // The glyph + wording is the whole point of the two states.
      expect(html.replace('&#9733;', '★').replace('&#9734;', '☆')).toContain(mine.label);
      expect(html).toContain(mine.background);
      expect(html).toContain(mine.borderColor);
    });
  }
});

// ── The artist card ──────────────────────────────────────────────────────────

describe('artMapArtistCard', () => {
  const edges = [
    { source: 1, target: 2 },
    { source: 3, target: 1 },
  ];

  const cases: [string, Partial<ArtMapNode>, Partial<ArtMapState>][] = [
    ['a plain similar node', {}, { edges }],
    ['a watchlist node', { type: 'watchlist' }, { edges }],
    ['a centre node also reads "On watchlist"', { type: 'center' }, { edges }],
    ['no hue falls back to 270', { _hue: undefined }, { edges }],
    ['hue 0 is kept', { _hue: 0 }, { edges }],
    ['popularity above 100 clamps', { popularity: 250 }, { edges }],
    ['negative popularity clamps to 0', { popularity: -5 }, { edges }],
    ['a fractional popularity rounds', { popularity: 62.6 }, { edges }],
    ['missing popularity reads 0', { popularity: undefined }, { edges }],
    [
      'more than five genres are trimmed',
      { genres: ['a', 'b', 'c', 'd', 'e', 'f', 'g'] },
      { edges },
    ],
    ['a node with source ids', { spotify_id: 'sp', itunes_id: 'it' }, { edges }],
    ['a node with only a musicbrainz id', { musicbrainz_id: 'mb' }, { edges }],
    ['a node with a string id', { id: 'label-ish' }, { edges }],
  ];

  for (const [label, over, state] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      const n = node(over);
      sync({ placed: [n], ...state });
      V._artMapEnsurePanel(); // the card paints into an EXISTING panel body
      V._artMapPanelArtist(n);
      const body = document.getElementById('artmap-panel-body') as HTMLElement;
      const mine = artMapArtistCard(n);

      expect(body.textContent).toContain(mine.name);
      expect(body.textContent).toContain(mine.typeLabel);

      // The genre hue tints the avatar ring. Asserting it is what makes a hue of
      // 0 distinguishable from "no hue" — a `|| 270` fallback survives otherwise.
      expect(body.innerHTML).toContain(`hsl(${mine.hue},80%,65%)`);

      // Popularity + connections are the two stat tiles on the card.
      const tiles = Array.from(body.querySelectorAll<HTMLElement>('[style*="flex:1"]')).filter(
        (t) => t.children.length === 2,
      );
      expect(tiles[0].children[0].textContent).toBe(String(mine.popularity));
      expect(tiles[0].children[1].textContent).toBe('Popularity');
      expect(tiles[1].children[0].textContent).toBe(String(mine.connections));
      expect(tiles[1].children[1].textContent).toBe('Connections');
      expect(mine.connections).toBe(V._artMapConnCount(n));

      // The popularity bar width is the same clamped number.
      const bar = body.querySelector<HTMLElement>('[style*="linear-gradient(90deg"]');
      expect(bar?.style.width).toBe(`${mine.popularity}%`);

      // Genre pills
      const pills = Array.from(
        body.querySelectorAll<HTMLElement>('[style*="border-radius:999px"]'),
      );
      expect(pills.map((p) => p.textContent?.trim())).toEqual(mine.genres);

      // The "Open artist page" link only exists when a source id resolved.
      const link = body.querySelector('a[href^="/artist-detail/"]');
      expect(!!link).toBe(!!mine.best.id);
      if (link) {
        expect(link.getAttribute('href')).toBe(
          `/artist-detail/${mine.best.source}/${mine.best.id}`,
        );
      }
      expect(mine.best).toEqual(V._artMapNodeBest(n));
    });
  }

  it('prefers a cached bitmap over the image url', () => {
    const n = node({ id: 7, image_url: '/a.jpg' });
    const bitmap = document.createElement('canvas');
    sync({ placed: [n], images: { 7: bitmap } });
    expect(artMapArtistCard(n).hasBitmap).toBe(true);
    V._artMapEnsurePanel();
    V._artMapPanelArtist(n);
    const body = document.getElementById('artmap-panel-body') as HTMLElement;
    // The vanilla paints into a canvas rather than emitting an <img>.
    expect(body.querySelector('#artmap-card-canvas')).not.toBeNull();
    expect(body.querySelector('img[src="/a.jpg"]')).toBeNull();
  });

  it('falls back to the image url with no cached bitmap', () => {
    const n = node({ id: 7, image_url: '/a.jpg' });
    sync({ placed: [n], images: {} });
    expect(artMapArtistCard(n).hasBitmap).toBe(false);
    V._artMapEnsurePanel();
    V._artMapPanelArtist(n);
    const body = document.getElementById('artmap-panel-body') as HTMLElement;
    expect(body.querySelector('#artmap-card-canvas')).toBeNull();
    expect(body.querySelector('img[src="/a.jpg"]')).not.toBeNull();
  });
});

// ── The island nav bar ───────────────────────────────────────────────────────

describe('artMapIslandNav', () => {
  const islands: ArtMapIsland[] = [
    { name: 'Rock', cx: 0, cy: 0, r: 500, hue: 42, count: 900 },
    { name: 'hip hop', cx: 900, cy: 0, r: 200, hue: 180, count: 12 },
    { name: 'Jazz', cx: -900, cy: 0, r: 200, hue: 300, count: 1 },
  ];

  it('renders nothing outside one-island mode', () => {
    sync({ _islands: islands, _oneIsland: false });
    expect(artMapIslandNav()).toBeNull();
    V._artMapUpdateIslandNav();
    expect(document.getElementById('artmap-island-nav')).toBeNull();
  });

  it('renders nothing with no islands', () => {
    sync({ _oneIsland: true, _islands: [] });
    expect(artMapIslandNav()).toBeNull();
    V._artMapUpdateIslandNav();
    expect(document.getElementById('artmap-island-nav')).toBeNull();
  });

  islands.forEach((_isl, idx) => {
    it(`matches the vanilla at island ${idx}`, () => {
      sync({ _islands: islands, _oneIsland: true, _focusIdx: idx });
      V._artMapUpdateIslandNav();
      const nav = document.getElementById('artmap-island-nav') as HTMLElement;
      const mine = artMapIslandNav();
      expect(mine).not.toBeNull();
      // The name is uppercased and followed by the ▾ affordance.
      expect(nav.textContent).toContain(mine?.display);
      expect(nav.textContent).toContain(`${mine?.count} artists`);
      expect(nav.textContent).toContain(`${mine?.position} / ${mine?.total}`);
      expect(nav.innerHTML).toContain(`hsl(${mine?.hue},80%,80%)`);
    });
  });

  it('tears down an existing bar when one-island mode ends', () => {
    sync({ _islands: islands, _oneIsland: true, _focusIdx: 0 });
    V._artMapUpdateIslandNav();
    expect(document.getElementById('artmap-island-nav')).not.toBeNull();
    sync({ _islands: islands, _oneIsland: false });
    V._artMapUpdateIslandNav();
    expect(document.getElementById('artmap-island-nav')).toBeNull();
    expect(artMapIslandNav()).toBeNull();
  });

  describe('the jump menu', () => {
    it('matches the vanilla row for row', () => {
      sync({ _islands: islands, _oneIsland: true, _focusIdx: 1 });
      V._artMapUpdateIslandNav();
      V._artMapToggleIslandMenu(null);
      const menu = document.getElementById('artmap-island-menu') as HTMLElement;
      const rows = Array.from(menu.children) as HTMLElement[];
      const mine = artMapIslandMenu();
      expect(rows).toHaveLength(mine.length);
      rows.forEach((row, i) => {
        expect(row.textContent).toContain(mine[i].name);
        expect(row.textContent).toContain(String(mine[i].count));
        expect(row.innerHTML).toContain(`hsl(${mine[i].hue},75%,62%)`);
        // The current island is the highlighted row.
        expect(row.getAttribute('style')?.includes('rgba(168,85,247,0.18)')).toBe(mine[i].active);
      });
    });

    it('toggles closed on a second call', () => {
      sync({ _islands: islands, _oneIsland: true, _focusIdx: 0 });
      V._artMapUpdateIslandNav();
      V._artMapToggleIslandMenu(null);
      expect(document.getElementById('artmap-island-menu')).not.toBeNull();
      V._artMapToggleIslandMenu(null);
      expect(document.getElementById('artmap-island-menu')).toBeNull();
    });
  });

  describe('stepping', () => {
    const steps: [number, number, number | null][] = [
      [0, 1, 1],
      [0, -1, 2], //  wraps to the end
      [2, 1, 0], //   wraps to the start
      [2, -1, 1],
      [1, 1, 2],
    ];
    for (const [from, dir, expected] of steps) {
      it(`from ${from} by ${dir} lands on ${expected}`, () => {
        sync({ _islands: islands, _oneIsland: true, _focusIdx: from });
        expect(artMapIslandNavStep(dir)).toBe(expected);
      });
    }

    it('refuses to step with fewer than two islands', () => {
      sync({ _islands: [islands[0]], _oneIsland: true, _focusIdx: 0 });
      expect(artMapIslandNavStep(1)).toBeNull();
    });

    it('clamps an out-of-range focus rather than wrapping', () => {
      sync({ _islands: islands });
      expect(artMapClampIsland(-5)).toBe(0);
      expect(artMapClampIsland(99)).toBe(2);
      sync({ _islands: [] });
      expect(artMapClampIsland(0)).toBeNull();
    });
  });
});

// ── The hover tooltip ────────────────────────────────────────────────────────

describe('artMapTooltip', () => {
  const edges = [
    { source: 1, target: 2 },
    { source: 3, target: 1 },
  ];

  const cases: [string, Partial<ArtMapNode>, Partial<ArtMapState>][] = [
    ['a similar node with two connections', {}, { edges }],
    ['a watchlist node gets the star badge', { type: 'watchlist' }, { edges }],
    ['a CENTRE node gets no badge, unlike the card', { type: 'center' }, { edges }],
    ['exactly one connection is singular', { id: 2 }, { edges }],
    ['no connections hides the line', { id: 99 }, { edges }],
    ['genres are trimmed to three', { genres: ['a', 'b', 'c', 'd'] }, { edges }],
    ['no genres', { genres: undefined }, { edges }],
  ];

  for (const [label, over, state] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      const n = node(over);
      sync({ placed: [n], ...state });
      V._artMapShowTooltip({ clientX: 10, clientY: 10 }, n);
      const tip = document.getElementById('artist-map-tooltip') as HTMLElement;
      const mine = artMapTooltip(n);

      expect(tip.querySelector('.artmap-tip-name')?.textContent).toBe(mine.name);
      expect(!!tip.querySelector('.artmap-tip-badge')).toBe(!!mine.badge);
      const conn = tip.querySelector('.artmap-tip-conn');
      expect(conn?.textContent ?? '').toBe(mine.connectionText);
      const genres = Array.from(tip.querySelectorAll('.artmap-tip-genres span')).map(
        (s) => s.textContent,
      );
      expect(genres).toEqual(mine.genres);
    });
  }

  it('hides the tooltip when handed no node', () => {
    V._artMapShowTooltip({ clientX: 0, clientY: 0 }, null);
    expect((document.getElementById('artist-map-tooltip') as HTMLElement).style.display).toBe(
      'none',
    );
  });

  describe('positioning', () => {
    const cases: [number, number, number, number, number, number][] = [
      [100, 200, 220, 120, 1400, 800],
      [1390, 200, 220, 120, 1400, 800], //  pulled back from the right edge
      [100, 795, 220, 120, 1400, 800], //   pulled up from the bottom
      [0, 0, 220, 120, 1400, 800], //       the top-left corner still offsets up by 10
    ];
    for (const [x, y, w, h, vw, vh] of cases) {
      it(`keeps the tip on screen at (${x},${y})`, () => {
        const pos = artMapTooltipPosition(x, y, w, h, vw, vh);
        // Reproduce the vanilla's own expression as the oracle (9348-9349).
        expect(pos.left).toBe(Math.min(x + 16, vw - w - 10));
        expect(pos.top).toBe(Math.min(y - 10, vh - h - 10));
      });
    }
  });
});

// ── The right-click menu ─────────────────────────────────────────────────────

describe('artMapContextMenu', () => {
  const cases: [string, Partial<ArtMapNode>, string][] = [
    [
      'all three ids, spotify active',
      { spotify_id: 's', itunes_id: 'i', deezer_id: 'd' },
      'spotify',
    ],
    ['all three ids, deezer active', { spotify_id: 's', itunes_id: 'i', deezer_id: 'd' }, 'deezer'],
    ['active source has no id — falls back down the chain', { itunes_id: 'i' }, 'deezer'],
    ['only a deezer id', { deezer_id: 'd' }, 'spotify'],
    ['no ids at all', {}, 'spotify'],
    ['a discogs id does not count as "hasId"', { discogs_id: 'dc' }, 'spotify'],
    [
      'a watchlist node labels the entry differently',
      { spotify_id: 's', type: 'watchlist' },
      'spotify',
    ],
    ['an unknown active source', { spotify_id: 's' }, 'lastfm'],
  ];

  for (const [label, over, active] of cases) {
    it(`matches the vanilla — ${label}`, () => {
      const n = node(over);
      // Re-derive with the vanilla's own expressions (10057-10070) as the oracle.
      const rec = n as unknown as Record<string, string | undefined>;
      const hasId = n.spotify_id || n.itunes_id || n.deezer_id;
      const bestId = rec[active + '_id'] || n.spotify_id || n.itunes_id || n.deezer_id || '';
      const bestSource = rec[active + '_id']
        ? active
        : n.spotify_id
          ? 'spotify'
          : n.itunes_id
            ? 'itunes'
            : 'deezer';
      const mine = artMapContextMenu(n, active);
      expect(mine.hasId).toBe(!!hasId);
      expect(mine.bestId).toBe(bestId);
      expect(mine.bestSource).toBe(bestSource);
      expect(mine.watchLabel).toBe(n.type === 'watchlist' ? 'On Watchlist' : 'Add to Watchlist');
    });
  }

  it('says "deezer" with an empty id when the node has nothing', () => {
    // Not a bug to fix here — the caller disables the link on the empty id.
    expect(artMapContextMenu(node({}), 'spotify')).toMatchObject({
      hasId: false,
      bestId: '',
      bestSource: 'deezer',
    });
  });

  it('defaults the active source to the window global, then spotify', () => {
    const w = window as unknown as { _yaActiveSource?: string };
    delete w._yaActiveSource;
    expect(artMapContextMenu(node({ spotify_id: 's', itunes_id: 'i' })).bestSource).toBe('spotify');
    w._yaActiveSource = 'itunes';
    expect(artMapContextMenu(node({ spotify_id: 's', itunes_id: 'i' })).bestSource).toBe('itunes');
    delete w._yaActiveSource;
  });
});

// ── The shortcuts overlay + the mobile sheet ─────────────────────────────────

describe('the shortcuts overlay', () => {
  it('lists the same rows as the vanilla modal, in order', () => {
    V.artMapShowShortcuts();
    const overlay = document.getElementById('artmap-shortcuts-overlay') as HTMLElement;
    const rows = Array.from(overlay.querySelectorAll('.artmap-shortcut'));
    expect(rows).toHaveLength(10);
    expect(ARTMAP_SHORTCUTS).toHaveLength(10);
    rows.forEach((row, i) => {
      const keys = Array.from(row.querySelectorAll('kbd')).map((k) => k.textContent);
      expect(keys).toEqual(ARTMAP_SHORTCUTS[i].keys);
      expect(row.querySelector('span')?.textContent).toBe(ARTMAP_SHORTCUTS[i].action);
    });
    overlay.remove();
  });
});

describe('the mobile bottom sheet', () => {
  it('slides fully off screen when closed', () => {
    expect(artMapSheetTransform(true)).toBe('translateY(0)');
    expect(artMapSheetTransform(false)).toBe('translateY(100%)');
  });
});
