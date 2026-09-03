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
