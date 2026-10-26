import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * The Oct 26 detail-page pass, on the real vanilla functions.
 *
 * Boulder: "deep dive the artist detail pages for movies, tv and youtube and
 * elevate it". What the audit found and these pin:
 * - a show counted its specials and unaired episodes, so Breaking Bad read
 *   "74% in library · 6 Seasons · 84 Episodes" with every aired episode on disk
 * - specials led the season rail at "0%"
 * - a channel's videos were wide TV-episode rows; they're a thumbnail grid
 *   now, and the cards must keep every hook live download status reads
 * - a one-line TMDB review got its own section
 */

const SRC = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');

function fn<T>(name: string, preamble = ''): T {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(`${preamble}\n${extractFunction(name, SRC)}\nreturn ${name};`)() as T;
}

type Ep = { owned?: boolean; air_date?: string | null; watched?: boolean };
type Season = { season_number: number; episodes: Ep[] };

describe('a show counts like a viewer reads it', () => {
  const showCounts = fn<(d: unknown) => Record<string, number>>('showCounts');
  const bb = (owned: number, missing: number): Season => ({
    season_number: 1,
    episodes: [
      ...Array.from({ length: owned }, () => ({
        owned: true,
        air_date: '2008-01-20',
        watched: true,
      })),
      ...Array.from({ length: missing }, () => ({ owned: false, air_date: '2008-02-01' })),
    ],
  });

  it('leaves specials out of seasons, episodes and the percentage', () => {
    const specials: Season = {
      season_number: 0,
      episodes: [{ owned: false }, { owned: true, watched: true }],
    };
    const c = showCounts({ seasons: [specials, bb(5, 0)] });
    expect(c.seasons).toBe(1);
    expect(c.episodes).toBe(5);
    expect(c.aired).toBe(5);
    expect(c.owned).toBe(5);
    expect(c.watched).toBe(5);
    expect(c.specials).toBe(1);
  });

  it('an owned special never tips a show to complete', () => {
    const c = showCounts({
      seasons: [bb(1, 1), { season_number: 0, episodes: [{ owned: true }] }],
    });
    expect(c.owned).toBe(1);
    expect(c.aired).toBe(2);
  });

  it('an unaired episode is not missing', () => {
    const c = showCounts({
      seasons: [
        { season_number: 1, episodes: [{ owned: true }, { owned: false, air_date: '2999-01-01' }] },
      ],
    });
    expect(c.episodes).toBe(2);
    expect(c.aired).toBe(1);
  });

  it('falls back to the payload totals when it has no episode list', () => {
    const c = showCounts({ seasons: [], episode_total: 9, episode_owned: 4 });
    expect([c.episodes, c.aired, c.owned]).toEqual([9, 9, 4]);
  });
});

describe('specials go after the real seasons', () => {
  const specialsLast = fn<(s: Season[]) => Season[]>('specialsLast');

  it('moves season 0 to the end and keeps the rest in order', () => {
    const order = specialsLast([
      { season_number: 0, episodes: [] },
      { season_number: 1, episodes: [] },
      { season_number: 2, episodes: [] },
    ]).map((s) => s.season_number);
    expect(order).toEqual([1, 2, 0]);
  });

  it('leaves a show without specials alone', () => {
    expect(specialsLast([{ season_number: 1, episodes: [] }]).length).toBe(1);
  });
});

describe('channel videos as a grid', () => {
  const card = fn<(ep: Record<string, unknown>) => string>(
    'ytVideoCard',
    `
    function esc(s) { return String(s == null ? '' : s); }
    function fmtDate(s) { return String(s); }
    var data = { _channel: { youtube_id: 'UC1', title: 'The Action Lab' } };
  `,
  );
  const host = (html: string) => {
    const el = document.createElement('div');
    el.innerHTML = html;
    return el;
  };

  it('keeps the hooks the live download painter reads', () => {
    const el = host(
      card({ youtube_id: 'abc', title: 'Sun', view_count: 1200, air_date: '2026-10-24' }),
    );
    // applyDlStates keys youtube rows on data-vd-ep-get = the video id
    expect(el.querySelector('[data-vd-ep-get="abc"] [data-vd-ep-dl]')).not.toBeNull();
    expect(el.querySelector('[data-vd-yt-grab="abc"]')).not.toBeNull();
    expect(el.querySelector('[data-vd-yt-wish="abc"]')).not.toBeNull();
    expect(el.querySelector('[data-vd-yt-play="abc"]')).not.toBeNull();
  });

  it('an owned video says so and offers a re-download, not a wishlist', () => {
    const el = host(card({ youtube_id: 'abc', title: 'Sun', owned: true }));
    expect(el.textContent).toContain('Downloaded');
    expect(el.querySelector('[data-vd-yt-wish]')).toBeNull();
    expect(el.querySelector('[data-vd-pop-menu] [data-vd-yt-grab="abc"]')).not.toBeNull();
  });

  it('the wish item uses pill chrome, so the toggle repaints its label in place', () => {
    // toggleYtWish rewrites .watchlist-text on pill buttons; without it the
    // menu item's whole label would be replaced by a bare "✓"
    const el = host(card({ youtube_id: 'abc', title: 'Sun' }));
    expect(el.querySelector('[data-vd-yt-wish] .watchlist-text')?.textContent).toBe('Wishlist');
  });

  it('is the default layout, and list is remembered when picked', () => {
    const ytLayout = fn<() => string>('ytLayout', `var YT_LAYOUT_KEY = 'soulsync.vdYtLayout';`);
    localStorage.removeItem('soulsync.vdYtLayout');
    expect(ytLayout()).toBe('grid');
    localStorage.setItem('soulsync.vdYtLayout', 'list');
    expect(ytLayout()).toBe('list');
    localStorage.removeItem('soulsync.vdYtLayout');
  });
});

