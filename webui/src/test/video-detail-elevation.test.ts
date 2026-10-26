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
