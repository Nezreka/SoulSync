import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * #2177: a show with old seasons left unmonitored on purpose read "273
 * missing", and the Get Missing button and the wishlist-everything action
 * grabbed all 273. Missing is what SoulSync hunts, like sonarr's: aired, no
 * file, monitored. The unmonitored gaps are still said, quieter.
 *
 * These run the REAL vanilla bodies (show page hero, acquisition panel, Get
 * modal), not a re-implementation of them.
 */

const DETAIL = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');
const MODAL = readFileSync(resolve(process.cwd(), 'static/video/video-get-modal.js'), 'utf8');

const PAST = '2009-03-08';
const FUTURE = '2999-01-01';

// a show the way the library detail payload carries it: S1 monitored with one
// gap, S2 unmonitored with three, S3 monitored with one hand-unmonitored gap
// and one not aired yet, plus a special
function show() {
  return {
    kind: 'show',
    source: 'library',
    seasons: [
      { season_number: 0, episodes: [{ episode_number: 1, owned: false, air_date: PAST }] },
      {
        season_number: 1,
        episodes: [
          { episode_number: 1, owned: true, monitored: true, air_date: PAST },
          { episode_number: 2, owned: false, monitored: true, air_date: PAST },
        ],
      },
      {
        season_number: 2,
        episodes: [1, 2, 3].map((n) => ({
          episode_number: n,
          owned: false,
          monitored: false,
          air_date: PAST,
        })),
      },
      {
        season_number: 3,
        episodes: [
          { episode_number: 1, owned: false, monitored: false, air_date: PAST },
          { episode_number: 2, owned: false, monitored: true, air_date: FUTURE },
        ],
      },
    ],
  };
}

describe('the show page hero', () => {
  const count = new Function(
    `${extractFunction('missingAiredCount', DETAIL)}\nreturn missingAiredCount;`,
  )() as (d: unknown) => number;

  it('counts only the monitored gaps for Get Missing', () => {
    expect(count(show())).toBe(1);
  });

  it('a tmdb preview has no monitored flag, so every aired gap is wanted', () => {
    const preview = show();
    for (const s of preview.seasons)
      for (const e of s.episodes as { monitored?: boolean }[]) delete e.monitored;
    expect(count(preview)).toBe(5);
  });
});

describe('wishlist every missing episode', () => {
  const metas = (data: unknown) =>
    new Function(
      'data',
      `function seasonArt() { return null; }\n${extractFunction('_allMissingMetas', DETAIL)}\nreturn _allMissingMetas();`,
    )(data) as { season_number: number; episode_number: number }[];

  it('leaves unmonitored episodes off the wishlist', () => {
    expect(metas(show()).map((e) => [e.season_number, e.episode_number])).toEqual([
      [0, 1],
      [1, 2],
    ]);
  });
});

describe('the acquisition panel', () => {
  const panel = (state: unknown) => {
    const states = DETAIL.slice(
      DETAIL.indexOf('var _ACQ_STATES'),
      DETAIL.indexOf('function acqPanelHtml'),
    );
    const build = new Function(
      `function esc(s) { return String(s == null ? '' : s); }\n${states}\n${extractFunction('acqPanelHtml', DETAIL)}\nreturn acqPanelHtml;`,
    )() as (s: unknown) => string;
    const host = document.createElement('div');
    host.innerHTML = build(state);
    return host;
  };
  const ZERO = { owned: 0, wanted: 0, queued: 0, downloading: 0, failed: 0, ignored: 0 };

  it('headlines the monitored gaps and says the ignored ones apart', () => {
    const host = panel({ total: 276, counts: { ...ZERO, owned: 3, ignored: 271 } });
    expect(host.querySelector('.vd-acq-summary strong')?.textContent).toBe('2 missing');
    expect(host.textContent).toContain('271 more aren’t monitored');
  });

  it('a show missing only unmonitored episodes is not "missing" anything', () => {
    const host = panel({ total: 274, counts: { ...ZERO, owned: 3, ignored: 271 } });
    expect(host.querySelector('.vd-acq-summary strong')?.textContent).toBe('271 ignored');
    expect(host.textContent).not.toContain('more aren’t monitored');
  });
});

describe('the Get modal', () => {
  const row = (e: unknown) => {
    const fn = new Function(
      `var modalState = null;
       function esc(s) { return String(s == null ? '' : s); }
       function fmtDate(s) { return s; }
       ${extractFunction('epState', MODAL)}
       ${extractFunction('wantedMissing', MODAL)}
       ${extractFunction('epRow', MODAL)}
       return epRow;`,
    )() as (sn: number, e: unknown, today: string) => string;
    const host = document.createElement('div');
    host.innerHTML = fn(2, e, '2026-11-18');
    return host.firstElementChild as HTMLElement;
  };

  it('pre-ticks a monitored gap', () => {
    const r = row({ episode_number: 2, owned: false, monitored: true, air_date: PAST });
    expect((r.querySelector('.vgm-ep-cb') as HTMLInputElement).checked).toBe(true);
    expect(r.querySelector('.vgm-ep-badge')?.textContent).toBe('Missing');
  });

  it('lists an unmonitored gap unticked and says why', () => {
    const r = row({ episode_number: 3, owned: false, monitored: false, air_date: PAST });
    expect((r.querySelector('.vgm-ep-cb') as HTMLInputElement).checked).toBe(false);
    expect(r.querySelector('.vgm-ep-badge')?.textContent).toBe('Not monitored');
    // Select all missing skips it by this mark
    expect(r.hasAttribute('data-vgm-unmonitored')).toBe(true);
  });
});
