import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * The bar over a YouTube channel's year, or a playlist.
 *
 * A TV season's bar is React now (features/video-library/episode-list.tsx,
 * tested beside it). What video-detail.js still draws is the YouTube one:
 * acquisition only, because a channel has no episodes to monitor and no
 * release to pick.
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
    ${extractFunction('grabScope', SRC)}
  `;
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const build = new Function(
    `${preamble}\n${extractFunction('seasonActionsHtml', SRC)}\nreturn seasonActionsHtml;`,
  )() as (s: unknown) => string;
  const host = document.createElement('div');
  host.innerHTML = build(season);
  return host;
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

/** every action the bar offers, visible or behind its ⋯ menu (not the toggle). */
function buttons(host: HTMLElement): string[] {
  // the Monitored switch is not one of the action buttons; it has its own test
  return Array.from(host.querySelectorAll('button:not([data-vd-pop]):not([role="switch"])')).map(
    (b) => b.querySelector('.discog-btn-text')?.textContent ?? '',
  );
}

describe('the youtube bar', () => {
  it('gives a youtube year acquisition only, never monitoring', () => {
    const host = bar(partial(), { source: 'youtube', kind: 'channel' });
    // No manual search on YouTube: the video IS the release, there is nothing to pick.
    expect(buttons(host)).toEqual(['Grab year', 'Wishlist year']);
    expect(host.querySelector('[data-vd-season-monitor]')).toBeNull();
    expect(host.querySelector('[data-vd-season-clearfail]')).toBeNull();
  });

  it('a playlist grabs all of it, not a year it does not have', () => {
    const host = bar(partial(), { source: 'youtube', kind: 'playlist' });
    expect(buttons(host)).toEqual(['Grab all', 'Wishlist all']);
    expect(host.querySelector('[data-vd-pop]')?.getAttribute('aria-label')).toBe(
      'More for this playlist',
    );
  });

  it('treats a filtered youtube view as not-the-season', () => {
    // A search result is a slice, so "grab everything missing" would grab the
    // wrong set. The old bar already knew this; keep it true after the lift.
    expect(bar(partial(), { source: 'youtube', kind: 'channel', q: 'anything' }).innerHTML).toBe(
      '',
    );
  });

  it('draws nothing for a show: that bar is the React list’s', () => {
    expect(bar(partial()).innerHTML).toBe('');
  });
});
