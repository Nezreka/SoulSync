import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * Hero density.
 *
 * The show hero was carrying nine same-sized buttons across three rows, so
 * "Play on Plex" and "Manage Poster" had identical weight, and the star rating
 * was printed in the meta line while IMDb/Trakt/TVmaze printed theirs two rows
 * below - four numbers for one question, disagreeing.
 */

const SRC = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');

describe('the star rating is not printed twice', () => {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const hasRatingRow = new Function(
    `${extractFunction('hasRatingRow', SRC)}\nreturn hasRatingRow;`,
  )() as (d: Record<string, unknown>) => boolean;

  it('defers to the ratings row whenever that row has anything', () => {
    expect(hasRatingRow({ imdb_rating: 7.6 })).toBe(true);
    expect(hasRatingRow({ trakt_rating: 7.6 })).toBe(true);
    expect(hasRatingRow({ tvmaze_rating: 7.4 })).toBe(true);
    expect(hasRatingRow({ metacritic: 0 })).toBe(true); // a zero score is a score
    expect(hasRatingRow({ rt_rating: 0 })).toBe(true);
  });

  it('is actually consulted before the meta line prints a star', () => {
    // Testing the helper alone is not a guard: deleting the call site leaves
    // hasRatingRow perfectly correct and unused, and the duplicate comes back.
    // renderBillboard closes over too much of the module to run here, so this
    // pins the call site itself.
    const body = extractFunction('renderBillboard', SRC);
    const star = body.split('\n').find((l) => l.includes('vd-score')) ?? '';
    expect(star, 'the meta line still prints a star').not.toBe('');
    const guard = body.slice(0, body.indexOf(star));
    expect(
      guard.split('\n').slice(-4).join('\n'),
      'the star push is no longer guarded by hasRatingRow',
    ).toContain('hasRatingRow(d)');
  });

  it('lets the meta line carry the score when nothing else will', () => {
    expect(hasRatingRow({ rating: 7.8 })).toBe(false);
    expect(hasRatingRow({})).toBe(false);
    expect(hasRatingRow({ imdb_rating: null, trakt_rating: 0 })).toBe(false);
  });
});

describe('the management actions collapse into one button', () => {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const moreCount = new Function(
    `${extractFunction('moreCount', SRC)}\nreturn moreCount;`,
  )() as (html: string) => number;

  it('counts the actions destined for the menu', () => {
    expect(moreCount('')).toBe(0);
    expect(moreCount('<button a></button>')).toBe(1);
    expect(moreCount('<button a></button><button b></button><button c></button>')).toBe(3);
  });

  it('keeps a lone action in the main row rather than hiding it behind a menu', () => {
    // A "More" button guarding one item is pure overhead - two clicks for what
    // was one, and the label tells you nothing about what is inside.
    const body = extractFunction('renderActions', SRC);
    expect(body).toContain('(moreCount(more) === 1) ? more :');
  });

  it('routes every management action into the menu, not the main row', () => {
    const body = extractFunction('renderActions', SRC);
    for (const act of ['poster', 'manage', 'sync-show', 'sync-movie', 'watched-toggle']) {
      const line = body.split('\n').find((l) => l.includes(`data-vd-act="${act}"`)) ?? '';
      expect(line, `${act} should build into the overflow, not the main row`).not.toMatch(
        /^\s*html \+=/,
      );
    }
  });

  it('leaves the primary actions in the main row', () => {
    const body = extractFunction('renderActions', SRC);
    for (const act of ['get', 'missing', 'wishtoggle', 'trailer']) {
      const line = body.split('\n').find((l) => l.includes(`data-vd-act="${act}"`)) ?? '';
      expect(line, `${act} is a primary action and must stay visible`).not.toContain('more +=');
    }
  });
});

describe('the overflow menu stays inside the hero', () => {
  const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');
  const rule = CSS.slice(CSS.indexOf('\n.vd-more-menu {'),
                         CSS.indexOf('}', CSS.indexOf('\n.vd-more-menu {')));

  it('opens upward, because the billboard clips whatever drops below', () => {
    // .vd-billboard has overflow:hidden - its backdrop runs a Ken Burns scale to
    // 1.1 and would spill out of the hero otherwise. A menu dropping DOWN from a
    // button near the bottom loses its last item with no sign it was ever there.
    expect(rule).toContain('bottom: calc(100% + 6px)');
    expect(rule).not.toMatch(/top:\s*calc\(100%/);
  });

  it('scrolls rather than truncating, whatever the room', () => {
    expect(rule).toContain('max-height');
    expect(rule).toContain('overflow-y: auto');
  });

  it('still clips the backdrop, which is what the overflow was for', () => {
    const bb = CSS.slice(CSS.indexOf('\n.vd-billboard {'),
                         CSS.indexOf('}', CSS.indexOf('\n.vd-billboard {')));
    expect(bb).toContain('overflow: hidden');
  });
});

describe('the overflow menu opens and closes', () => {
  function wire() {
    const host = document.createElement('div');
    host.innerHTML =
      '<div class="vd-more" data-vd-more>' +
      '<button class="vd-manage-btn vd-more-btn" data-vd-act="more" aria-expanded="false"></button>' +
      '<div class="vd-more-menu" data-vd-more-menu hidden></div></div>';
    const preamble = `function q(sel) { return host.querySelector(sel); }`;
    const bodies = ['toggleMoreMenu', 'closeMoreMenu'].map((n) => extractFunction(n, SRC)).join('\n');
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const api = new Function(
      'host',
      `${preamble}\n${bodies}\nreturn { toggleMoreMenu: toggleMoreMenu, closeMoreMenu: closeMoreMenu };`,
    )(host) as { toggleMoreMenu: (b: Element) => void; closeMoreMenu: () => void };
    return {
      host,
      api,
      btn: host.querySelector('[data-vd-act="more"]') as HTMLElement,
      menu: host.querySelector('[data-vd-more-menu]') as HTMLElement,
    };
  }

  it('toggles the menu and reports it to assistive tech', () => {
    const { api, btn, menu } = wire();
    expect(menu.hidden).toBe(true);
    api.toggleMoreMenu(btn);
    expect(menu.hidden).toBe(false);
    expect(btn.getAttribute('aria-expanded')).toBe('true');
    api.toggleMoreMenu(btn);
    expect(menu.hidden).toBe(true);
    expect(btn.getAttribute('aria-expanded')).toBe('false');
  });

  it('closes cleanly even when it was already closed', () => {
    const { api, btn, menu } = wire();
    api.closeMoreMenu();
    expect(menu.hidden).toBe(true);
    expect(btn.getAttribute('aria-expanded')).toBe('false');
  });

  it('resets aria-expanded when closed from outside', () => {
    const { api, btn, menu } = wire();
    api.toggleMoreMenu(btn);
    api.closeMoreMenu();
    expect(menu.hidden).toBe(true);
    expect(btn.getAttribute('aria-expanded')).toBe('false');
  });
});

describe('hero cover art earns its space', () => {
  it('only shows a cover when it adds identity rather than duplicating strong title art', () => {
    const body = extractFunction('renderBillboard', SRC);
    expect(body).toContain("d.source === 'youtube' || (!d.logo && !d.has_backdrop)");
    expect(body).toContain('vd-bb-content--no-cover');
  });
});