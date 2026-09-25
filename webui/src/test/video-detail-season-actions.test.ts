import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * The season action bar.
 *
 * Grab / manual search / wishlist only mean something when episodes are
 * missing, so they stay conditional. Monitoring and stale-failure resets are
 * management, and they matter just as much on a season you own in full - a
 * complete season you no longer want hunted still needs the toggle. The bar
 * used to render only when something was missing, which put both of those out
 * of reach exactly when the season was healthy.
 */

const SRC = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');

type Ctx = { source?: string; kind?: string; tmdb_id?: number | null; grab?: boolean; q?: string };

function bar(season: Record<string, unknown>, ctx: Ctx = {}): HTMLElement {
  const data = {
    kind: ctx.kind ?? 'show',
    source: ctx.source ?? 'library',
    tmdb_id: ctx.tmdb_id === undefined ? 1396 : ctx.tmdb_id,
  };
  const preamble = `
    var data = ${JSON.stringify(data)};
    var ytFilter = { q: ${JSON.stringify(ctx.q ?? '')}, state: 'all', duration: 'all' };
    window.VideoGrab = ${ctx.grab === false ? 'undefined' : '{}'};
  `;
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const build = new Function(
    `${preamble}\n${extractFunction('seasonActionsHtml', SRC)}\nreturn seasonActionsHtml;`,
  )() as (s: unknown) => string;
  const host = document.createElement('div');
  host.innerHTML = build(season);
  return host;
}

/** Two episodes, both owned. `mon` = how many are monitored. */
function complete(mon = 2) {
  return {
    season_number: 1,
    episode_monitored: mon,
    episodes: [
      { episode_number: 1, owned: true },
      { episode_number: 2, owned: true },
    ],
  };
}

function partial(mon = 2) {
  return {
    season_number: 1,
    episode_monitored: mon,
    episodes: [
      { episode_number: 1, owned: true },
      { episode_number: 2, owned: false },
    ],
  };
}

function buttons(host: HTMLElement): string[] {
  return Array.from(host.querySelectorAll('button')).map(
    (b) => b.querySelector('.discog-btn-text')?.textContent ?? '',
  );
}

describe('the season action bar', () => {
  it('offers management on a season with nothing missing', () => {
    // This is the case the old bar dropped entirely.
    expect(buttons(bar(complete()))).toEqual(['Monitored', 'Clear failures']);
  });

  it('adds the acquisition actions when episodes are missing', () => {
    expect(buttons(bar(partial()))).toEqual([
      'Grab season',
      'Manual search',
      'Wishlist season',
      'Monitored',
      'Clear failures',
    ]);
    expect(bar(partial()).textContent).toContain('1 missing');
  });

  it('states which way the monitor toggle will flip', () => {
    const on = bar(complete(2)).querySelector('[data-vd-season-monitor]') as HTMLElement;
    expect(on.getAttribute('data-vd-season-monitor')).toBe('0'); // monitored -> turn off
    expect(on.className).toContain('vd-season-mon--on');

    const off = bar(complete(0)).querySelector('[data-vd-season-monitor]') as HTMLElement;
    expect(off.getAttribute('data-vd-season-monitor')).toBe('1'); // unmonitored -> turn on
    expect(off.className).not.toContain('vd-season-mon--on');
    expect(off.textContent).toContain('Unmonitored');
  });

  it('hides clear-failures when there is no tmdb id to retry against', () => {
    // The retry endpoint keys on tmdb_id; without one the button could only fail.
    expect(buttons(bar(complete(), { tmdb_id: null }))).toEqual(['Monitored']);
  });

  it('offers no management on a preview, which has no library row', () => {
    expect(bar(complete(), { source: 'tmdb' }).innerHTML).toBe('');
    expect(buttons(bar(partial(), { source: 'tmdb' }))).toEqual([
      'Grab season',
      'Manual search',
      'Wishlist season',
    ]);
  });

  it('gives a youtube year acquisition only, never monitoring', () => {
    const host = bar(partial(), { source: 'youtube', kind: 'channel' });
    // No manual search on YouTube: the video IS the release, there is nothing to pick.
    expect(buttons(host)).toEqual(['Grab year', 'Wishlist year']);
    expect(host.querySelector('[data-vd-season-monitor]')).toBeNull();
    expect(host.querySelector('[data-vd-season-clearfail]')).toBeNull();
  });

  it('treats a filtered youtube view as not-the-season', () => {
    // A search result is a slice, so "grab everything missing" would grab the
    // wrong set. The old bar already knew this; keep it true after the lift.
    expect(bar(partial(), { source: 'youtube', kind: 'channel', q: 'anything' }).innerHTML).toBe('');
  });

  it('drops the grab buttons when the grab module never loaded', () => {
    expect(buttons(bar(partial(), { grab: false }))).toEqual(['Monitored', 'Clear failures']);
  });
});
