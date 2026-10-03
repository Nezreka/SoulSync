import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * The calendar's acquisition badges, filter and card actions.
 *
 * The page could show you that an episode aired and that you didn't have it.
 * It could not show you that something was already downloading it, that a
 * search had failed, or that you'd told it to stop looking - so every one of
 * those read as the same "Not in library", and the only offered action was to
 * wishlist something that might already be mid-transfer.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-calendar.js'), 'utf8');
const HTML = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');
const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');

function loadFilter() {
  const labels = JS.slice(JS.indexOf('var ACQ_LABEL'), JS.indexOf('function filterEps'));
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(
    'state',
    `${labels}\n${extractFunction('filterEps', JS)}\nreturn filterEps;`,
  ) as (s: unknown) => (eps: unknown[]) => unknown[];
}

const EPS = [
  { id: 1, acq: 'owned', has_file: 1, needs_action: false },
  { id: 2, acq: 'missing', has_file: 0, needs_action: true },
  { id: 3, acq: 'downloading', has_file: 0, needs_action: false },
  { id: 4, acq: 'failed', has_file: 0, needs_action: true },
  { id: 5, acq: 'ignored', has_file: 0, needs_action: false },
  { id: 6, acq: 'unaired', has_file: 0, needs_action: false },
];

function ids(rows: unknown[]) {
  return (rows as { id: number }[]).map((r) => r.id);
}