describe('the rest of the pass', () => {
  it('a channel starts on year pills, a show on its own remembered view', () => {
    const body = extractFunction('restoreView', SRC);
    expect(body).toContain("(yt ? 'pills' : 'rail')");
  });

  it('a one-line review gets no section', () => {
    const body = extractFunction('renderReview', SRC);
    expect(body).toMatch(/trim\(\)\.length < 80/);
  });

  it('the season rail scrolls itself, never the page', () => {
    const body = extractFunction('renderSeasonNav', SRC);
    expect(body).not.toMatch(/\.scrollIntoView\(/);
    expect(body).toContain('centerInStrip(host)');
  });
});

describe('round two', () => {
  const ratings = (d: Record<string, unknown>) => {
    const host = document.createElement('div');
    host.innerHTML = '<div data-vd-ratings></div>';
    const render = fn<(d: unknown) => void>(
      'renderRatings',
      `function esc(s) { return String(s == null ? '' : s); }
       function q(sel) { return globalThis.__host.querySelector(sel); }`,
    );
    (globalThis as Record<string, unknown>).__host = host;
    render(d);
    return host.querySelector('[data-vd-ratings]') as HTMLElement;
  };

  it('each rating opens its own site on a library page', () => {
    const el = ratings({
      source: 'library',
      kind: 'movie',
      title: 'Heat',
      imdb_id: 'tt0113277',
      imdb_rating: 8.3,
      rt_rating: 88,
      tmdb_id: 949,
      trakt_rating: 8.1,
    });
    expect(el.querySelector('a.vd-rt--imdb')?.getAttribute('href')).toBe(
      'https://www.imdb.com/title/tt0113277/',
    );
    expect(el.querySelector('a.vd-rt--rt')?.getAttribute('href')).toContain(
      'rottentomatoes.com/search?search=Heat',
    );
    expect(el.querySelector('a.vd-rt--trakt')?.getAttribute('href')).toContain(
      '/tmdb/949?id_type=movie',
    );
  });

  it('a preview keeps everything in-app', () => {
    const el = ratings({
      source: 'tmdb',
      kind: 'movie',
      title: 'Heat',
      imdb_id: 'tt0113277',
      imdb_rating: 8.3,
    });
    expect(el.querySelector('a')).toBeNull();
    expect(el.querySelector('span.vd-rt--imdb')).not.toBeNull();
  });

  it('a channel shows six playlists and folds the rest behind one button', () => {
    const body = extractFunction('ytLoadPlaylists', SRC);
    expect(body).toContain('pls.length > PL_VISIBLE');
    expect(SRC).toContain('var PL_VISIBLE = 6;');
    const css = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');
    expect(css).toContain(
      '.vc-playlists:not(.vc-playlists--all) > .vc-pl:nth-child(n + 7) { display: none; }',
    );
  });

  it('a channel page reaches its own settings', () => {
    const body = extractFunction('renderActions', SRC);
    expect(body).toContain("moreItem('yt-settings'");
    expect(SRC).toContain('VideoYoutube.openChannelSettings(data.id, data.title');
  });
});

describe('a page that cannot load', () => {
  it('every load failure lands on the one error state, never a bare headline', () => {
    for (const name of ['loadMovie', 'loadShow', 'loadChannel', 'loadPlaylist']) {
      const body = extractFunction(name, SRC);
      expect(body).toContain('showLoadError(');
      expect(body).toContain('_retry = function');
      expect(body).not.toMatch(
        /setText\('\[data-vd-title\]', '(Not found|Could not load|\w+ unavailable)/,
      );
    }
  });

  it('offers a retry, and youtube pages keep a way out to youtube', () => {
    const host = document.createElement('div');
    host.innerHTML =
      '<div class="vd-page"><h1 data-vd-title></h1><p data-vd-overview></p>' +
      '<div data-vd-actions></div><div data-vd-tabs></div></div>';
    (globalThis as Record<string, unknown>).__host = host;
    const show = fn<(n: string, w: string, u?: string) => void>(
      'showLoadError',
      `function esc(s) { return String(s == null ? '' : s); }
       function q(sel) { return globalThis.__host.querySelector(sel); }
       function root() { return globalThis.__host.querySelector('.vd-page'); }
       function setText(sel, t) { var n = q(sel); if (n) n.textContent = t || ''; }
       function showLoading() {}
       function renderOverview(t) { q('[data-vd-overview]').textContent = t; }`,
    );
    show('playlist', 'gone', 'https://www.youtube.com/playlist?list=PL1');
    expect(host.querySelector('.vd-page')?.classList.contains('vd-page--error')).toBe(true);
    expect(host.querySelector('[data-vd-title]')?.textContent).toBe('Couldn’t open this playlist');
    expect(host.querySelector('[data-vd-overview]')?.textContent).toContain('private, deleted');
    expect(host.querySelector('[data-vd-act="retry-load"]')).not.toBeNull();
    expect(host.querySelector('a.vd-error-ext')?.getAttribute('href')).toContain('list=PL1');
    expect((host.querySelector('[data-vd-tabs]') as HTMLElement).hidden).toBe(true);
  });

  it('the back pill never says "Back to Couldn\u2019t open this movie"', () => {
    const side = readFileSync(resolve(process.cwd(), 'static/video/video-side.js'), 'utf8');
    expect(extractFunction('detailTitleOf', side)).toContain(
      "classList.contains('vd-page--error')",
    );
  });

  it('the next load clears the error state', () => {
    expect(extractFunction('resetExtras', SRC)).toContain("classList.remove('vd-page--error')");
  });
});
