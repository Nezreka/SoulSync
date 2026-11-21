import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * Manual search for a whole show (#2233).
 *
 * Thibs: "add the manual search for the tv show in the ... menu, perhaps add a
 * kind of selector in the manual search menu for grabbing a specific season."
 *
 * The show's ⋯ menu opens the same Manual search a movie's does, and for a
 * show it carries a scope picker: whole series, a season, or one episode. The
 * box follows the scope, the search runs again, and a grab out of it goes the
 * way the scope says (a pack as a pack, a single episode as that episode).
 *
 * The real video-download-view.js runs here, in jsdom, against a stubbed fetch.
 */

const VIEW = readFileSync(resolve(process.cwd(), 'static/video/video-download-view.js'), 'utf8');
const DETAIL = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');

type Body = Record<string, unknown>;
type Row = Record<string, unknown>;

interface VD {
  manualSearch: (opts: Record<string, unknown>) => void;
}

let searches: Body[] = [];
let grabs: Array<{ url: string; body: Body }> = [];
let rows: Row[] = [];

const SEASONS = [
  { season: 1, title: 'Season 1', total: 3, have: 3, episodes: [] },
  {
    season: 2,
    title: 'Season 2',
    total: 4,
    have: 1,
    episodes: [
      { episode: 1, title: 'Good News About Hell', have: true },
      { episode: 2, title: 'Half Loop', have: false },
      { episode: 3, title: 'In Perpetuity', have: false },
      { episode: 4, title: 'The You You Are', have: false },
    ],
  },
];

function load(): VD {
  new Function(VIEW)();
  return (window as unknown as { VideoDownload: VD }).VideoDownload;
}

const flush = () => new Promise((r) => setTimeout(r, 0));
async function settle() {
  for (let i = 0; i < 6; i++) await flush();
}

beforeEach(() => {
  searches = [];
  grabs = [];
  rows = [];
  document.body.innerHTML = '';
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: { body?: string }) => {
      const json = (d: unknown) =>
        Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(d) });
      if (url.startsWith('/api/video/downloads/config')) return json({ download_mode: 'usenet' });
      if (url.startsWith('/api/video/downloads/search/start')) {
        searches.push(JSON.parse(init?.body || '{}'));
        return json({ id: null, live: true, complete: true, results: rows });
      }
      if (url.startsWith('/api/video/downloads/grab')) {
        grabs.push({ url, body: JSON.parse(init?.body || '{}') });
        return json({ ok: true, id: 7, started: 1 });
      }
      return json(null);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

function open(extra: Record<string, unknown> = {}) {
  load().manualSearch({
    title: 'Severed',
    scope: 'season',
    season: 2,
    mediaId: 11,
    mediaSource: 'library',
    tmdbId: 95396,
    year: 2022,
    seasons: SEASONS,
    ...extra,
  });
}
const q = <T extends Element = HTMLElement>(sel: string) => document.querySelector(sel) as T | null;
const query = () => (q<HTMLInputElement>('[data-vms-query]') as HTMLInputElement).value;
const pressed = () =>
  Array.from(document.querySelectorAll('[data-vms-scope]'))
    .filter((b) => b.getAttribute('aria-pressed') === 'true')
    .map((b) => b.getAttribute('data-vms-scope'));

describe('the scope picker', () => {
  it('opens on the season it was asked for, with that season’s query', async () => {
    open();
    await settle();
    expect(pressed()).toEqual(['season']);
    expect(query()).toBe('Severed S02');
    expect(q('[data-vms-scope-label]')?.textContent).toBe('Season 2 · you have 1 of 4');
    expect(q<HTMLSelectElement>('[data-vms-season]')?.hidden).toBe(false);
    expect(q<HTMLSelectElement>('[data-vms-episode]')?.hidden).toBe(true);
    // a season search lists that season's single episodes beside its packs
    expect(searches.at(-1)).toMatchObject({
      scope: 'season',
      season: 2,
      episode: null,
      with_episodes: true,
    });
  });

  it('whole series searches the title alone, for complete packs', async () => {
    open();
    await settle();
    (q('[data-vms-scope="series"]') as HTMLButtonElement).click();
    await settle();
    expect(pressed()).toEqual(['series']);
    expect(query()).toBe('Severed');
    expect(q<HTMLSelectElement>('[data-vms-season]')?.hidden).toBe(true);
    expect(q('[data-vms-scope-label]')?.textContent).toBe(
      'Complete series · you have 4 of 7 episodes',
    );
    const last = searches.at(-1) as Body;
    expect(last).toMatchObject({ scope: 'series', season: null, episode: null });
    expect(last.with_episodes).toBeUndefined();
  });

  it('episode picks one, and the box and the search follow the pick', async () => {
    open();
    await settle();
    (q('[data-vms-scope="episode"]') as HTMLButtonElement).click();
    await settle();
    expect(query()).toBe('Severed S02E01');
    const ep = q<HTMLSelectElement>('[data-vms-episode]') as HTMLSelectElement;
    expect(ep.hidden).toBe(false);
    expect(ep.options.length).toBe(4);
    expect(q('[data-vms-scope-label]')?.textContent).toBe(
      'S02E01 · Good News About Hell · in your library',
    );
    ep.value = '3';
    ep.dispatchEvent(new Event('change', { bubbles: true }));
    await settle();
    expect(query()).toBe('Severed S02E03');
    expect(searches.at(-1)).toMatchObject({ scope: 'episode', season: 2, episode: 3 });
  });

  it('a season without listed episodes offers 1 to its total', async () => {
    open({ scope: 'episode', season: 1, episode: 2 });
    await settle();
    const ep = q<HTMLSelectElement>('[data-vms-episode]') as HTMLSelectElement;
    expect(Array.from(ep.options).map((o) => o.value)).toEqual(['1', '2', '3']);
    expect(ep.value).toBe('2');
    // a new season lands on its first episode
    const se = q<HTMLSelectElement>('[data-vms-season]') as HTMLSelectElement;
    se.value = '2';
    se.dispatchEvent(new Event('change', { bubbles: true }));
    await settle();
    expect(query()).toBe('Severed S02E01');
  });

  it('a typed query survives until the scope changes', async () => {
    open();
    await settle();
    const input = q<HTMLInputElement>('[data-vms-query]') as HTMLInputElement;
    input.value = 'Severance S02';
    (q('[data-vms-query-form]') as HTMLFormElement).dispatchEvent(
      new Event('submit', { cancelable: true }),
    );
    await settle();
    expect(searches.at(-1)).toMatchObject({ query: 'Severance S02' });
    (q('[data-vms-scope="series"]') as HTMLButtonElement).click();
    await settle();
    expect(query()).toBe('Severed');
    expect((searches.at(-1) as Body).query).toBeUndefined();
  });

  it('a movie, or a show opened without its seasons, has no picker', async () => {
    open({ scope: 'movie', seasons: undefined });
    await settle();
    expect(q('[data-vms-scopes]')).toBeNull();
    document.body.innerHTML = '';
    open({ seasons: [] });
    await settle();
    expect(q('[data-vms-scopes]')).toBeNull();
  });

  it('the pickers are real buttons and labelled selects', async () => {
    open();
    await settle();
    document.querySelectorAll('[data-vms-scope]').forEach((b) => {
      expect(b.tagName).toBe('BUTTON');
      expect(b.getAttribute('type')).toBe('button');
    });
    expect(q('[data-vms-season]')?.getAttribute('aria-label')).toBe('Season');
    expect(q('[data-vms-episode]')?.getAttribute('aria-label')).toBe('Episode');
    expect(q('.vms-modal')?.getAttribute('role')).toBe('dialog');
  });
});

const PACK: Row = {
  title: 'Severed.S02.1080p.WEB-DL-GRP',
  username: 'NZBgeek',
  accepted: true,
  resolution: '1080p',
  size_bytes: 20e9,
  season: 2,
  is_season_pack: true,
  files: [],
  cover: { have: 1, total: 4 },
  download_url: 'http://x/pack',
  protocol: 'usenet',
};
const EPISODE: Row = {
  title: 'Severed.S02E03.1080p.WEB-DL-GRP',
  username: 'NZBgeek',
  accepted: true,
  resolution: '1080p',
  size_gb: 2,
  season: 2,
  episode: 3,
  cover: { have: 0, total: 1 },
  download_url: 'http://x/ep',
  protocol: 'usenet',
};

describe('a soulseek season search', () => {
  it('polls with the same season-episodes switch it started with', async () => {
    const polls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        const json = (d: unknown) =>
          Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(d) });
        if (url.startsWith('/api/video/downloads/config'))
          return json({ download_mode: 'soulseek' });
        if (url.startsWith('/api/video/downloads/search/start'))
          return json({ id: 'abc', poll_ms: 1 });
        if (url.startsWith('/api/video/downloads/search/poll')) {
          polls.push(url);
          return json({ results: [], total_files: 0 });
        }
        return json(null);
      }),
    );
    open();
    await settle();
    expect(polls.length).toBeGreaterThan(0);
    expect(polls[0]).toContain('&with_episodes=1');
    expect(polls[0]).toContain('&season=2');
  });
});

