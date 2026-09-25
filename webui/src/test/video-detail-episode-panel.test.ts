import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * The expanded episode panel.
 *
 * It printed the episode description a second time (the row above already has
 * it, and TMDB returns the same string), then fourteen guest-star faces - most
 * of them a grey initial in a circle - which ended up louder than the episode's
 * own download and wishlist buttons.
 */

const SRC = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');

/** A row + its panel, wired the way renderEpisodes emits them (panel is the row's sibling). */
function expand(ex: Record<string, unknown>, rowDesc = '', showTmdb: number | null = 1396): HTMLElement {
  const host = document.createElement('div');
  host.innerHTML =
    `<div class="vd-ep">${rowDesc ? `<p class="vd-ep-desc">${rowDesc}</p>` : ''}</div>` +
    '<div class="vd-ep-extra"></div>';
  const panel = host.querySelector('.vd-ep-extra') as HTMLElement;
  const preamble = `
    function esc(s) { return String(s == null ? '' : s); }
    function badge() { return ''; }
    var data = { source: 'library' };
    var TMDB_LOGO = '';
  `;
  const bodies = ['episodeLinks', 'renderEpisodeExtra'].map((n) => extractFunction(n, SRC)).join('\n');
  const cap = SRC.slice(SRC.indexOf('var GUEST_VISIBLE'), SRC.indexOf('function renderEpisodeExtra'));
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const render = new Function(
    `${preamble}\n${cap}\n${bodies}\nreturn renderEpisodeExtra;`,
  )() as (p: HTMLElement, ex: unknown, t: unknown, s: unknown, e: unknown) => void;
  render(panel, ex, showTmdb, 1, 1);
  return panel;
}

function guests(n: number) {
  return Array.from({ length: n }, (_, i) => ({ name: `Actor ${i}`, character: `Role ${i}` }));
}

describe('the expanded episode panel', () => {
  it('does not repeat the description the row already shows', () => {
    const panel = expand({ overview: 'The Dessens receive a visitor.' }, 'The Dessens receive a visitor.');
    expect(panel.querySelector('.vd-ep-extra-ov')).toBeNull();
  });

  it('ignores incidental whitespace when comparing', () => {
    const panel = expand({ overview: '  A visitor arrives. ' }, 'A visitor arrives.');
    expect(panel.querySelector('.vd-ep-extra-ov')).toBeNull();
  });

  it('still shows a description the row does not have', () => {
    // A row with no description of its own, or a genuinely longer TMDB synopsis.
    expect(expand({ overview: 'A longer synopsis.' }, '').querySelector('.vd-ep-extra-ov')
      ?.textContent).toBe('A longer synopsis.');
    expect(expand({ overview: 'A longer synopsis.' }, 'Short one.')
      .querySelector('.vd-ep-extra-ov')).not.toBeNull();
  });

  it('renders every guest but folds the overflow behind one button', () => {
    const panel = expand({ guest_stars: guests(14) });
    // All fourteen stay in the DOM - CSS hides the tail, so expanding never
    // re-renders or re-fetches.
    expect(panel.querySelectorAll('.vd-guest')).toHaveLength(14);
    const more = panel.querySelector('[data-vd-guests-all]') as HTMLElement;
    expect(more.textContent).toBe('+6 more');
  });

  it('offers no expander when everyone already fits', () => {
    expect(expand({ guest_stars: guests(8) }).querySelector('[data-vd-guests-all]')).toBeNull();
    expect(expand({ guest_stars: guests(3) }).querySelector('[data-vd-guests-all]')).toBeNull();
  });

  it('hides the tail with a rule that matches what the button promises', () => {
    // The CSS cut-off and GUEST_VISIBLE have to agree, or the button's count lies.
    const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');
    const visible = Number(/var GUEST_VISIBLE = (\d+)/.exec(SRC)?.[1]);
    expect(visible).toBe(8);
    expect(CSS).toContain(`> .vd-guest:nth-child(n + ${visible + 1}) { display: none; }`);
  });

  it('says so plainly when there is genuinely nothing to show', () => {
    // This empty-state was unreachable before: `html` always held the wrapper
    // div, so `html || fallback` never took the fallback and the panel opened
    // as a blank box. It is rare - a show with a tmdb id always yields at least
    // an episode link - so it needs a case with no id and no extras.
    expect(expand({}, '', null).textContent).toContain('No extra info.');
  });

  it('still opens onto the still image when that is all there is', () => {
    const panel = expand({ still_url: '/still.jpg' }, '', null);
    expect(panel.querySelector('.vd-ep-extra-still')).not.toBeNull();
    expect(panel.textContent).toContain('No extra info.');
  });
});