describe('the needs-action filter', () => {
  it('keeps only what nobody is handling', () => {
    const f = loadFilter()({ filter: 'needs' });
    // Missing and failed. NOT downloading (in flight), not ignored (you already
    // answered), not unaired (nothing is wrong yet).
    expect(ids(f(EPS))).toEqual([2, 4]);
  });

  it('trusts the server rather than re-deriving the rule', () => {
    // The header count and the filter have to agree; computing "needs action"
    // separately on the client is how they drift apart.
    expect(extractFunction('filterEps', JS)).toContain('e.needs_action');
  });

  it('leaves the existing filters alone', () => {
    expect(ids(loadFilter()({ filter: 'owned' })(EPS))).toEqual([1]);
    expect(ids(loadFilter()({ filter: 'missing' })(EPS))).toEqual([2, 3, 4, 5, 6]);
    expect(ids(loadFilter()({ filter: 'all' })(EPS))).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it('is reachable from the toolbar', () => {
    expect(HTML).toContain('data-video-cal-filter="needs"');
  });
});

describe('the state badge', () => {
  function badge(acq: string | null) {
    const labels = JS.slice(JS.indexOf('var ACQ_LABEL'), JS.indexOf('function filterEps'));
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const fn = new Function(
      `function esc(s) { return String(s == null ? '' : s); }\n${labels}\nreturn acqBadge;`,
    )() as (ep: unknown, extra?: string) => string;
    return fn({ acq });
  }

  it('says what each state actually is', () => {
    expect(badge('downloading')).toContain('Downloading');
    expect(badge('failed')).toContain('Failed');
    expect(badge('ignored')).toContain('Not monitored');
    expect(badge('missing')).toContain('Missing');
  });

  it('draws nothing for unaired, which is most of the week', () => {
    // Badging the ordinary case would bury the few rows that want attention.
    expect(badge('unaired')).toBe('');
    expect(badge(null)).toBe('');
    expect(badge('nonsense')).toBe('');
  });

  it('has a colour for every state it can emit', () => {
    const labels = JS.slice(JS.indexOf('var ACQ_LABEL'), JS.indexOf('function acqBadge'));
    for (const cls of ['owned', 'live', 'bad', 'want', 'mut', 'miss']) {
      if (labels.includes(`vcal-acq--${cls}`)) {
        expect(CSS, `.vcal-acq--${cls} is used but never styled`).toContain(`.vcal-acq--${cls}`);
      }
    }
  });
});

describe('the card actions', () => {
  it('offers retry only where a retry means something', () => {
    // Retry clears the backoff. On a row that never failed there is no backoff
    // to clear, and the button would just be a confusing second wishlist.
    expect(JS).toContain("ep.acq === 'failed'");
    expect(extractFunction('retryThis', JS)).toContain("scope: 'episode'");
    expect(extractFunction('retryThis', JS)).toContain('/api/video/wishlist/retry');
  });

  it('does not offer to ignore what you already own or what has not aired', () => {
    const guard = JS.slice(JS.indexOf('data-vcm-ignore') - 400, JS.indexOf('data-vcm-ignore'));
    expect(guard).toContain('!ep.has_file');
    expect(guard).toContain("ep.acq !== 'ignored'");
    expect(guard).toContain("ep.acq !== 'unaired'");
  });

  it('moves the row out of needs-action when you ignore it', () => {
    // Otherwise the count keeps claiming work you just dismissed.
    const body = extractFunction('ignoreThis', JS);
    expect(body).toContain("ep.acq = 'ignored'");
    expect(body).toContain('ep.needs_action = false');
    expect(body).toContain('render()');
  });

  it('addresses the episode the way the calendar knows it', () => {
    // The calendar carries the show's tmdb id and season/episode, never the
    // local episode row id.
    const body = extractFunction('ignoreThis', JS);
    expect(body).toContain('/api/video/episode/monitor');
    expect(body).toContain('tmdb_id: ep.show_tmdb_id');
    expect(body).toContain('monitored: false');
  });
});

describe('the card status badges', () => {
  function realAcqBadge() {
    const labels = JS.slice(JS.indexOf('var ACQ_LABEL'), JS.indexOf('function filterEps'));
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    return new Function(
      `function esc(s) { return String(s == null ? '' : s); }\n${labels}\nreturn acqBadge;`,
    )() as (ep: unknown, extra?: string) => string;
  }

  // Run a real card renderer with stubbed helpers.
  function renderCard(name: string, ...args: unknown[]) {
    const deps: Record<string, unknown> = {
      showHue: () => 200,
      airMins: () => null,
      fmtMins: () => '',
      esc: (s: unknown) => String(s == null ? '' : s),
      acqBadge: realAcqBadge(),
      whenLabel: () => 'Today',
      state: { offset: 0, movieEvents: [] },
      MOVIE_TYPE: {
        cinema: { chip: 'In Cinemas', cls: 'vcal-mv--cinema' },
        available: { chip: 'Home Release', cls: 'vcal-mv--home' },
      },
    };
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const fn = new Function(...Object.keys(deps), 'args', `${extractFunction(name, JS)}\nreturn ${name}(...args);`);
    return fn(...Object.values(deps), args) as string;
  }

  const EP = { id: 7, show_id: 3, show_title: 'Show', season_number: 1, episode_number: 2 };

  it('grid episode cards badge the wishlist, not just the library', () => {
    // Owned keeps the ✓ flag and gets no acquisition badge.
    const owned = renderCard('epCell', { ...EP, has_file: 1, acq: 'owned' });
    expect(owned).toContain('vcal-flag');
    expect(owned).not.toContain('vcal-acq');
    // Wishlisted (wanted) shows the badge the agenda already had.
    const wanted = renderCard('epCell', { ...EP, has_file: 0, acq: 'wanted' });
    expect(wanted).not.toContain('vcal-flag');
    expect(wanted).toContain('vcal-acq--want');
    expect(wanted).toContain('Wanted');
    // Other in-flight states ride along too.
    expect(renderCard('epCell', { ...EP, has_file: 0, acq: 'downloading' })).toContain('Downloading');
    // The ordinary unaired case stays clean.
    const unaired = renderCard('epCell', { ...EP, has_file: 0, acq: 'unaired' });
    expect(unaired).not.toContain('vcal-flag');
    expect(unaired).not.toContain('vcal-acq');
  });

  it('grid movie cards say wishlist until owned', () => {
    // The movie lane is built from the wishlist, so a non-owned card is
    // wishlisted by construction.
    const wished = renderCard('movieCell', { title: 'Film', tmdb_id: 9, type: 'available', owned: 0 });
    expect(wished).not.toContain('vcal-flag');
    expect(wished).toContain('vcal-acq--want');
    expect(wished).toContain('Wishlist');
    const owned = renderCard('movieCell', { title: 'Film', tmdb_id: 9, type: 'available', owned: 1, library_id: 4 });
    expect(owned).toContain('vcal-flag');
    expect(owned).not.toContain('vcal-acq');
  });

  it('the hero billboard badges status, not just ownership', () => {
    const d = { today: '2026-10-03' };
    const owned = renderCard('heroPanel', { ...EP, has_file: 1, acq: 'owned' }, d, {});
    expect(owned).toContain('In your library');
    const wanted = renderCard('heroPanel', { ...EP, has_file: 0, acq: 'wanted' }, d, {});
    expect(wanted).not.toContain('In your library');
    expect(wanted).toContain('Wanted');
  });

  it('the calendar refetches when the wishlist changes', () => {
    // The modal's "Wishlist episode" button fires this; without the listener
    // the card behind it keeps its stale badge until a week change.
    expect(JS).toContain("addEventListener('soulsync:video-wishlist-changed'");
    expect(extractFunction('wire', JS)).toContain('load({ quiet: true })');
  });
});
