/**
 * the video dashboard's phone fixes, asserted as text. jsdom can't lay
 * anything out, so these were measured in chromium at 360 and 390 and pinned
 * here, or a cleanup puts them back.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const read = (path: string) =>
  readFileSync(resolve(process.cwd(), path), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

function mediaBlock(css: string, width: number): string {
  const m = new RegExp(`@media \\(max-width: ${width}px\\) \\{([\\s\\S]*?)\\n\\}`).exec(css);
  return m ? m[1] : '';
}

function rule(block: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const m = new RegExp(`(?:^|[,{}\\n])\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(block);
  return m ? m[1] : '';
}

describe('the next up billboard on a phone', () => {
  const phone = mediaBlock(read('src/routes/calendar/-ui/calendar.module.css'), 768);

  it('stacked panels size to their content', () => {
    // flex: 1 1 0 held each panel at its 150px floor and the bottom-anchored
    // text spilled out the top, so the first title was cut off
    expect(rule(phone, '.bbPanel:last-child')).toMatch(/flex:\s*0 0 auto/);
  });

  it('drops the left pad meant for the slanted edge', () => {
    expect(rule(phone, '.bbPanel .bbContent')).toMatch(/padding:\s*22px 20px 20px/);
  });
});

describe('the count tiles on a phone', () => {
  const css = read('src/routes/dashboard/-video/video-dashboard.module.css');
  const premier = read('static/dashboard-premier.css');

  it('are one over the other, each with its number beside what it is made of', () => {
    // the card follows the room it is given (a container query), so the
    // column beside the week and a phone get the same layout. the selector
    // doubles .tile to outrank the music rule (#dashboard-page .dash-tiles
    // .dash-tile.header-button)
    const narrow = css.slice(css.indexOf('@container (max-width: 640px)'));
    expect(rule(narrow, ':global(#dashboard-page) .tiles[data-rail] .tile.tile')).toMatch(
      /flex-direction:\s*row/,
    );
    // and on a phone the card itself is a column (the file has several 768
    // blocks, so this looks for the rule, not the first block)
    expect(premier).toMatch(
      /@media \(max-width: 768px\) \{\s*#dashboard-page\.dash-room\[data-media='video'\] \.room-board > \.dash-tiles\[data-rail='tiles'\] \{\s*flex-direction: column;/,
    );
  });

  it('shows the week as two rows of days, with today filling the second', () => {
    const phone = mediaBlock(css, 460);
    expect(rule(phone, '.days')).toMatch(/repeat\(4,/);
    expect(rule(phone, '.dayToday')).toMatch(/grid-column:\s*span 2/);
  });

  it('keeps the studio button on one line', () => {
    expect(rule(css, '.studioCta')).toMatch(/white-space:\s*nowrap/);
  });
});