describe('what each result covers', () => {
  it('a pack says how many of its episodes you have', async () => {
    rows = [PACK];
    open();
    await settle();
    const card = q('.vdl-pack') as HTMLElement;
    expect(card.querySelector('.vdl-pack-meta')?.textContent).toBe('Season 2 · 4 eps · 18.6 GB');
    expect(card.querySelector('.vdl-cover')?.textContent).toBe('You have 1 of 4');
  });

  it('a soulseek pack marks each file you have', async () => {
    rows = [
      {
        ...PACK,
        username: 'peer',
        files: [
          { filename: 'Severed/Severed.S02E01.mkv', size_bytes: 1e9, have: true },
          { filename: 'Severed/Severed.S02E02.mkv', size_bytes: 1e9 },
        ],
      },
    ];
    open();
    await settle();
    const eps = document.querySelectorAll('.vdl-pack-ep');
    expect(eps[0].classList.contains('vdl-pack-ep--have')).toBe(true);
    expect(eps[0].querySelector('.vdl-pack-ep-have')?.textContent).toBe('In library');
    expect(eps[1].querySelector('.vdl-pack-ep-have')).toBeNull();
  });

  it('a single episode names itself and says when you have it', async () => {
    rows = [
      EPISODE,
      { ...EPISODE, title: 'Severed.S02E01.720p', episode: 1, cover: { have: 1, total: 1 } },
    ];
    open();
    await settle();
    const cards = document.querySelectorAll('.vdl-res:not(.vdl-pack)');
    expect(cards[0].querySelector('.vdl-r-ep')?.textContent).toBe('S02E03');
    expect(cards[0].querySelector('.vdl-cover')).toBeNull();
    expect(cards[1].querySelector('.vdl-cover')?.textContent).toBe('In your library');
  });
});

describe('a grab goes the way the scope says', () => {
  it('a single episode out of a season search is grabbed as that episode', async () => {
    rows = [EPISODE];
    open();
    await settle();
    (q('[data-vdl-grab="0"]') as HTMLButtonElement).click();
    await settle();
    const g = grabs.at(-1) as { body: Body };
    expect(g.body.kind).toBe('episode');
    expect(g.body.search_ctx).toMatchObject({ scope: 'episode', season: 2, episode: 3 });
  });

  it('a season pack is grabbed as its season', async () => {
    rows = [PACK];
    open();
    await settle();
    (q('[data-vdl-grab-pack="0"]') as HTMLButtonElement).click();
    await settle();
    const g = grabs.at(-1) as { url: string; body: Body };
    expect(g.url).toBe('/api/video/downloads/grab');
    expect(g.body.search_ctx).toMatchObject({ scope: 'season', season: 2, episode: null });
  });

  it('a complete-series pack stays a series, with no season', async () => {
    rows = [
      { ...PACK, title: 'Severed.Complete.Series.1080p', season: null, is_series_pack: true },
    ];
    open({ scope: 'series' });
    await settle();
    (q('[data-vdl-grab-pack="0"]') as HTMLButtonElement).click();
    await settle();
    expect((grabs.at(-1) as { body: Body }).body.search_ctx).toMatchObject({
      scope: 'series',
      season: null,
    });
  });
});

describe('the show page', () => {
  it('offers Manual search in the show’s ⋯ menu', () => {
    const menu = DETAIL.slice(
      DETAIL.indexOf("if (d.kind === 'show' && d.source !== 'youtube' && _canDl &&"),
    );
    expect(menu.slice(0, 400)).toContain("moreItem('manualsearch'");
    expect(DETAIL).toContain(
      "else if (which === 'manualsearch') { if (data && data.kind === 'show') manualSearchShow(); else manualSearchMovie(); }",
    );
  });

  it('hands the modal the show’s seasons, from every show entry point', () => {
    const open = DETAIL.slice(
      DETAIL.indexOf('function _openManualSearch('),
      DETAIL.indexOf('function _manualSeasons('),
    );
    expect(open).toContain('seasons: _manualSeasons()');
  });
});
